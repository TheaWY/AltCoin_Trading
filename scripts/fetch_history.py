#!/usr/bin/env python3
"""Download historical futures data from data.binance.vision into a SQLite DB.

Binance Vision is a public archive (works even where api.binance.com is
geo-blocked). Fetches monthly 1h klines + funding rates per symbol, plus
daily files for the current partial month, and loads them through the
normal Storage layer so backtests read data in exactly the production shape.

Usage:
    python scripts/fetch_history.py --months 12
    python scripts/fetch_history.py --months 6 --symbols BTC/USDT,ETH/USDT
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import Storage  # noqa: E402

BASE = "https://data.binance.vision/data/futures/um"
DEFAULT_DB = PROJECT_ROOT / "data" / "backtest.db"


def _to_seconds(raw: str) -> int:
    ts = int(raw)
    if ts > 10**14:  # microseconds (newer archives)
        return ts // 1_000_000
    if ts > 10**11:  # milliseconds
        return ts // 1_000
    return ts


def _download(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _csv_rows(payload: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        text = archive.read(name).decode()
    rows = list(csv.reader(io.StringIO(text)))
    if rows and not rows[0][0].isdigit():  # header row present in newer files
        rows = rows[1:]
    return rows


def _kline_rows(symbol: str, payload: bytes) -> list[dict]:
    return [
        {
            "symbol": symbol,
            "timestamp": _to_seconds(row[0]),
            "timeframe": "1h",
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in _csv_rows(payload)
    ]


def _funding_rows(symbol: str, payload: bytes) -> list[dict]:
    return [
        {
            "symbol": symbol,
            "timestamp": _to_seconds(row[0]),
            "funding_rate": float(row[-1]),
        }
        for row in _csv_rows(payload)
    ]


def _month_list(months: int) -> list[str]:
    """Last N complete months, oldest first (current month handled daily)."""
    first_of_current = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    result = []
    cursor = first_of_current
    for _ in range(months):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        result.append(f"{cursor:%Y-%m}")
    return sorted(result)


def _current_month_days() -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [
        f"{today.replace(day=day):%Y-%m-%d}"
        for day in range(1, today.day)  # up to yesterday (today is incomplete)
    ]


def fetch_symbol(storage: Storage, symbol: str, months: list[str], days: list[str]) -> dict:
    pair = symbol.replace("/", "")
    candles = funding = 0

    for month in months:
        payload = _download(f"{BASE}/monthly/klines/{pair}/1h/{pair}-1h-{month}.zip")
        if payload:
            candles += storage.insert_prices(_kline_rows(symbol, payload))
        payload = _download(
            f"{BASE}/monthly/fundingRate/{pair}/{pair}-fundingRate-{month}.zip"
        )
        if payload:
            funding += storage.insert_funding_rates(_funding_rows(symbol, payload))

    for day in days:
        payload = _download(f"{BASE}/daily/klines/{pair}/1h/{pair}-1h-{day}.zip")
        if payload:
            candles += storage.insert_prices(_kline_rows(symbol, payload))

    return {"symbol": symbol, "candles": candles, "funding": funding}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=12, help="complete months of history")
    parser.add_argument("--symbols", default=None, help="comma-separated, e.g. BTC/USDT,ETH/USDT")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="target SQLite file")
    args = parser.parse_args()

    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else list(config.TRADING_SYMBOLS)
    )
    months = _month_list(args.months)
    days = _current_month_days()
    storage = Storage(db_path=Path(args.db))

    print(f"Fetching {months[0]} .. {months[-1]} (+{len(days)} daily) for {len(symbols)} symbols")
    for symbol in symbols:
        stats = fetch_symbol(storage, symbol, months, days)
        print(f"  {symbol}: +{stats['candles']} candles, +{stats['funding']} funding rows")
    print(f"Done -> {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
