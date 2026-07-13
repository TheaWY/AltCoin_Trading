"""Positioning data collector — global long/short account ratio and open interest.

Uses Binance's public /futures/data/ endpoints directly (not ccxt: these
sentiment endpoints aren't part of ccxt's unified API). No API key needed.

IMPORTANT: Binance only serves ~30 days of history for these endpoints.
The positioning_short strategy needs 90 days for percentile mode, so this
collector must run on every cycle from day one to accumulate history.
Until ~90 days accumulate, the strategy falls back to an absolute
ratio threshold (config.POS_SHORT_RATIO_ABS).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from src.data.storage import Storage, get_storage

logger = logging.getLogger(__name__)

_FAPI = "https://fapi.binance.com"
_PERIOD = "1h"
_LIMIT = 500  # max rows per request; 500 x 1h ≈ 20 days, fine for incremental
_MIN_COLLECTION_INTERVAL_SECONDS = 3600
_last_collection_started_at: float | None = None


def positioning_collection_symbols(symbols: list[str]) -> list[str]:
    """No re-slicing here, deliberately (fixed 2026-07-13): this used to be
    symbols[:20], which silently dropped an open position that only made
    the caller's list via the union-with-open-positions branch in
    src.symbols.cycle_symbols() (appended past the slice point) -- the same
    class of failure as the price-feed gap that froze entries for 10 hours.
    The caller already scopes correctly; trust it. Widen/narrow by changing
    what cycle.py passes in (see src.symbols.cycle_symbols's limit param),
    not by re-capping here."""
    return symbols


def _current_hour_start() -> int:
    return int(time.time() // _MIN_COLLECTION_INTERVAL_SECONDS) * _MIN_COLLECTION_INTERVAL_SECONDS


def _latest_stored_positioning_timestamp(storage: Storage, symbols: list[str]) -> int | None:
    latest: int | None = None
    for symbol in symbols:
        for getter in (storage.get_ls_ratio_history, storage.get_open_interest_history):
            rows = getter(symbol, limit=1)
            if not rows:
                continue
            timestamp = int(rows[-1]["timestamp"])
            latest = timestamp if latest is None else max(latest, timestamp)
    return latest


def _futures_symbol(spot_symbol: str) -> str:
    """'BTC/USDT' -> 'BTCUSDT' (fapi futures/data uses the raw pair string)."""
    return spot_symbol.replace("/", "").split(":")[0]


def _get(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    url = f"{_FAPI}{path}"
    for attempt in range(3):
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 429:
            time.sleep(2**attempt)
            continue
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    logger.warning("Rate limited fetching %s", path)
    return []


class PositioningCollector:
    """Fetches long/short account ratio and open interest history."""

    def __init__(self, symbol: str, storage: Storage | None = None) -> None:
        self.symbol = symbol
        self.futures_symbol = _futures_symbol(symbol)
        self.storage = storage or get_storage()

    def collect_ls_ratio(self) -> int:
        raw = _get(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": self.futures_symbol, "period": _PERIOD, "limit": _LIMIT},
        )
        rows = [
            {
                "symbol": self.symbol,
                "timestamp": int(item["timestamp"]) // 1000,
                "ratio": float(item["longShortRatio"]),
            }
            for item in raw
            if item.get("longShortRatio") is not None
        ]
        inserted = self.storage.insert_ls_ratios(rows)
        logger.info("Collected %d long/short ratio rows for %s", len(rows), self.symbol)
        return inserted

    def collect_open_interest(self) -> int:
        raw = _get(
            "/futures/data/openInterestHist",
            {"symbol": self.futures_symbol, "period": _PERIOD, "limit": _LIMIT},
        )
        rows = [
            {
                "symbol": self.symbol,
                "timestamp": int(item["timestamp"]) // 1000,
                "open_interest": float(item["sumOpenInterest"]),
            }
            for item in raw
            if item.get("sumOpenInterest") is not None
        ]
        inserted = self.storage.insert_open_interest(rows)
        logger.info("Collected %d open interest rows for %s", len(rows), self.symbol)
        return inserted

    def collect_all(self) -> dict[str, int]:
        return {
            "ls_ratio": self.collect_ls_ratio(),
            "open_interest": self.collect_open_interest(),
        }


def run_positioning_collection(
    symbols: list[str], storage: Storage | None = None
) -> dict[str, dict[str, int]]:
    """Collect positioning data for the highest-ranked active symbols.

    The Binance positioning endpoints are 1h-period data and each symbol
    writes hundreds of historical rows, which is too slow to run across the
    full auto-discovered universe every 5-minute trading cycle. The caller's
    symbol order is the existing universe ranking, so we collect only the top
    slice and skip runs until the next hourly window.

    Failures are logged per symbol, never raised — positioning data is
    enrichment, and a fetch failure must not break the trading cycle.
    Strategies that require this data return NONE signals through
    validate_data() when it's missing.
    """
    global _last_collection_started_at

    now = time.monotonic()
    if (
        _last_collection_started_at is not None
        and now - _last_collection_started_at < _MIN_COLLECTION_INTERVAL_SECONDS
    ):
        logger.info(
            "Skipping positioning collection; last run started %.0f seconds ago",
            now - _last_collection_started_at,
        )
        return {}

    _last_collection_started_at = now
    results: dict[str, dict[str, int]] = {}
    target_symbols = positioning_collection_symbols(symbols)
    storage = storage or get_storage()
    latest_stored_ts = _latest_stored_positioning_timestamp(storage, target_symbols)
    current_hour_start = _current_hour_start()
    if latest_stored_ts is not None and latest_stored_ts >= current_hour_start:
        logger.info(
            "Skipping positioning collection; stored data covers current hourly period"
        )
        return {}

    logger.info("Collecting positioning data for %d ranked symbols", len(target_symbols))
    for symbol in target_symbols:
        try:
            results[symbol] = PositioningCollector(symbol, storage).collect_all()
            time.sleep(0.2)  # stay well under fapi rate limits
        except Exception:
            logger.exception("Positioning collection failed for %s", symbol)
    return results
