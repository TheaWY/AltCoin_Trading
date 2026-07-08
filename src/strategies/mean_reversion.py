"""Mean Reversion (bidirectional) — RSI + Bollinger band extremes.

RSI(14) < oversold  AND close below lower band (percent_b <= 0) → LONG
RSI(14) > overbought AND close above upper band (percent_b >= 1) → SHORT
else → NO SIGNAL

Exits (stop / take profit / trailing) are the trader's job, not the
strategy's — consistent with how PaperTrader sizes ATR-based exit levels
for every strategy in this repo.

Note: with the default direction policy (ALLOW_LONG=false) the LONG side
of this strategy is filtered out downstream. The strategy still emits it
so backtests can evaluate both legs when the policy allows.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.engine.indicators import bollinger, closes, rsi
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "recent_prices"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        entry_price = float(data["latest_price"]["close"])
        values = closes(data["recent_prices"])

        min_bars = max(config.MEANREV_RSI_PERIOD + 1, config.MEANREV_BB_PERIOD)
        if len(values) < min_bars:
            return Signal(
                direction=SignalDirection.NONE,
                reason=f"Not enough history ({len(values)}/{min_bars} bars) for mean reversion",
                symbol=symbol,
                entry_price=entry_price,
                metadata={"bars": len(values)},
            )

        rsi_value = rsi(values, config.MEANREV_RSI_PERIOD)
        bands = bollinger(values, config.MEANREV_BB_PERIOD, config.MEANREV_BB_STD)
        if rsi_value is None or bands is None:
            return Signal(
                direction=SignalDirection.NONE,
                reason="Indicators unavailable for mean reversion",
                symbol=symbol,
                entry_price=entry_price,
                metadata={},
            )

        percent_b = bands["percent_b"]
        metadata = {
            "rsi": round(rsi_value, 2),
            "percent_b": round(percent_b, 4),
            "bandwidth_pct": round(bands["bandwidth_pct"], 4),
        }

        if rsi_value < config.MEANREV_RSI_OVERSOLD and percent_b <= 0.0:
            return Signal(
                direction=SignalDirection.LONG,
                reason=(
                    f"RSI {rsi_value:.1f} < {config.MEANREV_RSI_OVERSOLD:.0f} and close "
                    f"below lower Bollinger band (%B {percent_b:.2f}) — oversold reversion long"
                ),
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )

        if rsi_value > config.MEANREV_RSI_OVERBOUGHT and percent_b >= 1.0:
            return Signal(
                direction=SignalDirection.SHORT,
                reason=(
                    f"RSI {rsi_value:.1f} > {config.MEANREV_RSI_OVERBOUGHT:.0f} and close "
                    f"above upper Bollinger band (%B {percent_b:.2f}) — overbought reversion short"
                ),
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )

        return Signal(
            direction=SignalDirection.NONE,
            reason=(
                f"RSI {rsi_value:.1f} / %B {percent_b:.2f} — no band+momentum extreme"
            ),
            symbol=symbol,
            entry_price=entry_price,
            metadata=metadata,
        )
