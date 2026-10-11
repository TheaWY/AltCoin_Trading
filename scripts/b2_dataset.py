"""Event datasets for the B2 ML/DL layer: every trigger hour (no cooldown) for pump / dump / breakout.

Per event: tabular features at the hour close T (from the B2 hourly panel), the 120 one-minute bars before T
(5 channels), a 64x60 OHLC+volume chart image of the last 60 minutes (Jiang-Kelly-Xiu style), and the 4h
gross return from the next-minute open (entry T+60s, exit T+60s+240m) plus cost.
Out: data/cache/b2_ds_{pump,dump,brk}.npz
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b2_panel import OUT as PANEL, load_minutes  # noqa: E402
from b2_hypotheses import FEE, events, slip  # noqa: E402

TAB = ["ret_1h", "ret_2h", "ret_4h", "ret_24h", "ret_7d", "ret_28d", "rv24", "rv_7d", "max_7d", "ldv", "taker_1h",
       "taker_24h", "vsurge", "dhi30", "dlo30", "range_1h", "upwick_1h", "pumps_30d", "lage", "fund24",
       "btc_ret_1h", "btc_ret_24h", "breadth_pump", "hour_s", "hour_c", "weekday"]
import os
SUFFIX = "_2024" if os.environ.get("B2_ERA") == "2024" else ""
SEQ = 120
IMG_H, IMG_W = 64, 60


def image(o, h, l_, c, v) -> np.ndarray:
    img = np.zeros((IMG_H, IMG_W), np.uint8)
    top = IMG_H - 13                                   # price area rows 0..50, volume rows 52..63
    lo, hi = np.nanmin(l_), np.nanmax(h)
    if not (hi > lo):
        return img
    sc = lambda x: np.clip(((hi - x) / (hi - lo) * (top - 1)).astype(int), 0, top - 1)  # noqa: E731
    for k in range(IMG_W):
        a, b = sc(h[k]), sc(l_[k])
        img[a:b + 1, k] = 255
        img[sc(c[k]), k] = 255
    vm = np.nanmax(v)
    if vm > 0:
        hh = (v / vm * 11).astype(int)
        for k in range(IMG_W):
            if hh[k] > 0:
                img[IMG_H - hh[k]:, k] = 255
    return img


def one(args):
    code, rows = args
    d = load_minutes(code)
    if d is None:
        return []
    t0 = int(d.index[0])
    o, h, l_, c = (d[k].to_numpy(np.float64) for k in ("o", "h", "l", "c"))
    qv, tb = d["qv"].to_numpy(np.float64), d["tbq"].to_numpy(np.float64)
    n = len(c)
    out = []
    for r in rows:
        iT = (int(r["ts"]) - t0) // 60                  # minute starting at T (first minute after the signal)
        i0, i1 = iT + 1, iT + 1 + 240
        if iT - SEQ < 0 or i1 >= n:
            continue
        s = slice(iT - SEQ, iT)
        cT = c[iT - 1]
        vv = qv[s]
        vm = vv.mean() if vv.mean() > 0 else 1.0
        seq = np.stack([c[s] / cT - 1, h[s] / cT - 1, l_[s] / cT - 1, np.log1p(vv / vm),
                        np.where(vv > 0, tb[s] / np.maximum(vv, 1e-9), 0.5)]).astype(np.float32)
        s60 = slice(iT - IMG_W, iT)
        img = image(o[s60], h[s60], l_[s60], c[s60], qv[s60])
        gross = o[i1] / o[i0] - 1
        out.append((r["idx"], seq, img, gross))
    return out


def build(name: str, fid: str) -> None:
    P = pd.read_parquet(PANEL)
    e = events(P, fid, "L").copy()
    e["ldv"] = np.log1p(e["dv24"])
    e["lage"] = np.log1p(e["age_h"])
    e["hour_s"] = np.sin(2 * np.pi * e["hour"] / 24)
    e["hour_c"] = np.cos(2 * np.pi * e["hour"] / 24)
    e = e.reset_index(drop=True)
    e["idx"] = np.arange(len(e))
    jobs = [(code, g[["idx", "ts"]].to_dict("records")) for code, g in e.groupby("code")]
    res = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(one, jobs, chunksize=2):
            res += r
    idx = np.array([x[0] for x in res])
    e = e.iloc[idx]
    np.savez_compressed(ROOT / f"data/cache/b2_ds_{name}{SUFFIX}.npz",
                        tab=e[TAB].to_numpy(np.float32), seq=np.stack([x[1] for x in res]),
                        img=np.stack([x[2] for x in res]), gross=np.array([x[3] for x in res], np.float32),
                        cost=(2 * (FEE + slip(e["dv24"].to_numpy()))).astype(np.float32),
                        ts=e["ts"].to_numpy(), code=e["code"].to_numpy().astype(str))
    print(name, len(res), flush=True)


if __name__ == "__main__":
    for name, fid in ((("pump", "P1"),) if SUFFIX else (("pump", "P1"), ("dump", "D1"), ("brk", "BH"))):
        build(name, fid)
