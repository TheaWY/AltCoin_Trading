"""Donchian Breakout — paper-only Turtle-style swing strategy.

20d high breakout -> LONG, 20d low breakout -> SHORT. With the repo default
ALLOW_LONG=false, the LONG leg is filtered downstream; the strategy still emits
it so paper/backtests can evaluate both legs when policy allows.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.engine.indicators import atr_pct
from src.engine.small_account import check_edge
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class DonchianBreakoutStrategy(BaseStrategy):
    name = "donchian_breakout"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "recent_prices_720"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        rows = data["recent_prices_720"] or []
        latest = data["latest_price"]
        entry_price = float(latest["close"])
        window = int(getattr(config, "DONCHIAN_WINDOW_HOURS", 480))  # 20d x 24h

        if len(rows) < max(48, window // 2):
            return Signal(SignalDirection.NONE, f"Only {len(rows)}/{window} bars for Donchian breakout", symbol, entry_price, {"bars": len(rows)})

        prior = rows[-window - 1 : -1]
        if len(prior) < 24:
            return Signal(SignalDirection.NONE, "Not enough prior candles for Donchian channel", symbol, entry_price, {})

        channel_high = max(float(r["high"]) for r in prior)
        channel_low = min(float(r["low"]) for r in prior)
        last = float(rows[-1]["close"])
        atr = atr_pct(rows[-48:], 14) or 1.0
        # Expected move proxy: target ATR multiple. Must clear costs for $730 account.
        expected_edge = (float(getattr(config, "ATR_TP_MULT", 2.5)) * atr) / 100.0
        cost = check_edge(expected_edge_pct=expected_edge)
        metadata = {
            "channel_high": channel_high,
            "channel_low": channel_low,
            "atr_pct": atr,
            "expected_edge_pct": expected_edge,
            "expected_net_usd": cost.expected_net_usd,
            "cost_ok": cost.ok,
        }

        if last > channel_high:
            if not cost.ok:
                return Signal(SignalDirection.NONE, f"Donchian high breakout but cost gate failed: {cost.reason}", symbol, entry_price, metadata)
            return Signal(SignalDirection.LONG, f"20d high breakout above {channel_high:.6g}; paper Turtle long ({cost.reason})", symbol, entry_price, metadata)

        if last < channel_low:
            if not cost.ok:
                return Signal(SignalDirection.NONE, f"Donchian low breakout but cost gate failed: {cost.reason}", symbol, entry_price, metadata)
            return Signal(SignalDirection.SHORT, f"20d low breakout below {channel_low:.6g}; paper Turtle short ({cost.reason})", symbol, entry_price, metadata)

        return Signal(SignalDirection.NONE, "No 20d Donchian breakout", symbol, entry_price, metadata)
