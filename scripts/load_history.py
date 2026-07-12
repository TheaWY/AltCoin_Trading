#!/usr/bin/env python3
"""Download historical 1h klines + funding from data.binance.vision.

Resumable monthly loader for top USDT perp symbols (2020-01 → now).
Uses INSERT OR IGNORE via Storage helpers — safe to re-run.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import Storage, get_storage  # noqa: E402

VISION_BASE = "https://data.binance.vision/data"
DEFAULT_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "MATIC/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "ATOM/USDT",
    "UNI/USDT",
    "ETC/USDT",
    "FIL/USDT",
    "APT/USDT",
    "ARB/USDT",
    "OP/USDT",
]
CHUNK = 2000


def _timestamp_seconds(value: str | int | float) -> int:
    ts = int(float(value))
    while ts > 20_000_000_000:
        ts //= 1000
    return ts


def _spot_code(symbol: str) -> str:
    return symbol.replace("/", "")


def _iter_months(start: datetime, end: datetime) -> Iterator[tuple[int, int]]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        month += 1
        if month > 12:
            month = 1
            year += 1


def _download_zip(url: str) -> bytes | None:
    try:
        with urlopen(url, timeout=120) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except URLError:
        return None


def _kline_url(symbol: str, year: int, month: int) -> str:
    code = _spot_code(symbol)
    ym = f"{year}-{month:02d}"
    return f"{VISION_BASE}/spot/monthly/klines/{code}/1h/{code}-1h-{ym}.zip"


def _funding_url(symbol: str, year: int, month: int) -> str:
    code = _spot_code(symbol)
    ym = f"{year}-{month:02d}"
    return f"{VISION_BASE}/futures/um/monthly/fundingRate/{code}/{code}-fundingRate-{ym}.zip"


def _parse_klines(symbol: str, payload: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            with zf.open(name) as handle:
                reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8"))
                for line in reader:
                    if not line or not line[0].isdigit():
                        continue
                    ts = _timestamp_seconds(line[0])
                    rows.append(
                        {
                            "symbol": symbol,
                            "timestamp": ts,
                            "open": float(line[1]),
                            "high": float(line[2]),
                            "low": float(line[3]),
                            "close": float(line[4]),
                            "volume": float(line[5]),
                        }
                    )
    return rows


def _parse_funding(symbol: str, payload: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            with zf.open(name) as handle:
                reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8"))
                for line in reader:
                    if not line or not line[0].isdigit():
                        continue
                    ts = _timestamp_seconds(line[0])
                    rate = float(line[2] if len(line) > 2 else line[1])
                    rows.append(
                        {"symbol": symbol, "timestamp": ts, "funding_rate": rate}
                    )
    return rows


def _insert_chunked(storage: Storage, kind: str, rows: list[dict[str, Any]]) -> int:
    total = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        if kind == "prices":
            total += storage.insert_prices(chunk, timeframe="1h")
        else:
            total += storage.insert_funding_rates(chunk)
    return total


def load_symbol(
    storage: Storage,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    klines: bool = True,
    funding: bool = True,
) -> dict[str, int]:
    stats = {"klines_inserted": 0, "funding_inserted": 0, "months_skipped": 0}
    for year, month in _iter_months(start, end):
        if klines:
            url = _kline_url(symbol, year, month)
            payload = _download_zip(url)
            if payload is None:
                stats["months_skipped"] += 1
            else:
                rows = _parse_klines(symbol, payload)
                stats["klines_inserted"] += _insert_chunked(storage, "prices", rows)
                print(f"[klines] {symbol} {year}-{month:02d}: {len(rows)} rows")
        if funding:
            url = _funding_url(symbol, year, month)
            payload = _download_zip(url)
            if payload is None:
                continue
            rows = _parse_funding(symbol, payload)
            stats["funding_inserted"] += _insert_chunked(storage, "funding", rows)
            print(f"[funding] {symbol} {year}-{month:02d}: {len(rows)} rows")
        time.sleep(0.15)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Load Binance Vision history into storage.")
    parser.add_argument("--start", default="2020-01", help="Start month YYYY-MM")
    parser.add_argument("--end", default=None, help="End month YYYY-MM (default: now)")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated spot symbols",
    )
    parser.add_argument("--no-klines", action="store_true")
    parser.add_argument("--no-funding", action="store_true")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m").replace(tzinfo=timezone.utc)
    end = (
        datetime.strptime(args.end, "%Y-%m").replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    storage = get_storage()
    summary: dict[str, Any] = {"symbols": {}, "started_at": int(time.time())}
    for symbol in symbols:
        print(f"=== {symbol} ===")
        summary["symbols"][symbol] = load_symbol(
            storage,
            symbol,
            start,
            end,
            klines=not args.no_klines,
            funding=not args.no_funding,
        )
    summary["finished_at"] = int(time.time())
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
