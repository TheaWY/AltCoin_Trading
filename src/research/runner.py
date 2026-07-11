"""Nightly research runner — consumes the experiment queue.

Each candidate is evaluated in isolated subprocesses over walk-forward test
windows. Results are persisted for promotion gates, while fresh evaluations
use only data that arrived after the candidate was created.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data.storage import get_storage
from src.research import decisions
from src.research.promotion import _ensure_schema
from src.research.robust_stats import max_trials_for_history

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ONCE = PROJECT_ROOT / "scripts" / "run_backtest_once.py"
MARKER_BEGIN = "===RESULT_JSON_BEGIN==="
MARKER_END = "===RESULT_JSON_END==="

# Historical research must stop at this boundary. Data after candidate creation
# is handled separately by the fresh-evaluation gate.
HOLDOUT_START = os.getenv("RESEARCH_HOLDOUT_START", "2026-06-01")
WINDOW_TEST_DAYS = int(os.getenv("RESEARCH_WINDOW_TEST_DAYS", "60"))
WINDOW_COUNT = int(os.getenv("RESEARCH_WINDOW_COUNT", "18"))
WINDOW_STEP_DAYS = int(os.getenv("RESEARCH_WINDOW_STEP_DAYS", str(WINDOW_TEST_DAYS)))
SYMBOLS = os.getenv("RESEARCH_SYMBOLS", "BTC/USDT,ETH/USDT")
TIMEOUT_S = int(os.getenv("RESEARCH_RUN_TIMEOUT_S", "600"))


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _date_to_ts(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _windows() -> list[tuple[str, str]]:
    """Rolling test windows ending at the holdout boundary, newest last."""
    end = datetime.strptime(HOLDOUT_START, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    out: list[tuple[str, str]] = []
    for i in range(WINDOW_COUNT, 0, -1):
        w_end = end.timestamp() - (i - 1) * WINDOW_STEP_DAYS * 86_400
        w_start = w_end - WINDOW_TEST_DAYS * 86_400
        out.append(
            (
                datetime.fromtimestamp(w_start, tz=timezone.utc).strftime("%Y-%m-%d"),
                datetime.fromtimestamp(w_end, tz=timezone.utc).strftime("%Y-%m-%d"),
            )
        )
    return out


def _run_subprocess(overrides: dict[str, Any], start: str, end: str) -> dict[str, Any] | None:
    env = {**os.environ, **{key: str(value) for key, value in overrides.items()}}

    # ACTIVE_STRATEGIES takes precedence over ACTIVE_STRATEGY in config.py. If
    # the parent worker has ACTIVE_STRATEGIES set, an experiment that changes
    # only ACTIVE_STRATEGY would otherwise test the wrong strategy.
    if "ACTIVE_STRATEGY" in overrides and "ACTIVE_STRATEGIES" not in overrides:
        env["ACTIVE_STRATEGIES"] = str(overrides["ACTIVE_STRATEGY"])

    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(RUN_ONCE),
                "--start",
                start,
                "--end",
                end,
                "--symbols",
                SYMBOLS,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        return None

    if proc.returncode != 0:
        return None
    out = proc.stdout
    if MARKER_BEGIN not in out or MARKER_END not in out:
        return None
    payload = out.split(MARKER_BEGIN, 1)[1].split(MARKER_END, 1)[0].strip()
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _aggregate(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate windows without using window PnL as a fake profit factor.

    Profit factor is gross winning *trade* PnL divided by gross losing trade
    PnL. The previous implementation compared profitable windows with losing
    windows, which could materially overstate or understate strategy quality.
    """
    trades = sum(int(window.get("trade_count", 0)) for window in windows)
    total = sum(float(window.get("total_pnl", 0.0)) for window in windows)
    fees = sum(float(window.get("total_fees", 0.0)) for window in windows)
    gross = sum(float(window.get("gross_pnl", 0.0)) for window in windows)
    trade_pnls = [
        float(pnl)
        for window in windows
        for pnl in (window.get("trade_pnls") or [])
    ]
    gross_wins = sum(pnl for pnl in trade_pnls if pnl > 0)
    gross_losses = sum(pnl for pnl in trade_pnls if pnl < 0)
    profit_factor = (
        gross_wins / abs(gross_losses)
        if gross_losses
        else (999.0 if gross_wins else 0.0)
    )
    return {
        "trade_count": trades,
        "total_pnl": round(total, 4),
        "gross_pnl": round(gross, 4),
        "total_fees": round(fees, 4),
        "expectancy": round(total / trades, 6) if trades else 0.0,
        "profit_factor": round(profit_factor, 4),
        "positive_windows": sum(1 for window in windows if window.get("total_pnl", 0) > 0),
        "window_count": len(windows),
        "trade_pnl_samples": len(trade_pnls),
    }


def _claim_experiment(storage: Any, experiment_id: int) -> bool:
    """Atomically claim one queued row so concurrent runners cannot duplicate it."""
    with storage._connect() as conn:  # noqa: SLF001
        cursor = conn.execute(
            "UPDATE experiments SET status = 'running' WHERE id = ? AND status = 'queued'",
            (experiment_id,),
        )
        return int(cursor.rowcount or 0) == 1


def run_experiments(max_runs: int) -> dict[str, Any]:
    _ensure_schema()
    storage = get_storage()

    history_years = ((WINDOW_COUNT - 1) * WINDOW_STEP_DAYS + WINDOW_TEST_DAYS) / 365.0
    budget_override = int(os.getenv("RESEARCH_TRIAL_BUDGET", "0"))
    budget = budget_override if budget_override > 0 else max_trials_for_history(history_years)
    with storage._connect() as conn:  # noqa: SLF001
        tried_row = conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE status IN ('done','failed')"
        ).fetchone()
        tried = int(_row_value(tried_row, "count", 0) or 0)
    remaining = max(budget - tried, 0)
    if remaining <= 0:
        decisions.log(
            "runner",
            "trial_budget_refused",
            detail={
                "budget": budget,
                "tried": tried,
                "history_years": round(history_years, 2),
                "fix": "extend history rather than increasing search breadth",
            },
        )
        return {
            "attempted": 0,
            "done": 0,
            "failed": 0,
            "trial_budget": {"budget": budget, "tried": tried},
        }

    max_runs = min(max_runs, remaining)
    with storage._connect() as conn:  # noqa: SLF001
        queue = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM experiments WHERE status = 'queued' "
                "ORDER BY priority ASC, created_at ASC LIMIT ?",
                (max_runs,),
            ).fetchall()
        ]

    windows = _windows()
    done = 0
    failed = 0
    claimed = 0
    for exp in queue:
        if not _claim_experiment(storage, int(exp["id"])):
            continue
        claimed += 1
        overrides = json.loads(exp["config_json"])
        decisions.log(
            "runner",
            "experiment_started",
            exp["config_hash"],
            {"priority": exp["priority"]},
        )

        window_results: list[dict[str, Any]] = []
        ok = True
        for start, end in windows:
            result = _run_subprocess(overrides, start, end)
            if result is None:
                ok = False
                break
            window_results.append({"start": start, "end": end, **result})

        with storage._connect() as conn:  # noqa: SLF001
            if ok:
                metrics = {"windows": window_results, "aggregate": _aggregate(window_results)}
                conn.execute(
                    "UPDATE experiments SET status = 'done', finished_at = ?, "
                    "metrics_json = ?, period_start = ?, period_end = ?, symbols = ? "
                    "WHERE id = ? AND status = 'running'",
                    (
                        int(time.time()),
                        json.dumps(metrics),
                        _date_to_ts(windows[0][0]),
                        _date_to_ts(windows[-1][1]),
                        SYMBOLS,
                        exp["id"],
                    ),
                )
                done += 1
                aggregate = metrics["aggregate"]
                decisions.log(
                    "runner",
                    "experiment_done",
                    exp["config_hash"],
                    {
                        "expectancy": aggregate["expectancy"],
                        "pf": aggregate["profit_factor"],
                        "trades": aggregate["trade_count"],
                        "windows": f"{aggregate['positive_windows']}/{aggregate['window_count']}+",
                    },
                )
            else:
                conn.execute(
                    "UPDATE experiments SET status = 'failed', finished_at = ? "
                    "WHERE id = ? AND status = 'running'",
                    (int(time.time()), exp["id"]),
                )
                failed += 1
                decisions.log("runner", "experiment_failed", exp["config_hash"])

    return {
        "attempted": claimed,
        "done": done,
        "failed": failed,
        "windows": windows,
        "trial_budget": {"budget": budget, "tried": tried + done + failed},
    }


def run_fresh_evals(max_runs: int = 50) -> dict[str, Any]:
    """Replay done experiments over data collected strictly after creation."""
    _ensure_schema()
    storage = get_storage()
    now = int(time.time())
    with storage._connect() as conn:  # noqa: SLF001
        candidates = [
            dict(row)
            for row in conn.execute(
                "SELECT e.* FROM experiments e WHERE e.status = 'done' "
                "AND e.is_champion_baseline = 0 "
                "AND (? - e.created_at) >= 86400 "
                "ORDER BY e.finished_at DESC LIMIT ?",
                (now, max_runs),
            ).fetchall()
        ]

    evaluated = 0
    for exp in candidates:
        start_ts = int(exp["created_at"])
        start = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
        end = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        result = _run_subprocess(json.loads(exp["config_json"]), start, end)
        if result is None:
            continue
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT OR IGNORE INTO fresh_evals (config_hash, eval_start, eval_end, "
                "trade_count, expectancy, profit_factor, total_pnl, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    exp["config_hash"],
                    start_ts,
                    now,
                    result["trade_count"],
                    result["expectancy"],
                    result["profit_factor"],
                    result["total_pnl"],
                    now,
                ),
            )
        evaluated += 1
    return {"fresh_evaluated": evaluated, "candidates": len(candidates)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-runs",
        type=int,
        default=int(os.getenv("RESEARCH_MAX_RUNS_PER_NIGHT", "200")),
    )
    parser.add_argument("--fresh-only", action="store_true")
    args = parser.parse_args()
    report: dict[str, Any] = {}
    if not args.fresh_only:
        report["experiments"] = run_experiments(args.max_runs)
    report["fresh"] = run_fresh_evals()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
