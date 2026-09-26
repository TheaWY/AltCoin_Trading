#!/usr/bin/env python3
"""Funding-rate history (every 8h / 4h / 1h settlement) for every USD-M perp
from data.binance.vision monthly archives, plus the current month from the
REST API. Parquet per symbol: data/cache/funding/<CODE>.parquet (ts, f)."""

from __future__ import annotations

import csv
import io
import logging
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
OUT = PROJECT_ROOT / "data" / "cache" / "funding"
DATA = "https://data.binance.vision"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("funding")


def one(code: str, months: list[str]) -> int:
    s = requests.Session()
    rows = []
    for m in months:
        r = s.get(f"{DATA}/data/futures/um/monthly/fundingRate/{code}/{code}-fundingRate-{m}.zip", timeout=30)
        if r.status_code != 200:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for rec in csv.reader(io.StringIO(z.read(z.namelist()[0]).decode())):
                if rec and rec[0].isdigit():
                    rows.append((int(rec[0]) // 1000, float(rec[2])))
    r = s.get("https://fapi.binance.com/fapi/v1/fundingRate", params={"symbol": code, "limit": 1000}, timeout=30)
    if r.status_code == 200:
        rows += [(int(x["fundingTime"]) // 1000, float(x["fundingRate"])) for x in r.json()]
    if not rows:
        return 0
    df = pd.DataFrame(rows, columns=["ts", "f"]).drop_duplicates("ts").sort_values("ts")
    df.to_parquet(OUT / f"{code}.parquet", index=False)
    return len(df)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(8):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append(f"{y}-{m:02d}")
    codes = sorted(p.stem for p in (PROJECT_ROOT / "data" / "cache" / "k1m").glob("*.parquet"))
    with ThreadPoolExecutor(8) as ex:
        for i, n in enumerate(ex.map(lambda c: one(c, months), codes)):
            if i % 100 == 0:
                log.info("funding %d/%d %s", i + 1, len(codes), n)
    log.info("funding backfill done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
