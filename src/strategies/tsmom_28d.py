"""28d Time-Series Momentum — paper-only swing strategy.

The research notes point to 28d lookbacks as a useful crypto trend horizon.
This strategy uses the existing 720-hour candle store and requires price to be
on the correct side of SMA20 before emitting a signal.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.engine.indicators import atr_pct, closes, momentum_pct, sma
from src.engine.small_account import check_edge
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class TSMom28dStrategy(BaseStrategy):
    name = "tsmom_28d"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "recent_prices_720"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        rows = data["recent_prices_720"] or []
        entry_price = float(data["latest_price"]["close"])
        values = closes(rows)
        lookback = int(getattr(config, "TSMOM_LOOKBACK_HOURS", 24 * 28))
        threshold = float(getattr(config, "MOMENTUM_28D_STRONG_PCT", 15.0))

        pct = momentum_pct(values, lookback)
        if pct is None and len(values) >= 24 * 27:
            pct = momentum_pct(values, len(values) - 1)
        if pct is None:
            return Signal(SignalDirection.NONE, f"Not enough history for 28d momentum ({len(values)}/{lookback + 1})", symbol, entry_price, {"bars": len(values)})

        sma20 = sma(values, 20)
        atr = atr_pct(rows[-48:], 14) or 1.0
        expected_edge = min(abs(pct) / 100.0, (float(getattr(config, "ATR_TP_MULT", 2.5)) * atr) / 100.0)
        cost = check_edge(expected_edge_pct=expected_edge)
        metadata = {
            "pct_28d": pct,
            "sma20": sma20,
            "atr_pct": atr,
            "expected_edge_pct": expected_edge,
            "expected_net_usd": cost.expected_net_usd,
            "cost_ok": cost.ok,
        }

        if pct >= threshold and sma20 is not None and entry_price > sma20:
            if not cost.ok:
                return Signal(SignalDirection.NONE, f"28d up momentum but cost gate failed: {cost.reason}", symbol, entry_price, metadata)
            return Signal(SignalDirection.LONG, f"28d momentum +{pct:.1f}% and price>SMA20 — paper trend long ({cost.reason})", symbol, entry_price, metadata)

        if pct <= -threshold and sma20 is not None and entry_price < sma20:
            if not cost.ok:
                return Signal(SignalDirection.NONE, f"28d down momentum but cost gate failed: {cost.reason}", symbol, entry_price, metadata)
            return Signal(SignalDirection.SHORT, f"28d momentum {pct:.1f}% and price<SMA20 — paper trend short ({cost.reason})", symbol, entry_price, metadata)

        return Signal(SignalDirection.NONE, f"28d momentum {pct:+.1f}% not confirmed by threshold/SMA20", symbol, entry_price, metadata)
