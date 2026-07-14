"""Strategy registry — register and load strategies by name."""

from __future__ import annotations

from src.strategies.base import BaseStrategy
from src.strategies.candle_signals_round2 import (
    CapitulationBarStrategy,
    FailedPumpLongStrategy,
    MeanReversionLongStrategy,
    Pump24ExtremeStrategy,
    VolumeZscoreStrategy,
)
from src.strategies.funding_carry import FundingCarryStrategy
from src.strategies.funding_rate import FundingRateStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.positioning_short import PositioningShortStrategy
from src.strategies.random_entry import RandomEntryStrategy
from src.strategies.rel_strength_rotation import RelStrengthRotationStrategy
from src.strategies.volume_spike import VolumeSpikeStrategy

_REGISTRY: dict[str, type[BaseStrategy]] = {
    FundingCarryStrategy.name: FundingCarryStrategy,
    FundingRateStrategy.name: FundingRateStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    MomentumStrategy.name: MomentumStrategy,
    PositioningShortStrategy.name: PositioningShortStrategy,
    RelStrengthRotationStrategy.name: RelStrengthRotationStrategy,
    VolumeSpikeStrategy.name: VolumeSpikeStrategy,
    CapitulationBarStrategy.name: CapitulationBarStrategy,
    VolumeZscoreStrategy.name: VolumeZscoreStrategy,
    Pump24ExtremeStrategy.name: Pump24ExtremeStrategy,
    FailedPumpLongStrategy.name: FailedPumpLongStrategy,
    MeanReversionLongStrategy.name: MeanReversionLongStrategy,
    # Benchmark null (BENCHMARK_PERCENTILE ensemble) -- registered so the
    # walk-forward harness can run it; deliberately NOT in
    # research_space.yaml and never promotable.
    RandomEntryStrategy.name: RandomEntryStrategy,
}


def register_strategy(strategy_cls: type[BaseStrategy]) -> None:
    _REGISTRY[strategy_cls.name] = strategy_cls


def list_strategies() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_strategy(name: str) -> BaseStrategy:
    if name not in _REGISTRY:
        available = ", ".join(list_strategies()) or "(none)"
        raise KeyError(f"Unknown strategy '{name}'. Available: {available}")
    return _REGISTRY[name]()
