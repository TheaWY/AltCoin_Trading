"""B28_DIRECTION (registered 2026-10-01, before running). Broad search for ANY directional predictability, short horizons.

Data: Binance USDT-perp 1m klines, Sep 2025 .. Sep 2026 (data/cache/k1m_oot + k1m), top 50 crypto perps by volume (TradFi
tokens excluded) + BTC, ETH. DISCOVERY = 2025-09-17 .. 2026-02-28, VALIDATION = 2026-03-01 .. 2026-09-24 (read once).
Rows: every coin every 5 minutes. Target: sign of the forward return from the close of minute t to t+H, H in {5, 15, 60, 240} min;
also a 1-minute-delay variant (enter at the close of t+1) for H=5 and 15.
Features (all known at the close of minute t):
  own return 1/5/15/60/240m; BTC return 1/5/15/60m; ETH return 1/5m; equal-weight market return 5/15/60m;
  own minus beta*BTC (catch-up gap) 5/15/60m; taker-buy share 1/5/15/60m; volume surprise 5m and 60m vs 24h;
  trade-count surprise 5m; position in 60m range; realised vol 60m and 1d; minute-of-day (sin/cos), weekday;
  BTC taker-buy share 5m; dispersion of 5m returns across coins.
Models: LightGBM classifier per horizon (pooled over coins), trained on discovery (15-minute subsample), scored on validation;
  logistic regression as a check.
Metrics: AUC with day-block bootstrap CI; trading check: go with the model when |p-0.5| is above the discovery 80th pct,
  mean gross return per trade in bp vs round-trip cost (taker 2x5bp + slippage 2bp for top-50 = 12bp; maker 2x2bp = 4bp).
Single-variable ICs (Spearman, pooled) for interpretation.
Pass (per horizon): validation AUC CI lower bound > 0.52 AND mean gross per selected trade > 12bp (taker) - or > 4bp,
  flagged maker-only.
Output data/reports/b28/direction.{json,md}
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "data/reports/b28"
VAL0 = int(pd.Timestamp("2026-03-01").timestamp())
END = int(pd.Timestamp("2026-09-24").timestamp())
HS = (5, 15, 60, 240)


def universe():
    try:
        info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20).json()["symbols"]
        coin = {x["symbol"] for x in info if x.get("underlyingType", "COIN") == "COIN"}
    except Exception:  # noqa: BLE001
        coin = None
    have = {os.path.basename(f)[:-8] for f in glob.glob(str(ROOT / "data/cache/k1m/*.parquet"))} & \
           {os.path.basename(f)[:-8] for f in glob.glob(str(ROOT / "data/cache/k1m_oot/*.parquet"))}
    vol = []
    for s in have:
        if coin is not None and s not in coin:
            continue
        x = pd.read_parquet(ROOT / f"data/cache/k1m/{s}.parquet", columns=["qv"])
        vol.append((s, float(x.qv.mean())))
    vol.sort(key=lambda r: -r[1])
    syms = [s for s, _ in vol[:52]]
    for must in ("BTCUSDT", "ETHUSDT"):
        if must not in syms:
            syms.append(must)
    return syms


def load(syms):
    t0 = int(pd.Timestamp("2025-09-17").timestamp()); grid = np.arange(t0, END, 60)
    M = {k: np.full((len(grid), len(syms)), np.nan, np.float32) for k in ("c", "qv", "tbq", "n", "h", "l")}
    for j, s in enumerate(syms):
        x = pd.concat([pd.read_parquet(ROOT / f"data/cache/{d}/{s}.parquet") for d in ("k1m_oot", "k1m")]).drop_duplicates("ts")
        x = x[(x.ts >= t0) & (x.ts < END)]
        pos = ((x.ts.to_numpy() - t0) // 60).astype(int)
        for k in M:
            M[k][pos, j] = x[k].to_numpy(np.float32)
    M["c"] = pd.DataFrame(M["c"]).ffill(limit=5).to_numpy(np.float32)
    return grid, M


class RowDict(dict):
    """stores only the evaluation rows of each full-minute array (memory)."""
    def __init__(self, rows, T):
        super().__init__(); self.rows, self.T = rows, T

    def __setitem__(self, k, v):
        if getattr(v, "ndim", 0) == 2 and v.shape[0] == self.T:
            v = np.ascontiguousarray(v[self.rows]).astype(np.float32)
        super().__setitem__(k, v)


def features(grid, M, syms, rows):
    c = M["c"]; lc = np.log(c); N = c.shape[1]
    bi, ei = syms.index("BTCUSDT"), syms.index("ETHUSDT")
    qv = np.nan_to_num(M["qv"]); tb = np.nan_to_num(M["tbq"]); n = np.nan_to_num(M["n"])

    def ret(k):
        out = np.full_like(lc, np.nan); out[k:] = lc[k:] - lc[:-k]; return out

    def rsum(a, k):
        cs = np.cumsum(a, 0, dtype=np.float64); out = np.full(a.shape, np.nan, np.float32); out[k:] = (cs[k:] - cs[:-k]); out[k - 1] = cs[k - 1]; return out
    F = RowDict(rows, len(grid))
    R = {k: ret(k) for k in (1, 5, 15, 60, 240, 1440)}
    for k in (1, 5, 15, 60, 240):
        F[f"r{k}"] = R[k]
    for k in (1, 5, 15, 60):
        F[f"btc_r{k}"] = np.repeat(R[k][:, [bi]], N, 1)
    for k in (1, 5):
        F[f"eth_r{k}"] = np.repeat(R[k][:, [ei]], N, 1)
    for k in (5, 15, 60):
        F[f"mkt_r{k}"] = np.repeat(np.nanmean(R[k], 1, keepdims=True), N, 1)
    # beta to BTC over last 1440 minutes on 5m returns (approx via 1m covariance)
    r1 = np.nan_to_num(R[1]); rb = r1[:, [bi]]
    cov = rsum(r1 * rb, 1440) / 1440 - (rsum(r1, 1440) / 1440) * (rsum(rb, 1440) / 1440)
    var = rsum(rb * rb, 1440) / 1440 - (rsum(rb, 1440) / 1440) ** 2
    beta = cov / np.maximum(var, 1e-12)
    for k in (5, 15, 60):
        F[f"gap{k}"] = R[k] - beta * R[k][:, [bi]]
    for k in (1, 5, 15, 60):
        F[f"taker{k}"] = rsum(tb, k) / np.maximum(rsum(qv, k), 1e-9) - 0.5
    d_qv = rsum(qv, 1440) / 1440
    F["vsur5"] = np.log((rsum(qv, 5) / 5 + 1) / (d_qv + 1)); F["vsur60"] = np.log((rsum(qv, 60) / 60 + 1) / (d_qv + 1))
    F["nsur5"] = np.log((rsum(n, 5) / 5 + 1) / (rsum(n, 1440) / 1440 + 1))
    hi = pd.DataFrame(M["h"]).rolling(60, min_periods=30).max().to_numpy(); lo = pd.DataFrame(M["l"]).rolling(60, min_periods=30).min().to_numpy()
    F["rangepos"] = (c - lo) / np.maximum(hi - lo, 1e-12) - 0.5
    F["rv60"] = np.sqrt(rsum(r1 * r1, 60)); F["rv1d"] = np.sqrt(rsum(r1 * r1, 1440))
    mod = (grid % 86400) / 86400 * 2 * np.pi
    F["tod_sin"] = np.repeat(np.sin(mod)[rows][:, None], N, 1); F["tod_cos"] = np.repeat(np.cos(mod)[rows][:, None], N, 1)
    F["weekday"] = np.repeat((((grid[rows] // 86400) + 3) % 7)[:, None], N, 1).astype(np.float32)
    F["btc_taker5"] = np.repeat(F["taker5"][:, [bi]], N, 1)
    F["disp5"] = np.repeat(np.nanstd(R[5], 1, keepdims=True), N, 1)
    Y = RowDict(rows, len(grid))
    for H in HS:
        f = np.full_like(lc, np.nan); f[:-H] = lc[H:] - lc[:-H]; Y[H] = f
    for H in (5, 15):
        f = np.full_like(lc, np.nan); f[:-(H + 1)] = lc[H + 1:] - lc[1:-H]; Y[f"{H}d"] = f       # enter one minute later
    return F, Y


def block_ci(y, p, day, reps=500, rng=np.random.default_rng(28)):
    from sklearn.metrics import roc_auc_score
    u = np.unique(day); idx = {d: np.flatnonzero(day == d) for d in u}; out = []
    for _ in range(reps):
        ix = np.concatenate([idx[d] for d in rng.choice(u, len(u))])
        out.append(roc_auc_score(y[ix], p[ix]))
    return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))]


def main():
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    OUT.mkdir(parents=True, exist_ok=True)
    syms = universe(); print("universe", len(syms), syms[:10], flush=True)
    grid, M = load(syms)
    rows = np.flatnonzero((grid % 300 == 0) & (np.arange(len(grid)) >= 1440))
    F, Y = features(grid, M, syms, rows)
    del M
    names = list(F)
    T = len(rows); N = len(syms)
    Xall = np.stack([F[k].reshape(-1) for k in names], 1).astype(np.float32)
    ts_r = np.repeat(grid[rows], N); sym_r = np.tile(np.arange(N), T)
    del F
    disc = ts_r < VAL0; val = ts_r >= VAL0
    train_sub = disc & ((ts_r % 900) == 0)
    R = {"universe": syms, "n_rows": int(len(ts_r)), "horizons": {}}
    for H in list(HS) + ["5d", "15d"]:
        y = Y[H].reshape(-1)
        ok = np.isfinite(y) & np.isfinite(Xall).all(1) & (y != 0)
        yb = (y > 0).astype(int)
        tr, te = train_sub & ok, val & ok
        gb = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=500, subsample=0.7, subsample_freq=1,
                                colsample_bytree=0.7, reg_lambda=5.0, verbose=-1, random_state=28, n_jobs=6).fit(Xall[tr], yb[tr])
        p = gb.predict_proba(Xall[te])[:, 1]
        mu, sd = Xall[tr].mean(0), Xall[tr].std(0) + 1e-9
        lr = LogisticRegression(C=0.01, max_iter=500).fit((Xall[tr] - mu) / sd, yb[tr])
        pl = lr.predict_proba((Xall[te] - mu) / sd)[:, 1]
        day = ts_r[te] // 86400
        auc = float(roc_auc_score(yb[te], p)); ci = block_ci(yb[te], p, day)
        pd_ = gb.predict_proba(Xall[disc & ok & ((ts_r % 900) == 300)])[:, 1]
        thr = float(np.percentile(np.abs(pd_ - 0.5), 80))
        sel = np.abs(p - 0.5) > thr
        side = np.sign(p - 0.5)
        gross = side[sel] * y[te][sel]
        is_btc = sym_r[te] == syms.index("BTCUSDT")
        r = {"auc_lgbm": auc, "ci": ci, "auc_logit": float(roc_auc_score(yb[te], pl)), "n_val": int(te.sum()),
             "share_up": float(yb[te].mean()), "trades": int(sel.sum()), "gross_bp_per_trade": float(gross.mean() * 1e4),
             "hit_rate": float((gross > 0).mean()), "auc_btc_only": float(roc_auc_score(yb[te][is_btc], p[is_btc])),
             "auc_by_month": {m: float(roc_auc_score(yb[te][mm], p[mm])) for m in np.unique(pd.to_datetime(ts_r[te], unit="s").strftime("%Y-%m"))
                              for mm in [pd.to_datetime(ts_r[te], unit="s").strftime("%Y-%m") == m]},
             "top_features": dict(sorted(zip(names, gb.booster_.feature_importance("gain").round(0).tolist()), key=lambda kv: -kv[1])[:8])}
        r["pass_taker"] = bool(ci[0] > 0.52 and r["gross_bp_per_trade"] > 12)
        r["pass_maker_only"] = bool(ci[0] > 0.52 and r["gross_bp_per_trade"] > 4)
        # single-variable ICs on validation
        ic = {}
        yv = y[te]
        for k_i, k in enumerate(names):
            x = Xall[te][:, k_i]
            ic[k] = float(pd.Series(x).corr(pd.Series(yv), method="spearman")) if np.nanstd(x) > 0 else 0.0
        r["top_ic"] = dict(sorted(ic.items(), key=lambda kv: -abs(kv[1]))[:8])
        R["horizons"][str(H)] = r
        print(H, json.dumps({k: v for k, v in r.items() if k not in ("auc_by_month", "top_ic", "top_features")}), flush=True)
        json.dump(R, open(OUT / "direction.json", "w"), indent=1, default=float)
    Lm = ["# B28: can we predict direction? 1-minute data, top-50 perps, validation Mar-Sep 2026", "",
          "| horizon | AUC LightGBM [CI] | AUC logistic | BTC only | confident trades | gross bp/trade | hit rate | pass taker (12bp) | maker only (4bp) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for H, r in R["horizons"].items():
        lab = f"{H}m" if not H.endswith("d") else f"{H[:-1]}m, 1m delay"
        Lm.append(f"| {lab} | {r['auc_lgbm']:.3f} [{r['ci'][0]:.3f}, {r['ci'][1]:.3f}] | {r['auc_logit']:.3f} | {r['auc_btc_only']:.3f} | {r['trades']:,} | {r['gross_bp_per_trade']:+.1f} | {r['hit_rate']:.1%} | {r['pass_taker']} | {r['pass_maker_only']} |")
    Lm += ["", "## What drives it (validation Spearman IC, top 8 per horizon)", ""]
    for H, r in R["horizons"].items():
        Lm.append(f"- {H}: " + ", ".join(f"{k} {v:+.3f}" for k, v in r["top_ic"].items()))
    Lm += ["", "## AUC by month (stability)", ""]
    for H, r in R["horizons"].items():
        Lm.append(f"- {H}: " + ", ".join(f"{m[2:]} {v:.3f}" for m, v in r["auc_by_month"].items()))
    (OUT / "direction.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
