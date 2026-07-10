#!/usr/bin/env python3
"""Backfill historical Binance futures data into local Postgres.

This is meant for the Mac mini local-server setup. It builds the deeper history
needed for real backtesting. The regular bootstrap only loads ~720 hourly candles
(~30 days); this script pages backward/forward and stores years of candles for a
controlled symbol universe.

Start narrow, then expand:

    # Recommended first serious dataset: top 50 liquid crypto perps since 2022
    python scripts/backfill_history.py --limit 50 --start 2022-01-01 --timeframes 1h --funding

    # After that is stable, expand to top 100
    python scripts/backfill_history.py --limit 100 --start 2021-01-01 --timeframes 1h --funding

    # Faster sanity check
    python scripts/backfill_history.py --only BTC/USDT,ETH/USDT,SOL/USDT --start 2023-01-01 --funding

Notes:
- Uses your current DATABASE_URL. In Mac mini mode this should be local Postgres.
- Does not enable live trading.
- Resumable/idempotent: inserts use ON CONFLICT/INSERT OR IGNORE.
- Funding history is fetched from Binance USD-M futures public REST where possible.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.collectors.binance import _build_exchange, _candle_to_price_row  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from src.symbols import ccxt_symbol, trading_symbols  # noqa: E402

logger = logging.getLogger(__name__)

BINANCE_FAPI_BASE = "https://fapi.binance.com"
MSEC = 1000
TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
}


@dataclass
class SymbolBackfillResult:
    symbol: str
    timeframe: str
    fetched: int = 0
    inserted: int = 0
    error: str | None = None


@dataclass
class FundingBackfillResult:
    symbol: str
    fetched: int = 0
    inserted: int = 0
    error: str | None = None


def parse_ts(value: str | None, default: datetime | None = None) -> int:
    if not value:
        if default is None:
            raise ValueError("missing datetime")
        return int(default.replace(tzinfo=timezone.utc).timestamp())
    value = value.strip()
    if value.isdigit():
        return int(value)
    if len(value) == 10:
        value = value + "T00:00:00+00:00"
    elif value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def utc_label(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def set_runtime_env(limit: int) -> None:
    os.environ.setdefault("LIVE_TRADING", "false")
    os.environ.setdefault("BINANCE_MARKET_DATA_TESTNET", "false")
    os.environ["SYMBOL_UNIVERSE"] = "auto"
    os.environ["TRADING_SYMBOLS_LIMIT"] = str(limit)
    config.LIVE_TRADING = False
    config.BINANCE_MARKET_DATA_TESTNET = False
    config.SYMBOL_UNIVERSE = "auto"
    config.TRADING_SYMBOLS_LIMIT = limit


def parse_symbols(value: str | None, limit: int) -> list[str]:
    if value:
        return [s.strip() for s in value.split(",") if s.strip()]
    symbols = trading_symbols()
    if limit > 0:
        symbols = symbols[:limit]
    return symbols


def is_railway_url() -> bool:
    url = (config.DATABASE_URL or "").lower()
    return any(marker in url for marker in ("railway", "rlwy", "proxy.rlwy.net", "up.railway.app"))


def insert_price_rows_bulk(storage: Any, rows: list[dict[str, Any]], timeframe: str) -> int:
    if not rows:
        return 0
    if not getattr(storage, "is_postgres", False):
        return int(storage.insert_prices(rows, timeframe=timeframe))

    values = [
        (
            row["symbol"],
            int(row["timestamp"]),
            row.get("timeframe", timeframe),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
        )
        for row in rows
    ]
    affected = 0
    chunk_size = 1000
    with storage._pool().connection() as conn:  # noqa: SLF001
        with conn.cursor() as cur:
            for start in range(0, len(values), chunk_size):
                chunk = values[start : start + chunk_size]
                placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s)"] * len(chunk))
                flat: list[Any] = []
                for item in chunk:
                    flat.extend(item)
                cur.execute(
                    f"""
                    INSERT INTO prices
                        (symbol, timestamp, timeframe, open, high, low, close, volume)
                    VALUES {placeholders}
                    ON CONFLICT DO NOTHING
                    """,
                    flat,
                )
                affected += max(cur.rowcount, 0)
    return affected


def insert_funding_rows_bulk(storage: Any, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    if not getattr(storage, "is_postgres", False):
        return int(storage.insert_funding_rates(rows))

    values = [(r["symbol"], int(r["timestamp"]), float(r["funding_rate"])) for r in rows]
    affected = 0
    chunk_size = 1000
    with storage._pool().connection() as conn:  # noqa: SLF001
        with conn.cursor() as cur:
            for start in range(0, len(values), chunk_size):
                chunk = values[start : start + chunk_size]
                placeholders = ",".join(["(%s,%s,%s)"] * len(chunk))
                flat: list[Any] = []
                for item in chunk:
                    flat.extend(item)
                cur.execute(
                    f"""
                    INSERT INTO funding_rates (symbol, timestamp, funding_rate)
                    VALUES {placeholders}
                    ON CONFLICT DO NOTHING
                    """,
                    flat,
                )
                affected += max(cur.rowcount, 0)
    return affected


def existing_coverage(storage: Any, symbol: str, timeframe: str) -> dict[str, Any]:
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts
            FROM prices
            WHERE symbol = ? AND timeframe = ?
            """,
            (symbol, timeframe),
        ).fetchone()
    return dict(row) if row else {"n": 0, "min_ts": None, "max_ts": None}


def fetch_ohlcv_page(exchange: Any, futures_symbol: str, timeframe: str, since_ms: int, limit: int) -> list[list[Any]]:
    return exchange.fetch_ohlcv(futures_symbol, timeframe=timeframe, since=since_ms, limit=limit)


def backfill_symbol_timeframe(
    storage: Any,
    exchange: Any,
    symbol: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
    page_limit: int,
    sleep_seconds: float,
    max_pages: int | None = None,
) -> SymbolBackfillResult:
    result = SymbolBackfillResult(symbol=symbol, timeframe=timeframe)
    tf_seconds = TIMEFRAME_SECONDS.get(timeframe)
    if not tf_seconds:
        result.error = f"unsupported timeframe: {timeframe}"
        return result

    futures_symbol = ccxt_symbol(symbol)
    cursor_ms = start_ts * MSEC
    end_ms = end_ts * MSEC
    pages = 0
    try:
        while cursor_ms < end_ms:
            candles = fetch_ohlcv_page(exchange, futures_symbol, timeframe, cursor_ms, page_limit)
            if not candles:
                break
            rows = [_candle_to_price_row(symbol, candle, timeframe) for candle in candles if int(candle[0]) < end_ms]
            if not rows:
                break
            inserted = insert_price_rows_bulk(storage, rows, timeframe)
            result.fetched += len(rows)
            result.inserted += inserted
            last_open_ms = int(candles[-1][0])
            next_ms = last_open_ms + tf_seconds * MSEC
            if next_ms <= cursor_ms:
                next_ms = cursor_ms + tf_seconds * MSEC
            cursor_ms = next_ms
            pages += 1
            if pages % 20 == 0:
                print(
                    f"    {symbol} {timeframe}: pages={pages} fetched={result.fetched} inserted={result.inserted} cursor={utc_label(cursor_ms//1000)}",
                    flush=True,
                )
            if max_pages and pages >= max_pages:
                break
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    except Exception as exc:
        logger.exception("OHLCV backfill failed for %s %s", symbol, timeframe)
        result.error = str(exc)[:500]
    return result


def http_json(path: str, params: dict[str, Any], timeout: int = 30) -> Any:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BINANCE_FAPI_BASE}{path}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "AltCoin-Trading-MacMini/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def binance_api_symbol(symbol: str) -> str:
    return symbol.replace("/", "").upper()


def backfill_funding_symbol(
    storage: Any,
    symbol: str,
    start_ts: int,
    end_ts: int,
    sleep_seconds: float,
    max_pages: int | None = None,
) -> FundingBackfillResult:
    result = FundingBackfillResult(symbol=symbol)
    cursor_ms = start_ts * MSEC
    end_ms = end_ts * MSEC
    pages = 0
    try:
        while cursor_ms < end_ms:
            data = http_json(
                "/fapi/v1/fundingRate",
                {
                    "symbol": binance_api_symbol(symbol),
                    "startTime": cursor_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            if not data:
                break
            rows = [
                {
                    "symbol": symbol,
                    "timestamp": int(item["fundingTime"]) // MSEC,
                    "funding_rate": float(item["fundingRate"]),
                }
                for item in data
                if int(item.get("fundingTime", 0)) < end_ms
            ]
            inserted = insert_funding_rows_bulk(storage, rows)
            result.fetched += len(rows)
            result.inserted += inserted
            last_ms = int(data[-1]["fundingTime"])
            next_ms = last_ms + 1
            if next_ms <= cursor_ms:
                next_ms = cursor_ms + 8 * 3600 * MSEC
            cursor_ms = next_ms
            pages += 1
            if pages % 10 == 0:
                print(
                    f"    {symbol} funding: pages={pages} fetched={result.fetched} inserted={result.inserted} cursor={utc_label(cursor_ms//1000)}",
                    flush=True,
                )
            if len(data) < 1000:
                break
            if max_pages and pages >= max_pages:
                break
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    except Exception as exc:
        logger.exception("Funding backfill failed for %s", symbol)
        result.error = str(exc)[:500]
    return result


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2022-01-01", help="UTC start date, e.g. 2022-01-01")
    parser.add_argument("--end", default=None, help="UTC end date, default now")
    parser.add_argument("--limit", type=int, default=50, help="Top N symbols by volume; 0 = all, not recommended for first run")
    parser.add_argument("--only", default="", help="Comma-separated symbols instead of auto universe")
    parser.add_argument("--timeframes", default="1h", help="Comma-separated timeframes, e.g. 1h or 15m,1h,1d")
    parser.add_argument("--funding", action="store_true", help="Also backfill funding history")
    parser.add_argument("--page-limit", type=int, default=1500, help="OHLCV page size")
    parser.add_argument("--sleep", type=float, default=0.08, help="Sleep between pages")
    parser.add_argument("--max-pages", type=int, default=0, help="Safety cap per symbol/timeframe; 0 = unlimited")
    parser.add_argument("--allow-railway", action="store_true", help="Allow running against Railway DATABASE_URL; default refuses")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without fetching")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    set_runtime_env(args.limit)
    storage = get_storage()
    if is_railway_url() and not args.allow_railway:
        print("Refusing to backfill: DATABASE_URL appears to point to Railway. Use local Mac mini Postgres or pass --allow-railway intentionally.")
        return 2

    start_ts = parse_ts(args.start)
    end_ts = parse_ts(args.end, datetime.now(timezone.utc))
    timeframes = [tf.strip() for tf in args.timeframes.split(",") if tf.strip()]
    symbols = parse_symbols(args.only, args.limit)
    max_pages = args.max_pages or None

    print("\n=== Historical backfill plan ===")
    print(f"Storage: {'Postgres' if storage.is_postgres else 'SQLite'}")
    print(f"Railway URL detected: {is_railway_url()}")
    print(f"Symbols: {len(symbols)}")
    print(f"Timeframes: {timeframes}")
    print(f"Start: {utc_label(start_ts)}")
    print(f"End: {utc_label(end_ts)}")
    print(f"Funding: {args.funding}")
    print("Recommended first run: --limit 50 --start 2022-01-01 --timeframes 1h --funding")
    print("================================\n")

    if args.dry_run:
        for symbol in symbols[:20]:
            print(symbol)
        if len(symbols) > 20:
            print(f"... {len(symbols)-20} more")
        return 0

    exchange = _build_exchange(use_testnet=False, authenticated=False)
    all_price_results: list[SymbolBackfillResult] = []
    all_funding_results: list[FundingBackfillResult] = []
    started = time.monotonic()

    for i, symbol in enumerate(symbols, start=1):
        print(f"[{i}/{len(symbols)}] {symbol}", flush=True)
        for timeframe in timeframes:
            cov = existing_coverage(storage, symbol, timeframe)
            print(
                f"  {timeframe} existing rows={cov.get('n')} range={utc_label(cov['min_ts']) if cov.get('min_ts') else 'none'} → {utc_label(cov['max_ts']) if cov.get('max_ts') else 'none'}",
                flush=True,
            )
            res = backfill_symbol_timeframe(
                storage,
                exchange,
                symbol,
                timeframe,
                start_ts,
                end_ts,
                args.page_limit,
                args.sleep,
                max_pages=max_pages,
            )
            all_price_results.append(res)
            if res.error:
                print(f"  FAIL {timeframe}: {res.error}", flush=True)
            else:
                print(f"  OK {timeframe}: fetched={res.fetched} inserted={res.inserted}", flush=True)
        if args.funding:
            fres = backfill_funding_symbol(storage, symbol, start_ts, end_ts, args.sleep, max_pages=max_pages)
            all_funding_results.append(fres)
            if fres.error:
                print(f"  FAIL funding: {fres.error}", flush=True)
            else:
                print(f"  OK funding: fetched={fres.fetched} inserted={fres.inserted}", flush=True)

    elapsed = time.monotonic() - started
    report = {
        "params": {
            "start": start_ts,
            "end": end_ts,
            "symbols": len(symbols),
            "timeframes": timeframes,
            "funding": args.funding,
        },
        "elapsed_minutes": round(elapsed / 60, 2),
        "prices": [r.__dict__ for r in all_price_results],
        "funding": [r.__dict__ for r in all_funding_results],
        "summary": {
            "price_fetched": sum(r.fetched for r in all_price_results),
            "price_inserted": sum(r.inserted for r in all_price_results),
            "price_errors": sum(1 for r in all_price_results if r.error),
            "funding_fetched": sum(r.fetched for r in all_funding_results),
            "funding_inserted": sum(r.inserted for r in all_funding_results),
            "funding_errors": sum(1 for r in all_funding_results if r.error),
        },
    }
    output = PROJECT_ROOT / "data" / f"history_backfill_{int(time.time())}.json"
    write_report(output, report)
    print("\n=== Backfill summary ===")
    print(json.dumps(report["summary"], indent=2))
    print(f"Elapsed: {elapsed/60:.1f} min")
    print(f"Report: {output}")
    print("========================\n")
    return 1 if report["summary"]["price_errors"] or report["summary"]["funding_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
