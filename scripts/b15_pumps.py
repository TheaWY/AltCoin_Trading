"""B15 pump event table (research/batch_B15_B16.yaml): one row per pump onset with minute-level paths and outcomes.
Onset m0 = first minute where close(m0)/close(m0-60) - 1 >= +10%, dv24 >= $2M, >= 72h listed, 6h cooldown per coin.
Out: data/cache/b15/pumps.parquet, data/cache/b15/paths.npy (n x 1501 x 4 float16: ret since m0, log vol ratio, taker share, range)
     minute index 0 = m0-60 ... 60 = m0 ... 1500 = m0+1440"""
from __future__ import annotations

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

OUT = ROOT / "data/cache/b15"
T0, T1 = int(pd.Timestamp("2024-03-15").timestamp()), int(pd.Timestamp("2026-09-23").timestamp())
W0, W1 = 60, 1440


def one(code):
    d = bp.load_minutes(code)
    if d is None:
        return None
    t = d.index.to_numpy()
    c = d["c"].to_numpy(np.float64)
    qv = d["qv"].to_numpy(np.float64)
    tbq = d["tbq"].to_numpy(np.float64)
    h, l = d["h"].to_numpy(np.float64), d["l"].to_numpy(np.float64)
    n = len(c)
    r60 = np.full(n, np.nan)
    r60[60:] = c[60:] / c[:-60] - 1
    dv24 = pd.Series(qv).rolling(1440, min_periods=720).sum().to_numpy()
    age_ok = np.arange(n) >= 72 * 60
    cand = np.flatnonzero((r60 >= 0.10) & (dv24 >= 2e6) & age_ok & (t >= T0) & (t <= T1) & (np.arange(n) + W1 < n) & (np.arange(n) >= W0))
    rows, paths = [], []
    last = -10 ** 9
    vol_med = pd.Series(qv).rolling(1440, min_periods=720).median().to_numpy()
    for m0 in cand:
        if m0 - last < 360:
            continue
        last = m0
        seg = slice(m0 - W0, m0 + W1 + 1)
        cs = c[seg] / c[m0] - 1
        vr = np.log((qv[seg] + 1) / (vol_med[m0] + 1))
        tk = np.where(qv[seg] > 0, tbq[seg] / np.maximum(qv[seg], 1e-9), 0.5) - 0.5
        rg = h[seg] / np.maximum(l[seg], 1e-12) - 1
        paths.append(np.stack([cs, vr, tk, rg], 1).astype(np.float16))
        fut = c[m0 + 1:m0 + W1 + 1]
        pk = int(np.argmax(fut)) + 1
        peak_gain = fut[pk - 1] / c[m0] - 1
        run_max = np.maximum.accumulate(fut)
        dd = fut / run_max - 1
        e1 = c[m0 + 1]                                             # entry at next minute open ~ close of m0+1 (1m data)
        rows.append(dict(code=code, ts=int(t[m0]), pump_size=float(r60[m0]), dv24=float(dv24[m0]),
                         gain_5m=float(c[m0 + 5] / c[m0] - 1), gain_15m=float(c[m0 + 15] / c[m0] - 1), gain_60m=float(c[m0 + 60] / c[m0] - 1),
                         peak_gain=float(peak_gain), min_to_peak=pk, dd_1h=float(dd[59]), dd_6h=float(dd[359]), dd_24h=float(dd[1439]),
                         ret24_from_entry=float(c[m0 + W1] / e1 - 1), ret6h_from_entry=float(c[m0 + 360] / e1 - 1),
                         taker_0_5=float(tbq[m0:m0 + 5].sum() / max(qv[m0:m0 + 5].sum(), 1e-9)),
                         taker_20_60=float(tbq[m0 + 20:m0 + 60].sum() / max(qv[m0 + 20:m0 + 60].sum(), 1e-9)),
                         vol_ratio_0_5=float(qv[m0:m0 + 5].mean() / max(vol_med[m0], 1e-9)),
                         retrace50_24h=bool(np.any(fut[pk:] <= c[m0] + 0.5 * (fut[pk - 1] - c[m0]))) if pk < W1 else False))
    return rows, paths


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    codes = sorted({p.stem for d in bp.K for p in d.glob("*USDT.parquet")} - {f"{t}USDT" for t in bp.TRADFI})
    rows, paths = [], []
    with ProcessPoolExecutor(6) as ex:
        for i, res in enumerate(ex.map(one, codes, chunksize=4)):
            if res:
                rows += res[0]
                paths += res[1]
            if i % 100 == 0:
                print("coins", i, len(codes), "pumps", len(rows), flush=True)
    P = pd.DataFrame(rows)
    P["pump_id"] = np.arange(len(P))
    P.to_parquet(OUT / "pumps.parquet", index=False)
    np.save(OUT / "paths.npy", np.stack(paths))
    print("pumps", len(P), "disc", int((P["ts"] < 1756684800).sum()), "peak_gain median", P["peak_gain"].median().round(4),
          "min_to_peak median", int(P["min_to_peak"].median()), "ret24 mean", P["ret24_from_entry"].mean().round(4))


if __name__ == "__main__":
    main()
