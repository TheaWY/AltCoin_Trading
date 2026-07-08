"""Pair Trading Sandbox — paper-only spread/z-score strategy.

This is intentionally conservative and disabled unless pair history is supplied
by the data path. It is meant for research experiments, not live execution.

Because the base signal engine is symbol-centric, this strategy currently uses
`pair_recent_prices` when available. If the data fetcher cannot provide the
paired leg, it emits NONE with a clear reason instead of guessing.
"""

from __future__ import annotations

import math
from typing import Any

from src import config
from src.engine.indicators import closes
from src.engine.small_account import check_edge, max_strategy_notional, min_notional
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class PairTradingStrategy(BaseStrategy):
    name = "pair_trading"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "recent_prices_720", "pair_recent_prices"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        entry_price = float(data["latest_price"]["close"])
        rows_a = data.get("recent_prices_720") or []
        rows_b = data.get("pair_recent_prices") or []
        pair_symbol = data.get("pair_symbol") or getattr(config, "PAIR_TRADING_BENCHMARK", "BTC/USDT")

        if not rows_b:
            return Signal(SignalDirection.NONE, f"Pair sandbox waiting: no paired-leg history for {pair_symbol}", symbol, entry_price, {"paper_only": True, "pair_symbol": pair_symbol})
        if len(rows_a) < 168 or len(rows_b) < 168:
            return Signal(SignalDirection.NONE, "Pair sandbox needs at least 168 aligned hourly bars", symbol, entry_price, {"paper_only": True})

        a = closes(rows_a[-168:])
        b = closes(rows_b[-168:])
        n = min(len(a), len(b))
        a, b = a[-n:], b[-n:]
        spreads = []
        for av, bv in zip(a, b):
            if av > 0 and bv > 0:
                spreads.append(math.log(av) - math.log(bv))
        if len(spreads) < 48:
            return Signal(SignalDirection.NONE, "Pair sandbox lacks valid spread samples", symbol, entry_price, {"paper_only": True})

        mean = sum(spreads) / len(spreads)
        var = sum((x - mean) ** 2 for x in spreads) / len(spreads)
        std = math.sqrt(var)
        if std <= 0:
            return Signal(SignalDirection.NONE, "Pair sandbox spread has no variance", symbol, entry_price, {"paper_only": True})
        z = (spreads[-1] - mean) / std
        threshold = float(getattr(config, "PAIR_Z_ENTRY", 2.0))
        # Pair uses two legs, so require a larger edge than ordinary one-leg trades.
        expected_edge = abs(z) / 100.0
        cost = check_edge(expected_edge_pct=expected_edge, notional=max_strategy_notional() / 2, min_net_usd=0.05)
        metadata = {"paper_only": True, "pair_symbol": pair_symbol, "zscore": z, "expected_net_usd": cost.expected_net_usd}

        if abs(z) < threshold:
            return Signal(SignalDirection.NONE, f"Pair spread z={z:.2f}; waiting for ±{threshold:.1f}", symbol, entry_price, metadata)
        if not cost.ok:
            return Signal(SignalDirection.NONE, f"Pair spread z={z:.2f} but cost gate failed: {cost.reason}", symbol, entry_price, metadata)
        if max_strategy_notional() / 2 < min_notional():
            return Signal(SignalDirection.NONE, "Pair notional per leg below minimum", symbol, entry_price, metadata)

        # z>0 means symbol rich vs benchmark -> short symbol leg. z<0 would be long
        # symbol leg and is filtered by default ALLOW_LONG=false.
        if z > threshold:
            return Signal(SignalDirection.SHORT, f"Pair sandbox: {symbol} rich vs {pair_symbol}, z={z:.2f} — short symbol leg", symbol, entry_price, metadata)
        return Signal(SignalDirection.LONG, f"Pair sandbox: {symbol} cheap vs {pair_symbol}, z={z:.2f} — long symbol leg", symbol, entry_price, metadata)
