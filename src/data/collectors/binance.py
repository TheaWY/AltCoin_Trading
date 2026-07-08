"""Binance data collector — OHLCV candles and perpetual funding rates."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import ccxt

from src import config
from src.data.storage import Storage, get_storage

logger = logging.getLogger(__name__)

_TIMEFRAME_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _timeframe_seconds(timeframe: str) -> int | None:
    try:
        return int(timeframe[:-1]) * _TIMEFRAME_UNIT_SECONDS[timeframe[-1]]
    except (KeyError, ValueError):
        return None


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
        timeframe: str | None = None,
    ) -> None:
        self.storage = storage or get_storage()
        self.exchange = exchange or _build_exchange()
        self.symbol = symbol or config.SYMBOL
        self.futures_symbol = futures_symbol or config.CCXT_SYMBOL
        self.timeframe = timeframe

    def collect_ohlcv(
        self,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """Fetch OHLCV candles and persist raw rows to the prices table."""
        if timeframe is None and self.timeframe is None:
            return {
                tf: self._collect_ohlcv_for_timeframe(tf, limit)
                for tf in config.OHLCV_TIMEFRAMES
            }

        return self._collect_ohlcv_for_timeframe(
            timeframe or self.timeframe or config.OHLCV_TIMEFRAME,
            limit,
        )

    def _collect_ohlcv_for_timeframe(
        self,
        timeframe: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = self._incremental_limit(timeframe, limit or config.OHLCV_LIMIT)

        candles = self.exchange.fetch_ohlcv(
            self.futures_symbol, timeframe=timeframe, limit=limit
        )
        rows = [_candle_to_price_row(self.symbol, candle, timeframe) for candle in candles]
        inserted = self.storage.insert_prices(rows, timeframe=timeframe)
        logger.info(
            "OHLCV collected for %s %s: %d candles fetched, %d new rows",
            self.symbol,
            timeframe,
            len(rows),
            inserted,
        )
        return rows

    def _incremental_limit(self, timeframe: str, max_limit: int) -> int:
        """Only request the candles missing since the newest stored one."""
        tf_seconds = _timeframe_seconds(timeframe)
        if not tf_seconds:
            return max_limit
        latest = self.storage.get_latest_price(self.symbol, timeframe)
        if not latest:
            return max_limit
        now_ts = int(datetime.now(timezone.utc).timestamp())
        # +2 candle overlap: re-fetch the (mutable) current candle and its predecessor
        missing = (now_ts - int(latest["timestamp"])) // tf_seconds + 2
        return max(2, min(max_limit, int(missing)))

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

    def collect_all(self, funding_row: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run all Binance collectors in one pass.

        `funding_row` lets run_collection() pass a rate that was already
        fetched in the batch call, skipping the per-symbol request.
        """
        ohlcv = self.collect_ohlcv()
        funding = funding_row if funding_row is not None else self.collect_funding_rate()
        return {"ohlcv": ohlcv, "funding_rate": funding}


def _candle_to_price_row(symbol: str, candle: list, timeframe: str) -> dict[str, Any]:
    ts_ms, open_, high, low, close, volume = candle
    return {
        "symbol": symbol,
        "timestamp": int(ts_ms // 1000),
        "timeframe": timeframe,
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


def _collect_funding_batch(
    exchange: ccxt.binance, storage: Storage, symbols: list[str]
) -> dict[str, dict[str, Any]]:
    """Fetch funding rates for every symbol in one API call.

    Returns spot symbol -> stored funding row. An empty dict means the batch
    failed and callers should fall back to per-symbol requests.
    """
    from src.symbols import ccxt_symbol

    mapping = {ccxt_symbol(spot): spot for spot in symbols}
    try:
        rates = exchange.fetch_funding_rates(list(mapping.keys()))
    except Exception:
        logger.warning(
            "Batch funding-rate fetch failed; falling back to per-symbol requests",
            exc_info=True,
        )
        return {}

    rows: dict[str, dict[str, Any]] = {}
    for market_symbol, funding in (rates or {}).items():
        spot = mapping.get(market_symbol)
        if not spot or not funding:
            continue
        rows[spot] = _funding_to_row(spot, funding)

    if rows:
        storage.insert_funding_rates(list(rows.values()))
        logger.info("Funding rates collected in one batch call for %d symbols", len(rows))
    return rows


def run_collection(symbols: list[str] | None = None) -> dict[str, Any]:
    """Collect OHLCV + funding for all configured symbols."""
    from src.symbols import ccxt_symbol, trading_symbols

    symbols = symbols or trading_symbols()
    exchange = _build_exchange()
    storage = get_storage()
    results: dict[str, Any] = {}

    funding_batch = _collect_funding_batch(exchange, storage, symbols)

    for spot in symbols:
        try:
            collector = BinanceCollector(
                storage=storage,
                exchange=exchange,
                symbol=spot,
                futures_symbol=ccxt_symbol(spot),
            )
            results[spot] = collector.collect_all(funding_row=funding_batch.get(spot))
        except Exception:
            logger.exception("Collection failed for %s", spot)
            results[spot] = {"error": True}

    return results
