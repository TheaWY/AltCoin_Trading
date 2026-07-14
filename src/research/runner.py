"""Nightly research runner — consumes the experiment queue.

For each queued experiment (priority order):
  1. Spawn scripts/run_backtest_once.py in a SUBPROCESS with the challenger
     config injected as environment variables (config.py reads env at import,
     so subprocess isolation is what makes many configs per night possible).
  2. Walk-forward: rolling windows (train handled implicitly by using fixed
     rule parameters — windows here are TEST slices), aggregate per-window
     + overall metrics into experiments.metrics_json.
  3. Enforce the held-out guard: windows never extend past HOLDOUT_START.

Then fresh evals: for every 'done' non-baseline experiment, replay its config
over data collected AFTER the experiment was created (capped at now) and
record into fresh_evals — the feed for promotion gate 2.

    python -m src.research.runner --max-runs 200
    python -m src.research.runner --fresh-only
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import config
from src.data.storage import get_storage
from src.research import decisions
from src.research.promotion import _ensure_schema
from src.research.robust_stats import max_trials_for_history

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ONCE = PROJECT_ROOT / "scripts" / "run_backtest_once.py"
MARKER_BEGIN = "===RESULT_JSON_BEGIN==="
MARKER_END = "===RESULT_JSON_END==="

# Held-out data: never used by research. Opened exactly once at final
# promotion review, manually. Keep in sync with your held-out policy.
HOLDOUT_START = os.getenv("RESEARCH_HOLDOUT_START", "2026-06-01")

WINDOW_TEST_DAYS = int(os.getenv("RESEARCH_WINDOW_TEST_DAYS", "60"))
WINDOW_COUNT = int(os.getenv("RESEARCH_WINDOW_COUNT", "18"))
# step < test length = overlapping windows (more active-regime coverage,
# but overlap adds no independent data — the trial budget uses SPAN, not count)
WINDOW_STEP_DAYS = int(os.getenv("RESEARCH_WINDOW_STEP_DAYS", str(WINDOW_TEST_DAYS)))
SYMBOLS = os.getenv("RESEARCH_SYMBOLS", "BTC/USDT,ETH/USDT")
TIMEOUT_S = int(os.getenv("RESEARCH_RUN_TIMEOUT_S", "600"))
PARALLEL_EXPERIMENTS = max(1, int(os.getenv("RESEARCH_PARALLEL_EXPERIMENTS", "1")))


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def _date_to_ts(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _windows() -> list[tuple[str, str]]:
    """Rolling test windows ending at the holdout boundary, newest last."""
    end = datetime.strptime(HOLDOUT_START, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    out: list[tuple[str, str]] = []
    for i in range(WINDOW_COUNT, 0, -1):
        w_end = end.timestamp() - (i - 1) * WINDOW_STEP_DAYS * 86400
        w_start = w_end - WINDOW_TEST_DAYS * 86400
        out.append(
            (
                datetime.fromtimestamp(w_start, tz=timezone.utc).strftime("%Y-%m-%d"),
                datetime.fromtimestamp(w_end, tz=timezone.utc).strftime("%Y-%m-%d"),
            )
        )
    return out


def _run_subprocess(overrides: dict[str, Any], start: str, end: str) -> dict[str, Any] | None:
    env = {**os.environ, **{k: str(v) for k, v in overrides.items()}}
    try:
        proc = subprocess.run(
            [sys.executable, str(RUN_ONCE), "--start", start, "--end", end,
             "--symbols", SYMBOLS],
            env=env, capture_output=True, text=True, timeout=TIMEOUT_S,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        return None
    out = proc.stdout
    if MARKER_BEGIN not in out or MARKER_END not in out:
        return None
    payload = out.split(MARKER_BEGIN, 1)[1].split(MARKER_END, 1)[0].strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _hedge_aggregate(windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Merge every window's raw hedge_trades into one exact (not
    weighted-average-of-averages) net-of-funding expectancy and basis-risk
    split. research_decisions, subject='rel_strength_market_neutral',
    additions 2 & 3. None when no window produced any hedge trades."""
    from src.engine import hedge as hedge_engine  # noqa: PLC0415 — avoid import cost for non-hedge strategies

    trades = [t for w in windows for t in (w.get("hedge_trades") or [])]
    if not trades:
        return None

    def pct(pnl: float, notional: float) -> float | None:
        return (pnl / notional * 100.0) if notional else None

    with_funding = [float(t["pnl"] or 0.0) for t in trades]
    without_funding = [float(t["pnl"] or 0.0) - float(t.get("funding_pnl") or 0.0) for t in trades]

    buckets: dict[str, list[dict[str, Any]]] = {
        "correlation_held": [], "correlation_spiked": [], "unknown": [],
    }
    for t in trades:
        status = hedge_engine.basis_risk_status(
            t.get("hedge_beta"), t.get("realized_beta"), config.BASIS_RISK_BETA_TOLERANCE
        )
        buckets[status].append(t)

    def bucket_summary(bucket: list[dict[str, Any]]) -> dict[str, Any]:
        if not bucket:
            return {"n": 0, "avg_pnl": None}
        pnls = [float(t["pnl"] or 0.0) for t in bucket]
        return {"n": len(bucket), "avg_pnl": round(sum(pnls) / len(pnls), 6)}

    return {
        "trades": len(trades),
        "avg_pnl_with_funding": round(sum(with_funding) / len(with_funding), 6),
        "avg_pnl_without_funding": round(sum(without_funding) / len(without_funding), 6),
        "avg_funding_pnl": round(
            sum(float(t.get("funding_pnl") or 0.0) for t in trades) / len(trades), 6
        ),
        "basis_risk": {
            "correlation_held": bucket_summary(buckets["correlation_held"]),
            "correlation_spiked": bucket_summary(buckets["correlation_spiked"]),
            "unknown": bucket_summary(buckets["unknown"]),
        },
    }


def _aggregate(windows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = sum(w["trade_count"] for w in windows)
    total = sum(w["total_pnl"] for w in windows)
    fees = sum(w["total_fees"] for w in windows)
    slippage_cost = sum(w.get("total_slippage_cost", 0.0) for w in windows)
    gross = sum(w["gross_pnl"] for w in windows)
    # Pooled MFE-capture distribution across all windows' trades -- the
    # standing exit-geometry metric (how much of each trade's favorable
    # move the exit captured).
    captures = sorted(
        t["mfe_capture"]
        for w in windows for t in (w.get("trade_detail") or [])
        if t.get("mfe_capture") is not None
    )
    category_checks = sum(int(w.get("category_checks") or 0) for w in windows)
    category_present = sum(int(w.get("category_present") or 0) for w in windows)
    wins = sum(w["total_pnl"] for w in windows if w["total_pnl"] > 0)
    losses = sum(w["total_pnl"] for w in windows if w["total_pnl"] <= 0)
    return {
        "trade_count": trades,
        "total_pnl": round(total, 4),
        "gross_pnl": round(gross, 4),
        "total_fees": round(fees, 4),
        "total_slippage_cost": round(slippage_cost, 4),
        "mfe_capture_median": (
            round(captures[len(captures) // 2], 4) if captures else None
        ),
        "mfe_capture_p25": (
            round(captures[len(captures) // 4], 4) if captures else None
        ),
        "mfe_capture_p75": (
            round(captures[3 * len(captures) // 4], 4) if captures else None
        ),
        "mfe_capture_n": len(captures),
        "expectancy": round(total / trades, 6) if trades else 0.0,
        "profit_factor": round(wins / abs(losses), 4) if losses else (999.0 if wins else 0.0),
        "positive_windows": sum(1 for w in windows if w["total_pnl"] > 0),
        "window_count": len(windows),
        "category_checks": category_checks,
        "category_present": category_present,
        "category_coverage_pct": round(
            category_present / category_checks * 100.0, 2
        ) if category_checks else None,
        # None for non-hedge strategies; the plain "expectancy" field above
        # is ALREADY net-of-funding for hedge trades (trade.pnl folds
        # funding_pnl in at close via hedge.combined_trade_pnl) -- this key
        # additionally reports what it would have been WITHOUT funding and
        # the basis-risk split, so funding's contribution is visible, not
        # assumed.
        "hedge_aggregate": _hedge_aggregate(windows),
    }


def _run_experiment_windows(
    exp: dict[str, Any],
    windows: list[tuple[str, str]],
) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
    overrides = json.loads(exp["config_json"])
    window_results = []
    ok = True
    for start, end in windows:
        result = _run_subprocess(overrides, start, end)
        if result is None:
            ok = False
            break
        window_results.append({"start": start, "end": end, **result})
    return exp, ok, window_results


def run_experiments(max_runs: int) -> dict[str, Any]:
    _ensure_schema()
    storage = get_storage()

    # MinBTL trial budget: refuse to exceed the number of independent configs
    # this much walk-forward history can statistically support (Bailey et al.
    # 2014). Without this cap the nightly queue is an overfitting machine.
    # calendar span actually covered: (count-1)*step + test — overlapping
    # windows do NOT extend the span, so they do not raise the budget.
    history_years = ((WINDOW_COUNT - 1) * WINDOW_STEP_DAYS + WINDOW_TEST_DAYS) / 365.0
    budget_override = int(os.getenv("RESEARCH_TRIAL_BUDGET", "0"))
    budget = budget_override if budget_override > 0 else max_trials_for_history(history_years)
    with storage._connect() as conn:  # noqa: SLF001
        tried_row = conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE status IN ('done','failed') "
            "AND counts_against_trial_budget = 1"
        ).fetchone()
        tried = int(_row_value(tried_row, "count", 0) or 0)
    remaining = max(budget - tried, 0)
    if remaining <= 0:
        decisions.log("runner", "trial_budget_refused", detail={
            "budget": budget, "tried": tried, "history_years": round(history_years, 2),
            "fix": "extend history (load more years) or raise window count",
        })
        return {"attempted": 0, "done": 0, "failed": 0,
                "trial_budget": {"budget": budget, "tried": tried}}
    max_runs = min(max_runs, remaining)
    with storage._connect() as conn:  # noqa: SLF001
        queue = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM experiments WHERE status = 'queued' "
                "ORDER BY priority ASC, created_at ASC LIMIT ?",
                (max_runs,),
            ).fetchall()
        ]

    windows = _windows()
    done = failed = 0
    for exp in queue:
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE experiments SET status = 'running' WHERE id = ?", (exp["id"],)
            )
        decisions.log("runner", "experiment_started", exp["config_hash"],
                      {"priority": exp["priority"]})

    if PARALLEL_EXPERIMENTS > 1 and len(queue) > 1:
        completed = []
        with ThreadPoolExecutor(max_workers=min(PARALLEL_EXPERIMENTS, len(queue))) as pool:
            futures = [pool.submit(_run_experiment_windows, exp, windows) for exp in queue]
            for future in as_completed(futures):
                completed.append(future.result())
    else:
        completed = [_run_experiment_windows(exp, windows) for exp in queue]

    for exp, ok, window_results in completed:
        with storage._connect() as conn:  # noqa: SLF001
            if ok:
                metrics = {"windows": window_results, "aggregate": _aggregate(window_results)}
                # BENCHMARK_PERCENTILE: where this result falls in the
                # random-entry distribution over the SAME windows. None (and
                # therefore absent) until ops/generate_random_ensemble.py has
                # produced an ensemble for this window config. A strategy
                # that can't beat random entries with identical risk
                # management isn't a strategy.
                try:
                    from src.research.benchmark_percentile import (
                        benchmark_percentile, windows_config_hash,
                    )

                    pct = benchmark_percentile(
                        storage, metrics["aggregate"]["total_pnl"],
                        windows_config_hash(windows, SYMBOLS),
                    )
                    if pct:
                        metrics["aggregate"].update(pct)
                except Exception:  # noqa: BLE001 -- percentile is enrichment, never blocks
                    pass
                conn.execute(
                    "UPDATE experiments SET status = 'done', finished_at = ?, "
                    "metrics_json = ?, period_start = ?, period_end = ?, symbols = ? "
                    "WHERE id = ?",
                    (int(time.time()), json.dumps(metrics), _date_to_ts(windows[0][0]),
                     _date_to_ts(windows[-1][1]), SYMBOLS, exp["id"]),
                )
                done += 1
                agg = metrics["aggregate"]
                decisions.log("runner", "experiment_done", exp["config_hash"], {
                    "expectancy": agg["expectancy"], "pf": agg["profit_factor"],
                    "trades": agg["trade_count"],
                    "windows": f"{agg['positive_windows']}/{agg['window_count']}+",
                })
            else:
                conn.execute(
                    "UPDATE experiments SET status = 'failed', finished_at = ? WHERE id = ?",
                    (int(time.time()), exp["id"]),
                )
                failed += 1
                decisions.log("runner", "experiment_failed", exp["config_hash"])
    return {"attempted": len(queue), "done": done, "failed": failed,
            "windows": windows,
            "trial_budget": {"budget": budget, "tried": tried + done + failed}}


def run_fresh_evals(max_runs: int = 50) -> dict[str, Any]:
    """Replay done experiments over data collected AFTER they were created."""
    _ensure_schema()
    storage = get_storage()
    now = int(time.time())
    with storage._connect() as conn:  # noqa: SLF001
        candidates = [
            dict(r)
            for r in conn.execute(
                "SELECT e.* FROM experiments e WHERE e.status = 'done' "
                "AND e.is_champion_baseline = 0 "
                "AND (? - e.created_at) >= 86400 "        # at least 1 day of fresh data
                "ORDER BY e.finished_at DESC LIMIT ?",
                (now, max_runs),
            ).fetchall()
        ]
    evaluated = 0
    for exp in candidates:
        start_ts = int(exp["created_at"])
        start = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        end = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        result = _run_subprocess(json.loads(exp["config_json"]), start, end)
        if result is None:
            continue
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT OR IGNORE INTO fresh_evals (config_hash, eval_start, eval_end, "
                "trade_count, expectancy, profit_factor, total_pnl, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (exp["config_hash"], start_ts, now, result["trade_count"],
                 result["expectancy"], result["profit_factor"],
                 result["total_pnl"], now),
            )
        evaluated += 1
    return {"fresh_evaluated": evaluated, "candidates": len(candidates)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-runs", type=int,
                        default=int(os.getenv("RESEARCH_MAX_RUNS_PER_NIGHT", "200")))
    parser.add_argument("--fresh-only", action="store_true")
    args = parser.parse_args()
    report: dict[str, Any] = {}
    if not args.fresh_only:
        report["experiments"] = run_experiments(args.max_runs)
    report["fresh"] = run_fresh_evals()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
