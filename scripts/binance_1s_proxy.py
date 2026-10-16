"""Binance-spot 1-second proxy paths for OLD Upbit pump events (older than Upbit's ~3-month 1s window).

For each event in data/upbit_db/events.parquet with no Upbit 1s file, if the coin trades on Binance spot as <BASE>USDT,
the daily aggTrades archive(s) from data.binance.vision (public, no key) are streamed into memory, cut to the window
T-1h .. T+4h+120s and aggregated to 1-second bars. Nothing raw is written to disk.
Output: data/upbit_db/bn1s/<MKT>_<T>.parquet  columns ts, o, h, l, c, v_usd, buy_usd
This is a PROXY: Binance price, not the Upbit fill price (Korean pumps can trade at a premium on Upbit).
Resumable (existing outputs skipped). Usage: python scripts/binance_1s_proxy.py [max_events]
"""
from __future__ import annotations

import io
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/upbit_db"
OUT = DB / "bn1s"; OUT.mkdir(parents=True, exist_ok=True)
URL = "https://data.binance.vision/data/spot/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip"
S = requests.Session()


def spot_symbols():
    j = S.get("https://api.binance.com/api/v3/exchangeInfo", timeout=30).json()
    return {x["symbol"] for x in j["symbols"] if x["quoteAsset"] == "USDT"}


def day_trades(sym, day):
    r = S.get(URL.format(s=sym, d=day), timeout=120)
    if r.status_code != 200:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    with z.open(z.namelist()[0]) as f:
        d = pd.read_csv(f, header=None, usecols=[1, 2, 5, 6], names=["p", "q", "t", "m"])
    if len(d) and not str(d.iloc[0, 0]).replace(".", "").isdigit():      # header row in some files
        d = d.iloc[1:].astype({"p": float, "q": float, "t": "int64"})
    t = d["t"].astype("int64")
    d["ts"] = np.where(t > 10**14, t // 10**6, t // 10**3)              # microseconds since 2025, ms before
    d["m"] = d["m"].astype(str).str.lower().eq("true")
    return d[["ts", "p", "q", "m"]]


def bars(tr, t0, t1):
    x = tr[(tr.ts >= t0) & (tr.ts < t1)].copy()
    if not len(x):
        return pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v_usd", "buy_usd"])
    x["usd"] = x.p * x.q
    x["buy"] = np.where(~x.m, x.usd, 0.0)                              # buyer is taker when is_buyer_maker is false
    g = x.groupby("ts")
    return pd.DataFrame({"o": g.p.first(), "h": g.p.max(), "l": g.p.min(), "c": g.p.last(),
                         "v_usd": g.usd.sum(), "buy_usd": g.buy.sum()}).reset_index()


def main(limit=None):
    e = pd.read_parquet(DB / "events.parquet")
    have_up = {f.stem for f in (DB / "s1").glob("*.parquet")}
    syms = spot_symbols()
    e["sym"] = e.market.str.replace("KRW-", "", regex=False) + "USDT"
    e["key"] = e.market + "_" + e["T"].astype(int).astype(str)
    todo = e[e.sym.isin(syms) & ~e.key.isin(have_up) & ~e.key.apply(lambda k: (OUT / f"{k}.parquet").exists())]
    todo = todo.sort_values(["sym", "T"])
    print("events", len(e), "on binance spot", int(e.sym.isin(syms).sum()), "todo", len(todo), flush=True)
    n, cache = 0, {}
    for _, r in todo.iterrows():
        t0, t1 = int(r["T"]) - 3600, int(r["T"]) + 4 * 3600 + 120
        days = sorted({datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d") for t in (t0, t1)})
        parts = []
        for d in days:
            k = (r.sym, d)
            if k not in cache:
                cache.clear()                                           # keep memory flat: one coin-day at a time
                cache[k] = day_trades(r.sym, d)
            if cache[k] is not None:
                parts.append(cache[k])
        if not parts:
            continue
        bars(pd.concat(parts), t0, t1).to_parquet(OUT / f"{r.key}.parquet", index=False)
        n += 1
        if n % 25 == 0:
            print(time.strftime("%H:%M:%S"), "bn1s", n, "of", len(todo), flush=True)
        if limit and n >= limit:
            break
    print("bn1s new", n, flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
