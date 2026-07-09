#!/usr/bin/env python3
"""Bootstrap 1h candle history for every active Binance USDT perpetual.

This is meant to be run on the Mac mini worker, not Railway:

    source .venv/bin/activate
    python scripts/bootstrap_all_candles.py --limit 0 --candles 720

It discovers the current Binance USDT perpetual universe, downloads historical
candles for each symbol, stores them through the normal Storage layer, and
prints a success/failure summary. It uses real public market-data endpoints by
default and does not require Binance API keys.
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
from src.data.collectors.binance import BinanceCollector, _build_exchange  # noqa: E402
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
    error: str | None = None


def _set_runtime_env(limit: int, timeframe: str, candles: int) -> None:
    """Set env defaults before importing any fresh process config next time.

    This script also directly passes the limit/timeframe to the collector, but
    env mutation makes the printed config and downstream symbol discovery match
    user intent in this process.
    """
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


def _collect_one(storage: Any, exchange: Any, symbol: str, timeframe: str, candles: int) -> SymbolResult:
    try:
        collector = BinanceCollector(
            storage=storage,
            exchange=exchange,
            symbol=symbol,
            futures_symbol=ccxt_symbol(symbol),
            timeframe=timeframe,
        )
        rows = collector.collect_ohlcv(timeframe=timeframe, limit=candles)
        stored_rows = storage.get_prices(symbol, limit=candles + 5, timeframe=timeframe)
        latest = stored_rows[-1]["close"] if stored_rows else None
        return SymbolResult(
            symbol=symbol,
            ok=True,
            fetched=len(rows),
            stored=len(stored_rows),
            latest_close=latest,
        )
    except Exception as exc:  # keep going; one bad/delisted symbol should not stop bootstrap
        logger.exception("Bootstrap failed for %s", symbol)
        return SymbolResult(symbol=symbol, ok=False, error=str(exc)[:300])


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="0 = all active USDT perps; N = top N by 24h volume")
    parser.add_argument("--candles", type=int, default=720, help="Number of candles to fetch per symbol")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe, default 1h")
    parser.add_argument("--sleep", type=float, default=0.08, help="Delay between symbols to be gentle on Binance/Railway DB")
    parser.add_argument("--only", default="", help="Comma-separated symbols to bootstrap instead of discovery, e.g. BTC/USDT,ETH/USDT")
    parser.add_argument("--failures-file", default="data/bootstrap_failures.txt", help="Where to write failed symbols")
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
    print("Market data endpoint: Binance mainnet public")
    print("========================\n")

    results: list[SymbolResult] = []
    started = time.monotonic()
    for idx, symbol in enumerate(symbols, start=1):
        print(f"[{idx:>4}/{len(symbols)}] {symbol} ...", flush=True)
        result = _collect_one(storage, exchange, symbol, args.timeframe, args.candles)
        results.append(result)
        if result.ok:
            print(f"      OK fetched={result.fetched} stored={result.stored} latest={result.latest_close}")
        else:
            print(f"      FAIL {result.error}")
        if args.sleep > 0:
            time.sleep(args.sleep)

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
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
