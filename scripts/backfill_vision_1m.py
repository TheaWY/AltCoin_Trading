#!/usr/bin/env python3
"""Six months of 1-minute USD-M perp klines for every symbol (delisted included)
from data.binance.vision, stored as one parquet file per symbol under
data/cache/k1m/ (too big for Postgres: ~150M rows).

Columns: ts (unix s), o h l c (float32), qv quote volume, n trades,
tbq taker-buy quote volume. Monthly zips for full months, daily zips after.
Safe to re-run: a symbol file is rebuilt only when missing or --force.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.backfill_delisted import DATA, all_archive_symbols  # noqa: E402

OUT = PROJECT_ROOT / "data" / "cache" / "k1m"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("k1m")


def _get(s: requests.Session, url: str) -> list[list[str]]:
    for _ in range(3):
        try:
            r = s.get(url, timeout=60)
            if r.status_code == 404:
                return []
            if r.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    text = z.read(z.namelist()[0]).decode()
                return [x for x in csv.reader(io.StringIO(text)) if x and x[0].isdigit()]
        except Exception:  # noqa: BLE001
            pass
    return []


def one(code: str, months: list[str], days: list[str], force: bool) -> tuple[str, int]:
    path = OUT / f"{code}.parquet"
    if path.exists() and not force:
        return code, -1
    s = requests.Session()
    recs: list[list[str]] = []
    for m in months:
        recs += _get(s, f"{DATA}/data/futures/um/monthly/klines/{code}/1m/{code}-1m-{m}.zip")
        if not recs and m == months[0]:
            continue
    if not recs and not _get(s, f"{DATA}/data/futures/um/daily/klines/{code}/1m/{code}-1m-{days[-1]}.zip"):
        # not in any full month of the window and not trading now: nothing to do
        pass
    for d in days:
        recs += _get(s, f"{DATA}/data/futures/um/daily/klines/{code}/1m/{code}-1m-{d}.zip")
    if not recs:
        return code, 0
    a = np.array([[r[0], r[1], r[2], r[3], r[4], r[7], r[8], r[10]] for r in recs], dtype=np.float64)
    df = pd.DataFrame({"ts": (a[:, 0] // 1000).astype("int64"), "o": a[:, 1].astype("float32"),
                       "h": a[:, 2].astype("float32"), "l": a[:, 3].astype("float32"), "c": a[:, 4].astype("float32"),
                       "qv": a[:, 5].astype("float32"), "n": a[:, 6].astype("int32"), "tbq": a[:, 7].astype("float32")})
    df = df.drop_duplicates("ts").sort_values("ts")
    df.to_parquet(path, index=False, compression="zstd")
    return code, len(df)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=185)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--symbols", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    start = today - timedelta(days=a.days)
    first_full = date(start.year, start.month, 1)
    cur_month = date(today.year, today.month, 1)
    months, m = [], first_full
    while m < cur_month:
        months.append(m.strftime("%Y-%m"))
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    days = [(cur_month + timedelta(days=i)).isoformat() for i in range((today - cur_month).days)]
    s = requests.Session()
    codes = a.symbols.split(",") if a.symbols else [c for c in all_archive_symbols(s) if c.endswith("USDT")]
    log.info("k1m: %d symbols, months %s, %d daily files", len(codes), months, len(days))
    done = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for code, n in ex.map(lambda c: one(c, months, days, a.force), codes):
            done += 1
            if n > 0 or done % 50 == 0:
                log.info("k1m %d/%d %s %s", done, len(codes), code, n)
    log.info("k1m backfill done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
