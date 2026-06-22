"""Signal generation engine — gathers data and delegates to strategies."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src import config
from src.data.storage import Storage, get_storage, signal_row_from_result
from src.strategies.base import Signal
from src.strategies.registry import get_strategy

logger = logging.getLogger(__name__)

_DATA_FETCHERS = {
    "funding_rate": lambda storage, symbol: storage.get_latest_funding_rate(symbol),
    "latest_price": lambda storage, symbol: storage.get_latest_price(symbol),
}


class SignalEngine:
    """Loads strategy data, generates signals, and persists results."""

    def __init__(self, storage: Storage | None = None) -> None:
        self.storage = storage or get_storage()

    def gather_data(self, strategy_name: str, symbol: str) -> dict[str, Any]:
        strategy = get_strategy(strategy_name)
        data: dict[str, Any] = {"symbol": symbol}
        for key in strategy.get_required_data():
            fetcher = _DATA_FETCHERS.get(key)
            if fetcher is None:
                raise KeyError(f"No data fetcher registered for '{key}'")
            data[key] = fetcher(self.storage, symbol)
        return data

    def run(self, strategy_name: str | None = None) -> dict[str, Any]:
        strategy_name = strategy_name or config.ACTIVE_STRATEGY
        symbol = config.SYMBOL
        strategy = get_strategy(strategy_name)

        data = self.gather_data(strategy_name, symbol)
        missing = strategy.validate_data(data)
        if missing:
            logger.warning("Signal run skipped — missing data: %s", missing)
            return {"ok": False, "error": f"Missing data: {missing}"}

        signal = strategy.generate_signal(data)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        funding_rate = None
        if data.get("funding_rate"):
            funding_rate = data["funding_rate"]["funding_rate"]

        row = signal_row_from_result(strategy_name, signal, now_ts, funding_rate)
        signal_id = self.storage.insert_signal(row)

        result = {
            "ok": True,
            "signal_id": signal_id,
            "direction": signal.direction.value,
            "reason": signal.reason,
            "entry_price": signal.entry_price,
            "symbol": signal.symbol,
            "timestamp": now_ts,
        }
        logger.info(
            "Signal generated [%s] %s @ %s — %s",
            strategy_name,
            signal.direction.value,
            signal.entry_price,
            signal.reason,
        )
        return result


def signal_to_dict(signal: Signal) -> dict[str, Any]:
    return {
        "direction": signal.direction.value,
        "reason": signal.reason,
        "symbol": signal.symbol,
        "entry_price": signal.entry_price,
        "metadata": signal.metadata,
        "actionable": signal.is_actionable,
    }
