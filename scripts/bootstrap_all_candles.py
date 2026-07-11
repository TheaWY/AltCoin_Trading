#!/usr/bin/env python3
"""Bootstrap 1h candle history for active Binance USDT perpetual markets.

This is meant to be run on the Mac mini worker, not Railway:

    source .venv/bin/activate
    python scripts/bootstrap_all_candles.py --limit 0 --candles 720

It discovers the current Binance USDT perpetual universe, downloads historical
candles for each symbol, stores them through the normal Storage layer, and
prints a success/failure summary. It uses real public market-data endpoints by
default and does not require Binance API keys.

Important: when DATABASE_URL points to Railway Postgres, a full 600+ symbol
bootstrap is network/database-bound. This script skips symbols that already have
enough candles by default, so it is safe to stop and rerun.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
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

TIMEFRAME_SECONDS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "1d": 24 * 60 * 60,
}
DEFAULT_BACKFILL_START = "2024-07-01"


@dataclass
class SymbolResult:
    symbol: str
    ok: bool
    fetched: int = 0
    stored: int = 0
    latest_close: float | None = None
    skipped: bool = False
    error: str | None = None


def _set_runtime_env(limit: int, timeframe: str, candles: int) -> None:
    os.environ.setdefault("LIVE_TRADING", "false")
    os.environ.setdefault("BINANCE_MARKET_DATA_TESTNET", "false")
    os.environ["SYMBOL_UNIVERSE"] = "auto"
    os.environ["TRADING_SYMBOLS_LIMIT"] = str(limit)
    os.environ["OHLCV_TIMEFRAMES"] = timeframe
    os.environ["OHLCV_LIMIT"] = str(candles)

    config.SYMBOL_UNIVERSE = "auto"
    config.TRADING_SYMBOLS_LIMIT = limit
    config.OHLCV_TIMEFRAMES = [timeframe]
    config.OHLCV_LIMIT = candles
    config.BINANCE_MARKET_DATA_TESTNET = False


def _stored_count_and_latest(storage: Any, symbol: str, timeframe: str, candles: int) -> tuple[int, float | None]:
    rows = storage.get_prices(symbol, limit=candles + 5, timeframe=timeframe)
    latest = rows[-1]["close"] if rows else None
    return len(rows), latest


def _coverage(storage: Any, symbol: str, timeframe: str) -> dict[str, Any]:
    with storage._connect() as conn:  # noqa: SLF001 - bootstrap utility
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts "
            "FROM prices WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        ).fetchone()
    if not row:
        return {"n": 0, "min_ts": None, "max_ts": None}
    if isinstance(row, dict):
        return {
            "n": int(row.get("n") or row.get("count") or 0),
            "min_ts": row.get("min_ts"),
            "max_ts": row.get("max_ts"),
        }
    return {"n": int(row[0] or 0), "min_ts": row[1], "max_ts": row[2]}


def _parse_utc_date(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def _backfill_start_ts(years: float | None) -> int | None:
    if years is None:
        return None
    target = int(time.time() - years * 365 * 24 * 3600)
    return min(target, _parse_utc_date(DEFAULT_BACKFILL_START))


def _expected_candles(start_ts: int, end_ts: int, timeframe: str) -> int:
    seconds = TIMEFRAME_SECONDS.get(timeframe)
    if not seconds:
        return 0
    return max(1, int((end_ts - start_ts) / seconds))


def _insert_price_rows(storage: Any, rows: list[dict[str, Any]], timeframe: str) -> int:
    """Insert rows.

    The shared Storage layer is correct but slow against remote Railway Postgres
    because it loops one INSERT per row. For this one-shot bootstrap path, use a
    Postgres multi-row INSERT in chunks. SQLite/local fallback still uses Storage.
    """
    if not rows:
        return 0
    if not getattr(storage, "is_postgres", False):
        return int(storage.insert_prices(rows, timeframe=timeframe))

    from psycopg import sql

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
    chunk_size = 250
    with storage._pool().connection() as conn:  # uses the existing app pool
        with conn.cursor() as cur:
            for start in range(0, len(values), chunk_size):
                chunk = values[start : start + chunk_size]
                placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s)"] * len(chunk))
                flat: list[Any] = []
                for item in chunk:
                    flat.extend(item)
                query = f"""
                    INSERT INTO prices
                        (symbol, timestamp, timeframe, open, high, low, close, volume)
                    VALUES {placeholders}
                    ON CONFLICT DO NOTHING
                """
                cur.execute(query, flat)
                affected += max(cur.rowcount, 0)
    return affected


def _collect_one(
    storage: Any,
    exchange: Any,
    symbol: str,
    timeframe: str,
    candles: int,
    skip_ready: bool,
    min_ready: int,
) -> SymbolResult:
    try:
        existing, latest = _stored_count_and_latest(storage, symbol, timeframe, candles)
        if skip_ready and existing >= min_ready:
            return SymbolResult(
                symbol=symbol,
                ok=True,
                fetched=0,
                stored=existing,
                latest_close=latest,
                skipped=True,
            )

        market_symbol = ccxt_symbol(symbol)
        candles_raw = exchange.fetch_ohlcv(market_symbol, timeframe=timeframe, limit=candles)
        rows = [_candle_to_price_row(symbol, candle, timeframe) for candle in candles_raw]
        inserted = _insert_price_rows(storage, rows, timeframe)
        stored, latest = _stored_count_and_latest(storage, symbol, timeframe, candles)
        logger.info(
            "OHLCV collected for %s %s: %d candles fetched, %d new rows",
            symbol,
            timeframe,
            len(rows),
            inserted,
        )
        return SymbolResult(
            symbol=symbol,
            ok=True,
            fetched=len(rows),
            stored=stored,
            latest_close=latest,
        )
    except Exception as exc:  # keep going; one bad/delisted symbol should not stop bootstrap
        logger.exception("Bootstrap failed for %s", symbol)
        return SymbolResult(symbol=symbol, ok=False, error=str(exc)[:300])


def _collect_history(
    storage: Any,
    exchange: Any,
    symbol: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
    skip_ready: bool,
) -> SymbolResult:
    try:
        coverage = _coverage(storage, symbol, timeframe)
        if (
            skip_ready
            and coverage["min_ts"] is not None
            and int(coverage["min_ts"]) <= start_ts + TIMEFRAME_SECONDS.get(timeframe, 3600)
        ):
            latest_rows = storage.get_prices(symbol, limit=1, timeframe=timeframe)
            latest = latest_rows[-1]["close"] if latest_rows else None
            return SymbolResult(
                symbol=symbol,
                ok=True,
                fetched=0,
                stored=int(coverage["n"]),
                latest_close=latest,
                skipped=True,
            )

        market_symbol = ccxt_symbol(symbol)
        since_ms = start_ts * 1000
        end_ms = end_ts * 1000
        timeframe_ms = TIMEFRAME_SECONDS.get(timeframe, 3600) * 1000
        fetched = 0
        inserted_total = 0
        latest_close = None

        while since_ms <= end_ms:
            candles_raw = exchange.fetch_ohlcv(
                market_symbol,
                timeframe=timeframe,
                since=since_ms,
                limit=1000,
            )
            if not candles_raw:
                break
            rows = [
                _candle_to_price_row(symbol, candle, timeframe)
                for candle in candles_raw
                if int(candle[0]) <= end_ms
            ]
            if rows:
                inserted_total += _insert_price_rows(storage, rows, timeframe)
                fetched += len(rows)
                latest_close = rows[-1]["close"]
            last_ts_ms = int(candles_raw[-1][0])
            next_since_ms = last_ts_ms + timeframe_ms
            if next_since_ms <= since_ms or last_ts_ms >= end_ms:
                break
            since_ms = next_since_ms

        coverage = _coverage(storage, symbol, timeframe)
        return SymbolResult(
            symbol=symbol,
            ok=True,
            fetched=fetched,
            stored=int(coverage["n"]),
            latest_close=latest_close,
        )
    except Exception as exc:
        logger.exception("Historical bootstrap failed for %s", symbol)
        return SymbolResult(symbol=symbol, ok=False, error=str(exc)[:300])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="0 = all active USDT perps; N = top N by 24h volume")
    parser.add_argument("--symbols-top", type=int, default=None, help="Top N active symbols to bootstrap; always includes BTC/USDT and ETH/USDT")
    parser.add_argument("--candles", type=int, default=720, help="Number of candles to fetch per symbol")
    parser.add_argument("--years", type=float, default=None, help="Paginate historical candles back about this many years; currently floors at 2024-07-01")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe, default 1h")
    parser.add_argument("--sleep", type=float, default=0.02, help="Delay between symbols")
    parser.add_argument("--only", default="", help="Comma-separated symbols to bootstrap instead of discovery, e.g. BTC/USDT,ETH/USDT")
    parser.add_argument("--failures-file", default="data/bootstrap_failures.txt", help="Where to write failed symbols")
    parser.add_argument("--no-skip-ready", action="store_true", help="Refetch even symbols that already have enough candles")
    parser.add_argument("--min-ready", type=int, default=48, help="Skip symbols with at least this many stored candles")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    effective_limit = args.symbols_top if args.symbols_top is not None else args.limit
    _set_runtime_env(effective_limit, args.timeframe, args.candles)

    storage = get_storage()
    exchange = _build_exchange(use_testnet=False, authenticated=False)

    if args.only.strip():
        symbols = [s.strip() for s in args.only.split(",") if s.strip()]
    else:
        symbols = trading_symbols()
    if args.symbols_top is not None:
        symbols = symbols[: args.symbols_top]
        for core in ("BTC/USDT", "ETH/USDT"):
            if core not in symbols:
                symbols.insert(0, core)
        symbols = list(dict.fromkeys(symbols))
    elif args.limit > 0:
        symbols = symbols[: args.limit]

    backfill_start = _backfill_start_ts(args.years)
    backfill_end = int(time.time()) if backfill_start is not None else None

    print("\n=== Candle bootstrap ===")
    print(f"Symbols: {len(symbols)}")
    print(f"Timeframe: {args.timeframe}")
    print(f"Candles per symbol: {args.candles}")
    if backfill_start is not None:
        print(
            "Historical backfill: "
            f"{datetime.fromtimestamp(backfill_start, tz=timezone.utc).strftime('%Y-%m-%d')} "
            f"→ {datetime.fromtimestamp(backfill_end or int(time.time()), tz=timezone.utc).strftime('%Y-%m-%d')}"
        )
    print(f"DATABASE_URL set: {'yes' if bool(config.DATABASE_URL) else 'no (local SQLite)'}")
    print(f"Skip ready: {not args.no_skip_ready} (min_ready={args.min_ready})")
    print("Market data endpoint: Binance mainnet public")
    print("========================\n")

    results: list[SymbolResult] = []
    started = time.monotonic()
    for idx, symbol in enumerate(symbols, start=1):
        print(f"[{idx:>4}/{len(symbols)}] {symbol} ...", flush=True)
        if backfill_start is not None and backfill_end is not None:
            result = _collect_history(
                storage,
                exchange,
                symbol,
                args.timeframe,
                backfill_start,
                backfill_end,
                skip_ready=not args.no_skip_ready,
            )
        else:
            result = _collect_one(
                storage,
                exchange,
                symbol,
                args.timeframe,
                args.candles,
                skip_ready=not args.no_skip_ready,
                min_ready=args.min_ready,
            )
        results.append(result)
        if result.ok and result.skipped:
            print(f"      SKIP stored={result.stored} latest={result.latest_close}")
        elif result.ok:
            print(f"      OK fetched={result.fetched} stored={result.stored} latest={result.latest_close}")
        else:
            print(f"      FAIL {result.error}")
        if args.sleep > 0:
            time.sleep(args.sleep)

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    skipped = [r for r in ok if r.skipped]
    ready = [r for r in ok if r.stored >= min(48, args.candles)]
    elapsed = time.monotonic() - started

    failures_path = PROJECT_ROOT / args.failures_file
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    failures_path.write_text(
        "\n".join(f"{r.symbol}\t{r.error}" for r in failed),
        encoding="utf-8",
    )

    print("\n=== Bootstrap summary ===")
    print(f"Total symbols: {len(results)}")
    print(f"Succeeded: {len(ok)}")
    print(f"Skipped already-ready: {len(skipped)}")
    print(f"Ready >=48 candles: {len(ready)}")
    print(f"Failed: {len(failed)}")
    print(f"Elapsed: {elapsed / 60:.1f} min")
    print(f"Failures file: {failures_path}")
    if failed:
        print("First 20 failures:")
        for r in failed[:20]:
            print(f"- {r.symbol}: {r.error}")
    print("=========================\n")
    return 1 if not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
