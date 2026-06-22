"""Base strategy interface — all strategies must inherit from BaseStrategy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass
class Signal:
    """Normalized signal output consumed by the engine."""

    direction: SignalDirection
    reason: str
    symbol: str
    entry_price: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.direction != SignalDirection.NONE


class BaseStrategy(ABC):
    """
    Abstract base for all trading strategies.

    Subclasses must implement:
      - get_required_data() -> list of collector/data keys needed
      - generate_signal(data) -> Signal
    """

    name: str = "base"

    @abstractmethod
    def get_required_data(self) -> list[str]:
        """
        Return data keys this strategy needs from collectors/storage.

        Example: ["funding_rate", "latest_price"]
        """

    @abstractmethod
    def generate_signal(self, data: dict[str, Any]) -> Signal:
        """
        Produce a trading signal from the provided data snapshot.

        The engine gathers required data and passes it here — strategies
        must not fetch data themselves.
        """

    def validate_data(self, data: dict[str, Any]) -> list[str]:
        """Return missing required keys (empty list means data is sufficient)."""
        missing = []
        for key in self.get_required_data():
            if key not in data or data[key] is None:
                missing.append(key)
        return missing
