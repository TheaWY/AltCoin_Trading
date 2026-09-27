"""Binance vision 5m futures metrics -> hourly parquet per symbol (disk-light alternative to metrics_5m for long history).
data/cache/metrics1h/{SYM}.parquet columns: ts (hour close), oi, oi_usd, ls_top_acct, ls_top_pos, ls_global, taker_ratio
(last 5m value in the hour, taker_ratio = mean of the hour). Resumable per (symbol, day).
  .venv/bin/python scripts/backfill_metrics1h.py --start 2024-03-01 --end 2026-09-24 --workers 24"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/cache/metrics1h"
BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32))


def day(sym, d):
    try:
        r = S.get(f"{BASE}/{sym}/{sym}-metrics-{d}.zip", timeout=30)
        if r.status_code != 200:
            return None
        z = zipfile.ZipFile(io.BytesIO(r.content))
        df = pd.read_csv(z.open(z.namelist()[0]))
        if df.empty:
            return None
        t = pd.to_datetime(df["create_time"]).astype("int64") // 10**9
        out = pd.DataFrame({"t": t, "oi": df["sum_open_interest"], "oi_usd": df["sum_open_interest_value"],
                            "ls_top_acct": df["count_toptrader_long_short_ratio"],
                            "ls_top_pos": df["sum_toptrader_long_short_ratio"],
                            "ls_global": df["count_long_short_ratio"], "taker_ratio": df["sum_taker_long_short_vol_ratio"]})
        out["ts"] = (out["t"] // 3600 + 1) * 3600          # 5m stamp -> hour close containing it
        g = out.sort_values("t").groupby("ts")
        h = g[["oi", "oi_usd", "ls_top_acct", "ls_top_pos", "ls_global"]].last()
        h["taker_ratio"] = g["taker_ratio"].mean()
        return h.reset_index()
    except Exception:
        return None


def symbol(args):
    sym, days = args
    path = OUT / f"{sym}.parquet"
    have = set()
    old = None
    if path.exists():
        old = pd.read_parquet(path)
        have = set((old["ts"] - 1) // 86400)
    todo = [d for d in days if (pd.Timestamp(d).timestamp() // 86400) not in have]
    parts = [p for p in (day(sym, d) for d in todo) if p is not None]
    if not parts:
        return sym, 0
    new = pd.concat(parts + ([old] if old is not None else []), ignore_index=True).drop_duplicates("ts").sort_values("ts")
    new.to_parquet(path, index=False)
    return sym, len(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-03-01")
    ap.add_argument("--end", default="2026-09-24")
    ap.add_argument("--workers", type=int, default=24)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    codes = json.load(open(ROOT / "data/cache/b7/codes.json"))
    d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
    days = [str(d0 + timedelta(k)) for k in range((d1 - d0).days + 1)]
    # restrict each symbol's days to its own trading life (from the b7 panel) to avoid 404 storms
    import numpy as np
    ts = np.load(ROOT / "data/cache/b7/ts.npy")
    c = np.load(ROOT / "data/cache/b7/c.npy", mmap_mode="r")
    jobs = []
    for j, sym in enumerate(codes):
        live = np.flatnonzero(np.isfinite(c[:, j]))
        if len(live) == 0:
            continue
        a0 = pd.Timestamp(int(ts[live[0]]), unit="s").date()
        a1 = pd.Timestamp(int(ts[live[-1]]), unit="s").date()
        jobs.append((sym, [d for d in days if a0 <= date.fromisoformat(d) <= a1]))
    done = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for sym, n in ex.map(symbol, jobs):
            done += 1
            if done % 25 == 0:
                print("symbols", done, len(jobs), sym, n, flush=True)
    print("done", done)


if __name__ == "__main__":
    main()
