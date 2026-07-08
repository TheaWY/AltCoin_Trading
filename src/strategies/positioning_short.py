"""Positioning Short — contrarian short when the crowd is maximally long.

All three must hold simultaneously:
1. Long/short account ratio at an extreme: above its rolling 90-day 95th
   percentile once >= POS_SHORT_MIN_HISTORY_DAYS of history exists;
   absolute fallback (> POS_SHORT_RATIO_ABS) while history is short.
2. Funding hot: latest rate > POS_SHORT_FUNDING_MIN (+0.03%/8h default).
3. Open interest within POS_SHORT_OI_PROXIMITY of its 30-day high.

Crowded longs + expensive funding + peak OI is the setup where long
liquidation cascades start. Low trade frequency (a few signals per month)
is expected and correct — do not loosen thresholds to get more trades.

Requires the PositioningCollector to be running; Binance only serves ~30
days of ls-ratio/OI history, so percentile mode unlocks ~90 days after
collection starts.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.strategies.base import BaseStrategy, Signal, SignalDirection

_DAY_SECONDS = 86_400


def percentile_rank(values: list[float], current: float) -> float:
    """Fraction of historical values strictly below `current` (0..1)."""
    if not values:
        return 0.0
    below = sum(1 for value in values if value < current)
    return below / len(values)


class PositioningShortStrategy(BaseStrategy):
    name = "positioning_short"

    def get_required_data(self) -> list[str]:
        return ["ls_ratio_history", "open_interest_history", "funding_rate", "latest_price"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        entry_price = float(data["latest_price"]["close"])
        ls_rows = data["ls_ratio_history"]
        oi_rows = data["open_interest_history"]
        funding = float(data["funding_rate"]["funding_rate"])

        if not ls_rows or not oi_rows:
            return Signal(
                direction=SignalDirection.NONE,
                reason="No positioning data yet — is PositioningCollector running?",
                symbol=symbol,
                entry_price=entry_price,
                metadata={},
            )

        ratio_now = float(ls_rows[-1]["ratio"])
        history_days = (
            int(ls_rows[-1]["timestamp"]) - int(ls_rows[0]["timestamp"])
        ) / _DAY_SECONDS

        if history_days >= config.POS_SHORT_MIN_HISTORY_DAYS:
            history = [float(row["ratio"]) for row in ls_rows[:-1]]
            rank = percentile_rank(history, ratio_now)
            ratio_extreme = rank >= config.POS_SHORT_RATIO_PCTILE
            ratio_mode = f"pctile {rank:.2f} (>= {config.POS_SHORT_RATIO_PCTILE})"
        else:
            ratio_extreme = ratio_now > config.POS_SHORT_RATIO_ABS
            ratio_mode = (
                f"abs fallback ({history_days:.0f}d history < "
                f"{config.POS_SHORT_MIN_HISTORY_DAYS}d)"
            )

        funding_hot = funding > config.POS_SHORT_FUNDING_MIN

        oi_cutoff = int(oi_rows[-1]["timestamp"]) - config.POS_SHORT_OI_WINDOW_DAYS * _DAY_SECONDS
        oi_window = [
            float(row["open_interest"])
            for row in oi_rows
            if int(row["timestamp"]) >= oi_cutoff
        ]
        oi_now = oi_window[-1] if oi_window else 0.0
        oi_high = max(oi_window) if oi_window else 0.0
        oi_near_high = bool(oi_window) and oi_now >= (1 - config.POS_SHORT_OI_PROXIMITY) * oi_high

        metadata = {
            "ls_ratio": round(ratio_now, 3),
            "ratio_mode": ratio_mode,
            "ratio_extreme": ratio_extreme,
            "funding_rate": funding,
            "funding_hot": funding_hot,
            "oi_now": oi_now,
            "oi_30d_high": oi_high,
            "oi_near_high": oi_near_high,
            "history_days": round(history_days, 1),
        }

        if ratio_extreme and funding_hot and oi_near_high:
            return Signal(
                direction=SignalDirection.SHORT,
                reason=(
                    f"Crowded long extreme: L/S ratio {ratio_now:.2f} ({ratio_mode}), "
                    f"funding {funding * 100:.4f}% > {config.POS_SHORT_FUNDING_MIN * 100:.2f}%, "
                    f"OI within {config.POS_SHORT_OI_PROXIMITY * 100:.0f}% of 30d high — "
                    "contrarian short"
                ),
                symbol=symbol,
                entry_price=entry_price,
                metadata=metadata,
            )

        missing = []
        if not ratio_extreme:
            missing.append(f"ratio {ratio_now:.2f} not extreme ({ratio_mode})")
        if not funding_hot:
            missing.append(f"funding {funding * 100:.4f}% not hot")
        if not oi_near_high:
            missing.append("OI not near 30d high")
        return Signal(
            direction=SignalDirection.NONE,
            reason="Conditions unmet: " + "; ".join(missing),
            symbol=symbol,
            entry_price=entry_price,
            metadata=metadata,
        )
