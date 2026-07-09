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
_PLACEHOLDER_KEYS = {
    "",
    "your_testnet_api_key",
    "your_testnet_api_secret",
    "your_api_key",
    "your_api_secret",
    "changeme",
}


def _clean_credential(value: str | None) -> str:
    value = (value or "").strip()
    if value.lower() in _PLACEHOLDER_KEYS:
        return ""
    return value


def _timeframe_seconds(timeframe: str) -> int | None:
    try:
        return int(timeframe[:-1]) * _TIMEFRAME_UNIT_SECONDS[timeframe[-1]]
    except (KeyError, ValueError):
        return None


def _build_exchange(use_testnet: bool | None = None, *, authenticated: bool | None = None) -> ccxt.binance:
    """Build a Binance futures exchange.

    LiveTrader calls this with the legacy default (`BINANCE_TESTNET`) for order
    safety and needs credentials. Data collectors pass
    `BINANCE_MARKET_DATA_TESTNET=false` and do NOT need credentials; sending an
    invalid/placeholder key causes ccxt to call signed SAPI endpoints and fail
    before public OHLCV can load.
    """
    if use_testnet is None:
        use_testnet = config.BINANCE_TESTNET
    if authenticated is None:
        authenticated = use_testnet or config.LIVE_TRADING

    options: dict[str, Any] = {
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    }
    api_key = _clean_credential(config.BINANCE_API_KEY)
    api_secret = _clean_credential(config.BINANCE_API_SECRET)
    if authenticated and api_key and api_secret:
        options["apiKey"] = api_key
        options["secret"] = api_secret
    elif authenticated:
        logger.warning("Binance exchange requested authenticated mode but API keys are blank/placeholders")

    exchange = ccxt.binance(options)
    if use_testnet:
        exchange.set_sandbox_mode(True)
    return exchange


class BinanceCollector:
    """Fetches OHLCV and funding rate data from Binance."""

    def __init__(
        self,
        storage: Storage | None = None,
        exchange: ccxt.binance | None = None,
        symbol: str | None = None,
        futures_symbol: str | None = None,
        timeframe: str | None = None,
    ) -> None:
        self.storage = storage or get_storage()
        self.exchange = exchange or _build_exchange(use_testnet=config.BINANCE_MARKET_DATA_TESTNET, authenticated=False)
        self.symbol = symbol or config.SYMBOL
        self.futures_symbol = futures_symbol or config.CCXT_SYMBOL
        self.timeframe = timeframe

    def collect_ohlcv(
        self,
        timeframe: str | None = None,
        limit: int | None = None,
        timeframes: list[str] | None = None,
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """Fetch OHLCV candles and persist raw rows to the prices table."""
        if timeframe is None and self.timeframe is None:
            return {
                tf: self._collect_ohlcv_for_timeframe(tf, limit)
                for tf in (timeframes or config.OHLCV_TIMEFRAMES)
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
        if limit is None:
            # Scheduled collection: only fetch candles missing since last run.
            limit = self._incremental_limit(timeframe, config.OHLCV_LIMIT)
        # Explicit limit (e.g. backfill) is honored as-is.

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

    def collect_all(
        self,
        funding_row: dict[str, Any] | None = None,
        timeframes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run all Binance collectors in one pass.

        `funding_row` lets run_collection() pass a rate that was already
        fetched in the batch call, skipping the per-symbol request.
        """
        ohlcv = self.collect_ohlcv(timeframes=timeframes)
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


def _collect_market_metrics(
    exchange: ccxt.binance, storage: Storage, symbols: list[str]
) -> int:
    """Collect open interest + global long/short account ratio.

    Both are per-symbol endpoints on Binance futures, so this only runs for
    the core symbol list (not the full 500+ universe) to keep request volume
    sane. Failures are non-fatal — these metrics are enrichment, not gates.
    """
    from src.symbols import ccxt_symbol

    now_ts = int(datetime.now(timezone.utc).timestamp())
    rows: list[dict[str, Any]] = []
    for spot in symbols:
        market_symbol = ccxt_symbol(spot)
        row: dict[str, Any] = {
            "symbol": spot,
            "timestamp": now_ts,
            "open_interest": None,
            "open_interest_usd": None,
            "long_short_ratio": None,
        }
        try:
            oi = exchange.fetch_open_interest(market_symbol)
            row["open_interest"] = oi.get("openInterestAmount")
            row["open_interest_usd"] = oi.get("openInterestValue")
        except Exception:
            logger.debug("Open interest fetch failed for %s", spot, exc_info=True)
        try:
            market_id = exchange.market(market_symbol)["id"]
            data = exchange.fapiDataGetGlobalLongShortAccountRatio(
                {"symbol": market_id, "period": "1h", "limit": 1}
            )
            if data:
                row["long_short_ratio"] = float(data[-1]["longShortRatio"])
        except Exception:
            logger.debug("Long/short ratio fetch failed for %s", spot, exc_info=True)

        if row["open_interest"] is not None or row["long_short_ratio"] is not None:
            rows.append(row)

    inserted = storage.insert_market_metrics(rows)
    if rows:
        logger.info(
            "Market metrics (OI / long-short) collected for %d symbols", len(rows)
        )
    return inserted


def run_collection(symbols: list[str] | None = None) -> dict[str, Any]:
    """Collect OHLCV + funding for all configured symbols.

    Core symbols (the static TRADING_SYMBOLS list) get every configured
    timeframe; the extended auto-discovered universe gets 1h only, which is
    all the evaluation metrics need — this keeps the request count sane with
    hundreds of symbols.
    """
    from src.symbols import ccxt_symbol, core_symbols, trading_symbols

    symbols = symbols or trading_symbols()
    core = set(core_symbols())
    exchange = _build_exchange(use_testnet=config.BINANCE_MARKET_DATA_TESTNET, authenticated=False)
    storage = get_storage()
    results: dict[str, Any] = {}

    logger.info(
        "Market data collection using Binance %s endpoints for %d symbols",
        "testnet" if config.BINANCE_MARKET_DATA_TESTNET else "mainnet public",
        len(symbols),
    )

    funding_batch = _collect_funding_batch(exchange, storage, symbols)

    try:
        _collect_market_metrics(exchange, storage, sorted(core & set(symbols)))
        storage.cleanup_old_market_metrics()
    except Exception:
        logger.warning("Market metrics collection failed", exc_info=True)

    for spot in symbols:
        try:
            collector = BinanceCollector(
                storage=storage,
                exchange=exchange,
                symbol=spot,
                futures_symbol=ccxt_symbol(spot),
            )
            results[spot] = collector.collect_all(
                funding_row=funding_batch.get(spot),
                timeframes=None if spot in core else ["1h"],
            )
        except Exception:
            logger.exception("Collection failed for %s", spot)
            results[spot] = {"error": True}

    return results
