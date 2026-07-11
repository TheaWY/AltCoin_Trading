"""Shared dashboard API payload for REST + WebSocket."""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine.analyzer import AltAnalyzer
from src.engine.evaluation import evaluate_all
from src.engine.paper_trader import PaperTrader
from src.engine.regime import btc_regime
from src.symbols import trading_symbols


# With hundreds of symbols a payload build costs many queries; cache it and
# let a finished trading cycle (new data) invalidate the cache explicitly.
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"payload": None, "built_at": 0.0}


def build_data_health(
    storage: Storage | None = None,
    max_age_seconds: int = 15 * 60,
) -> dict[str, Any]:
    """Dashboard health based on newest market data, not server worker state."""
    storage = storage or get_storage()
    now = int(time.time())

    hb = storage.get_system_status("worker_heartbeat")
    hb_age = None
    if hb and hb.get("updated_at"):
        hb_age = max(0, now - int(hb["updated_at"]))

    latest_ts = None
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute("SELECT MAX(timestamp) AS ts FROM prices").fetchone()
        if row:
            raw_ts = row.get("ts") if isinstance(row, dict) else row[0]
            latest_ts = int(raw_ts) if raw_ts else None
    price_age = max(0, now - latest_ts) if latest_ts else None

    if hb_age is not None:
        online = hb_age < max_age_seconds
        basis = "heartbeat"
    else:
        online = price_age is not None and price_age < max(max_age_seconds, 75 * 60)
        basis = "price_data"

    return {
        "status": "online" if online else "offline",
        "live": online,
        "basis": basis,
        "heartbeat_age_seconds": hb_age,
        "latest_price_ts": latest_ts,
        "price_age_seconds": price_age,
        "max_age_seconds": max_age_seconds,
    }


def _config_diff(
    challenger: dict[str, Any], champion: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    for key in sorted(set(challenger) | set(champion)):
        base = champion.get(key)
        new = challenger.get(key)
        if base != new:
            diff[key] = {"from": base, "to": new}
    return diff


def _champion_config(storage: Storage) -> dict[str, Any]:
    from src.research.promotion import _ensure_schema

    _ensure_schema()
    with storage._connect() as conn:
        row = conn.execute(
            "SELECT config_json FROM experiments "
            "WHERE is_champion_baseline = 1 AND status = 'done' "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    if row:
        try:
            return json.loads(row["config_json"])
        except Exception:
            pass
    overrides_path = config.DATA_DIR / "config_overrides.json"
    if overrides_path.exists():
        try:
            return json.loads(overrides_path.read_text())
        except Exception:
            pass
    return {}


def build_research_experiments(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    from src.research.promotion import _ensure_schema

    _ensure_schema()
    champion_cfg = _champion_config(storage)

    with storage._connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM experiments ORDER BY priority ASC, created_at DESC LIMIT 200"
            ).fetchall()
        ]
        totals_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM experiments GROUP BY status"
        ).fetchall()

    totals = {str(r["status"]): int(r["n"]) for r in totals_rows}
    experiments: list[dict[str, Any]] = []
    for row in rows:
        try:
            cfg = json.loads(row.get("config_json") or "{}")
        except Exception:
            cfg = {}
        metrics = {}
        try:
            metrics = json.loads(row.get("metrics_json") or "{}")
        except Exception:
            pass
        agg = metrics.get("aggregate", {})
        windows = metrics.get("windows", [])
        positive = sum(1 for w in windows if float(w.get("total_pnl", 0)) > 0)
        experiments.append(
            {
                "id": row["id"],
                "config_hash": row["config_hash"],
                "status": row["status"],
                "priority": row.get("priority"),
                "is_champion_baseline": bool(row.get("is_champion_baseline")),
                "created_at": row.get("created_at"),
                "finished_at": row.get("finished_at"),
                "config_diff": _config_diff(cfg, champion_cfg),
                "aggregate": {
                    "trade_count": agg.get("trade_count"),
                    "expectancy": agg.get("expectancy"),
                    "profit_factor": agg.get("profit_factor"),
                    "gross_pnl": agg.get("gross_pnl"),
                    "positive_windows": f"{positive}/{len(windows)}" if windows else None,
                },
                "windows": windows,
            }
        )

    return {
        "totals": {
            "done": totals.get("done", 0),
            "queued": totals.get("queued", 0),
            "failed": totals.get("failed", 0),
            "running": totals.get("running", 0),
        },
        "champion_config": champion_cfg,
        "experiments": experiments,
    }


def build_event_study_grid(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    from src.research.event_study import _SCHEMA

    with storage._connect() as conn:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM event_study_results ORDER BY run_at DESC, signal, horizon_h, regime"
            ).fetchall()
        ]

    if not rows:
        return {"run_at": None, "grid": []}

    latest_run = max(int(r["run_at"]) for r in rows)
    grid = [
        {
            "signal": r["signal"],
            "horizon_h": r["horizon_h"],
            "regime": r["regime"],
            "n_events": r["n_events"],
            "effect": r["effect"],
            "ci_low": r.get("ci_low"),
            "ci_high": r.get("ci_high"),
            "verdict": r["verdict"],
        }
        for r in rows
        if int(r["run_at"]) == latest_run
    ]
    return {"run_at": latest_run, "grid": grid}


def build_research_payload(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    from src.research.report import build_report

    return {
        "experiments": build_research_experiments(storage),
        "event_study": build_event_study_grid(storage),
        "report": build_report(storage),
    }


def _window_performance(
    storage: Storage, start_ts: int, end_ts: int | None
) -> dict[str, Any]:
    clauses = ["status = 'closed'", "closed_at >= ?"]
    params: list[Any] = [start_ts]
    if end_ts is not None:
        clauses.append("closed_at < ?")
        params.append(end_ts)
    sql = f"SELECT pnl, fees FROM paper_trades WHERE {' AND '.join(clauses)} ORDER BY closed_at"
    with storage._connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if not rows:
        return {"trades": 0, "total_pnl": 0.0, "expectancy": 0.0, "win_rate_pct": None}
    pnls = [float(r["pnl"] or 0) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trades": len(pnls),
        "total_pnl": round(sum(pnls), 2),
        "expectancy": round(sum(pnls) / len(pnls), 4),
        "win_rate_pct": round(wins / len(pnls) * 100, 1),
    }


def build_capital_stage_progress(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    stage = config.CAPITAL_STAGE
    stages = ["paper", "live_150", "live_400", "live_full"]
    try:
        stage_idx = stages.index(stage)
    except ValueError:
        stage_idx = 0
        stage = "paper"
    next_stage = stages[stage_idx + 1] if stage_idx + 1 < len(stages) else None

    with storage._connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT pnl, closed_at FROM paper_trades "
                "WHERE status = 'closed' AND closed_at IS NOT NULL "
                "ORDER BY closed_at"
            ).fetchall()
        ]

    pnls = [float(r["pnl"] or 0) for r in rows]
    trade_count = len(pnls)
    expectancy = sum(pnls) / trade_count if trade_count else 0.0

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    max_dd_pct = (max_dd / config.PAPER_STARTING_CAPITAL * 100) if config.PAPER_STARTING_CAPITAL else 0.0

    rolling = pnls[-config.CAPITAL_DEMOTE_ROLLING_TRADES :]
    rolling_expectancy = sum(rolling) / len(rolling) if rolling else 0.0

    # Monthly buckets for consecutive-month gate
    monthly: dict[str, list[float]] = {}
    for row in rows:
        ts = int(row["closed_at"])
        key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
        monthly.setdefault(key, []).append(float(row["pnl"] or 0))

    month_keys = sorted(monthly.keys())
    qualifying_months = 0
    for key in reversed(month_keys):
        month_pnls = monthly[key]
        month_eq, month_peak, month_dd = 0.0, 0.0, 0.0
        for p in month_pnls:
            month_eq += p
            month_peak = max(month_peak, month_eq)
            month_dd = max(month_dd, month_peak - month_eq)
        month_dd_pct = (
            month_dd / config.PAPER_STARTING_CAPITAL * 100
            if config.PAPER_STARTING_CAPITAL
            else 0.0
        )
        month_expectancy = sum(month_pnls) / len(month_pnls)
        if month_expectancy > config.CAPITAL_STAGE_MIN_EXPECTANCY and month_dd_pct <= config.CAPITAL_STAGE_MAX_DD_PCT:
            qualifying_months += 1
        else:
            break

    criteria = {
        "trades": {
            "current": trade_count,
            "required": config.CAPITAL_STAGE_MIN_TRADES,
            "met": trade_count >= config.CAPITAL_STAGE_MIN_TRADES,
        },
        "expectancy": {
            "current": round(expectancy, 4),
            "required": config.CAPITAL_STAGE_MIN_EXPECTANCY,
            "met": expectancy > config.CAPITAL_STAGE_MIN_EXPECTANCY,
        },
        "max_dd_pct": {
            "current": round(max_dd_pct, 2),
            "required": config.CAPITAL_STAGE_MAX_DD_PCT,
            "met": max_dd_pct <= config.CAPITAL_STAGE_MAX_DD_PCT,
        },
        "consecutive_months": {
            "current": qualifying_months,
            "required": config.CAPITAL_STAGE_CONSECUTIVE_MONTHS,
            "met": qualifying_months >= config.CAPITAL_STAGE_CONSECUTIVE_MONTHS,
        },
    }
    promote_ready = all(c["met"] for c in criteria.values())
    demotion_risk = (
        max_dd_pct > config.CAPITAL_DEMOTE_DD_PCT
        or rolling_expectancy < config.CAPITAL_STAGE_MIN_EXPECTANCY
    )

    met_count = sum(1 for c in criteria.values() if c["met"])
    progress_pct = round(met_count / len(criteria) * 100)

    return {
        "stage": stage,
        "next_stage": next_stage,
        "criteria": criteria,
        "promote_ready": promote_ready and next_stage is not None,
        "demotion_risk": demotion_risk,
        "rolling_expectancy": round(rolling_expectancy, 4),
        "progress_pct": progress_pct,
    }


def build_history_payload(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    from src.research.data_quality import freshness, verdict
    from src.research.promotion import _ensure_schema

    _ensure_schema()
    now = int(time.time())
    week_ago = now - 7 * 86_400

    with storage._connect() as conn:
        promo_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM promotions ORDER BY created_at ASC"
            ).fetchall()
        ]
        weekly_experiments = conn.execute(
            "SELECT status, COUNT(*) AS n FROM experiments "
            "WHERE created_at >= ? GROUP BY status",
            (week_ago,),
        ).fetchall()
        best_candidate = conn.execute(
            "SELECT config_hash, metrics_json FROM experiments "
            "WHERE status = 'done' AND is_champion_baseline = 0 "
            "ORDER BY finished_at DESC LIMIT 50"
        ).fetchall()

    timeline: list[dict[str, Any]] = []
    for idx, row in enumerate(promo_rows):
        end_ts = promo_rows[idx + 1]["created_at"] if idx + 1 < len(promo_rows) else None
        try:
            overrides = json.loads(row.get("overrides_json") or "{}")
        except Exception:
            overrides = {}
        try:
            previous = json.loads(row.get("previous_overrides_json") or "{}")
        except Exception:
            previous = {}
        timeline.append(
            {
                "id": row["id"],
                "action": row["action"],
                "config_hash": row["config_hash"],
                "reason": row.get("reason"),
                "created_at": row["created_at"],
                "config_diff": _config_diff(overrides, previous),
                "performance": _window_performance(storage, int(row["created_at"]), end_ts),
            }
        )
    timeline.reverse()

    best_hash = None
    best_expectancy = None
    for row in best_candidate:
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
            exp_val = metrics.get("aggregate", {}).get("expectancy")
            if exp_val is not None and (best_expectancy is None or exp_val > best_expectancy):
                best_expectancy = exp_val
                best_hash = row["config_hash"]
        except Exception:
            continue

    champion_perf = _window_performance(
        storage,
        int(promo_rows[-1]["created_at"]) if promo_rows else week_ago,
        None,
    ) if promo_rows else {"trades": 0, "total_pnl": 0.0, "expectancy": 0.0}

    weekly_totals = {str(r["status"]): int(r["n"]) for r in weekly_experiments}

    return {
        "timeline": timeline,
        "weekly": {
            "experiments_run": weekly_totals.get("done", 0) + weekly_totals.get("failed", 0),
            "experiments_failed": weekly_totals.get("failed", 0),
            "best_candidate_hash": best_hash,
            "best_candidate_expectancy": best_expectancy,
            "champion_performance": champion_perf,
        },
        "data_freshness": freshness(),
        "data_verdict": verdict(),
        "capital_stage": build_capital_stage_progress(storage),
    }


def invalidate_payload_cache() -> None:
    _cache["payload"] = None
    _cache["built_at"] = 0.0


def _round_floats(obj: Any, sig_digits: int = 6) -> Any:
    """Round every float to N significant digits.

    Raw floats serialize with 17 digits ("-0.6800081715001535"); with hundreds
    of symbols that multiplies payload size ~3x for no informational value.
    Significant (not decimal) digits keep sub-cent coin prices intact.
    """
    if isinstance(obj, float):
        if obj == 0.0 or not math.isfinite(obj):
            return obj
        return round(obj, max(0, sig_digits - 1 - int(math.floor(math.log10(abs(obj))))))
    if isinstance(obj, dict):
        return {k: _round_floats(v, sig_digits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v, sig_digits) for v in obj]
    return obj


# Only the fields the dashboard actually renders — the analyzer's full output
# (nested short_term/swing/signal dicts) is dead weight at 500+ symbols.
_ALT_FIELDS = (
    "symbol",
    "base",
    "price",
    "pct_24h",
    "funding_rate",
    "funding_rate_pct",
    "direction",
    "worth_investing",
    "recommended_style",
    "recommended_action",
    "confidence",
    "volume_spike",
    "has_position",
    "total_pnl",
)


def _slim_alt(alt: dict[str, Any]) -> dict[str, Any]:
    slim = {key: alt.get(key) for key in _ALT_FIELDS}
    slim["investment"] = alt.get("investment") if alt.get("has_position") else {}
    return slim


def build_alts_payload(storage: Storage | None = None) -> dict[str, Any]:
    now = time.monotonic()
    cached = _cache["payload"]
    if cached is not None and now - _cache["built_at"] < config.DASHBOARD_CACHE_SECONDS:
        return cached

    with _cache_lock:
        cached = _cache["payload"]
        if cached is not None and now - _cache["built_at"] < config.DASHBOARD_CACHE_SECONDS:
            return cached
        payload = _build_alts_payload_uncached(storage)
        _cache["payload"] = payload
        _cache["built_at"] = time.monotonic()
        return payload


def _build_alts_payload_uncached(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    analyzer = AltAnalyzer(storage)
    trader = PaperTrader(storage)
    symbols = trading_symbols()

    alts = analyzer.analyze_all(symbols)
    holdings = trader.holdings_for_symbols(symbols)

    evaluation = evaluate_all(storage, symbols)
    for entry in evaluation:
        inv = holdings.get(entry["symbol"], {})
        entry["has_position"] = inv.get("status") == "open"
        entry["investment"] = inv if entry["has_position"] else {}

    for alt in alts:
        inv = holdings.get(alt["symbol"], {})
        alt["investment"] = inv
        alt["has_position"] = inv.get("status") == "open"
        alt["total_pnl"] = inv.get("total_pnl", 0)

    # Sort: open positions first, then by |total_pnl|, then worth investing
    alts.sort(
        key=lambda a: (
            0 if a["has_position"] else 1,
            -abs(a.get("total_pnl") or 0),
            0 if a["worth_investing"] else 1,
            -a["confidence"],
        )
    )

    btc = storage.get_latest_price(config.SYMBOL)
    btc_price = float(btc["close"]) if btc else None
    # Always return a portfolio object. The cash/start capital part does not
    # depend on BTC; BTC only affects the benchmark buy-and-hold comparison.
    portfolio = trader.summary(btc_price)

    payload = {
        "symbols_tracked": len(symbols),
        "evaluation": evaluation,
        "alts": [_slim_alt(a) for a in alts],
        "worth_investing_count": sum(1 for a in alts if a["worth_investing"]),
        "regime": btc_regime(storage),
        "strategy_stats": storage.get_strategy_stats(),
        "direction_policy": {
            "allow_long": config.ALLOW_LONG,
            "allow_short": config.ALLOW_SHORT,
            "long_term_hold_enabled": config.LONG_TERM_HOLD_ENABLED,
        },
        "strategy_config": {
            "scalp_max_hold_hours": config.SCALP_MAX_HOLD_HOURS,
            "swing_max_hold_days": config.SWING_MAX_HOLD_HOURS / 24,
            "atr_stop_mult": config.ATR_STOP_MULT,
            "atr_tp_mult": config.ATR_TP_MULT,
            "trail_atr_mult": config.TRAIL_ATR_MULT,
            "risk_per_trade_pct": config.RISK_PER_TRADE_PCT * 100,
            "max_position_pct": config.MAX_POSITION_PCT * 100,
            "max_open_positions": config.MAX_OPEN_POSITIONS,
            "funding_short_pct": config.FUNDING_RATE_SHORT_THRESHOLD * 100,
            "funding_long_pct": config.FUNDING_RATE_LONG_THRESHOLD * 100,
            "volume_spike_ratio": config.VOLUME_SPIKE_RATIO,
            "momentum_7d_pct": config.MOMENTUM_7D_STRONG_PCT,
            "momentum_28d_pct": config.MOMENTUM_28D_STRONG_PCT,
            "meanrev_rsi_high": config.MEANREV_RSI_HIGH,
            "min_dollar_volume_m": config.EVAL_MIN_DOLLAR_VOLUME_24H / 1e6,
            "min_atr_pct": config.EVAL_ATR_MIN_PCT,
            "aligned_bonus": config.CONFLUENCE_ALIGNED_BONUS,
            "conflict_penalty": config.CONFLUENCE_CONFLICT_PENALTY,
            "round_trip_cost_pct": config.round_trip_cost_pct() * 100,
            "min_confidence": config.MIN_CONFIDENCE,
        },
        "portfolio": portfolio,
        "open_positions": storage.get_open_trades(),
        "recent_trades": storage.get_recent_trades(20),
        "closed_trades": storage.get_recent_closed_trades(20),
        "accuracy": storage.get_signal_accuracy(config.SIGNAL_ACCURACY_ROLLING_DAYS),
        "health": build_data_health(storage),
        "research": build_research_payload(storage),
        "history": build_history_payload(storage),
    }
    return _round_floats(payload)
