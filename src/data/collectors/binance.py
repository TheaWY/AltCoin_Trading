"""Binance data collector — OHLCV candles and perpetual funding rates."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import ccxt

from src import config
from src.data.storage import Storage, get_storage
from src.market.bars import is_bar_closed, timeframe_to_seconds

logger = logging.getLogger(__name__)

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
        return timeframe_to_seconds(timeframe)
    except ValueError:
        return None


def _closed_candles(
    candles: list[list[Any]],
    timeframe: str,
    *,
    now_ms: int | None = None,
) -> list[list[Any]]:
    """Return only candles whose full interval has elapsed.

    CCXT may include the current, still-forming candle in ``fetch_ohlcv``.
    Persisting that row is unsafe because its high/low/close/volume can change
    and strategy code may mistake it for a completed observation. Binance/CCXT
    candle timestamps identify the interval start, so a candle is complete only
    when ``open_time + timeframe <= now``.
    """
    try:
        decision_time = (
            int(now_ms // 1000)
            if now_ms is not None
            else int(datetime.now(timezone.utc).timestamp())
        )
    except Exception:
        decision_time = int(datetime.now(timezone.utc).timestamp())
    try:
        timeframe_to_seconds(timeframe)
    except ValueError:
        logger.warning(
            "Unknown timeframe %s; refusing to infer candle completion",
            timeframe,
        )
        return []
    return [
        candle
        for candle in candles
        if candle and is_bar_closed({"timestamp": int(candle[0] // 1000)}, timeframe, decision_time)
    ]


def _build_exchange(
    use_testnet: bool | None = None,
    *,
    authenticated: bool | None = None,
) -> ccxt.binance:
    """Build a Binance futures exchange.

    LiveTrader calls this with the legacy default (``BINANCE_TESTNET``) for
    order safety and needs credentials. Data collectors use public endpoints
    and do not need credentials.
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
        logger.warning(
            "Binance exchange requested authenticated mode but API keys are blank/placeholders"
        )

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
        self.exchange = exchange or _build_exchange(
            use_testnet=config.BINANCE_MARKET_DATA_TESTNET,
            authenticated=False,
        )
        self.symbol = symbol or config.SYMBOL
        self.futures_symbol = futures_symbol or config.CCXT_SYMBOL
        self.timeframe = timeframe

    def collect_ohlcv(
        self,
        timeframe: str | None = None,
        limit: int | None = None,
        timeframes: list[str] | None = None,
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """Fetch completed OHLCV candles and persist them to the prices table."""
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
        report = self.collect_ohlcv_report(timeframe, limit)
        return report["rows"]

    def collect_ohlcv_report(
        self,
        timeframe: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if limit is None:
            limit = self._incremental_limit(timeframe, config.OHLCV_LIMIT)

        fetched = self.exchange.fetch_ohlcv(
            self.futures_symbol,
            timeframe=timeframe,
            limit=limit,
        )
        candles = _closed_candles(fetched, timeframe)
        rows = [
            _candle_to_price_row(self.symbol, candle, timeframe)
            for candle in candles
        ]
        inserted = self.storage.insert_prices(rows, timeframe=timeframe)
        latest = self.storage.get_latest_price(self.symbol, timeframe)
        latest_ts = int(latest["timestamp"]) if latest else None
        seconds = _timeframe_seconds(timeframe)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        expected_next = latest_ts + seconds if latest_ts is not None and seconds else None
        candle_age = now_ts - latest_ts if latest_ts is not None else None
        logger.info(
            "OHLCV collected for %s %s: %d fetched, %d completed, %d new rows",
            self.symbol,
            timeframe,
            len(fetched),
            len(rows),
            inserted,
        )
        return {
            "symbol": self.symbol,
            "timeframe": timeframe,
            "success": True,
            "latest_completed_candle_ts": latest_ts,
            "expected_next_timestamp": expected_next,
            "candle_age_seconds": candle_age,
            "rows_fetched": len(fetched),
            "rows_inserted": inserted,
            "error_category": None,
            "rows": rows,
        }

    def _incremental_limit(self, timeframe: str, max_limit: int) -> int:
        """Only request candles missing since the newest stored closed candle."""
        timeframe_seconds = _timeframe_seconds(timeframe)
        if not timeframe_seconds:
            return max_limit
        latest = self.storage.get_latest_price(self.symbol, timeframe)
        if not latest:
            return max_limit
        now_ts = int(datetime.now(timezone.utc).timestamp())
        # Re-fetch a small overlap; the current candle is filtered before insert.
        missing = (now_ts - int(latest["timestamp"])) // timeframe_seconds + 2
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
        """Run all Binance collectors in one pass."""
        ohlcv = self.collect_ohlcv(timeframes=timeframes)
        funding = (
            funding_row
            if funding_row is not None
            else self.collect_funding_rate()
        )
        return {"ohlcv": ohlcv, "funding_rate": funding}


def _candle_to_price_row(
    symbol: str,
    candle: list[Any],
    timeframe: str,
) -> dict[str, Any]:
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
    timestamp = funding.get("timestamp")
    if timestamp is None:
        timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    rate = funding.get("fundingRate")
    if rate is None:
        rate = funding.get("info", {}).get("lastFundingRate", 0)

    return {
        "symbol": symbol,
        "timestamp": int(timestamp // 1000),
        "funding_rate": float(rate),
    }


def _collect_funding_batch(
    exchange: ccxt.binance,
    storage: Storage,
    symbols: list[str],
) -> dict[str, dict[str, Any]]:
    """Fetch funding rates for every symbol in one API call."""
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
        logger.info(
            "Funding rates collected in one batch call for %d symbols",
            len(rows),
        )
    return rows


def _collect_market_metrics(
    exchange: ccxt.binance,
    storage: Storage,
    symbols: list[str],
) -> int:
    """Collect open interest and global long/short account ratio."""
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
            open_interest = exchange.fetch_open_interest(market_symbol)
            row["open_interest"] = open_interest.get("openInterestAmount")
            row["open_interest_usd"] = open_interest.get("openInterestValue")
        except Exception:
            logger.debug(
                "Open interest fetch failed for %s",
                spot,
                exc_info=True,
            )
        try:
            market_id = exchange.market(market_symbol)["id"]
            data = exchange.fapiDataGetGlobalLongShortAccountRatio(
                {"symbol": market_id, "period": "1h", "limit": 1}
            )
            if data:
                row["long_short_ratio"] = float(data[-1]["longShortRatio"])
        except Exception:
            logger.debug(
                "Long/short ratio fetch failed for %s",
                spot,
                exc_info=True,
            )

        if (
            row["open_interest"] is not None
            or row["long_short_ratio"] is not None
        ):
            rows.append(row)

    inserted = storage.insert_market_metrics(rows)
    if rows:
        logger.info(
            "Market metrics (OI / long-short) collected for %d symbols",
            len(rows),
        )
    return inserted


def run_collection(symbols: list[str] | None = None) -> dict[str, Any]:
    """Collect OHLCV and funding for configured symbols."""
    from src.symbols import ccxt_symbol, core_symbols, trading_symbols

    symbols = symbols or trading_symbols()
    core = set(core_symbols())
    exchange = _build_exchange(
        use_testnet=config.BINANCE_MARKET_DATA_TESTNET,
        authenticated=False,
    )
    storage = get_storage()
    reports: list[dict[str, Any]] = []

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
        timeframes = list(config.OHLCV_TIMEFRAMES) if spot in core else ["1h"]
        try:
            collector = BinanceCollector(
                storage=storage,
                exchange=exchange,
                symbol=spot,
                futures_symbol=ccxt_symbol(spot),
            )
            for timeframe in timeframes:
                try:
                    report = collector.collect_ohlcv_report(timeframe)
                    reports.append({k: v for k, v in report.items() if k != "rows"})
                except Exception as exc:
                    logger.exception("OHLCV collection failed for %s %s", spot, timeframe)
                    reports.append(
                        {
                            "symbol": spot,
                            "timeframe": timeframe,
                            "success": False,
                            "latest_completed_candle_ts": None,
                            "expected_next_timestamp": None,
                            "candle_age_seconds": None,
                            "rows_fetched": 0,
                            "rows_inserted": 0,
                            "error_category": exc.__class__.__name__,
                        }
                    )
            if spot not in funding_batch:
                try:
                    collector.collect_funding_rate()
                except Exception:
                    logger.warning("Funding collection failed for %s", spot, exc_info=True)
        except Exception:
            logger.exception("Collection failed for %s", spot)
            for timeframe in timeframes:
                reports.append(
                    {
                        "symbol": spot,
                        "timeframe": timeframe,
                        "success": False,
                        "latest_completed_candle_ts": None,
                        "expected_next_timestamp": None,
                        "candle_age_seconds": None,
                        "rows_fetched": 0,
                        "rows_inserted": 0,
                        "error_category": "symbol_collection_failed",
                    }
                )

    return {
        "ok": all(r["success"] for r in reports),
        "reports": reports,
        "symbols": len(symbols),
        "partial": any(r["success"] for r in reports) and not all(r["success"] for r in reports),
    }
