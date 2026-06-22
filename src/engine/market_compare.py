"""Market comparison — track actual price outcomes vs generated signals."""

from __future__ import annotations

import logging
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.strategies.base import SignalDirection

logger = logging.getLogger(__name__)

_HORIZON_FIELDS = {
    1: ("price_1h", "correct_1h"),
    4: ("price_4h", "correct_4h"),
    24: ("price_24h", "correct_24h"),
}


class MarketCompare:
    """Records signal outcomes at 1h / 4h / 24h horizons."""

    def __init__(self, storage: Storage | None = None) -> None:
        self.storage = storage or get_storage()

    def register_from_signal(self, signal_result: dict[str, Any]) -> int | None:
        direction = signal_result.get("direction")
        if direction not in (SignalDirection.LONG.value, SignalDirection.SHORT.value):
            return None

        signal_id = signal_result["signal_id"]
        if self.storage.get_market_outcome_by_signal(signal_id):
            return None

        outcome_id = self.storage.insert_market_outcome(
            {
                "signal_id": signal_id,
                "symbol": signal_result.get("symbol", config.SYMBOL),
                "direction": direction,
                "entry_price": signal_result["entry_price"],
                "price_1h": None,
                "price_4h": None,
                "price_24h": None,
                "correct_1h": None,
                "correct_4h": None,
                "correct_24h": None,
            }
        )
        logger.info("Market outcome tracking started for signal %s", signal_id)
        return outcome_id

    def backfill_missing_stubs(self) -> int:
        created = 0
        for signal in self.storage.get_actionable_signals_without_outcomes():
            self.storage.insert_market_outcome(
                {
                    "signal_id": signal["id"],
                    "symbol": signal["symbol"],
                    "direction": signal["direction"],
                    "entry_price": signal["entry_price"],
                    "price_1h": None,
                    "price_4h": None,
                    "price_24h": None,
                    "correct_1h": None,
                    "correct_4h": None,
                    "correct_24h": None,
                }
            )
            created += 1
        return created

    def update_pending(self, now_ts: int | None = None) -> int:
        from datetime import datetime, timezone

        now_ts = now_ts or int(datetime.now(timezone.utc).timestamp())
        updated = 0

        for outcome in self.storage.get_incomplete_market_outcomes():
            signal_ts = int(outcome["signal_timestamp"])
            symbol = outcome["symbol"]
            direction = outcome["direction"]
            entry = float(outcome["entry_price"])
            fields: dict[str, Any] = {}

            for hours in config.MARKET_COMPARE_HORIZONS_HOURS:
                price_field, correct_field = _HORIZON_FIELDS[hours]
                if outcome.get(price_field) is not None:
                    continue
                target_ts = signal_ts + hours * 3600
                if now_ts < target_ts:
                    continue
                candle = self.storage.get_price_at_or_after(symbol, target_ts)
                if not candle:
                    continue
                price = float(candle["close"])
                fields[price_field] = price
                fields[correct_field] = int(self._is_correct(direction, entry, price))

            if fields:
                self.storage.update_market_outcome(int(outcome["id"]), fields)
                updated += 1

        return updated

    @staticmethod
    def _is_correct(direction: str, entry: float, later_price: float) -> bool:
        if direction == SignalDirection.LONG.value:
            return later_price > entry
        return later_price < entry
