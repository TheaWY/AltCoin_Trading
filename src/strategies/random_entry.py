"""Random-entry null strategy -- the walk-forward control behind
BENCHMARK_PERCENTILE.

Enters a random direction on a random bar with probability
RANDOM_ENTRY_PROB_PER_BAR, then runs through the IDENTICAL machinery as any
real strategy (sizing, stops, targets, trailing, cooldowns, slots, fees,
slippage). A real strategy's walk-forward result is only evidence of signal
content to the extent it beats the distribution of these books: if it lands
inside the random band, its returns are coming from the risk management,
not the signals.

Deterministic: every draw comes from random.Random(seed, symbol, hour
bucket) -- same seed, same data -> same trades, reproducible forever. This
is a BENCHMARK strategy: it is deliberately not in research_space.yaml and
must never be promotable.
"""

from __future__ import annotations

import os
import random as _random
from typing import Any

from src import config
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class RandomEntryStrategy(BaseStrategy):
    name = "random_entry"

    def get_required_data(self) -> list[str]:
        return ["latest_price"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        latest = data["latest_price"]
        entry_price = float(latest["close"])
        ts = int(latest["timestamp"])

        seed = int(os.getenv("RANDOM_ENTRY_SEED", "0"))
        prob = float(os.getenv("RANDOM_ENTRY_PROB_PER_BAR", "0.002"))
        rng = _random.Random(f"walkforward:{seed}:{symbol}:{ts // 3600}")

        if rng.random() >= prob:
            return Signal(
                direction=SignalDirection.NONE,
                reason="no random draw this bar",
                symbol=symbol,
                entry_price=entry_price,
                metadata={"seed": seed},
            )

        direction = SignalDirection.LONG if rng.random() < 0.5 else SignalDirection.SHORT
        return Signal(
            direction=direction,
            reason=f"random entry (seed={seed}, benchmark null)",
            symbol=symbol,
            entry_price=entry_price,
            metadata={"style": "scalp", "seed": seed},
        )
