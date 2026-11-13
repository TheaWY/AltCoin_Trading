"""B7 shared library: dense hourly matrices, universe, BTC-neutral target, factor evaluation (research/batch_B7.yaml)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import bottleneck as bn
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data/cache/b7"
H_, D_ = 3600, 86400
FEE = 0.0005
DISC = (pd.Timestamp("2024-04-01").timestamp(), pd.Timestamp("2025-09-01").timestamp())
HOLD = (pd.Timestamp("2025-09-01").timestamp(), pd.Timestamp("2026-09-24").timestamp())
RNG = np.random.default_rng(17)


# ------------------------------------------------------------------ rolling helpers (axis 0 = time)
def S(x, w):
    return bn.move_sum(x, w, min_count=max(1, w // 2), axis=0)


def M(x, w):
    return bn.move_mean(x, w, min_count=max(1, w // 2), axis=0)


def SD(x, w):
    return bn.move_std(x, w, min_count=max(2, w // 2), axis=0)


def MX(x, w):
    return bn.move_max(x, w, min_count=max(1, w // 2), axis=0)


def MN(x, w):
    return bn.move_min(x, w, min_count=max(1, w // 2), axis=0)


def lag(x, k):
    out = np.full_like(x, np.nan)
    if k > 0:
        out[k:] = x[:-k]
    elif k < 0:
        out[:k] = x[-k:]
    else:
        out[:] = x
    return out


def corr(x, y, w):
    mx, my = M(x, w), M(y, w)
    cov = M(x * y, w) - mx * my
    return cov / (np.sqrt(np.maximum(M(x * x, w) - mx * mx, 0)) * np.sqrt(np.maximum(M(y * y, w) - my * my, 0)) + 1e-12)


def safe_div(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        out = a / b
    out[~np.isfinite(out)] = np.nan
    return out


def cs_rank(x):
    return pd.DataFrame(x).rank(axis=1, pct=True).to_numpy(np.float32) - 0.5


# ------------------------------------------------------------------ data
@lru_cache(maxsize=1)
def data():
    ts = np.load(DIR / "ts.npy")
    codes = json.load(open(DIR / "codes.json"))
    X = {f: np.load(DIR / f"{f}.npy") for f in
         ("o", "h", "l", "c", "qv", "tbq", "n", "qv_q", "tbq_q", "rv", "levy", "corr_btc", "f8")}
    lc = np.log(X["c"].astype(np.float64)).astype(np.float32)
    X["lc"] = lc
    X["r1"] = lc - lag(lc, 1)
    X["dv24"] = S(np.nan_to_num(X["qv"]), 24)
    listed = np.isfinite(X["c"])
    X["age"] = np.cumsum(listed, 0).astype(np.float32)
    bi = codes.index("BTCUSDT")
    rb = X["r1"][:, [bi]]
    X["btc_r1"] = rb
    # 30d rolling BTC beta (past only)
    mb = M(rb, 720)
    X["beta"] = safe_div(M(X["r1"] * rb, 720) - M(X["r1"], 720) * mb, M(rb * rb, 720) - mb * mb)
    fwd = lag(lc, -24) - lc
    X["fwd24"] = fwd
    X["fwd24_res"] = fwd - np.nan_to_num(X["beta"]) * fwd[:, [bi]]
    X["U"] = (X["dv24"] >= 5e6) & (X["age"] >= 720) & np.isfinite(X["fwd24_res"]) & np.isfinite(X["beta"])
    # liquid equal-weight market return
    X["mkt_r1"] = np.nanmean(np.where(X["U"], X["r1"], np.nan), 1, keepdims=True).astype(np.float32)
    return ts, codes, X


def eval_rows(period):
    ts, _, _ = data()
    return np.flatnonzero((ts % (8 * H_) == 0) & (ts >= period[0]) & (ts < period[1]))


def known_factors():
    _, _, X = data()
    lc, r1 = X["lc"], X["r1"]
    return {"ret_24h": lc - lag(lc, 24), "ret_7d": lc - lag(lc, 168), "ret_28d": lc - lag(lc, 672),
            "rv_7d": SD(r1, 168), "log_dv24": np.log(X["dv24"] + 1), "funding_8h": X["f8"], "max_1h_7d": MX(r1, 168)}


# ------------------------------------------------------------------ evaluation
def _rank_rows(A, mask):
    A = np.where(mask & np.isfinite(A), A, np.nan)
    return pd.DataFrame(A).rank(axis=1, pct=True).to_numpy(dtype=np.float64, copy=True)


def ic_series(F, rows, target="fwd24_res"):
    """Spearman IC per rebalance row within the universe."""
    ts, _, X = data()
    m = X["U"][rows] & np.isfinite(F[rows])
    a, b = _rank_rows(F[rows], m), _rank_rows(X[target][rows], m)
    a -= np.nanmean(a, 1, keepdims=True)
    b -= np.nanmean(b, 1, keepdims=True)
    ic = np.nansum(a * b, 1) / np.sqrt(np.nansum(a * a, 1) * np.nansum(b * b, 1))
    ic[m.sum(1) < 20] = np.nan
    return pd.Series(ic, index=ts[rows])


def daily(s):
    return s.groupby(s.index // D_).mean().dropna()


def boot_ci(x, reps=4000):
    x = np.asarray(x)
    b = [x[RNG.integers(0, len(x), len(x))].mean() for _ in range(reps)]
    return np.percentile(b, [2.5, 97.5]).tolist()


def residualise(F, rows, K):
    """Cross-sectional OLS of rank(F) on ranks of known factors, per row; returns residual matrix on rows."""
    _, _, X = data()
    out = np.full((len(rows), F.shape[1]), np.nan)
    Fr = cs_rank(np.where(X["U"][rows], F[rows], np.nan))
    Kr = [cs_rank(np.where(X["U"][rows], k[rows], np.nan)) for k in K.values()]
    for i in range(len(rows)):
        y = Fr[i]
        Z = np.column_stack([np.ones_like(y)] + [k[i] for k in Kr])
        ok = np.isfinite(y) & np.isfinite(Z).all(1)
        if ok.sum() < 30:
            continue
        beta, *_ = np.linalg.lstsq(Z[ok], y[ok], rcond=None)
        out[i, ok] = y[ok] - Z[ok] @ beta
    full = np.full(F.shape, np.nan)
    full[rows] = out
    return full


@lru_cache(maxsize=1)
def VOL7():
    _, _, X = data()
    return SD(X["r1"], 168)


def slip(dv):
    return np.select([dv > 1e8, dv > 2e7, dv > 5e6], [0.0002, 0.0005, 0.0010], 0.0020)


def long_short(F, rows, q=0.2, vol_scaled=False, nbooks=3):
    """Quintile L/S, equal weight, dollar neutral, gross 1. Three staggered 24h books (one opened every 8h row), so the
    live portfolio is the mean of the last three books. Per 8h step: price P&L - funding - |dw| x (fee + slippage).
    Also a BTC-hedged variant (portfolio beta shorted in BTC). Returns per-step frame indexed by step start."""
    ts, codes, X = data()
    c, N = X["c"], X["c"].shape[1]
    bi = codes.index("BTCUSDT")
    books, out = [], []
    w_prev = np.zeros(N)
    h_prev = 0.0
    for i in rows:
        if i + 8 >= len(ts):
            break
        m = X["U"][i] & np.isfinite(F[i])
        b = np.zeros(N)
        if m.sum() >= 20:
            f = F[i]
            lo, hi = np.nanquantile(f[m], [q, 1 - q])
            L, Sh = m & (f >= hi), m & (f <= lo)
            if vol_scaled:
                iv = 1.0 / np.maximum(np.nan_to_num(VOL7()[i], nan=1.0), 1e-3)
                b[L] = 0.5 * iv[L] / iv[L].sum()
                b[Sh] = -0.5 * iv[Sh] / iv[Sh].sum()
            else:
                b[L], b[Sh] = 0.5 / L.sum(), -0.5 / Sh.sum()
        books = (books + [b])[-nbooks:]
        w = np.mean(books, 0)
        g = np.nan_to_num(c[i + 8] / c[i] - 1)
        fund = np.nan_to_num(np.nanmean(X["f8"][i + 1:i + 9], 0))
        cost_rate = FEE + slip(np.nan_to_num(X["dv24"][i]))
        dw = np.abs(w - w_prev)
        net = (w * g).sum() - (w * fund).sum() - (dw * cost_rate).sum()
        hb = (w * np.nan_to_num(X["beta"][i])).sum()                # BTC hedge notional
        hedged = net - hb * g[bi] + hb * fund[bi] - abs(hb - h_prev) * (FEE + 0.0002)
        out.append((ts[i], net, (w * g).sum(), hedged, dw.sum()))
        w_prev, h_prev = w, hb
    return pd.DataFrame(out, columns=["ts", "net", "gross", "net_hedged", "turnover"]).set_index("ts")


def evaluate(F, period, sign=1.0, K=None, with_ls=True):
    rows = eval_rows(period)
    F = sign * F
    ic = daily(ic_series(F, rows))
    res = dict(ic=float(ic.mean()), ic_ci=boot_ci(ic.to_numpy()), ic_ir=float(ic.mean() / ic.std() * np.sqrt(365)),
               days=int(len(ic)), ic_pos_frac=float((ic > 0).mean()))
    if K is not None:
        R = residualise(F, rows, K)
        nic = daily(ic_series(R, rows))
        res.update(novel_ic=float(nic.mean()), novel_ci=boot_ci(nic.to_numpy()))
    if with_ls:
        ls = long_short(F, rows)
        day = ls.groupby(ls.index // D_).sum()                   # 3 steps of 8h = one day
        dn, dh = day["net"], day["net_hedged"]
        res.update(ls_net_day=float(dn.mean()), ls_net_ci=boot_ci(dn.to_numpy()), ls_gross_day=float(day["gross"].mean()),
                   ls_sharpe=float(dn.mean() / dn.std() * np.sqrt(365)), ls_hedged_day=float(dh.mean()),
                   ls_hedged_ci=boot_ci(dh.to_numpy()), ls_hedged_sharpe=float(dh.mean() / dh.std() * np.sqrt(365)),
                   turnover_day=float(day["turnover"].mean()))
    return res
