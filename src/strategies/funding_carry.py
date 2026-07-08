"""Funding Carry — persistent positive funding, delta-neutral intent.

Differs from FundingRateStrategy (single-print reversal bet) in two ways:
1. Persistence: requires funding >= CARRY_ENTRY_RATE for
   CARRY_ENTRY_CONSECUTIVE consecutive 8h settlements, not one spike.
2. Fee hurdle: skips entry when projected funding over the minimum hold
   can't cover the round-trip fee (CARRY_FEE_ROUNDTRIP).

Emits SHORT — the perp leg that RECEIVES funding while rates are positive.
metadata.execution_mode = "delta_neutral" marks that a full carry pairs
this with an equal spot long. Until the execution layer supports two-leg
positions, paper trading treats it as a plain short whose entries are
timed by persistent-crowded-funding conditions (a conservative proxy:
the short leg carries the price risk the spot leg would hedge).

The funding_rates table stores rows at collection frequency (every cycle),
not at settlement frequency, so rows are bucketed into 8h settlement
windows and the last print per window represents that settlement.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.strategies.base import BaseStrategy, Signal, SignalDirection

_SETTLEMENT_SECONDS = 8 * 3600


def settlement_rates(rows: list[dict[str, Any]]) -> list[float]:
    """Collapse collection-frequency funding rows into per-settlement rates.

    rows: ascending by timestamp (storage.get_funding_rates order).
    Returns one rate per 8h window (the last print in each window),
    ascending. Missing windows are simply absent — consecutiveness is
    then measured over observed settlements, which is the conservative
    reading when collection had gaps.
    """
    buckets: dict[int, float] = {}
    for row in rows:
        bucket = int(row["timestamp"]) // _SETTLEMENT_SECONDS
        buckets[bucket] = float(row["funding_rate"])
    return [buckets[key] for key in sorted(buckets)]


class FundingCarryStrategy(BaseStrategy):
    name = "funding_carry"

    def get_required_data(self) -> list[str]:
        return ["funding_history", "latest_price"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        entry_price = float(data["latest_price"]["close"])
        rates = settlement_rates(data["funding_history"])

        need = config.CARRY_ENTRY_CONSECUTIVE
        if len(rates) < need:
            return Signal(
                direction=SignalDirection.NONE,
                reason=f"Only {len(rates)}/{need} funding settlements collected",
                symbol=symbol,
                entry_price=entry_price,
                metadata={"settlements": len(rates)},
            )

        recent = rates[-need:]
        current = rates[-1]
        all_above = all(rate >= config.CARRY_ENTRY_RATE for rate in recent)
        projected = current * config.CARRY_MIN_HOLD_SETTLEMENTS
        clears_fees = projected > config.CARRY_FEE_ROUNDTRIP

        metadata = {
            "execution_mode": "delta_neutral",
            "current_rate": current,
            "consecutive_required": need,
            "recent_min_rate": min(recent),
            "projected_funding_min_hold": round(projected, 6),
            "fee_roundtrip": config.CARRY_FEE_ROUNDTRIP,
            "exit_rate": config.CARRY_EXIT_RATE,
        }

        if all_above and clears_fees:
            return Signal(
                direction=SignalDirection.SHORT,
                reason=(
                    f"Funding >= {config.CARRY_ENTRY_RATE * 100:.3f}%/8h for {need} "
                    f"consecutive settlements (now {current * 100:.4f}%) and projected "
                    f"funding over {config.CARRY_MIN_HOLD_SETTLEMENTS} settlements "
                    f"({projected * 100:.3f}%) clears round-trip fees "
                    f"({config.CARRY_FEE_ROUNDTRIP * 100:.2f}%) — carry short leg"
                ),
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )

        if all_above and not clears_fees:
            reason = (
                f"Funding persistent but too thin: projected {projected * 100:.3f}% "
                f"over min hold <= fees {config.CARRY_FEE_ROUNDTRIP * 100:.2f}% — skipped"
            )
        else:
            reason = (
                f"Funding not persistent: min of last {need} settlements "
                f"{min(recent) * 100:.4f}% < {config.CARRY_ENTRY_RATE * 100:.3f}%"
            )
        return Signal(
            direction=SignalDirection.NONE,
            reason=reason,
            symbol=symbol,
            entry_price=entry_price,
            metadata=metadata,
        )
