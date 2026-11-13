"""B7 dense hourly matrices (T hours x N coins), 2024-03..2026-09, from 1-minute Binance perp klines.

Raw per coin-hour (hour closing at ts, built only from minutes before ts):
  o h l c qv tbq n        standard bar + taker-buy quote volume + trade count
  qv_q tbq_q              volume / taker-buy in the quarter-hour boundary minutes (:00 :15 :30 :45) of the hour
  rv                      realised variance from 1m log returns
  levy                    Levy area of (BTC, coin) normalised 1m paths within the hour (>0: BTC leads the coin)
  corr_btc                1m return correlation with BTC within the hour
  f8                      latest known 8h-normalised funding rate at ts
Out: data/cache/b7/{field}.npy (float32, T x N), ts.npy, codes.json
Env B7_WORKERS (default 8).
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

os.environ["B2_ERA"] = "all"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b2_panel as bp  # noqa: E402

OUT = ROOT / "data/cache/b7"
H_ = 3600
T0, T1 = int(pd.Timestamp("2024-03-01").timestamp()), int(pd.Timestamp("2026-09-25").timestamp())
TS = np.arange(T0 + H_, T1 + 1, H_)                       # hour-close timestamps
FIELDS = ["o", "h", "l", "c", "qv", "tbq", "n", "qv_q", "tbq_q", "rv", "levy", "corr_btc", "f8"]
_BTC = None


def btc_minutes():
    global _BTC
    if _BTC is None:
        d = bp.load_minutes("BTCUSDT")
        _BTC = np.log(d["c"].astype(np.float64)).diff().fillna(0.0)
    return _BTC


def one(code: str):
    d = bp.load_minutes(code)
    if d is None:
        return None
    d = d[(d.index >= T0) & (d.index < T1)]
    if len(d) < 3 * 1440:
        return None
    t = d.index.to_numpy()
    hour_close = (t // H_ + 1) * H_
    minute = (t % H_) // 60
    r = np.log(d["c"].astype(np.float64)).diff().fillna(0.0).to_numpy()
    b = btc_minutes().reindex(d.index).fillna(0.0).to_numpy()
    df = pd.DataFrame({"hc": hour_close, "o": d["o"].to_numpy(float), "h": d["h"].to_numpy(float),
                       "l": d["l"].to_numpy(float), "c": d["c"].to_numpy(float), "qv": d["qv"].to_numpy(float),
                       "tbq": d["tbq"].to_numpy(float), "n": d["n"].to_numpy(float), "r": r, "b": b,
                       "q": np.isin(minute, (0, 15, 30, 45))})
    df["qv_q"] = df["qv"] * df["q"]
    df["tbq_q"] = df["tbq"] * df["q"]
    df["r2"] = df["r"] ** 2
    g = df.groupby("hc", sort=True)
    A = g.agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), qv=("qv", "sum"), tbq=("tbq", "sum"),
              n=("n", "sum"), qv_q=("qv_q", "sum"), tbq_q=("tbq_q", "sum"), rv=("r2", "sum"), cnt=("r", "size"))
    # Levy area + correlation with BTC per hour (vectorised on full hours only)
    full = A.index[A["cnt"] == 60]
    sub = df[df["hc"].isin(full)]
    R = sub["r"].to_numpy().reshape(-1, 60)
    B = sub["b"].to_numpy().reshape(-1, 60)
    Rs = R / (R.std(1, keepdims=True) + 1e-12)
    Bs = B / (B.std(1, keepdims=True) + 1e-12)
    X = np.cumsum(Bs, 1) - Bs                                # path before each increment
    Y = np.cumsum(Rs, 1) - Rs
    levy = 0.5 * (X * Rs - Y * Bs).sum(1) / 60
    corr = ((R - R.mean(1, keepdims=True)) * (B - B.mean(1, keepdims=True))).mean(1) / (R.std(1) * B.std(1) + 1e-12)
    A["levy"] = np.nan
    A["corr_btc"] = np.nan
    A.loc[full, "levy"] = levy
    A.loc[full, "corr_btc"] = corr
    fu = bp.load_funding(code)
    if len(fu):
        k = np.searchsorted(fu["ts"].to_numpy(), A.index.to_numpy(), side="right") - 1
        A["f8"] = np.where(k >= 0, fu["f8"].to_numpy()[np.maximum(k, 0)], np.nan)
    else:
        A["f8"] = np.nan
    pos = np.searchsorted(TS, A.index.to_numpy())
    ok = (pos < len(TS)) & (TS[np.minimum(pos, len(TS) - 1)] == A.index.to_numpy())
    return code, pos[ok], {f: A[f].to_numpy(np.float32)[ok] for f in FIELDS}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    codes = sorted({p.stem for d in bp.K for p in d.glob("*USDT.parquet")} - {f"{t}USDT" for t in bp.TRADFI})
    M = {f: np.full((len(TS), len(codes)), np.nan, np.float32) for f in FIELDS}
    kept = []
    with ProcessPoolExecutor(int(os.environ.get("B7_WORKERS", "8"))) as ex:
        for i, res in enumerate(ex.map(one, codes, chunksize=2)):
            if res is not None:
                code, pos, arr = res
                j = codes.index(code)
                for f in FIELDS:
                    M[f][pos, j] = arr[f]
                kept.append(j)
            if i % 100 == 0:
                print("coins", i, len(codes), flush=True)
    kept = sorted(kept)
    for f in FIELDS:
        np.save(OUT / f"{f}.npy", M[f][:, kept])
    np.save(OUT / "ts.npy", TS)
    json.dump([codes[j] for j in kept], open(OUT / "codes.json", "w"))
    print("done", len(TS), len(kept))


if __name__ == "__main__":
    main()
