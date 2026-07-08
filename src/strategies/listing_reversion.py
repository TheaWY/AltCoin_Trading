"""Listing Reversion Sandbox — paper-only event strategy.

Designed for future CEX listing-event research. It requires explicit listing
metadata in the data snapshot. Without that event context, it emits NONE.

Rule sketch:
- only trade inside LISTING_MAX_AGE_HOURS after listing,
- if price dumps hard from post-listing high and RSI/short-term momentum is
  washed out, emit a tiny paper LONG mean-reversion attempt,
- if price is still in blow-off pump mode near post-listing high, emit SHORT.

Default repo policy filters LONG entries, so this remains safe unless the user
explicitly enables longs for paper research.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.engine.indicators import closes, momentum_pct, rsi
from src.engine.small_account import check_edge
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class ListingReversionStrategy(BaseStrategy):
    name = "listing_reversion"

    def get_required_data(self) -> list[str]:
        return ["latest_price", "recent_prices", "listing_event"]

    def generate_signal(self, data: dict[str, Any]) -> Signal:
        symbol = data.get("symbol", config.SYMBOL)
        latest = data["latest_price"]
        entry_price = float(latest["close"])
        event = data.get("listing_event")
        if not event:
            return Signal(SignalDirection.NONE, "Listing sandbox waiting: no listing event metadata", symbol, entry_price, {"paper_only": True})

        listed_at = int(event.get("listed_at", 0))
        age_hours = (int(latest["timestamp"]) - listed_at) / 3600 if listed_at else 999999
        max_age = float(getattr(config, "LISTING_MAX_AGE_HOURS", 72))
        if age_hours > max_age:
            return Signal(SignalDirection.NONE, f"Listing event too old ({age_hours:.1f}h > {max_age:.0f}h)", symbol, entry_price, {"paper_only": True, "age_hours": age_hours})

        rows = data.get("recent_prices") or []
        values = closes(rows)
        if len(values) < 24:
            return Signal(SignalDirection.NONE, "Listing sandbox needs 24 hourly bars", symbol, entry_price, {"paper_only": True})

        high = max(float(r["high"]) for r in rows)
        drawdown = (entry_price - high) / high if high else 0.0
        pct_24h = momentum_pct(values, min(24, len(values) - 1)) or 0.0
        rsi_value = rsi(values, 14)
        expected_edge = abs(drawdown)
        cost = check_edge(expected_edge_pct=expected_edge, min_net_usd=0.10)
        metadata = {
            "paper_only": True,
            "age_hours": age_hours,
            "from_listing_high_pct": drawdown * 100,
            "pct_24h": pct_24h,
            "rsi": rsi_value,
            "expected_net_usd": cost.expected_net_usd,
        }

        dump_trigger = float(getattr(config, "LISTING_DUMP_TRIGGER_PCT", -12.0))
        pump_trigger = float(getattr(config, "LISTING_PUMP_TRIGGER_PCT", 20.0))
        if drawdown * 100 <= dump_trigger and (rsi_value is None or rsi_value <= 35):
            if not cost.ok:
                return Signal(SignalDirection.NONE, f"Listing dump seen but cost gate failed: {cost.reason}", symbol, entry_price, metadata)
            return Signal(SignalDirection.LONG, f"Paper listing rebound: {drawdown*100:.1f}% from listing high, RSI {rsi_value} — tiny mean-reversion long", symbol, entry_price, metadata)

        if pct_24h >= pump_trigger and drawdown > -0.03:
            if not cost.ok:
                return Signal(SignalDirection.NONE, f"Listing pump seen but cost gate failed: {cost.reason}", symbol, entry_price, metadata)
            return Signal(SignalDirection.SHORT, f"Paper listing fade: 24h +{pct_24h:.1f}% near listing high — tiny reversal short", symbol, entry_price, metadata)

        return Signal(SignalDirection.NONE, "Listing event active but no pump/dump reversal setup", symbol, entry_price, metadata)
