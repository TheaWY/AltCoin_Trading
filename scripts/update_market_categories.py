#!/usr/bin/env python3
"""Update dynamic market categories on the Mac mini worker.

Run once:
    python scripts/update_market_categories.py --once --limit 0

Run as a 30-minute loop:
    python scripts/update_market_categories.py --interval-minutes 30 --limit 0

The script writes to Railway Postgres when DATABASE_URL is set. It does not use
Cursor and does not place load on Railway's web process; the Mac mini becomes the
research/data hub.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from src.research.market_categories import update_categories, summary  # noqa: E402
from src.symbols import trading_symbols  # noqa: E402


def _set_runtime_env(limit: int) -> None:
    os.environ.setdefault("LIVE_TRADING", "false")
    os.environ["SYMBOL_UNIVERSE"] = "auto"
    os.environ["TRADING_SYMBOLS_LIMIT"] = str(limit)
    config.SYMBOL_UNIVERSE = "auto"
    config.TRADING_SYMBOLS_LIMIT = limit


def _run_once(limit: int) -> dict:
    storage = get_storage()
    symbols = trading_symbols()
    if limit > 0:
        symbols = symbols[:limit]
    result = update_categories(storage, symbols=symbols)
    cat_summary = summary(storage)
    print("\n=== Market categories updated ===")
    print(f"DATABASE_URL set: {'yes' if bool(config.DATABASE_URL) else 'no (local SQLite)'}")
    print(f"Symbols categorized: {result['snapshots']}")
    print(f"Transitions recorded: {result['transitions']}")
    print(f"Trade-allowed symbols: {cat_summary['trade_allowed']} / {cat_summary['symbols']}")
    print("Counts:")
    for category, count in cat_summary["counts"].items():
        print(f"- {category}: {count}")
    print("=================================\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="0 = all crypto perps; N = top N")
    parser.add_argument("--interval-minutes", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")
    _set_runtime_env(args.limit)

    while True:
        _run_once(args.limit)
        if args.once:
            return 0
        time.sleep(max(60.0, args.interval_minutes * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())
