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
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.collectors.binance import _build_exchange, _candle_to_price_row  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from src.symbols import ccxt_symbol, trading_symbols  # noqa: E402

logger = logging.getLogger(__name__)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="0 = all active USDT perps; N = top N by 24h volume")
    parser.add_argument("--candles", type=int, default=720, help="Number of candles to fetch per symbol")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe, default 1h")
    parser.add_argument("--sleep", type=float, default=0.02, help="Delay between symbols")
    parser.add_argument("--only", default="", help="Comma-separated symbols to bootstrap instead of discovery, e.g. BTC/USDT,ETH/USDT")
    parser.add_argument("--failures-file", default="data/bootstrap_failures.txt", help="Where to write failed symbols")
    parser.add_argument("--no-skip-ready", action="store_true", help="Refetch even symbols that already have enough candles")
    parser.add_argument("--min-ready", type=int, default=48, help="Skip symbols with at least this many stored candles")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _set_runtime_env(args.limit, args.timeframe, args.candles)

    storage = get_storage()
    exchange = _build_exchange(use_testnet=False, authenticated=False)

    if args.only.strip():
        symbols = [s.strip() for s in args.only.split(",") if s.strip()]
    else:
        symbols = trading_symbols()
    if args.limit > 0:
        symbols = symbols[: args.limit]

    print("\n=== Candle bootstrap ===")
    print(f"Symbols: {len(symbols)}")
    print(f"Timeframe: {args.timeframe}")
    print(f"Candles per symbol: {args.candles}")
    print(f"DATABASE_URL set: {'yes' if bool(config.DATABASE_URL) else 'no (local SQLite)'}")
    print(f"Skip ready: {not args.no_skip_ready} (min_ready={args.min_ready})")
    print("Market data endpoint: Binance mainnet public")
    print("========================\n")

    results: list[SymbolResult] = []
    started = time.monotonic()
    for idx, symbol in enumerate(symbols, start=1):
        print(f"[{idx:>4}/{len(symbols)}] {symbol} ...", flush=True)
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
