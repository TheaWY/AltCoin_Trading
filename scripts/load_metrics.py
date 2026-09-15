"""Backfill Binance futures METRICS (positioning + flow) from data.binance.vision.

A genuinely different signal class from price/funding/basis: open interest,
long/short account ratios (retail AND top-trader), and taker buy/sell flow.
Daily per-symbol files at 5-min granularity -> aggregated to hourly (last sample
per hour) into the futures_metrics table. Reachable from Korea (static CDN).

    python scripts/load_metrics.py --symbols BTC/USDT,ETH/USDT --start 2023-01
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import sys
import time
import urllib.request as U
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

VBASE = "https://data.binance.vision/data/futures/um/daily/metrics"
HOUR = 3600


def _ddl(pg: bool) -> str:
    t = "DOUBLE PRECISION" if pg else "REAL"
    return (f"CREATE TABLE IF NOT EXISTS futures_metrics ("
            f"symbol TEXT, timestamp BIGINT, oi_value {t}, toptrader_lsr {t}, "
            f"retail_lsr {t}, taker_lsr {t}, PRIMARY KEY(symbol, timestamp))"
            if pg else
            "CREATE TABLE IF NOT EXISTS futures_metrics ("
            "symbol TEXT, timestamp INTEGER, oi_value REAL, toptrader_lsr REAL, "
            "retail_lsr REAL, taker_lsr REAL, PRIMARY KEY(symbol, timestamp))")


def _days(start: dt.datetime, end: dt.datetime):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def _fetch_day(code: str, day: dt.datetime) -> dict[int, tuple]:
    url = f"{VBASE}/{code}/{code}-metrics-{day:%Y-%m-%d}.zip"
    out: dict[int, tuple] = {}
    try:
        with U.urlopen(url, timeout=40) as r:
            payload = r.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for nm in zf.namelist():
                if not nm.endswith(".csv"):
                    continue
                for row in csv.DictReader(io.TextIOWrapper(zf.open(nm), "utf-8")):
                    try:
                        ts = int(dt.datetime.strptime(row["create_time"], "%Y-%m-%d %H:%M:%S")
                                 .replace(tzinfo=dt.timezone.utc).timestamp())
                        bucket = (ts // HOUR) * HOUR
                        out[bucket] = (  # last 5-min sample in the hour wins
                            float(row["sum_open_interest_value"] or 0),
                            float(row["sum_toptrader_long_short_ratio"] or 0),
                            float(row["count_long_short_ratio"] or 0),
                            float(row["sum_taker_long_short_vol_ratio"] or 0),
                        )
                    except Exception:
                        continue
    except Exception:
        pass
    return out


def load_symbol(storage, symbol: str, start: dt.datetime, end: dt.datetime) -> int:
    code = symbol.replace("/", "")
    ph = "%s" if storage.is_postgres else "?"
    total = 0
    batch = []
    for day in _days(start, end):
        for bucket, (oi, tt, rt, tk) in _fetch_day(code, day).items():
            batch.append((symbol, bucket, oi, tt, rt, tk))
        if len(batch) >= 2000:
            total += _flush(storage, ph, batch); batch = []
    total += _flush(storage, ph, batch)
    return total


def _flush(storage, ph, batch) -> int:
    if not batch:
        return 0
    conflict = ("ON CONFLICT (symbol,timestamp) DO NOTHING" if storage.is_postgres
                else "ON CONFLICT(symbol,timestamp) DO NOTHING")
    with storage._connect() as c:
        c.executemany(
            f"INSERT INTO futures_metrics (symbol,timestamp,oi_value,toptrader_lsr,retail_lsr,taker_lsr) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph}) " + conflict, batch)
    return len(batch)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--start", default="2023-01")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()
    from src.data.storage import get_storage
    storage = get_storage()
    with storage._connect() as c:
        c.execute(_ddl(storage.is_postgres))
    start = dt.datetime.strptime(args.start, "%Y-%m").replace(tzinfo=dt.timezone.utc)
    end = (dt.datetime.strptime(args.end, "%Y-%m").replace(tzinfo=dt.timezone.utc)
           if args.end else dt.datetime.now(dt.timezone.utc))
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    for s in syms:
        n = load_symbol(storage, s, start, end)
        print(f"[metrics] {s}: {n} hourly rows  ({int(time.time())})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
