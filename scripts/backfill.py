#!/usr/bin/env python3
"""Backfill OHLCV history for every configured symbol and timeframe."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.collectors.binance import BinanceCollector, _build_exchange  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from src.symbols import ccxt_symbol, trading_symbols  # noqa: E402


BACKFILL_LIMITS = {
    "1d": 500,
    "1h": 1000,
    "15m": 500,
}


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("backfill")
    storage = get_storage()
    exchange = _build_exchange()
    symbols = trading_symbols()
    timeframes = config.OHLCV_TIMEFRAMES

    for symbol in symbols:
        collector = BinanceCollector(
            storage=storage,
            exchange=exchange,
            symbol=symbol,
            futures_symbol=ccxt_symbol(symbol),
        )
        for timeframe in timeframes:
            limit = BACKFILL_LIMITS.get(timeframe, config.OHLCV_LIMIT)
            try:
                rows = collector.collect_ohlcv(timeframe=timeframe, limit=limit)
                logger.info(
                    "Backfilled %s %s: %d candles",
                    symbol,
                    timeframe,
                    len(rows),
                )
            except Exception:
                logger.exception("Backfill failed for %s %s", symbol, timeframe)
            time.sleep(0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
