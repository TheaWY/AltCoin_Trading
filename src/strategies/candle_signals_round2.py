"""Registry placeholders for the candle_signals_round2 setups.

These three strategies are validated exclusively through evaluate_symbol()
(src/engine/evaluation.py: _capitulation_bar_setup, _volume_zscore_setup,
_pump24_extreme_setup), which calls the EXACT src.research.candle_signals /
src.research.event_study signal functions directly -- there is no second
implementation to keep in sync. scripts/backtest.py routes
ACTIVE_STRATEGY={capitulation_bar,volume_zscore_3plus,pump24_extreme}
through EVALUATION_ENGINE_STRATEGIES, which bypasses generate_signal()
below entirely in favor of the real evaluate_symbol() decision. These
classes exist only so src.strategies.registry.get_strategy() has something
to resolve the name to; generate_signal() is never actually called for real
capital or backtest decisions and deliberately returns NONE rather than a
second, divergent definition of the signal.
"""

from __future__ import annotations

from typing import Any

from src.strategies.base import BaseStrategy, Signal, SignalDirection


class CapitulationBarStrategy(BaseStrategy):
    name = "capitulation_bar"

    def get_required_data(self) -> list[str]:
        return ["latest_price"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        return Signal(
            direction=SignalDirection.NONE,
            reason="capitulation_bar is validated via evaluate_symbol() only -- see module docstring",
            symbol=data.get("symbol", ""),
        )


class VolumeZscoreStrategy(BaseStrategy):
    name = "volume_zscore_3plus"

    def get_required_data(self) -> list[str]:
        return ["latest_price"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        return Signal(
            direction=SignalDirection.NONE,
            reason="volume_zscore_3plus is validated via evaluate_symbol() only -- see module docstring",
            symbol=data.get("symbol", ""),
        )


class Pump24ExtremeStrategy(BaseStrategy):
    name = "pump24_extreme"

    def get_required_data(self) -> list[str]:
        return ["latest_price"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        return Signal(
            direction=SignalDirection.NONE,
            reason="pump24_extreme is validated via evaluate_symbol() only -- see module docstring",
            symbol=data.get("symbol", ""),
        )


class MeanReversionLongStrategy(BaseStrategy):
    """Registry placeholder -- validated via evaluate_symbol()
    (_mean_reversion_long_setup) only. The 'buy dislocation' third family."""

    name = "mean_reversion_long"

    def get_required_data(self) -> list[str]:
        return ["latest_price"]

    def generate_signal(self, data):
        return Signal(direction=SignalDirection.NONE,
                      reason="mean_reversion_long is validated via evaluate_symbol() only",
                      symbol=data.get("symbol", ""))


class MeanReversionShortStrategy(BaseStrategy):
    """Registry placeholder -- validated via evaluate_symbol()
    (_mean_reversion_short_setup) only. Sell-strength mirror of
    mean_reversion_long: short top-percentile 24h overbought, market-neutral."""

    name = "mean_reversion_short"

    def get_required_data(self) -> list[str]:
        return ["latest_price"]

    def generate_signal(self, data):
        return Signal(direction=SignalDirection.NONE,
                      reason="mean_reversion_short is validated via evaluate_symbol() only",
                      symbol=data.get("symbol", ""))


class VolatilityExpansionStrategy(BaseStrategy):
    """Registry placeholder -- validated via evaluate_symbol()
    (_volatility_expansion_setup) only. Phase-3 discovery survivor #1: buy
    top-decile range% expansion, market-neutral (BTC-hedged)."""

    name = "volatility_expansion"

    def get_required_data(self) -> list[str]:
        return ["latest_price"]

    def generate_signal(self, data):
        return Signal(direction=SignalDirection.NONE,
                      reason="volatility_expansion is validated via evaluate_symbol() only",
                      symbol=data.get("symbol", ""))


class FailedPumpLongStrategy(BaseStrategy):
    """Registry placeholder for the validated LONG inversion of the disabled
    failed_pump_short -- same as the three above, validated via
    evaluate_symbol() (_failed_pump_long_setup) only."""

    name = "failed_pump_long"

    def get_required_data(self) -> list[str]:
        return ["latest_price"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        return Signal(
            direction=SignalDirection.NONE,
            reason="failed_pump_long is validated via evaluate_symbol() only -- see module docstring",
            symbol=data.get("symbol", ""),
        )
