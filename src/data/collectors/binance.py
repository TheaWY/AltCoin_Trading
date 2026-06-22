"""Binance data collector — OHLCV candles and perpetual funding rates."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import ccxt

from src import config
from src.data.storage import Storage, get_storage

logger = logging.getLogger(__name__)


def _build_exchange() -> ccxt.binance:
    exchange = ccxt.binance(
        {
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
    )
    if config.BINANCE_TESTNET:
        exchange.set_sandbox_mode(True)
    return exchange


class BinanceCollector:
    """Fetches OHLCV and funding rate data from Binance (testnet by default)."""

    def __init__(
        self,
        storage: Storage | None = None,
        exchange: ccxt.binance | None = None,
        symbol: str | None = None,
        futures_symbol: str | None = None,
    ) -> None:
        self.storage = storage or get_storage()
        self.exchange = exchange or _build_exchange()
        self.symbol = symbol or config.SYMBOL
        self.futures_symbol = futures_symbol or config.CCXT_SYMBOL

    def collect_ohlcv(
        self,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV candles and persist raw rows to the prices table."""
        timeframe = timeframe or config.OHLCV_TIMEFRAME
        limit = limit or config.OHLCV_LIMIT

        candles = self.exchange.fetch_ohlcv(
            self.futures_symbol, timeframe=timeframe, limit=limit
        )
        rows = [_candle_to_price_row(self.symbol, candle) for candle in candles]
        inserted = self.storage.insert_prices(rows)
        logger.info(
            "OHLCV collected for %s: %d candles fetched, %d new rows",
            self.symbol,
            len(rows),
            inserted,
        )
        return rows

    def collect_funding_rate(self) -> dict[str, Any] | None:
        """Fetch the latest funding rate and persist it."""
        funding = self.exchange.fetch_funding_rate(self.futures_symbol)
        if not funding:
            logger.warning("No funding rate returned for %s", self.futures_symbol)
            return None

        row = _funding_to_row(self.symbol, funding)
        self.storage.insert_funding_rates([row])
        logger.info(
            "Funding rate collected for %s: %.6f at ts=%s",
            self.symbol,
            row["funding_rate"],
            row["timestamp"],
        )
        return row

    def collect_all(self) -> dict[str, Any]:
        """Run all Binance collectors in one pass."""
        ohlcv = self.collect_ohlcv()
        funding = self.collect_funding_rate()
        return {"ohlcv": ohlcv, "funding_rate": funding}


def _candle_to_price_row(symbol: str, candle: list) -> dict[str, Any]:
    ts_ms, open_, high, low, close, volume = candle
    return {
        "symbol": symbol,
        "timestamp": int(ts_ms // 1000),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


def _funding_to_row(symbol: str, funding: dict[str, Any]) -> dict[str, Any]:
    ts = funding.get("timestamp")
    if ts is None:
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    rate = funding.get("fundingRate")
    if rate is None:
        rate = funding.get("info", {}).get("lastFundingRate", 0)

    return {
        "symbol": symbol,
        "timestamp": int(ts // 1000),
        "funding_rate": float(rate),
    }


def run_collection() -> dict[str, Any]:
    """Convenience entry point for scheduler / manual runs."""
    collector = BinanceCollector()
    return collector.collect_all()
