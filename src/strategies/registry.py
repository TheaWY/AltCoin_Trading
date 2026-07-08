"""Strategy registry — register and load strategies by name."""

from __future__ import annotations

from src.strategies.base import BaseStrategy
from src.strategies.donchian_breakout import DonchianBreakoutStrategy
from src.strategies.funding_carry import FundingCarryStrategy
from src.strategies.funding_rate import FundingRateStrategy
from src.strategies.grid import GridStrategy
from src.strategies.listing_reversion import ListingReversionStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.pair_trading import PairTradingStrategy
from src.strategies.positioning_short import PositioningShortStrategy
from src.strategies.tsmom_28d import TSMom28dStrategy
from src.strategies.volume_spike import VolumeSpikeStrategy

_REGISTRY: dict[str, type[BaseStrategy]] = {
    DonchianBreakoutStrategy.name: DonchianBreakoutStrategy,
    FundingCarryStrategy.name: FundingCarryStrategy,
    FundingRateStrategy.name: FundingRateStrategy,
    GridStrategy.name: GridStrategy,
    ListingReversionStrategy.name: ListingReversionStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    MomentumStrategy.name: MomentumStrategy,
    PairTradingStrategy.name: PairTradingStrategy,
    PositioningShortStrategy.name: PositioningShortStrategy,
    TSMom28dStrategy.name: TSMom28dStrategy,
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
