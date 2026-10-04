#!/usr/bin/env python3
"""Order book depth history from data.binance.vision (futures/um bookDepth,
free, every symbol, snapshot every ~30 s since 2023): cumulative bid/ask
notional within 0.2, 1, 2, 3, 4, 5% of the price.

Kept as 5-minute features per symbol (mean of the snapshots in the bar):
bid/ask notional at 0.2/1/2/5%, imbalance at 1/2/5% -> data/cache/bookdepth/<CODE>.parquet
Zips are read in memory; nothing raw is written. Resumable per symbol.
"""

from __future__ import annotations

import argparse
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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache" / "bookdepth"
URL = "https://data.binance.vision/data/futures/um/daily/bookDepth/{c}/{c}-bookDepth-{d}.zip"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bookdepth")
BANDS = (0.2, 1.0, 2.0, 5.0)


def day_frame(s: requests.Session, code: str, d: str) -> pd.DataFrame | None:
    for _ in range(3):
        try:
            r = s.get(URL.format(c=code, d=d), timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]))
            break
        except Exception:  # noqa: BLE001
            df = None
    if df is None or df.empty:
        return None
    df["ts"] = (pd.to_datetime(df["timestamp"]) - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
    df["bar"] = df["ts"] // 300 * 300
    df = df[df["percentage"].abs().isin(BANDS)]
    w = df.pivot_table(index="bar", columns="percentage", values="notional", aggfunc="mean")
    out = pd.DataFrame(index=w.index)
    for b in BANDS:
        lab = str(b).replace(".0", "").replace("0.", "0")
        bid, ask = w.get(-b), w.get(b)
        if bid is None or ask is None:
            continue
        out[f"bid_{lab}"], out[f"ask_{lab}"] = bid, ask
        if b >= 1:
            out[f"imb_{lab}"] = (bid - ask) / (bid + ask)
    return out.reset_index().rename(columns={"bar": "ts"})


def one(code: str, days: list[str]) -> tuple[str, int]:
    path = OUT / f"{code}.parquet"
    if path.exists():
        return code, -1
    s = requests.Session()
    parts = [f for f in (day_frame(s, code, d) for d in days) if f is not None]
    if not parts:
        return code, 0
    df = pd.concat(parts).sort_values("ts").drop_duplicates("ts")
    df.astype({c: "float32" for c in df.columns if c != "ts"}).to_parquet(path, index=False, compression="zstd")
    return code, len(df)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=185)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(a.days, 0, -1)]
    codes = sorted(p.stem for p in (ROOT / "data" / "cache" / "k1m").glob("*.parquet"))
    log.info("bookDepth: %d symbols x %d days", len(codes), len(days))
    with ThreadPoolExecutor(a.workers) as ex:
        for i, (code, n) in enumerate(ex.map(lambda c: one(c, days), codes), 1):
            if n > 0 or i % 50 == 0:
                log.info("bookDepth %d/%d %s %s", i, len(codes), code, n)
    log.info("bookDepth backfill done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
