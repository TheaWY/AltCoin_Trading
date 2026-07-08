"""Signal generation engine — gathers data and delegates to strategies."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src import config
from src.data.storage import Storage, get_storage, signal_row_from_result
from src.strategies.base import BaseStrategy, Signal
from src.strategies.registry import get_strategy

logger = logging.getLogger(__name__)

# Data keys a strategy may declare in get_required_data(). Each fetcher works
# against any storage-like object (live Storage or the backtester's
# SnapshotStorage), so live trading and backtests share one data path.
_DATA_FETCHERS = {
    "funding_rate": lambda storage, symbol: storage.get_latest_funding_rate(symbol),
    "latest_price": lambda storage, symbol: storage.get_latest_price(symbol),
    "recent_prices": lambda storage, symbol: storage.get_prices(symbol, limit=48) or None,
    "volume_stats": lambda storage, symbol: storage.get_volume_stats(symbol),
    # funding_carry: enough collection-frequency rows to cover 6+ settlements
    # (rows are bucketed into 8h windows inside the strategy)
    "funding_history": lambda storage, symbol: storage.get_funding_rates(symbol, limit=800) or None,
    # positioning_short: 90d of hourly ratio rows, 30d of hourly OI rows
    "ls_ratio_history": lambda storage, symbol: storage.get_ls_ratio_history(symbol, limit=2160) or None,
    "open_interest_history": lambda storage, symbol: storage.get_open_interest_history(symbol, limit=720) or None,
}


def gather_strategy_data(storage: Any, strategy: BaseStrategy, symbol: str) -> dict[str, Any]:
    """Collect the data snapshot a strategy needs from a storage-like object."""
    data: dict[str, Any] = {"symbol": symbol}
    for key in strategy.get_required_data():
        fetcher = _DATA_FETCHERS.get(key)
        if fetcher is None:
            raise KeyError(f"No data fetcher registered for '{key}'")
        data[key] = fetcher(storage, symbol)
    return data


class SignalEngine:
    """Loads strategy data, generates signals, and persists results."""

    def __init__(self, storage: Storage | None = None) -> None:
        self.storage = storage or get_storage()

    def gather_data(self, strategy_name: str, symbol: str) -> dict[str, Any]:
        strategy = get_strategy(strategy_name)
        return gather_strategy_data(self.storage, strategy, symbol)

    def run(self, strategy_name: str | None = None, symbol: str | None = None) -> dict[str, Any]:
        return self.run_for_symbol(symbol or config.SYMBOL, strategy_name)

    def run_for_symbol(
        self, symbol: str, strategy_name: str | None = None
    ) -> dict[str, Any]:
        strategy_name = strategy_name or config.PRIMARY_STRATEGY
        strategy = get_strategy(strategy_name)

        data = self.gather_data(strategy_name, symbol)
        missing = strategy.validate_data(data)
        if missing:
            logger.warning("Signal skipped for %s — missing: %s", symbol, missing)
            return {"ok": False, "symbol": symbol, "error": f"Missing data: {missing}"}

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
            "strategy": strategy_name,
            "direction": signal.direction.value,
            "reason": signal.reason,
            "entry_price": signal.entry_price,
            "symbol": signal.symbol,
            "timestamp": now_ts,
        }
        logger.info(
            "Signal [%s] %s %s @ %s",
            strategy_name,
            symbol,
            signal.direction.value,
            signal.entry_price,
        )
        return result

    def run_all(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        from src.symbols import trading_symbols

        symbols = symbols or trading_symbols()
        return [self.run_for_symbol(sym) for sym in symbols]


def signal_to_dict(signal: Signal) -> dict[str, Any]:
    return {
        "direction": signal.direction.value,
        "reason": signal.reason,
        "symbol": signal.symbol,
        "entry_price": signal.entry_price,
        "metadata": signal.metadata,
        "actionable": signal.is_actionable,
    }
