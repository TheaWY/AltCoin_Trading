"""Champion-challenger promotion engine.

Historical walk-forward tests, a statistically corrected Sharpe gate, and a
strictly out-of-sample fresh-data gate protect automatic configuration changes.
Promotion is limited to whitelisted strategy settings and can be rolled back by
paper-trading health checks.
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

OVERRIDE_KEY_PREFIXES = (
    "MEANREV_",
    "CONFLUENCE_",
    "LSR_",
    "SETUP_",
    "SCAN_",
    "COOLDOWN_",
    "FEE_",
    "FUNDING_RATE_",
    "CARRY_",
    "POS_SHORT_",
    "BREAKOUT_",
    "TSMOM_",
)
OVERRIDE_KEY_EXACT = frozenset(
    {"ACTIVE_STRATEGY", "MIN_CONFIDENCE", "MAX_OPEN_POSITIONS", "CATEGORY_STRATEGY_MODE"}
)

OVERRIDES_PATH = config.DATA_DIR / "config_overrides.json"
OVERRIDES_PREV_PATH = config.DATA_DIR / "config_overrides.prev.json"
RESTART_FLAG_PATH = config.DATA_DIR / "restart.flag"

S1_MIN_WINDOW_WIN_FRACTION = float(os.getenv("PROMO_S1_WINDOW_FRACTION", "0.66"))
S1_MIN_TRADES = int(os.getenv("PROMO_S1_MIN_TRADES", "30"))
S1_MIN_PROFIT_FACTOR = float(os.getenv("PROMO_S1_MIN_PF", "1.2"))
PROMO_MIN_DSR = float(os.getenv("PROMO_MIN_DSR", "0.95"))
RESEARCH_CAPITAL = float(os.getenv("RESEARCH_CAPITAL", "730"))
S2_MIN_FRESH_DAYS = float(os.getenv("PROMO_S2_MIN_FRESH_DAYS", "14"))
S2_MIN_TRADES = int(os.getenv("PROMO_S2_MIN_TRADES", "10"))
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


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _ensure_schema() -> None:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)


def config_hash(config_dict: dict[str, Any]) -> str:
    canonical = json.dumps(config_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _now() -> int:
    return int(time.time())


def _is_allowed_key(key: str) -> bool:
    return key in OVERRIDE_KEY_EXACT or key.startswith(OVERRIDE_KEY_PREFIXES)


def stage1_pass(
    exp: dict[str, Any], champion: dict[str, Any] | None
) -> tuple[bool, str]:
    """Require broad walk-forward consistency and improvement over baseline."""
    metrics = json.loads(exp.get("metrics_json") or "{}")
    windows = metrics.get("windows", [])
    aggregate = metrics.get("aggregate", {})

    if not windows:
        return False, "no walk-forward windows in metrics"
    positive = sum(1 for window in windows if window.get("total_pnl", 0) > 0)
    fraction = positive / len(windows)
    if fraction < S1_MIN_WINDOW_WIN_FRACTION:
        return (
            False,
            f"window consistency {positive}/{len(windows)} < "
            f"{S1_MIN_WINDOW_WIN_FRACTION:.0%}",
        )
    if aggregate.get("trade_count", 0) < S1_MIN_TRADES:
        return False, f"trades {aggregate.get('trade_count', 0)} < {S1_MIN_TRADES}"
    if aggregate.get("expectancy", 0) <= 0:
        return False, "aggregate expectancy <= 0"
    if aggregate.get("profit_factor", 0) < S1_MIN_PROFIT_FACTOR:
        return (
            False,
            f"PF {aggregate.get('profit_factor', 0):.2f} < {S1_MIN_PROFIT_FACTOR}",
        )

    if champion is not None:
        champion_metrics = json.loads(champion.get("metrics_json") or "{}")
        champion_aggregate = champion_metrics.get("aggregate", {})
        if aggregate.get("expectancy", 0) <= champion_aggregate.get("expectancy", 0):
            return (
                False,
                f"expectancy {aggregate.get('expectancy', 0):.3f} does not beat "
                f"champion {champion_aggregate.get('expectancy', 0):.3f} "
                "on the same windows",
            )
    return True, f"windows {positive}/{len(windows)} positive, beats champion"


def dsr_pass(exp: dict[str, Any]) -> tuple[bool, str]:
    """Deflated Sharpe gate corrected for the consumed experiment count."""
    metrics = json.loads(exp.get("metrics_json") or "{}")
    pnls = [
        float(pnl)
        for window in metrics.get("windows", [])
        for pnl in (window.get("trade_pnls") or [])
    ]
    if not pnls:
        return False, "no trade-level pnls stored (re-run experiment)"
    returns = [pnl / RESEARCH_CAPITAL for pnl in pnls]

    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        n_trials_row = conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE status IN ('done','failed')"
        ).fetchone()
        n_trials = int(_row_value(n_trials_row, "count", 0) or 0)
        rows = conn.execute(
            "SELECT metrics_json FROM experiments WHERE status='done' "
            "AND config_hash != ? LIMIT 500",
            (exp["config_hash"],),
        ).fetchall()

    trial_sharpes: list[float] = []
    for row in rows:
        metrics_json = _row_value(row, "metrics_json")
        try:
            other = [
                float(pnl)
                for window in json.loads(metrics_json or "{}").get("windows", [])
                for pnl in (window.get("trade_pnls") or [])
            ]
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if len(other) >= 10:
            trial_sharpes.append(
                sharpe([pnl / RESEARCH_CAPITAL for pnl in other])
            )

    result = deflated_sharpe(
        returns,
        n_trials=max(n_trials, 1),
        trial_sharpes=trial_sharpes,
    )
    ok = result["dsr"] >= PROMO_MIN_DSR
    reason = (
        f"DSR {result['dsr']:.3f} (SR {result['sr']:.3f} vs luck-hurdle "
        f"SR0 {result['sr0']:.3f}, N={n_trials} trials)"
    )
    return ok, reason


def _latest_fresh_eval(config_hash_value: str, created_at: int) -> dict[str, Any] | None:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM fresh_evals WHERE config_hash = ? AND eval_start >= ? "
            "ORDER BY eval_end DESC LIMIT 1",
            (config_hash_value, created_at),
        ).fetchone()
    return dict(row) if row else None


def stage2_pass(exp: dict[str, Any]) -> tuple[bool, str]:
    """Require one latest expanding replay over genuinely unseen data.

    Fresh evaluations are cumulative snapshots from candidate creation to the
    evaluation time. Summing every snapshot double-counts the same early trades
    repeatedly and can make a candidate satisfy the sample-size gate far too
    soon. Only the latest snapshot is therefore used.
    """
    row = _latest_fresh_eval(exp["config_hash"], int(exp["created_at"]))
    if row is None:
        return False, "no fresh-data evaluations yet"

    span_days = (int(row["eval_end"]) - int(row["eval_start"])) / 86_400
    trades = int(row["trade_count"] or 0)
    expectancy = float(row["expectancy"] or 0.0)
    total_pnl = float(row["total_pnl"] or 0.0)
    if span_days < S2_MIN_FRESH_DAYS:
        return False, f"fresh span {span_days:.1f}d < {S2_MIN_FRESH_DAYS}d"
    if trades < S2_MIN_TRADES:
        return False, f"fresh trades {trades} < {S2_MIN_TRADES}"
    if expectancy <= 0 or total_pnl <= 0:
        return (
            False,
            f"fresh expectancy {expectancy:.3f}, total PnL {total_pnl:.3f} must both be > 0",
        )
    return True, (
        f"fresh {span_days:.0f}d, {trades} trades, expectancy {expectancy:.3f}"
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _audit(
    action: str,
    cfg_hash: str,
    overrides: dict[str, Any],
    previous: dict[str, Any],
    reason: str,
) -> None:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO promotions (config_hash, action, overrides_json, "
            "previous_overrides_json, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                cfg_hash,
                action,
                json.dumps(overrides),
                json.dumps(previous),
                reason,
                _now(),
            ),
        )


def _last_promotion() -> dict[str, Any] | None:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM promotions WHERE action = 'promote' "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def _last_action() -> dict[str, Any] | None:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM promotions ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def promote(exp: dict[str, Any], reason: str) -> dict[str, Any]:
    challenger_config = json.loads(exp["config_json"])
    overrides = {
        key: value
        for key, value in challenger_config.items()
        if _is_allowed_key(key)
    }
    if not overrides:
        raise ValueError("Candidate contains no promotable configuration keys")
    rejected = sorted(set(challenger_config) - set(overrides))
    previous = _read_json(OVERRIDES_PATH)

    _write_json_atomic(OVERRIDES_PREV_PATH, previous)
    _write_json_atomic(OVERRIDES_PATH, overrides)
    RESTART_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESTART_FLAG_PATH.touch()
    _audit("promote", exp["config_hash"], overrides, previous, reason)
    return {"applied": overrides, "rejected_keys": rejected}


def rollback(reason: str) -> dict[str, Any]:
    current = _read_json(OVERRIDES_PATH)
    previous = _read_json(OVERRIDES_PREV_PATH)
    _write_json_atomic(OVERRIDES_PATH, previous)
    RESTART_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESTART_FLAG_PATH.touch()
    _audit("rollback", "-", previous, current, reason)
    decisions.log("promotion", "rolled_back", detail={"reason": reason})
    return {"restored": previous, "reverted_from": current}


def promote_if_ready() -> dict[str, Any]:
    _ensure_schema()
    last_promotion = _last_promotion()
    if (
        last_promotion
        and (_now() - int(last_promotion["created_at"]))
        < PROMOTION_COOLDOWN_HOURS * 3600
    ):
        return {"promoted": False, "reason": "promotion cooldown active"}

    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        champion_row = conn.execute(
            "SELECT * FROM experiments WHERE is_champion_baseline = 1 "
            "AND status = 'done' ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        champion = dict(champion_row) if champion_row else None
        candidates = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM experiments WHERE status = 'done' "
                "AND is_champion_baseline = 0 "
                "ORDER BY finished_at DESC LIMIT 200"
            ).fetchall()
        ]

    latest_action = _last_action()
    active_hash = (
        latest_action["config_hash"]
        if latest_action and latest_action.get("action") == "promote"
        else None
    )
    report: list[dict[str, Any]] = []
    for exp in candidates:
        if exp["config_hash"] == active_hash:
            continue
        ok1, why1 = stage1_pass(exp, champion)
        if not ok1:
            report.append({"hash": exp["config_hash"], "stage": 1, "why": why1})
            decisions.log("promotion", "gate1_blocked", exp["config_hash"], why1)
            continue
        ok_dsr, why_dsr = dsr_pass(exp)
        if not ok_dsr:
            report.append(
                {"hash": exp["config_hash"], "stage": "dsr", "why": why_dsr}
            )
            decisions.log(
                "promotion", "gate_dsr_blocked", exp["config_hash"], why_dsr
            )
            continue
        ok2, why2 = stage2_pass(exp)
        if not ok2:
            report.append({"hash": exp["config_hash"], "stage": 2, "why": why2})
            decisions.log("promotion", "gate2_blocked", exp["config_hash"], why2)
            continue

        result = promote(
            exp,
            reason=f"stage1: {why1} | dsr: {why_dsr} | stage2: {why2}",
        )
        decisions.log(
            "promotion",
            "promoted",
            exp["config_hash"],
            {"applied": result["applied"]},
        )
        return {"promoted": True, "hash": exp["config_hash"], **result}

    return {
        "promoted": False,
        "evaluated": len(candidates),
        "blocked": report[:10],
    }


def _promoted_backtest_expectancy(config_hash_value: str) -> float:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT metrics_json FROM experiments WHERE config_hash = ? "
            "AND status = 'done' LIMIT 1",
            (config_hash_value,),
        ).fetchone()
    if row is None:
        return 0.0
    metrics_json = _row_value(row, "metrics_json")
    try:
        return float(
            json.loads(metrics_json or "{}").get("aggregate", {}).get("expectancy", 0)
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def health_check() -> dict[str, Any]:
    """Monitor only the currently active promotion and roll it back on decay."""
    _ensure_schema()
    active = _last_action()
    if active is None or active.get("action") != "promote":
        return {"status": "no promotion active"}

    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT pnl, fees FROM paper_trades WHERE status = 'closed' "
                "AND closed_at >= ? ORDER BY closed_at",
                (active["created_at"],),
            ).fetchall()
        ]
    if len(rows) < ROLLBACK_MIN_TRADES:
        return {"status": "monitoring", "trades_since_promotion": len(rows)}

    pnls = [float(row.get("pnl") or 0.0) for row in rows]
    expectancy = sum(pnls) / len(pnls)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    drawdown_pct = max_drawdown / config.PAPER_STARTING_CAPITAL * 100

    if expectancy < 0:
        return {
            "status": "rolled_back",
            **rollback(
                f"expectancy {expectancy:.3f} < 0 over {len(pnls)} trades since promotion"
            ),
        }
    if drawdown_pct > ROLLBACK_MAX_DD_PCT:
        return {
            "status": "rolled_back",
            **rollback(
                f"drawdown {drawdown_pct:.1f}% > {ROLLBACK_MAX_DD_PCT}% since promotion"
            ),
        }

    decay = None
    if len(pnls) >= 30:
        backtest_expectancy = _promoted_backtest_expectancy(active["config_hash"])
        live_expectancy = sum(pnls[-30:]) / 30
        if backtest_expectancy > 0 and live_expectancy < 0.5 * backtest_expectancy:
            decay = {
                "live_exp_30": round(live_expectancy, 4),
                "backtest_exp": backtest_expectancy,
            }
            decisions.log("health", "decay_flagged", detail=decay)

    return {
        "status": "healthy",
        "trades": len(pnls),
        "expectancy": round(expectancy, 4),
        "dd_pct": round(drawdown_pct, 2),
        "decay": decay,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Champion-challenger promotion engine")
    parser.add_argument(
        "command", choices=["status", "promote-if-ready", "health", "rollback"]
    )
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args()

    _ensure_schema()
    if args.command == "status":
        print(
            json.dumps(
                {
                    "active_overrides": _read_json(OVERRIDES_PATH),
                    "last_promotion": _last_promotion(),
                    "last_action": _last_action(),
                },
                indent=2,
                default=str,
            )
        )
    elif args.command == "promote-if-ready":
        print(json.dumps(promote_if_ready(), indent=2))
    elif args.command == "health":
        print(json.dumps(health_check(), indent=2))
    else:
        print(json.dumps(rollback(args.reason), indent=2))


if __name__ == "__main__":
    main()
