"""Upbit research database (2026-10-04, asked by 유리): Upbit-native price history around pump events, long-only research.

Layout (all parquet, outside Postgres to keep the DB small):
  data/upbit_db/h1/<MKT>.parquet          hourly candles, 2024-01-01 -> now, every current KRW market
  data/upbit_db/events.parquet            +10% close-to-close hours (value24 >= 2.7bn KRW, >= 72h history)
  data/upbit_db/m1/<MKT>_<T>.parquet      1-minute candles  T-2h .. T+12h   (every event)
  data/upbit_db/s1/<MKT>_<T>.parquet      1-second candles  T-1h .. T+4h+60s (events inside Upbit's ~3-month 1s window)
T = end of the pump hour (epoch s, UTC). Columns: ts (candle start, epoch s), o, h, l, c, v (KRW value).
Upbit REST quotation API only (public, no key), <= 9 requests/s (limit 10/s, 600/min). Resumable: existing files skipped.
Caveat: /v1/market/all lists CURRENT markets only, so delisted coins are missing (survivorship bias against losers).

Usage: python scripts/upbit_db.py h1 | events | m1 | s1 | all
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/upbit_db"
START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
S = requests.Session()
_last = [0.0]


def get(path, params):
    for attempt in range(6):
        wait = 0.112 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = S.get(f"https://api.upbit.com{path}", params=params, timeout=15)
        except requests.RequestException:
            time.sleep(2 + attempt * 2); continue
        if r.status_code == 429:
            time.sleep(1 + attempt); continue
        if r.status_code != 200:
            return None
        return r.json()
    return None


def iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candles(market, unit, t0, t1):
    """candles with start in [t0, t1); unit = 'minutes/60', 'minutes/1' or 'seconds'. Walks backwards from t1."""
    rows, to = [], t1
    while to > t0:
        d = get(f"/v1/candles/{unit}", {"market": market, "count": 200, "to": iso(to)})
        if not d:
            break
        rows += d
        first = int(pd.Timestamp(d[-1]["candle_date_time_utc"], tz="UTC").timestamp())
        if first >= to:
            break
        to = first
        if len(d) < 200:
            break
    if not rows:
        return pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v"])
    d = pd.DataFrame(rows)
    d["ts"] = (pd.to_datetime(d["candle_date_time_utc"], utc=True) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    d = d.rename(columns={"opening_price": "o", "high_price": "h", "low_price": "l", "trade_price": "c", "candle_acc_trade_price": "v"})
    d = d[["ts", "o", "h", "l", "c", "v"]].drop_duplicates("ts").sort_values("ts")
    return d[(d.ts >= t0) & (d.ts < t1)].reset_index(drop=True)


def markets():
    return sorted(x["market"] for x in get("/v1/market/all", {}) if x["market"].startswith("KRW-"))


def step_h1():
    out = DB / "h1"; out.mkdir(parents=True, exist_ok=True)
    now = int(time.time()) // 3600 * 3600
    for i, m in enumerate(markets()):
        f = out / f"{m}.parquet"
        old = pd.read_parquet(f) if f.exists() else None
        t0 = int(old.ts.max()) + 3600 if old is not None and len(old) else START
        if t0 >= now:
            continue
        d = candles(m, "minutes/60", t0, now)
        d = pd.concat([old, d]) if old is not None else d
        d.drop_duplicates("ts").sort_values("ts").to_parquet(f, index=False)
        if i % 25 == 0:
            print(time.strftime("%H:%M:%S"), "h1", i, m, len(d), flush=True)


def step_events():
    ev = []
    for f in sorted((DB / "h1").glob("*.parquet")):
        g = pd.read_parquet(f).set_index("ts").sort_index()
        if len(g) < 100:
            continue
        g = g.reindex(range(int(g.index[0]), int(g.index[-1]) + 3600, 3600))
        g["c"] = g["c"].ffill(); g["v"] = g["v"].fillna(0)
        r1 = g["c"] / g["c"].shift(1) - 1
        v24 = g["v"].rolling(24, min_periods=24).sum()
        age = np.arange(len(g))
        m = ((r1 >= 0.10) & (v24 >= 2.7e9) & (age >= 72)).to_numpy()
        for ts in g.index[m]:
            ev.append((f.stem, int(ts) + 3600, float(r1.at[ts]), float(v24.at[ts])))
    e = pd.DataFrame(ev, columns=["market", "T", "ret_1h", "v24_krw"]).sort_values("T").reset_index(drop=True)
    e.to_parquet(DB / "events.parquet", index=False)
    print("events", len(e), pd.Timestamp(e["T"].min(), unit="s"), pd.Timestamp(e["T"].max(), unit="s"), flush=True)
    return e


def step_windows(kind):
    e = pd.read_parquet(DB / "events.parquet")
    out = DB / kind; out.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    if kind == "m1":
        unit, pre, post = "minutes/1", 2 * 3600, 12 * 3600
    else:
        unit, pre, post = "seconds", 3600, 4 * 3600 + 120
        e = e[e["T"] >= now - 88 * 86400]                    # Upbit keeps ~3 months of 1s candles
    done = 0
    for i, r in e.iterrows():
        f = out / f"{r.market}_{int(r['T'])}.parquet"
        if f.exists() or r["T"] + post > now:
            continue
        candles(r.market, unit, int(r["T"]) - pre, int(r["T"]) + post).to_parquet(f, index=False)
        done += 1
        if done % 50 == 0:
            print(time.strftime("%H:%M:%S"), kind, done, "of", len(e), flush=True)
    print(kind, "new", done, flush=True)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("h1", "all"):
        step_h1()
    if what in ("events", "all"):
        step_events()
    if what in ("s1", "all"):
        step_windows("s1")                                   # 1s first: it expires, 1m does not
    if what in ("m1", "all"):
        step_windows("m1")
