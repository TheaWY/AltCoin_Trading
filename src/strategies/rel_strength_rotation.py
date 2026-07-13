"""Rel-strength rotation — LONG when a symbol's 7d return has cleared BTC's
by more than the recent 95th percentile of that spread.

This class exists to validate the setup through the existing walk-forward /
trial-budget / DSR / PBO research pipeline (registered under ACTIVE_STRATEGY,
same mechanism every other strategy is backtested through). The entry
criterion is byte-identical to src/engine/evaluation.py:_rel_strength_setup()
-- both call src.research.rel_strength, so there is one definition, not two.
The live-reachable path is the evaluation setup, gated by
SETUP_REL_STRENGTH_ENABLED; this strategy is not meant to become
ACTIVE_STRATEGY in production (see src/research/promotion.py comments).

Evidence: src/research/event_study.py round 2, n=529/530, +1.46%/+2.88%
effect at 24h/72h, CI excludes zero, consistent across BTC up/down/flat
regimes. Fails at 4h -- swing hold (metadata style="swing"), not a scalp.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.research.rel_strength import MIN_HISTORY, PCTL, SEVEN_DAYS_S, percentile_rank, spread
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class RelStrengthRotationStrategy(BaseStrategy):
    name = "rel_strength_rotation"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "rel_strength_own_history", "rel_strength_btc_history"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        latest = data["latest_price"]
        entry_price = float(latest["close"])

        if symbol == config.SYMBOL:
            return Signal(
                direction=SignalDirection.NONE,
                reason="rel_strength_rotation excludes BTC itself",
                symbol=symbol,
                entry_price=entry_price,
            )

        sym_rows = data.get("rel_strength_own_history") or []
        btc_rows = data.get("rel_strength_btc_history") or []
        if not sym_rows or not btc_rows:
            return Signal(
                direction=SignalDirection.NONE,
                reason="No price history for rel-strength computation",
                symbol=symbol,
                entry_price=entry_price,
            )

        sym_idx = {int(r["timestamp"]): float(r["close"]) for r in sym_rows}
        btc_idx = {int(r["timestamp"]): float(r["close"]) for r in btc_rows}

        history: list[float] = []
        current: float | None = None
        latest_ts = int(sym_rows[-1]["timestamp"])
        for r in sym_rows:
            ts = int(r["timestamp"])
            prior_ts = ts - SEVEN_DAYS_S
            value = spread(sym_idx.get(ts), sym_idx.get(prior_ts), btc_idx.get(ts), btc_idx.get(prior_ts))
            if value is None:
                continue
            if ts == latest_ts:
                current = value
                break
            history.append(value)

        metadata = {"spread": current, "history_n": len(history)}
        if current is None or len(history) < MIN_HISTORY:
            return Signal(
                direction=SignalDirection.NONE,
                reason=f"Insufficient rel-strength history ({len(history)}<{MIN_HISTORY})",
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )

        rank = percentile_rank(current, history)
        metadata["rank"] = round(rank, 4)

        if rank >= PCTL:
            return Signal(
                direction=SignalDirection.LONG,
                reason=f"7d rel strength vs BTC spread {current:+.2%} at pctl {rank:.3f} >= {PCTL}",
                symbol=symbol,
                entry_price=entry_price,
                metadata={**metadata, "style": "swing"},
            )
        return Signal(
            direction=SignalDirection.NONE,
            reason="Rel strength spread below entry percentile",
            symbol=symbol,
            entry_price=entry_price,
            metadata=metadata,
        )
