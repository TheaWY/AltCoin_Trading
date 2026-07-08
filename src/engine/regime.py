"""BTC market-regime filter.

Alts are dominated by the BTC factor in stress periods (Liu, Tsyvinski & Wu,
JF 2022): when BTC dumps, alt longs get run over regardless of their own
setup; when BTC squeezes violently upward, alt shorts do. This module reduces
that to one gate consulted before any new entry.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine import indicators


def btc_regime(storage: Storage | None = None) -> dict[str, Any]:
    """Classify the current BTC regime and which entry directions it blocks."""
    storage = storage or get_storage()
    result: dict[str, Any] = {
        "state": "neutral",
        "blocked_directions": [],
        "pct_24h": None,
        "pct_7d": None,
        "reason": None,
        "enabled": config.REGIME_FILTER_ENABLED,
    }
    if not config.REGIME_FILTER_ENABLED:
        return result

    rows = storage.get_prices(config.SYMBOL, limit=168 + 1, timeframe="1h")
    values = indicators.closes(rows)
    pct_24h = indicators.momentum_pct(values, 24)
    pct_7d = indicators.momentum_pct(values, 168)
    result["pct_24h"] = pct_24h
    result["pct_7d"] = pct_7d
    if pct_24h is None:
        return result

    dumping = pct_24h <= config.REGIME_BTC_DROP_24H_PCT or (
        pct_7d is not None and pct_7d <= config.REGIME_BTC_DROP_7D_PCT
    )
    pumping = pct_24h >= config.REGIME_BTC_PUMP_24H_PCT or (
        pct_7d is not None and pct_7d >= config.REGIME_BTC_PUMP_7D_PCT
    )

    if dumping:
        result["state"] = "risk_off"
        result["blocked_directions"] = ["LONG"]
        result["reason"] = (
            f"BTC 24h {pct_24h:+.1f}%"
            + (f" · 7d {pct_7d:+.1f}%" if pct_7d is not None else "")
            + " 하락 레짐 — 신규 롱 차단"
        )
    elif pumping:
        result["state"] = "squeeze"
        result["blocked_directions"] = ["SHORT"]
        result["reason"] = (
            f"BTC 24h {pct_24h:+.1f}%"
            + (f" · 7d {pct_7d:+.1f}%" if pct_7d is not None else "")
            + " 급등 레짐 — 신규 숏 차단 (숏 스퀴즈 위험)"
        )
    return result


def direction_blocked(regime: dict[str, Any], direction: str | None) -> bool:
    return direction in (regime.get("blocked_directions") or [])
