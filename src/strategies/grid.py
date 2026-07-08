"""Paper Grid Strategy — range-only neutral/short grid proxy.

This does not place live maker orders. It emits a paper signal only when:
- realized range is wide enough to pay grid fees,
- trend is mild enough for a range strategy,
- estimated grid notional respects small-account minimum order constraints.

Default behavior is short-biased: if price is near the upper part of the range,
it emits SHORT; otherwise it waits. This reflects the research preference for
neutral/short grids over long-only exposure.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.engine.indicators import atr_pct, closes, momentum_pct
from src.engine.small_account import check_edge, max_strategy_notional, min_notional
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class GridStrategy(BaseStrategy):
    name = "grid"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "recent_prices_720"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        rows = data["recent_prices_720"] or []
        entry_price = float(data["latest_price"]["close"])
        lookback = int(getattr(config, "GRID_RANGE_HOURS", 24 * 7))
        grid_levels = int(getattr(config, "GRID_LEVELS", 10))
        max_trend_pct = float(getattr(config, "GRID_MAX_TREND_PCT", 8.0))

        if len(rows) < max(48, lookback):
            return Signal(SignalDirection.NONE, f"Only {len(rows)}/{lookback} bars for grid range", symbol, entry_price, {"bars": len(rows)})

        window = rows[-lookback:]
        values = closes(window)
        high = max(float(r["high"]) for r in window)
        low = min(float(r["low"]) for r in window)
        if low <= 0 or high <= low:
            return Signal(SignalDirection.NONE, "Invalid grid range", symbol, entry_price, {})

        range_pct = (high - low) / entry_price
        spacing_pct = range_pct / max(grid_levels, 1)
        trend_pct = momentum_pct(values, min(len(values) - 1, lookback - 1)) or 0.0
        atr = atr_pct(window[-48:], 14) or 0.0
        notional_per_grid = max_strategy_notional() / max(grid_levels, 1)
        maker_edge = spacing_pct
        cost = check_edge(expected_edge_pct=maker_edge, notional=notional_per_grid, maker=True, min_net_usd=0.02)
        pos_in_range = (entry_price - low) / (high - low)

        metadata = {
            "range_low": low,
            "range_high": high,
            "range_pct": round(range_pct * 100, 3),
            "spacing_pct": round(spacing_pct * 100, 3),
            "trend_pct": trend_pct,
            "atr_pct": atr,
            "grid_levels": grid_levels,
            "notional_per_grid": notional_per_grid,
            "min_notional": min_notional(),
            "position_in_range": round(pos_in_range, 3),
            "paper_only": True,
        }

        if abs(trend_pct) > max_trend_pct:
            return Signal(SignalDirection.NONE, f"Grid disabled: trend {trend_pct:+.1f}% exceeds ±{max_trend_pct:.1f}%", symbol, entry_price, metadata)
        if not cost.ok:
            return Signal(SignalDirection.NONE, f"Grid disabled: {cost.reason}", symbol, entry_price, metadata)
        if notional_per_grid < min_notional():
            return Signal(SignalDirection.NONE, f"Grid disabled: ${notional_per_grid:.2f}/level below ${min_notional():.2f} minimum", symbol, entry_price, metadata)

        # Short-biased upper-range signal. Middle/lower range is wait-only so the
        # default short-only policy remains coherent.
        if pos_in_range >= float(getattr(config, "GRID_SHORT_ZONE", 0.70)):
            return Signal(SignalDirection.SHORT, f"Paper short-grid: price in upper {pos_in_range:.0%} of 7d range, spacing {spacing_pct*100:.2f}% ({cost.reason})", symbol, entry_price, metadata)

        return Signal(SignalDirection.NONE, f"Grid range valid but price not in short zone ({pos_in_range:.0%} of range)", symbol, entry_price, metadata)
