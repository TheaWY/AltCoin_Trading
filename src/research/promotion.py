"""Champion-challenger promotion engine.

Pure Python, deterministic, no LLM anywhere. Runs on the Mac Mini via cron /
launchd / APScheduler. Zero external services.

The loop it closes:
  experiments (nightly walk-forward backtests over historical data)
    -> STAGE 1 gate: consistent across walk-forward windows, beats champion
    -> fresh_evals (the same config replayed over data collected AFTER the
       experiment was created — genuine out-of-sample that grows every day)
    -> STAGE 2 gate: still positive on fresh data over a minimum span
    -> promote: atomically write config_overrides.json (whitelisted keys only),
       snapshot the previous state, audit-log everything
    -> health: monitor live paper_trades since promotion; auto-rollback on
       degradation (this is the safety belt — improvement is automatic, and
       so is retreat)

Config changes apply on worker restart (config.py reads overrides at import).
The promote step touches data/restart.flag; wire launchd/watchdog to restart
the worker when that flag appears, or restart manually.

CLI:
  python -m src.research.promotion status
  python -m src.research.promotion promote-if-ready
  python -m src.research.promotion health
  python -m src.research.promotion rollback --reason "manual"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from src import config
from src.data.storage import get_storage
from src.research import decisions
from src.research.robust_stats import deflated_sharpe, sharpe

# ---------------------------------------------------------------------------
# Safety rails
# ---------------------------------------------------------------------------

# Only these env keys can ever be changed by automated promotion. Everything
# else (credentials, DB, direction policy, capital) is out of reach by design.
#
# CONFLUENCE_, LSR_, SETUP_*, BREAKOUT_, TSMOM_, and ACTIVE_STRATEGY are
# deliberately excluded even though they're varied in research_space.yaml:
# scripts/backtest.py (what every experiment runs through) calls
# strategy.generate_signal() and never reads these -- only the live-only
# evaluate_symbol() path (ENTRY_DECISION_ENGINE=evaluation) does. A backtest
# expectancy computed without ever exercising these keys is not evidence
# about them; promoting one would silently change live behavior on the
# strength of a number that had nothing to do with it. Re-add only once a
# validation path actually exercises evaluate_symbol().
OVERRIDE_KEY_PREFIXES = (
    "MEANREV_", "SCAN_", "COOLDOWN_", "FEE_", "FUNDING_RATE_", "CARRY_", "POS_SHORT_",
)
OVERRIDE_KEY_EXACT = frozenset(
    {
        "MIN_CONFIDENCE", "MAX_OPEN_POSITIONS", "CATEGORY_STRATEGY_MODE",
        # Exceptions to the SETUP_ ban above: scripts/backtest.py's
        # EVALUATION_ENGINE_STRATEGIES routes these ACTIVE_STRATEGY values
        # through evaluate_symbol() directly (the same function live's
        # ENTRY_DECISION_ENGINE=evaluation runs), bypassing
        # strategy.generate_signal() entirely -- unlike their siblings, a
        # backtest expectancy for these keys IS evidence about them, since
        # the exact same code path produced it. See
        # scripts/backtest.py:_EVALUATION_ENGINE_STRATEGY_OWN_FLAG.
        "SETUP_REL_STRENGTH_ENABLED",
        "SETUP_CAPITULATION_BAR_ENABLED",
        "SETUP_VOLUME_ZSCORE_ENABLED",
        "SETUP_PUMP24_EXTREME_ENABLED",
    }
)

OVERRIDES_PATH = config.DATA_DIR / "config_overrides.json"
OVERRIDES_PREV_PATH = config.DATA_DIR / "config_overrides.prev.json"
RESTART_FLAG_PATH = config.DATA_DIR / "restart.flag"


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


# Stage 1: walk-forward gates
S1_MIN_WINDOW_WIN_FRACTION = float(os.getenv("PROMO_S1_WINDOW_FRACTION", "0.66"))
S1_MIN_TRADES = int(os.getenv("PROMO_S1_MIN_TRADES", "30"))
S1_MIN_PROFIT_FACTOR = float(os.getenv("PROMO_S1_MIN_PF", "1.2"))

# DSR gate (between stage 1 and 2): deflated Sharpe corrected for the number
# of trials the queue has consumed and for non-normal trade returns.
PROMO_MIN_DSR = float(os.getenv("PROMO_MIN_DSR", "0.95"))
RESEARCH_CAPITAL = float(os.getenv("RESEARCH_CAPITAL", "730"))

# Stage 2: fresh-data gates (data collected after the experiment existed)
S2_MIN_FRESH_DAYS = float(os.getenv("PROMO_S2_MIN_FRESH_DAYS", "14"))
S2_MIN_TRADES = int(os.getenv("PROMO_S2_MIN_TRADES", "10"))

# Health / rollback
ROLLBACK_MIN_TRADES = int(os.getenv("PROMO_ROLLBACK_MIN_TRADES", "15"))
ROLLBACK_MAX_DD_PCT = float(os.getenv("PROMO_ROLLBACK_MAX_DD_PCT", "10.0"))
PROMOTION_COOLDOWN_HOURS = float(os.getenv("PROMO_COOLDOWN_HOURS", "72"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_hash TEXT NOT NULL UNIQUE,
    config_json TEXT NOT NULL,
    period_start INTEGER,
    period_end INTEGER,
    symbols TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 100,
    is_champion_baseline INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    finished_at INTEGER,
    metrics_json TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS fresh_evals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_hash TEXT NOT NULL,
    eval_start INTEGER NOT NULL,
    eval_end INTEGER NOT NULL,
    trade_count INTEGER NOT NULL,
    expectancy REAL NOT NULL,
    profit_factor REAL,
    total_pnl REAL NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(config_hash, eval_start, eval_end)
);

CREATE TABLE IF NOT EXISTS promotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_hash TEXT NOT NULL,
    action TEXT NOT NULL,
    overrides_json TEXT NOT NULL,
    previous_overrides_json TEXT,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""


def _migrate_experiments_columns(storage: Any) -> None:
    """counts_against_trial_budget: added 2026-07-13 so a completed trial
    that is a byte-identical duplicate of an earlier one (varied axes were
    no-ops on the code path that ran -- see OVERRIDE_KEY_PREFIXES comment
    above) can be excluded from the MinBTL trial-budget denominator without
    deleting the row or touching `status`. Defaults to 1 (counts) so every
    row -- past or future -- counts unless explicitly corrected; see
    research_decisions, subject='trial_budget_distinct_outcome_2026-07-13'
    for the one-time correction this enabled."""
    with storage._connect() as conn:  # noqa: SLF001
        if conn.is_postgres:
            conn.execute(
                "ALTER TABLE experiments ADD COLUMN IF NOT EXISTS "
                "counts_against_trial_budget INTEGER NOT NULL DEFAULT 1"
            )
            return
        existing = {
            row["name"] for row in conn.raw.execute("PRAGMA table_info(experiments)").fetchall()
        }
        if "counts_against_trial_budget" not in existing:
            conn.raw.execute(
                "ALTER TABLE experiments ADD COLUMN "
                "counts_against_trial_budget INTEGER NOT NULL DEFAULT 1"
            )


def _ensure_schema() -> None:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001 — research module, see NOTES
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
    _migrate_experiments_columns(storage)


def config_hash(config_dict: dict[str, Any]) -> str:
    canonical = json.dumps(config_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _now() -> int:
    return int(time.time())


def _is_allowed_key(key: str) -> bool:
    return key in OVERRIDE_KEY_EXACT or key.startswith(OVERRIDE_KEY_PREFIXES)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def stage1_pass(exp: dict[str, Any], champion: dict[str, Any] | None) -> tuple[bool, str]:
    """Walk-forward consistency + absolute floors + beats champion baseline."""
    metrics = json.loads(exp["metrics_json"] or "{}")
    windows = metrics.get("windows", [])
    agg = metrics.get("aggregate", {})

    if not windows:
        return False, "no walk-forward windows in metrics"
    positive = sum(1 for w in windows if w.get("total_pnl", 0) > 0)
    fraction = positive / len(windows)
    if fraction < S1_MIN_WINDOW_WIN_FRACTION:
        return False, f"window consistency {positive}/{len(windows)} < {S1_MIN_WINDOW_WIN_FRACTION:.0%}"
    if agg.get("trade_count", 0) < S1_MIN_TRADES:
        return False, f"trades {agg.get('trade_count', 0)} < {S1_MIN_TRADES}"
    if agg.get("expectancy", 0) <= 0:
        return False, "aggregate expectancy <= 0"
    if agg.get("profit_factor", 0) < S1_MIN_PROFIT_FACTOR:
        return False, f"PF {agg.get('profit_factor', 0):.2f} < {S1_MIN_PROFIT_FACTOR}"

    if champion is not None:
        champ_metrics = json.loads(champion["metrics_json"] or "{}")
        champ_agg = champ_metrics.get("aggregate", {})
        if agg.get("expectancy", 0) <= champ_agg.get("expectancy", 0):
            return False, (
                f"expectancy {agg.get('expectancy', 0):.3f} does not beat champion "
                f"{champ_agg.get('expectancy', 0):.3f} on the same windows"
            )
    return True, f"windows {positive}/{len(windows)} positive, beats champion"


def dsr_pass(exp: dict[str, Any]) -> tuple[bool, str]:
    """Deflated Sharpe gate. Trials N = all done/failed experiments so far;
    Var{SR} from other done experiments' trade-level Sharpes when >= 10 exist,
    else a harsh fallback (see robust_stats.deflated_sharpe)."""
    metrics = json.loads(exp["metrics_json"] or "{}")
    pnls: list[float] = []
    for w in metrics.get("windows", []):
        pnls.extend(w.get("trade_pnls", []))
    if not pnls:
        return False, "no trade-level pnls stored (re-run experiment)"
    returns = [p / RESEARCH_CAPITAL for p in pnls]

    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        n_trials_row = conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE status IN ('done','failed') "
            "AND counts_against_trial_budget = 1"
        ).fetchone()
        n_trials = int(_row_value(n_trials_row, "count", 0) or 0)
        rows = conn.execute(
            "SELECT metrics_json FROM experiments WHERE status='done' "
            "AND config_hash != ? LIMIT 500", (exp["config_hash"],)).fetchall()
    trial_srs = []
    for row in rows:
        mj = _row_value(row, "metrics_json")
        try:
            other = []
            for w in json.loads(mj or "{}").get("windows", []):
                other.extend(w.get("trade_pnls", []))
            if len(other) >= 10:
                trial_srs.append(sharpe([p / RESEARCH_CAPITAL for p in other]))
        except (TypeError, json.JSONDecodeError):
            continue

    d = deflated_sharpe(returns, n_trials=max(n_trials, 1), trial_sharpes=trial_srs)
    ok = d["dsr"] >= PROMO_MIN_DSR
    why = (f"DSR {d['dsr']:.3f} (SR {d['sr']:.3f} vs luck-hurdle SR0 {d['sr0']:.3f}, "
           f"N={n_trials} trials)")
    return ok, why


def stage2_pass(exp: dict[str, Any]) -> tuple[bool, str]:
    """Positive on data that did not exist when the experiment was created."""
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fresh_evals WHERE config_hash = ? AND eval_start >= ? "
                "ORDER BY eval_end",
                (exp["config_hash"], int(exp["created_at"])),
            ).fetchall()
        ]
    if not rows:
        return False, "no fresh-data evaluations yet"
    span_days = (rows[-1]["eval_end"] - rows[0]["eval_start"]) / 86_400
    trades = sum(r["trade_count"] for r in rows)
    total_pnl = sum(r["total_pnl"] for r in rows)
    weighted_expectancy = total_pnl / trades if trades else 0.0
    if span_days < S2_MIN_FRESH_DAYS:
        return False, f"fresh span {span_days:.1f}d < {S2_MIN_FRESH_DAYS}d"
    if trades < S2_MIN_TRADES:
        return False, f"fresh trades {trades} < {S2_MIN_TRADES}"
    if weighted_expectancy <= 0:
        return False, f"fresh expectancy {weighted_expectancy:.3f} <= 0"
    return True, (
        f"fresh {span_days:.0f}d, {trades} trades, expectancy {weighted_expectancy:.3f}"
    )


# ---------------------------------------------------------------------------
# Promotion / rollback
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def _audit(action: str, cfg_hash: str, overrides: dict, previous: dict, reason: str) -> None:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO promotions (config_hash, action, overrides_json, "
            "previous_overrides_json, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cfg_hash, action, json.dumps(overrides), json.dumps(previous), reason, _now()),
        )


def _last_promotion() -> dict[str, Any] | None:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM promotions WHERE action = 'promote' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def promote(exp: dict[str, Any], reason: str) -> dict[str, Any]:
    challenger_config = json.loads(exp["config_json"])
    overrides = {k: v for k, v in challenger_config.items() if _is_allowed_key(k)}
    rejected = sorted(set(challenger_config) - set(overrides))
    previous = _read_json(OVERRIDES_PATH)

    _write_json_atomic(OVERRIDES_PREV_PATH, previous)
    _write_json_atomic(OVERRIDES_PATH, overrides)
    RESTART_FLAG_PATH.touch()
    _audit("promote", exp["config_hash"], overrides, previous, reason)
    return {"applied": overrides, "rejected_keys": rejected}


def rollback(reason: str) -> dict[str, Any]:
    current = _read_json(OVERRIDES_PATH)
    previous = _read_json(OVERRIDES_PREV_PATH)
    _write_json_atomic(OVERRIDES_PATH, previous)
    RESTART_FLAG_PATH.touch()
    _audit("rollback", "-", previous, current, reason)
    decisions.log("promotion", "rolled_back", detail={"reason": reason})
    return {"restored": previous, "reverted_from": current}


def promote_if_ready() -> dict[str, Any]:
    """The single entrypoint a nightly job calls after the experiment runner."""
    _ensure_schema()
    last = _last_promotion()
    if last and (_now() - last["created_at"]) < PROMOTION_COOLDOWN_HOURS * 3600:
        return {"promoted": False, "reason": "promotion cooldown active"}

    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        champion = conn.execute(
            "SELECT * FROM experiments WHERE is_champion_baseline = 1 AND status = 'done' "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        champion = dict(champion) if champion else None
        candidates = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM experiments WHERE status = 'done' "
                "AND is_champion_baseline = 0 ORDER BY finished_at DESC LIMIT 200"
            ).fetchall()
        ]

    report: list[dict[str, Any]] = []
    active_hash = last["config_hash"] if last else None
    for exp in candidates:
        if exp["config_hash"] == active_hash:
            continue  # already the live champion — re-promoting is churn
        ok1, why1 = stage1_pass(exp, champion)
        if not ok1:
            report.append({"hash": exp["config_hash"], "stage": 1, "why": why1})
            decisions.log("promotion", "gate1_blocked", exp["config_hash"], why1)
            continue
        okd, whyd = dsr_pass(exp)
        if not okd:
            report.append({"hash": exp["config_hash"], "stage": "dsr", "why": whyd})
            decisions.log("promotion", "gate_dsr_blocked", exp["config_hash"], whyd)
            continue
        ok2, why2 = stage2_pass(exp)
        if not ok2:
            report.append({"hash": exp["config_hash"], "stage": 2, "why": why2})
            decisions.log("promotion", "gate2_blocked", exp["config_hash"], why2)
            continue
        result = promote(exp, reason=f"stage1: {why1} | dsr: {whyd} | stage2: {why2}")
        decisions.log("promotion", "promoted", exp["config_hash"],
                      {"applied": result["applied"]})
        return {"promoted": True, "hash": exp["config_hash"], **result}

    return {"promoted": False, "evaluated": len(candidates), "blocked": report[:10]}


def health_check() -> dict[str, Any]:
    """Live degradation watch. Run every cycle or hourly. Auto-rollback."""
    _ensure_schema()
    last = _last_promotion()
    if last is None:
        return {"status": "no promotion active"}

    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT pnl, fees FROM paper_trades WHERE status = 'closed' "
                "AND closed_at >= ? ORDER BY closed_at",
                (last["created_at"],),
            ).fetchall()
        ]
    if len(rows) < ROLLBACK_MIN_TRADES:
        return {"status": "monitoring", "trades_since_promotion": len(rows)}

    pnls = [float(r["pnl"] or 0) for r in rows]
    expectancy = sum(pnls) / len(pnls)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    dd_pct = max_dd / config.PAPER_STARTING_CAPITAL * 100

    if expectancy < 0:
        return {"status": "rolled_back", **rollback(
            f"expectancy {expectancy:.3f} < 0 over {len(pnls)} trades since promotion")}
    if dd_pct > ROLLBACK_MAX_DD_PCT:
        return {"status": "rolled_back", **rollback(
            f"drawdown {dd_pct:.1f}% > {ROLLBACK_MAX_DD_PCT}% since promotion")}
    # Decay watch (report-only): live rolling expectancy vs the champion's own
    # walk-forward expectancy. Live < 50% of backtest with n>=30 is the decay
    # signature (McLean-Pontiff out-of-sample degradation pattern).
    decay = None
    with storage._connect() as conn:  # noqa: SLF001
        champ = conn.execute(
            "SELECT metrics_json FROM experiments WHERE is_champion_baseline=1 "
            "AND status='done' ORDER BY finished_at DESC LIMIT 1").fetchone()
    if champ and champ[0] and len(pnls) >= 30:
        bt_exp = json.loads(champ[0]).get("aggregate", {}).get("expectancy", 0)
        live_exp = sum(pnls[-30:]) / 30
        if bt_exp > 0 and live_exp < 0.5 * bt_exp:
            decay = {"live_exp_30": round(live_exp, 4), "backtest_exp": bt_exp}
            decisions.log("health", "decay_flagged", detail=decay)
    return {
        "status": "healthy",
        "trades": len(pnls),
        "expectancy": round(expectancy, 4),
        "dd_pct": round(dd_pct, 2),
        "decay": decay,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Champion-challenger promotion engine")
    parser.add_argument("command", choices=["status", "promote-if-ready", "health", "rollback"])
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args()

    _ensure_schema()
    if args.command == "status":
        print(json.dumps({
            "active_overrides": _read_json(OVERRIDES_PATH),
            "last_promotion": _last_promotion(),
        }, indent=2, default=str))
    elif args.command == "promote-if-ready":
        print(json.dumps(promote_if_ready(), indent=2))
    elif args.command == "health":
        print(json.dumps(health_check(), indent=2))
    elif args.command == "rollback":
        print(json.dumps(rollback(args.reason), indent=2))


if __name__ == "__main__":
    main()
