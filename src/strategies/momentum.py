"""Momentum (trend following) — trade in the direction of the 24h move."""

from __future__ import annotations

from typing import Any

from src import config
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class MomentumStrategy(BaseStrategy):
    """
    24h change >= +MOMENTUM_ENTRY_PCT  → LONG (ride the trend)
    24h change <= -MOMENTUM_ENTRY_PCT  → SHORT
    else                               → NO SIGNAL
    """

    name = "momentum"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "recent_prices"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        latest = data["latest_price"]
        prices = data["recent_prices"]
        entry_price = float(latest["close"])

        past_close = _close_hours_ago(prices, int(latest["timestamp"]), hours=24)
        if past_close is None:
            return Signal(
                direction=SignalDirection.NONE,
                reason="Not enough price history for 24h momentum",
                symbol=symbol,
                entry_price=entry_price,
                metadata={"pct_24h": None},
            )

        pct_24h = ((entry_price - past_close) / past_close) * 100.0
        threshold = config.MOMENTUM_ENTRY_PCT
        metadata = {"pct_24h": round(pct_24h, 4), "threshold_pct": threshold}

        if pct_24h >= threshold:
            return Signal(
                direction=SignalDirection.LONG,
                reason=f"24h momentum +{pct_24h:.2f}% >= {threshold:.2f}% — trend continuation long",
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )
        if pct_24h <= -threshold:
            return Signal(
                direction=SignalDirection.SHORT,
                reason=f"24h momentum {pct_24h:.2f}% <= -{threshold:.2f}% — trend continuation short",
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )
        return Signal(
            direction=SignalDirection.NONE,
            reason=f"24h momentum {pct_24h:.2f}% within ±{threshold:.2f}% band",
            symbol=symbol,
            entry_price=entry_price,
            metadata=metadata,
        )


def _close_hours_ago(
    prices: list[dict[str, Any]], latest_ts: int, hours: int
) -> float | None:
    """Close of the newest candle at least `hours` before the latest one."""
    target = latest_ts - hours * 3600
    candidate: float | None = None
    for row in prices:
        if int(row["timestamp"]) <= target:
            candidate = float(row["close"])
    return candidate
