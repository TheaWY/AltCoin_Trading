"""Upbit KRW 1h candles history for all KRW markets (public REST, no key), incl. KRW-USDT for the KRW premium.
data/cache/upbit1h_hist/{BASE}.parquet: ts (hour close, UTC s), o, h, l, c, value_krw. Resumable (skips files already covering --start).
Rate limit: Upbit quotation 10 req/s -> we pace at ~8/s.
  .venv/bin/python scripts/backfill_upbit1h.py --start 2024-03-01"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/cache/upbit1h_hist"
API = "https://api.upbit.com/v1"
S = requests.Session()
_last = [0.0]


def get(url, params):
    for k in range(6):
        wait = 0.125 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        r = S.get(url, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        time.sleep(1 + k)
    return []


def market(m, start_ts):
    rows, to = [], None
    while True:
        par = {"market": m, "count": 200}
        if to:
            par["to"] = to
        js = get(f"{API}/candles/minutes/60", par)
        if not js:
            break
        rows += js
        oldest = js[-1]["candle_date_time_utc"]
        t_old = int(datetime.fromisoformat(oldest).replace(tzinfo=timezone.utc).timestamp())
        if t_old <= start_ts or len(js) < 200:
            break
        to = oldest.replace("T", " ")
    if not rows:
        return None
    df = pd.DataFrame(rows)
    t = (pd.to_datetime(df["candle_date_time_utc"], utc=True) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1s") + 3600
    return pd.DataFrame({"ts": t, "o": df["opening_price"], "h": df["high_price"], "l": df["low_price"],
                         "c": df["trade_price"], "value_krw": df["candle_acc_trade_price"]}).drop_duplicates("ts").sort_values("ts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-03-01")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    start_ts = int(pd.Timestamp(a.start, tz="UTC").timestamp())
    mk = [x["market"] for x in S.get(f"{API}/market/all", timeout=20).json() if x["market"].startswith("KRW-")]
    print("markets", len(mk), flush=True)
    for i, m in enumerate(mk):
        base = m.split("-", 1)[1]
        p = OUT / f"{base}.parquet"
        if p.exists() and pd.read_parquet(p, columns=["ts"])["ts"].min() <= start_ts + 86400:
            continue
        df = market(m, start_ts)
        if df is not None:
            df[df["ts"] >= start_ts].to_parquet(p, index=False)
        if i % 20 == 0:
            print(i, len(mk), m, 0 if df is None else len(df), flush=True)
    print("done")


if __name__ == "__main__":
    main()
