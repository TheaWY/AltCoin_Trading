#!/usr/bin/env python3
"""Backfill Binance USDT-M funding-rate history for active symbols.

Resumable by design: Storage.insert_funding_rates uses INSERT OR IGNORE /
ON CONFLICT semantics, so re-running this script is safe.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.collectors.binance import _build_exchange  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from src.symbols import ccxt_symbol, trading_symbols  # noqa: E402

DEFAULT_START = "2024-07-01"
PAGE_LIMIT = 1000
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000


def _parse_utc_date(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def _active_symbols(limit: int) -> list[str]:
    symbols = trading_symbols()
    if limit > 0:
        symbols = symbols[:limit]
    for core in ("BTC/USDT", "ETH/USDT"):
        if core not in symbols:
            symbols.insert(0, core)
    return list(dict.fromkeys(symbols))


def _row(symbol: str, item: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = item.get("timestamp") or item.get("fundingTimestamp")
    rate = item.get("fundingRate") if item.get("fundingRate") is not None else item.get("rate")
    if timestamp is None or rate is None:
        return None
    return {
        "symbol": symbol,
        "timestamp": int(int(timestamp) / 1000),
        "funding_rate": float(rate),
    }


def backfill_symbol(exchange: Any, storage: Any, symbol: str, start_ts: int, end_ts: int) -> dict[str, int]:
    market = ccxt_symbol(symbol)
    since_ms = start_ts * 1000
    end_ms = end_ts * 1000
    fetched = 0
    inserted = 0
    pages = 0

    while since_ms <= end_ms:
        history = exchange.fetch_funding_rate_history(
            market,
            since=since_ms,
            limit=PAGE_LIMIT,
            params={"endTime": end_ms},
        )
        pages += 1
        if not history:
            break

        rows = []
        for item in history:
            row = _row(symbol, item)
            if row and start_ts <= row["timestamp"] <= end_ts:
                rows.append(row)
        if rows:
            inserted += storage.insert_funding_rates(rows)
            fetched += len(rows)

        last_ms = int(history[-1].get("timestamp") or history[-1].get("fundingTimestamp") or since_ms)
        next_since = last_ms + FUNDING_INTERVAL_MS
        if next_since <= since_ms or last_ms >= end_ms:
            break
        since_ms = next_since
        time.sleep(0.05)

    return {"fetched": fetched, "inserted": inserted, "pages": pages}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill funding-rate history for active symbols.")
    parser.add_argument("--start", default=DEFAULT_START, help="UTC start date YYYY-MM-DD")
    parser.add_argument("--symbols-top", type=int, default=config.ACTIVE_TRADING_SYMBOLS_LIMIT)
    parser.add_argument("--only", default="", help="Comma-separated symbols instead of active universe")
    args = parser.parse_args()

    start_ts = _parse_utc_date(args.start)
    end_ts = int(time.time())
    symbols = (
        [s.strip() for s in args.only.split(",") if s.strip()]
        if args.only
        else _active_symbols(args.symbols_top)
    )
    storage = get_storage()
    exchange = _build_exchange(use_testnet=False, authenticated=False)

    print("\n=== Funding backfill ===", flush=True)
    print(f"Symbols: {len(symbols)}", flush=True)
    print(f"Start: {datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d')}", flush=True)
    print(f"End: {datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}", flush=True)
    print(f"DATABASE_URL set: {'yes' if bool(config.DATABASE_URL) else 'no (local SQLite)'}", flush=True)
    print("========================\n", flush=True)

    summary: dict[str, Any] = {"symbols": {}, "failed": {}}
    for idx, symbol in enumerate(symbols, start=1):
        print(f"[{idx:>3}/{len(symbols)}] {symbol} ...", flush=True)
        try:
            stats = backfill_symbol(exchange, storage, symbol, start_ts, end_ts)
            summary["symbols"][symbol] = stats
            print(
                f"      OK fetched={stats['fetched']} inserted={stats['inserted']} pages={stats['pages']}",
                flush=True,
            )
        except Exception as exc:
            summary["failed"][symbol] = str(exc)[:300]
            print(f"      FAIL {exc}", flush=True)
    print("\n=== Funding summary ===", flush=True)
    print(summary, flush=True)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
