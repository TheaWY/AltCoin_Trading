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
    """Collect positioning data for the given symbols. Failures are logged
    per symbol, never raised — positioning data is enrichment, and a fetch
    failure must not break the trading cycle. Strategies that require this
    data return NONE signals through validate_data() when it's missing.
    """
    storage = storage or get_storage()
    results: dict[str, dict[str, int]] = {}
    for symbol in symbols:
        try:
            results[symbol] = PositioningCollector(symbol, storage).collect_all()
            time.sleep(0.2)  # stay well under fapi rate limits
        except Exception:
            logger.exception("Positioning collection failed for %s", symbol)
    return results
