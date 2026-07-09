#!/usr/bin/env python3
"""Refresh non-candle market metrics for the dashboard.

RSI/ATR/7d/BTC-correlation are computed from stored candles at dashboard time.
This script fills the extra tables that are not derivable from OHLCV:

- funding_rates: latest Binance futures funding rate for each tracked symbol
- market_metrics: open interest + long/short ratio for a limited liquid subset

Run on the Mac mini worker with DATABASE_URL pointing to Railway Postgres:

    source .venv/bin/activate
    python scripts/refresh_all_metrics.py --limit 0 --market-metrics-limit 100
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.collectors.binance import (  # noqa: E402
    _build_exchange,
    _collect_funding_batch,
    _collect_market_metrics,
)
from src.data.storage import get_storage  # noqa: E402
from src.symbols import trading_symbols  # noqa: E402


def _set_runtime_env(limit: int) -> None:
    os.environ.setdefault("LIVE_TRADING", "false")
    os.environ.setdefault("BINANCE_MARKET_DATA_TESTNET", "false")
    os.environ["SYMBOL_UNIVERSE"] = "auto"
    os.environ["TRADING_SYMBOLS_LIMIT"] = str(limit)
    config.SYMBOL_UNIVERSE = "auto"
    config.TRADING_SYMBOLS_LIMIT = limit
    config.BINANCE_MARKET_DATA_TESTNET = False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="0 = all tracked crypto perps; N = top N")
    parser.add_argument("--market-metrics-limit", type=int, default=100, help="OI/long-short are per-symbol endpoints; limit these to top N")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _set_runtime_env(args.limit)

    storage = get_storage()
    exchange = _build_exchange(use_testnet=False, authenticated=False)
    symbols = trading_symbols()
    if args.limit > 0:
        symbols = symbols[: args.limit]

    print("\n=== Metrics refresh ===")
    print(f"Symbols: {len(symbols)}")
    print(f"DATABASE_URL set: {'yes' if bool(config.DATABASE_URL) else 'no (local SQLite)'}")
    print("RSI/ATR are computed from candles; they are not fetched/stored separately.")
    print("=======================\n")

    funding = _collect_funding_batch(exchange, storage, symbols)
    print(f"Funding rows refreshed: {len(funding)} / {len(symbols)}")

    metric_symbols = symbols[: max(0, args.market_metrics_limit)]
    inserted_metrics = _collect_market_metrics(exchange, storage, metric_symbols)
    print(f"OI/long-short metric rows inserted: {inserted_metrics} for top {len(metric_symbols)} symbols")

    ready = 0
    missing = []
    for symbol in symbols:
        rows = storage.get_prices(symbol, limit=48, timeframe="1h")
        if len(rows) >= 48:
            ready += 1
        else:
            missing.append((symbol, len(rows)))
    print(f"Candle-ready for RSI/ATR >=48: {ready} / {len(symbols)}")
    if missing:
        print("First 30 missing candle history:")
        for symbol, count in missing[:30]:
            print(f"- {symbol}: {count}/48 candles")
    print("\nRefresh complete. Hard-refresh /dashboard after Railway redeploy/cache expires.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
