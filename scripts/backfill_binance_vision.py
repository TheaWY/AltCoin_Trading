#!/usr/bin/env python3
"""Backfill 5-minute derivatives metrics for every USDT perp from Binance's
public data archive (data.binance.vision), which keeps full history -- the
REST /futures/data endpoints only keep 30 days.

Daily file per symbol: data/futures/um/daily/metrics/{SYM}/{SYM}-metrics-{date}.zip
columns: create_time, symbol, sum_open_interest, sum_open_interest_value,
count_toptrader_long_short_ratio (top-trader ACCOUNT ratio),
sum_toptrader_long_short_ratio (top-trader POSITION ratio),
count_long_short_ratio (all-accounts ratio), sum_taker_long_short_vol_ratio.

Stored in metrics_5m (never pruned). Resumable: skips (symbol, day) already
loaded.

    .venv/bin/python scripts/backfill_binance_vision.py --days 185 [--workers 16]
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.collectors import klines_1m as k1  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
SCHEMA = """CREATE TABLE IF NOT EXISTS metrics_5m (
    symbol TEXT NOT NULL, ts BIGINT NOT NULL, oi DOUBLE PRECISION, oi_usd DOUBLE PRECISION,
    ls_top_acct DOUBLE PRECISION, ls_top_pos DOUBLE PRECISION, ls_global DOUBLE PRECISION,
    taker_ratio DOUBLE PRECISION, PRIMARY KEY (symbol, ts))"""
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("binance_vision")


def _f(x: str):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch(session: requests.Session, sym: str, day: str) -> list[tuple]:
    code = sym.replace("/", "")
    r = session.get(f"{BASE}/{code}/{code}-metrics-{day}.zip", timeout=30)
    if r.status_code != 200:
        return []
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        text = z.read(z.namelist()[0]).decode()
    rows = []
    for d in csv.DictReader(io.StringIO(text)):
        try:
            ts = int(datetime.strptime(d["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        except (KeyError, ValueError):
            continue
        rows.append((sym, ts, _f(d.get("sum_open_interest")), _f(d.get("sum_open_interest_value")),
                     _f(d.get("count_toptrader_long_short_ratio")), _f(d.get("sum_toptrader_long_short_ratio")),
                     _f(d.get("count_long_short_ratio")), _f(d.get("sum_taker_long_short_vol_ratio"))))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=185)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    storage = get_storage()
    with storage._connect() as c:  # noqa: SLF001
        c.execute(SCHEMA)
        done = {(dict(r)["symbol"], int(dict(r)["d"])) for r in c.execute(
            "SELECT symbol, ts / 86400 AS d FROM metrics_5m GROUP BY 1, 2 HAVING COUNT(*) >= 280").fetchall()}
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=i) for i in range(1, args.days + 1)]   # yesterday backwards (archive lags ~1 day)
    syms = k1.usdt_perps()
    jobs = [(s, d.isoformat()) for s in syms for d in days
            if (s, int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) // 86400) not in done]
    log.info("%d symbols x %d days: %d files to fetch (%d already loaded)", len(syms), len(days), len(jobs), len(done))
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=args.workers, pool_maxsize=args.workers)
    session.mount("https://", adapter)
    t0, total, files = time.time(), 0, 0
    sql = ("INSERT INTO metrics_5m (symbol, ts, oi, oi_usd, ls_top_acct, ls_top_pos, ls_global, taker_ratio) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (symbol, ts) DO NOTHING")
    buf: list[tuple] = []
    with ThreadPoolExecutor(args.workers) as ex:
        for rows in ex.map(lambda j: _safe(session, *j), jobs):
            files += 1
            buf += rows
            if len(buf) >= 20_000 or files == len(jobs):
                with storage._connect() as c:  # noqa: SLF001
                    with c.raw.cursor() as cur:
                        cur.executemany(sql, buf)
                total += len(buf)
                buf = []
            if files % 2000 == 0:
                log.info("%d/%d files, %d rows, %.0f files/s", files, len(jobs), total, files / (time.time() - t0))
    if buf:
        with storage._connect() as c:  # noqa: SLF001
            with c.raw.cursor() as cur:
                cur.executemany(sql, buf)
        total += len(buf)
    log.info("backfill done: %d files, %d rows in %.0fs", files, total, time.time() - t0)
    return 0


def _safe(session, sym, day):
    for attempt in range(3):
        try:
            return fetch(session, sym, day)
        except Exception:  # noqa: BLE001
            time.sleep(1 + attempt)
    return []


if __name__ == "__main__":
    raise SystemExit(main())
