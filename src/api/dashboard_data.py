"""Shared dashboard API payload for REST + WebSocket."""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine.analyzer import AltAnalyzer
from src.engine.evaluation import evaluate_all
from src.engine.paper_trader import PaperTrader
from src.engine.regime import btc_regime
from src.health import get_health
from src.symbols import trading_symbols


# With hundreds of symbols a payload build costs many queries; cache it and
# let a finished trading cycle (new data) invalidate the cache explicitly.
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"payload": None, "built_at": 0.0}


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
    portfolio = trader.summary(btc_price) if btc_price else {}

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
        },
        "portfolio": portfolio,
        "open_positions": storage.get_open_trades(),
        "recent_trades": storage.get_recent_trades(20),
        "closed_trades": storage.get_recent_closed_trades(20),
        "accuracy": storage.get_signal_accuracy(config.SIGNAL_ACCURACY_ROLLING_DAYS),
        "health": get_health().get_status(),
    }
    return _round_floats(payload)
