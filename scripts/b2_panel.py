"""Combined 2025-03..2026-09 hourly panel from 1-minute Binance perp klines (both cache folders).

Causal features at each hour close T (minutes before T only) + forward returns for ML labels.
Out: data/cache/b2_hourly.parquet
Also exposes load_minutes(code) used by the B2 simulator and the ML/DL layers.
"""

from __future__ import annotations

import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import os
_ERA = os.environ.get("B2_ERA", "main")          # "main" = 2025-03..2026-09, "2024" = 2024-03..2025-02 holdout
_KS = {"2024": ["k1m_2024"], "main": ["k1m_oot", "k1m"], "all": ["k1m_2024", "k1m_oot", "k1m"]}[_ERA]
_FS = {"2024": ["funding_2024"], "main": ["funding_oot", "funding"], "all": ["funding_2024", "funding_oot", "funding"]}[_ERA]
K = [ROOT / "data/cache" / k for k in _KS]
F = [ROOT / "data/cache" / k for k in _FS]
OUT = ROOT / ("data/cache/b2_hourly_2024.parquet" if _ERA == "2024" else "data/cache/b2_hourly.parquet")
H_, D_ = 3600, 86400
TRADFI = {"AAPL", "AMD", "AMZN", "COIN", "COPPER", "CRCL", "CRWD", "EWY", "GOOGL", "HOOD", "INTC", "KORU", "META",
          "MSFT", "MSTR", "NATGAS", "NFLX", "NVDA", "PAXG", "PLTR", "QQQ", "SOXL", "SPY", "TQQQ", "TSLA", "XAG", "XAU",
          "XAUT", "XPD", "XPT"}
DATA_START = int(pd.Timestamp("2024-03-01" if _ERA == "2024" else "2025-03-01").timestamp())


def load_minutes(code: str) -> pd.DataFrame | None:
    parts = [pd.read_parquet(d / f"{code}.parquet") for d in K if (d / f"{code}.parquet").exists()]
    if not parts:
        return None
    d = pd.concat(parts).drop_duplicates("ts").sort_values("ts")
    live = d.loc[d["n"] > 0, "ts"]                  # vision writes flat zero-volume bars after a delisting
    if len(live) < 3 * 1440:
        return None
    d = d[(d["ts"] >= live.iloc[0]) & (d["ts"] <= live.iloc[-1])]
    idx = np.arange(int(d["ts"].iloc[0]), int(d["ts"].iloc[-1]) + 60, 60)
    d = d.set_index("ts").reindex(idx)
    d["c"] = d["c"].ffill()
    for k in ("o", "h", "l"):
        d[k] = d[k].fillna(d["c"])
    for k in ("qv", "tbq"):
        d[k] = d[k].fillna(0.0)
    d["n"] = d["n"].fillna(0)
    return d


def load_funding(code: str) -> pd.DataFrame:
    parts = [pd.read_parquet(d / f"{code}.parquet", columns=["ts", "f"]) for d in F if (d / f"{code}.parquet").exists()]
    if not parts:
        return pd.DataFrame({"ts": np.array([], "int64"), "f": [], "f8": []})
    d = pd.concat(parts).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    gap = d["ts"].diff().bfill().clip(lower=H_, upper=8 * H_) / H_
    d["f8"] = d["f"] * 8 / gap
    return d


def hourly(code: str) -> pd.DataFrame | None:
    d = load_minutes(code)
    if d is None:
        return None
    first_live = int(d.index[0])
    lr = np.log(d["c"].astype(np.float64)).diff()
    rv24 = lr.rolling(1440, min_periods=720).std() * math.sqrt(1440)
    qv60 = d["qv"].rolling(60, min_periods=1).sum()
    tb60 = d["tbq"].rolling(60, min_periods=1).sum()
    hi60 = d["h"].rolling(60, min_periods=1).max()
    lo60 = d["l"].rolling(60, min_periods=1).min()
    ends = d.index[(d.index + 60) % H_ == 0]
    T = ends + 60
    H = pd.DataFrame({"ts": T, "c": d["c"].reindex(ends).to_numpy(np.float64), "hi": hi60.reindex(ends).to_numpy(),
                      "lo": lo60.reindex(ends).to_numpy(), "qv": qv60.reindex(ends).to_numpy(),
                      "tb": tb60.reindex(ends).to_numpy(), "rv24": rv24.reindex(ends).to_numpy()})
    c = H["c"]
    for k, n in (("ret_1h", 1), ("ret_2h", 2), ("ret_4h", 4), ("ret_24h", 24), ("ret_7d", 168), ("ret_28d", 672)):
        H[k] = c / c.shift(n) - 1
    H["ret_prev1h"] = H["ret_1h"].shift(1)
    H["rv_7d"] = H["ret_1h"].rolling(168, min_periods=72).std() * math.sqrt(24)
    H["max_7d"] = H["ret_1h"].rolling(168, min_periods=72).max()
    H["dv24"] = H["qv"].rolling(24, min_periods=12).sum()
    H["taker_1h"] = H["tb"] / H["qv"].replace(0, np.nan)
    H["taker_24h"] = H["tb"].rolling(24, min_periods=12).sum() / H["dv24"].replace(0, np.nan)
    H["vsurge"] = H["qv"] / H["qv"].rolling(168, min_periods=72).mean().shift(1)
    H["hi30"] = c.rolling(720, min_periods=360).max().shift(1)
    H["lo30"] = c.rolling(720, min_periods=360).min().shift(1)
    H["dhi30"] = c / H["hi30"] - 1
    H["dlo30"] = c / H["lo30"] - 1
    H["range_1h"] = H["hi"] / H["lo"] - 1
    H["upwick_1h"] = (H["hi"] - c) / (H["hi"] - H["lo"]).replace(0, np.nan)
    pump = (H["ret_1h"] >= 0.10).astype(float)
    H["pumps_30d"] = pump.rolling(720, min_periods=1).sum().shift(1)
    H["age_h"] = (H["ts"] - first_live) / H_
    H["listed_in_window"] = float(first_live > DATA_START + 2 * D_)
    for h in (1, 4, 24):
        H[f"fwd_{h}h"] = c.shift(-h) / c - 1
    fu = load_funding(code)
    if len(fu):
        cs = np.concatenate([[0.0], np.cumsum(fu["f8"].to_numpy())])
        ts = fu["ts"].to_numpy()
        hi = np.searchsorted(ts, H["ts"].to_numpy(), side="right")
        lo = np.searchsorted(ts, H["ts"].to_numpy() - D_, side="right")
        n = hi - lo
        H["fund24"] = np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
    else:
        H["fund24"] = np.nan
    H["code"] = code
    H = H.drop(columns=["hi30", "lo30", "tb"])
    for k in H.columns:
        if H[k].dtype == np.float64 and k not in ("c",):
            H[k] = H[k].astype(np.float32)
    return H


def build() -> pd.DataFrame:
    codes = sorted({p.stem for d in K for p in d.glob("*USDT.parquet")} - {f"{t}USDT" for t in TRADFI})
    parts = []
    with ProcessPoolExecutor(8) as ex:
        for i, h in enumerate(ex.map(hourly, codes, chunksize=4)):
            if h is not None:
                parts.append(h)
            if i % 100 == 0:
                print("hourly", i, len(codes), flush=True)
    P = pd.concat(parts, ignore_index=True)
    btc = P[P["code"] == "BTCUSDT"].set_index("ts")
    for k in ("ret_1h", "ret_24h"):
        P[f"btc_{k}"] = P["ts"].map(btc[k]).astype(np.float32)
    g = P[(P["dv24"] >= 2e6)].groupby("ts")["ret_1h"]
    P["breadth_pump"] = P["ts"].map(g.apply(lambda x: int((x >= 0.05).sum()))).fillna(0).astype(np.float32)
    P["hour"] = ((P["ts"] // H_) % 24).astype(np.int8)
    P["weekday"] = (((P["ts"] // D_) + 3) % 7).astype(np.int8)
    P.to_parquet(OUT, index=False)
    return P


if __name__ == "__main__":
    P = build()
    print(P.shape, P["code"].nunique(), pd.to_datetime(P["ts"].min(), unit="s"), pd.to_datetime(P["ts"].max(), unit="s"))
    sys.exit(0)
