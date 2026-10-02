"""D-series: market DIRECTION battery (2026-10-02). Every test here scores one thing: the SIGN of the whole-market
return over the next h hours (h in 1, 4, 24, 168), from a market-wide predictor known at t.

Why: the cross-sectional engine (ar_engine xs_sort/layered/...) predicts RELATIVE direction (which coins beat which).
This module predicts the AGGREGATE direction, where the literature finds signal only at the fast end (order flow,
lead-lag) and the slow end (variance risk premium, aggregate carry). Each variable carries an a-priori sign.

Battery per (variable, horizon), in-sample 2024-05..2025-06 reported, HOLDOUT 2025-07.. decides:
  slope       NW (HAC, lag >= h) t of  fwd_h ~ z_t                      -> holdout_fm_t (feeds the engine-wide BHY)
  PT          Pesaran-Timmermann sign test of sign(z_t) vs sign(fwd_h)  -> holdout_pt_hit / holdout_pt_p
  AUC         ROC AUC of z_t for fwd_h > 0 with circular block bootstrap 95% CI -> holdout_auc / holdout_auc_lo
  OOS         Goyal-Welch recursive OLS from the in-sample start, R2_OS vs historical mean (Campbell-Thompson),
              Clark-West p                                            -> holdout_r2os / holdout_cw_p
  economic    sign(forecast) timing of the EW market at non-overlapping h steps, 10 bp per flip; Sharpe vs buy-hold,
              Campbell-Thompson mean-variance utility gain (gamma = 3) -> holdout_sharpe_net / holdout_bh_sharpe / holdout_ct_gain
  ALL         recursive ridge on every variable (no in-sample weights) -> the "market as a whole" forecast

PASS (ts_direction): holdout slope t >= 2 with the a-priori sign AND BHY AND in-sample slope > 0 AND PT p < 0.05 AND
AUC CI low > 0.5 AND Clark-West p < 0.05 AND CT utility gain > 0.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import aphx as A
import b7_lib as L

ROOT = Path(__file__).resolve().parents[1]
HORIZONS = {"1h": 1, "4h": 4, "24h": 24, "1w": 168}
GAMMA, COST_BP = 3.0, 10.0


def _roll(a, w, fn="mean", mp=None):
    return getattr(pd.Series(np.asarray(a, float)).rolling(w, min_periods=mp or max(2, w // 2)), fn)().to_numpy()


def _U(P, a):
    return np.where(P["U"], a, np.nan)


def mkt(P):
    """EW market log return per hour over the point-in-time universe U (cached)."""
    if "_mkt" not in P:
        with np.errstate(all="ignore"):
            P["_mkt"] = np.nanmean(_U(P, P["r1"]), 1)
    return P["_mkt"]


def mkt_ret(P, w):
    return pd.Series(mkt(P)).rolling(w, min_periods=w).sum().to_numpy()


def _ret(P, w):
    return P["lc"] - L.lag(P["lc"], w)


def _grp_ret(P, key, w, top=True, q=0.2):
    """EW w-hour return of the top (or bottom) q of the universe ranked on key (ranked at t-w, so no look-ahead)."""
    k = L.lag(np.where(P["U"], key, np.nan), w)
    rk = pd.DataFrame(k).rank(axis=1, pct=True).to_numpy()
    sel = (rk >= 1 - q) if top else (rk <= q)
    with np.errstate(all="ignore"):
        return np.nanmean(np.where(sel & P["U"], _ret(P, w), np.nan), 1)


def _dvol(P):
    if "_dvol" not in P:
        d = pd.read_parquet(ROOT / "data/cache/deribit_dvol_btc_1d.parquet")
        day = (d["ts"].to_numpy() // 1000) // 86400
        m = dict(zip(day, d["c"].to_numpy(float)))
        hd = P["ts"] // 86400 - 1                      # the DVOL close of day d is known from day d+1 00:00 UTC
        P["_dvol"] = np.array([m.get(x, np.nan) for x in hd])
    return P["_dvol"]


def _rv_ann(P, w):
    b = P["r1"][:, P["btc"]]
    return _roll(b ** 2, w) * 24 * 365


# name: (sign, family, builder(P) -> hourly T vector, source)
DVARS = {
    "vrp_30d": (+1, "riskprem", lambda P: (_dvol(P) / 100) ** 2 - _rv_ann(P, 720), "Bollerslev-Tauchen-Zhou 2009 variance risk premium; crypto: Alexander-Imeraj 2023"),
    "dvol_level": (+1, "riskprem", lambda P: _dvol(P), "risk-return trade-off: high implied vol -> higher expected return (Merton 1980)"),
    "agg_funding_24h": (-1, "riskprem", lambda P: _roll(np.nanmean(_U(P, P["f8"]), 1), 24), "Schmeling-Schrimpf-Todorov 2023 crypto carry: high aggregate funding -> crash risk"),
    "agg_funding_chg_7d": (-1, "riskprem", lambda P: _roll(np.nanmean(_U(P, P["f8"]), 1), 24) - _roll(np.nanmean(_U(P, P["f8"]), 1), 168), "carry build-up (STT 2023)"),
    "agg_oi_chg_24h": (-1, "flow", lambda P: np.nansum(_U(P, P["oi"]), 1) / L.lag(np.nansum(_U(P, P["oi"]), 1)[:, None], 24)[:, 0] - 1, "OI build-up = leverage crowding -> reversal (Schmeling et al. 2023)"),
    "breadth_20d": (+1, "breadth", lambda P: np.nanmean(_U(P, (P["lc"] - L.M(P["lc"], 480)) > 0), 1), "breadth momentum (Chen-Hong-Stein 2002 transplant)"),
    "dispersion_24h": (-1, "breadth", lambda P: np.nanstd(_U(P, _ret(P, 24)), 1), "cross-sectional dispersion = uncertainty -> lower market return (Stivers-Sun 2010)"),
    "xs_skew_24h": (-1, "breadth", lambda P: pd.DataFrame(_U(P, _ret(P, 24))).skew(axis=1).to_numpy(), "lottery demand peaks before drawdowns (Bali-Cakici-Whitelaw 2011 aggregate)"),
    "btc_minus_alts_24h": (+1, "leadlag", lambda P: _ret(P, 24)[:, P["btc"]] - np.nanmean(_U(P, _ret(P, 24)), 1), "BTC leads alts (Lo-MacKinlay 1990 large->small diffusion)"),
    "large_minus_small_24h": (+1, "leadlag", lambda P: _grp_ret(P, L.S(P["qv"], 24), 24, True) - _grp_ret(P, L.S(P["qv"], 24), 24, False), "Lo-MacKinlay 1990: large caps lead small caps"),
    "korea_heavy_minus_rest_24h": (-1, "korea", lambda P: _grp_ret(P, L.S(P["up_qv"], 24) / np.maximum(L.S(P["qv"], 24), 1), 24, True) - np.nanmean(_U(P, _ret(P, 24)), 1), "retail-sentiment extreme -> reversal (Baker-Wurgler 2006)"),
    "korea_share_total_24h": (-1, "korea", lambda P: np.nansum(_U(P, L.S(P["up_qv"], 24)), 1) / np.maximum(np.nansum(_U(P, L.S(P["qv"], 24)), 1), 1), "aggregate retail attention high -> lower future market return (Stambaugh-Yu-Yuan 2012)"),
    "agg_taker_buy_24h": (+1, "flow", lambda P: np.nansum(_U(P, L.S(P["tbq"], 24)), 1) / np.maximum(np.nansum(_U(P, L.S(P["qv"], 24)), 1), 1) - 0.5, "aggregate order imbalance persists (Chordia-Subrahmanyam 2004)"),
    "agg_ls_global": (-1, "positioning", lambda P: np.nanmean(_U(P, P["ls_global"]), 1), "crowded retail longs -> lower returns"),
    "mkt_mom_7d": (+1, "price", lambda P: mkt_ret(P, 168), "Liu-Tsyvinski 2021 time-series momentum of the crypto market"),
    "mkt_ret_24h": (+1, "price", lambda P: mkt_ret(P, 24), "Liu-Tsyvinski 2021 daily continuation"),
    "mkt_ret_1h": (+1, "price", lambda P: mkt_ret(P, 1), "intraday continuation of aggregate flow"),
    "rv_ratio_24h_720h": (-1, "vol", lambda P: _roll(mkt(P) ** 2, 24) / _roll(mkt(P) ** 2, 720), "vol spike -> lower short-run return (leverage effect)"),
}


def zscore(x, w=4320):
    s = pd.Series(np.asarray(x, float))
    m, sd = s.shift(1).rolling(w, min_periods=w // 3).mean(), s.shift(1).rolling(w, min_periods=w // 3).std()
    return ((s - m) / sd.replace(0, np.nan)).clip(-4, 4).to_numpy()


def fwd(P, h):
    cs = np.cumsum(np.nan_to_num(mkt(P)))
    out = np.full(len(cs), np.nan); out[:-h] = cs[h:] - cs[:-h]
    return out


def _auc(y, f):
    ok = np.isfinite(y) & np.isfinite(f)
    if ok.sum() < 30 or (y[ok] > 0).all() or (y[ok] <= 0).all():
        return np.nan
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score((y[ok] > 0).astype(int), f[ok]))


def auc_ci(y, f, block, B=300, seed=0):
    rng = np.random.default_rng(seed); n = len(y); vals = []
    for _ in range(B):
        starts = rng.integers(0, n, int(np.ceil(n / block)))
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        vals.append(_auc(y[idx], f[idx]))
    vals = np.array([v for v in vals if np.isfinite(v)])
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if len(vals) > 20 else (np.nan, np.nan)


def economic(y, f, hm, h, sel, per_year):
    """sign timing at non-overlapping h steps on the selected rows; 10 bp per position flip."""
    idx = np.flatnonzero(sel & np.isfinite(y) & np.isfinite(f))[::h]
    if len(idx) < 20:
        return {}
    r = y[idx]
    def _timing(sig):
        pos = np.sign(sig); flips = np.abs(np.diff(np.r_[0, pos])) / 2
        return pos * r - flips * COST_BP / 1e4
    net, net_hm = _timing(f[idx]), _timing(hm[idx])          # model sign timing vs historical-mean sign timing (same costs)
    var = pd.Series(y).rolling(max(5, 720 // h)).var().to_numpy()[idx]
    wm = np.clip(f[idx] / (GAMMA * var), -1.5, 1.5); wh = np.clip(hm[idx] / (GAMMA * var), -1.5, 1.5)
    um = np.nanmean(wm * r) - GAMMA / 2 * np.nanvar(wm * r); uh = np.nanmean(wh * r) - GAMMA / 2 * np.nanvar(wh * r)
    return {"sharpe_net": A.sharpe(net, per_year / h), "hm_sharpe": A.sharpe(net_hm, per_year / h), "bh_sharpe": A.sharpe(r, per_year / h),
            "ct_gain": float((um - uh) * per_year / h), "hit_timing": float((np.sign(f[idx]) * r > 0).mean()),
            "flip_share": float((np.sign(f[idx]) != np.sign(hm[idx])).mean()), "n_steps": int(len(idx))}


def run(P, var, hz, start_ts, hold_ts, end_ts):
    h = HORIZONS[hz]
    if var == "ALL":
        Z = np.column_stack([zscore(DVARS[v][0] * np.asarray(DVARS[v][2](P), float)) for v in DVARS]); sign = +1
    else:
        sign, fam, fn, src = DVARS[var]
        with np.errstate(all="ignore"):
            Z = zscore(sign * np.asarray(fn(P), float))
    y = fwd(P, h); ts = P["ts"]
    daily = h >= 24
    rows = (ts % 86400 == 0) if daily else np.ones(len(ts), bool)
    rows &= (ts >= start_ts) & (ts + h * 3600 <= end_ts)
    ins, hold = rows & (ts < hold_ts), rows & (ts >= hold_ts)
    per_year = 365 if daily else 365 * 24
    lag = max(A.nw_lag(int(rows.sum())), (h // 24 if daily else h))
    out = {"n_insample": int(ins.sum()), "n_holdout": int(hold.sum())}
    # recursive forecast: the fit at row i may only use pairs (Z_j, y_j) whose outcome y_j is known at i, i.e. j <= i - step
    i_rows = np.flatnonzero(rows); step = max(1, h // 24) if daily else h
    yy, ZZ = y[i_rows], (Z[i_rows] if var == "ALL" else Z[i_rows][:, None])
    y_known = np.r_[np.full(step, np.nan), yy[:-step]]
    Z_known = np.r_[np.full((step, ZZ.shape[1]), np.nan), ZZ[:-step]]
    start = int(max(60, ins[i_rows].sum() // 4)); min_obs = 60 if daily else 500; ridge = 5.0 if var == "ALL" else 0.0
    f = _recursive_apply(y_known, Z_known, ZZ, start=start, min_obs=min_obs, ridge=ridge)
    hm = pd.Series(y_known).expanding(min_obs).mean().to_numpy()
    F = np.full(len(y), np.nan); HM = np.full(len(y), np.nan); F[i_rows] = f; HM[i_rows] = hm
    zc = F if var == "ALL" else Z
    for tag, sel in (("insample", ins), ("holdout", hold)):
        yv, zv = y[sel], zc[sel]
        ok = np.isfinite(yv) & np.isfinite(zv)
        if ok.sum() < 50:
            out[f"{tag}_fm_t"] = np.nan; continue
        b, t, _ = A.nw_ols(yv[ok], zv[ok], lag=lag)
        out[f"{tag}_fm_t"] = float(t[1]); out[f"{tag}_slope_bp"] = float(b[1] * 1e4)
        yn, zn = yv[ok][::step], zv[ok][::step]                # non-overlapping outcomes for the sign tests
        hit, S, p = A.pesaran_timmermann(yn, zn)
        out[f"{tag}_pt_hit"] = float(hit); out[f"{tag}_pt_p"] = float(p); out[f"{tag}_n_nonoverlap"] = int(len(yn))
        out[f"{tag}_auc"] = _auc(yn, zn)
        out[f"{tag}_auc_lo"], out[f"{tag}_auc_hi"] = auc_ci(yn, zn, block=(7 if daily else max(6, 24 // step)))
        fv, hv = F[sel], HM[sel]
        ok2 = ok & np.isfinite(fv) & np.isfinite(hv)
        if ok2.sum() >= 50:
            out[f"{tag}_r2os"] = A.r2_os(yv[ok2], fv[ok2], hv[ok2])
            out[f"{tag}_cw_t"], out[f"{tag}_cw_p"] = A.clark_west(yv[ok2], hv[ok2], fv[ok2], lag=lag)
            for k, v in economic(y, F, HM, step, sel, per_year).items():
                out[f"{tag}_{k}"] = v
    out["horizon"] = hz; out["sign"] = int(sign)
    return out


def _recursive_apply(y_known, X_known, X_now, start, min_obs, ridge=0.0):
    """expanding-window OLS/ridge refit (daily for hourly samples) on pairs already known at i, applied to X_now[i]."""
    T = len(y_known); f = np.full(T, np.nan)
    ok_all = np.isfinite(y_known) & np.isfinite(X_known).all(1)
    step = 24 if T > 5000 else 1; w = None
    for i in range(start, T):
        if (i - start) % step == 0:
            ok = ok_all[:i]
            if ok.sum() >= min_obs:
                Xm = np.c_[np.ones(ok.sum()), X_known[:i][ok]]; ym = y_known[:i][ok]
                w = np.linalg.solve(Xm.T @ Xm + ridge * np.diag([0] + [1] * X_known.shape[1]), Xm.T @ ym)
        if w is not None and np.isfinite(X_now[i]).all():
            f[i] = w[0] + X_now[i] @ w[1:]
    return f


def verdict(out):
    ok = {"holdout_t2": bool((out.get("holdout_fm_t") or -9) >= 2), "insample_sign": bool((out.get("insample_fm_t") or -9) > 0),
          "pt": bool((out.get("holdout_pt_p") if out.get("holdout_pt_p") is not None else 1) < 0.05),
          "auc": bool((out.get("holdout_auc_lo") or 0) > 0.5), "cw": bool((out.get("holdout_cw_p") if out.get("holdout_cw_p") is not None else 1) < 0.05),
          "econ": bool((out.get("holdout_ct_gain") or -9) > 0 and (out.get("holdout_sharpe_net") or -9) > (out.get("holdout_hm_sharpe") if out.get("holdout_hm_sharpe") is not None else 9))}
    return ok


if __name__ == "__main__":
    import sys, time
    sys.path.insert(0, str(ROOT / "scripts"))
    import ar_engine as E, b30_rigorous as B30
    P = E.panel()
    vars_ = sys.argv[1:] or list(DVARS)
    for v in vars_:
        for hz in HORIZONS:
            t0 = time.time()
            try:
                o = run(P, v, hz, B30.START, B30.HOLD * 86400, B30.END)
                print(f"{v:28s} {hz:4s} in t {o.get('insample_fm_t', np.nan):+5.2f} | hold t {o.get('holdout_fm_t', np.nan):+5.2f} PT hit {o.get('holdout_pt_hit', np.nan):.3f} p {o.get('holdout_pt_p', np.nan):.3f} AUC {o.get('holdout_auc', np.nan):.3f} [{o.get('holdout_auc_lo', np.nan):.3f}] R2os {o.get('holdout_r2os', np.nan):+.4f} CW p {o.get('holdout_cw_p', np.nan):.3f} SR {o.get('holdout_sharpe_net', np.nan):+.2f} vs HM {o.get('holdout_hm_sharpe', np.nan):+.2f} BH {o.get('holdout_bh_sharpe', np.nan):+.2f} CT {o.get('holdout_ct_gain', np.nan):+.3f} flips {o.get('holdout_flip_share', np.nan):.2f} pass {sum(verdict(o).values())}/6  ({time.time() - t0:.0f}s)", flush=True)
            except Exception:  # noqa: BLE001
                import traceback; print(v, hz, "ERROR", traceback.format_exc()[-400:], flush=True)
