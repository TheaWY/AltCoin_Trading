"""Volume spike — unusual volume confirms the direction of the current candle."""

from __future__ import annotations

from typing import Any

from src import config
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class VolumeSpikeStrategy(BaseStrategy):
    """
    volume >= VOLUME_SPIKE_RATIO x 24h average:
        candle closed up   → LONG (buyers stepping in)
        candle closed down → SHORT (sellers stepping in)
    otherwise → NO SIGNAL
    """

    name = "volume_spike"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "volume_stats"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        latest = data["latest_price"]
        stats = data["volume_stats"]
        entry_price = float(latest["close"])

        avg_volume = stats.get("avg")
        if not avg_volume or int(stats.get("count") or 0) < config.VOLUME_SPIKE_MIN_CANDLES:
            return Signal(
                direction=SignalDirection.NONE,
                reason="Not enough volume history for spike detection",
                symbol=symbol,
                entry_price=entry_price,
                metadata={"volume_ratio": None},
            )

        volume = float(latest["volume"])
        ratio = volume / float(avg_volume)
        candle_change = entry_price - float(latest["open"])
        metadata = {
            "volume_ratio": round(ratio, 2),
            "spike_threshold": config.VOLUME_SPIKE_RATIO,
            "candle_change": candle_change,
        }

        if ratio < config.VOLUME_SPIKE_RATIO:
            return Signal(
                direction=SignalDirection.NONE,
                reason=(
                    f"Volume {ratio:.2f}x average, below "
                    f"{config.VOLUME_SPIKE_RATIO:.2f}x spike threshold"
                ),
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )

        if candle_change > 0:
            return Signal(
                direction=SignalDirection.LONG,
                reason=f"Volume spike {ratio:.2f}x average on an up candle — buying pressure",
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )
        if candle_change < 0:
            return Signal(
                direction=SignalDirection.SHORT,
                reason=f"Volume spike {ratio:.2f}x average on a down candle — selling pressure",
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )
        return Signal(
            direction=SignalDirection.NONE,
            reason=f"Volume spike {ratio:.2f}x average but flat candle — no direction",
            symbol=symbol,
            entry_price=entry_price,
            metadata=metadata,
        )
