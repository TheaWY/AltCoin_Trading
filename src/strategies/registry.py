"""Strategy registry — register and load strategies by name."""

from __future__ import annotations

from src.strategies.base import BaseStrategy
from src.strategies.funding_rate import FundingRateStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.volume_spike import VolumeSpikeStrategy

_REGISTRY: dict[str, type[BaseStrategy]] = {
    FundingRateStrategy.name: FundingRateStrategy,
    MomentumStrategy.name: MomentumStrategy,
    VolumeSpikeStrategy.name: VolumeSpikeStrategy,
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
