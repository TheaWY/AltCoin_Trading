"""Funding Rate Reversal — MVP strategy."""

from __future__ import annotations

from typing import Any

from src import config
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class FundingRateStrategy(BaseStrategy):
    """
    funding rate > short threshold  → SHORT (over-leveraged longs)
    funding rate < long threshold   → LONG
    else                            → NO SIGNAL
    """

    name = "funding_rate"

    def get_required_data(self) -> list[str]:
        return ["funding_rate", "latest_price"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        funding_rate = float(data["funding_rate"]["funding_rate"])
        entry_price = float(data["latest_price"]["close"])
        rate_pct = funding_rate * 100

        if funding_rate > config.FUNDING_RATE_SHORT_THRESHOLD:
            return Signal(
                direction=SignalDirection.SHORT,
                reason=(
                    f"Funding rate {rate_pct:.4f}% exceeds "
                    f"{config.FUNDING_RATE_SHORT_THRESHOLD * 100:.2f}% — "
                    "over-leveraged longs, likely reversal"
                ),
                symbol=symbol,
                entry_price=entry_price,
                metadata={"funding_rate": funding_rate},
            )

        if funding_rate < config.FUNDING_RATE_LONG_THRESHOLD:
            return Signal(
                direction=SignalDirection.LONG,
                reason=(
                    f"Funding rate {rate_pct:.4f}% below "
                    f"{config.FUNDING_RATE_LONG_THRESHOLD * 100:.2f}% — "
                    "shorts paying longs, bullish pressure"
                ),
                symbol=symbol,
                entry_price=entry_price,
                metadata={"funding_rate": funding_rate},
            )

        return Signal(
            direction=SignalDirection.NONE,
            reason=(
                f"Funding rate {rate_pct:.4f}% within neutral band "
                f"({config.FUNDING_RATE_LONG_THRESHOLD * 100:.2f}% to "
                f"{config.FUNDING_RATE_SHORT_THRESHOLD * 100:.2f}%)"
            ),
            symbol=symbol,
            entry_price=entry_price,
            metadata={"funding_rate": funding_rate},
        )
