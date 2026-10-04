"""B7_C step 1: out-of-sample PCA residuals + per-date tensors for deep residual stat-arb.

Every 168h block starting at s: estimate on the past 720h of hourly log returns of liquid coins
(dv24 >= $5M, >= 720h listed, >= 90% finite): 5 eigen-portfolios Q = v_k / sigma, betas by OLS on factor returns.
Apply the frozen Q, beta to hours [s, s+168): residual = r - (r @ Q) @ beta. No look-ahead.
Dataset: every 4h date, coins liquid at the date with >= 60 finite residual hours in the last 72:
  x (3 x 72): cumulative residual (scaled by its 72h std), taker imbalance, log volume demeaned
  y: next-4h simple return, funding over 4h, cost rate (fee + slippage)
Out: data/cache/b7/resid.npy, data/cache/b7/statarb.npz"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

H_ = 3600


def residuals():
    ts, codes, X = L.data()
    r = X["r1"].astype(np.float64)
    T, N = r.shape
    E = np.full((T, N), np.nan, np.float32)
    for s in range(720 + 24, T, 168):
        W = r[s - 720:s]
        ok = (X["dv24"][s - 1] >= 5e6) & (X["age"][s - 1] >= 720) & (np.isfinite(W).mean(0) >= 0.9)
        idx = np.flatnonzero(ok)
        if len(idx) < 30:
            continue
        Wf = np.nan_to_num(W[:, idx])
        sig = Wf.std(0) + 1e-9
        Z = (Wf - Wf.mean(0)) / sig
        C = np.corrcoef(Z.T)
        vals, vecs = np.linalg.eigh(C)
        V = vecs[:, -5:]
        Q = V / sig[:, None]
        Fw = Wf @ Q
        beta, *_ = np.linalg.lstsq(Fw, Wf, rcond=None)
        h1 = min(s + 168, T)
        R = r[s:h1][:, idx]
        Fh = np.nan_to_num(R) @ Q
        E[s:h1][:, idx] = (R - Fh @ beta).astype(np.float32)
    np.save(L.DIR / "resid.npy", E)
    return E


def dataset(E):
    ts, codes, X = L.data()
    T, N = E.shape
    qv = np.nan_to_num(X["qv"])
    ofi = L.safe_div(2 * np.nan_to_num(X["tbq"]) - qv, qv)
    lqv = np.log(qv + 1)
    c = X["c"]
    dates = np.flatnonzero((ts % (4 * H_) == 0) & (ts >= L.DISC[0] + 14 * 86400))
    dates = dates[dates + 4 < T]
    xs, meta = [], []
    for t in dates:
        liq = (X["dv24"][t] >= 5e6) & (X["age"][t] >= 720) & np.isfinite(c[t]) & np.isfinite(c[t + 4])
        e = E[t - 71:t + 1]
        good = liq & (np.isfinite(e).sum(0) >= 60)
        idx = np.flatnonzero(good)
        if len(idx) < 20:
            continue
        ee = np.nan_to_num(e[:, idx])
        cum = np.cumsum(ee, 0)
        cum = cum / (ee.std(0) * np.sqrt(72) + 1e-9)
        of = np.nan_to_num(ofi[t - 71:t + 1, idx])
        lv = lqv[t - 71:t + 1, idx]
        lv = lv - lv.mean(0)
        xs.append(np.stack([cum, of, lv], 1).transpose(2, 1, 0).astype(np.float16))     # (n, 3, 72)
        g = c[t + 4, idx] / c[t, idx] - 1
        fund = np.nan_to_num(np.nanmean(X["f8"][t + 1:t + 5, idx], 0)) * 0.5
        cost = L.FEE + L.slip(X["dv24"][t, idx])
        meta.append((t, idx, g.astype(np.float32), fund.astype(np.float32), cost.astype(np.float32)))
    offs = np.cumsum([0] + [len(m[1]) for m in meta])
    np.savez(L.DIR / "statarb.npz", x=np.concatenate(xs), offs=offs, t=np.array([m[0] for m in meta]),
             coin=np.concatenate([m[1] for m in meta]), g=np.concatenate([m[2] for m in meta]),
             fund=np.concatenate([m[3] for m in meta]), cost=np.concatenate([m[4] for m in meta]), ts=ts)
    print("dates", len(meta), "rows", offs[-1])


if __name__ == "__main__":
    dataset(residuals())
