"""B36 (2026-10-03): early detection of SAND/POD-class moves as a classification problem.

Event = a coin going a further >= +15% within 4 hours AFTER an onset hour. Onset = hourly close up >= +5% on the hour
with hourly quote volume >= 3x its prior-24h average (the cheapest possible trigger, fired by ~every big move and by many
fakes). Question: at the onset close, which features tell the ones that continue from the ones that fade, out of sample?

Data: the hourly research panel (b7_lib.data, 2024-03 .. 2026-09, 813 perps) + Upbit/Bithumb hourly KRW volume and close
from the research cache + metrics_5m (OI, long/short, taker; from 2026-03) + coinalyze liquidations (from 2026-06) +
exchange_notices (Upbit/Binance). Labels use highs/lows, entries use closes, so nothing in a feature is after the onset close.

Split: train onsets before 2025-07-01, HOLDOUT from 2025-07-01 (same convention as the rest of the research).
Model: LightGBM (shallow, regularised) + a depth-2 decision tree for a human-readable rule + single-feature screens.
Economics: 4h hold from the onset close, -3% stop on hourly lows, 15 bp round trip. Reported for all onsets, for the top
decile / quintile by predicted probability, and for the tree rule, holdout only.
Output: research/b36_event_detect.md, data/reports/b36/onsets.parquet (every onset with features + label).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b30_rigorous as B30  # noqa: E402

OUT = ROOT / "data/reports/b36"; OUT.mkdir(parents=True, exist_ok=True)
HOLD_TS = 1751328000  # 2025-07-01
ONSET_R1, ONSET_VOLX, LABEL_UP, STOP, COST = 0.05, 3.0, 0.15, 0.03, 0.0015


def roll_mean(a, w):
    return pd.DataFrame(a).rolling(w, min_periods=max(2, w // 2)).mean().to_numpy()


def main():
    t0 = time.time()
    ts, codes, X = L.data()
    lc, c, h, l, qv, tbq, n = (X[k].astype(np.float64) for k in ("lc", "c", "h", "l", "qv", "tbq", "n"))
    U = X["U"]; T, N = lc.shape
    # ---------------- labels: forward 4h max/min from the onset close
    fwd_max = np.full_like(lc, -np.inf); fwd_min = np.full_like(lc, np.inf); c4 = np.full_like(lc, np.nan)
    for k in range(1, 5):
        hs = np.roll(h, -k, 0); hs[-k:] = np.nan; fwd_max = np.fmax(fwd_max, hs)
        ls = np.roll(l, -k, 0); ls[-k:] = np.nan; fwd_min = np.fmin(fwd_min, ls)
    c4[:-4] = c[4:]
    up4 = fwd_max / c - 1; dn4 = fwd_min / c - 1; r4 = c4 / c - 1
    # ---------------- onset trigger
    r1 = np.exp(lc - L.lag(lc, 1)) - 1
    volx = qv / np.maximum(roll_mean(L.lag(qv, 1), 24), 1)
    onset = (r1 >= ONSET_R1) & (volx >= ONSET_VOLX) & U & np.isfinite(up4) & (ts[:, None] >= B30.START)
    # one onset per coin per 4h: drop onsets within 4h after another onset of the same coin
    keep = onset.copy()
    for j in range(N):
        idx = np.flatnonzero(onset[:, j]); last = -99
        for i in idx:
            if i - last < 4:
                keep[i, j] = False
            else:
                last = i
    onset = keep
    print("onsets", int(onset.sum()), "positives", int((onset & (up4 >= LABEL_UP)).sum()), f"{time.time() - t0:.0f}s", flush=True)
    # ---------------- features at the onset close (all from <= t)
    F = {}
    F["r1"] = r1; F["r3"] = np.exp(lc - L.lag(lc, 3)) - 1; F["r24"] = np.exp(lc - L.lag(lc, 24)) - 1
    F["r7d"] = np.exp(lc - L.lag(lc, 168)) - 1; F["r30d"] = np.exp(lc - L.lag(lc, 720)) - 1
    F["volx"] = volx; F["volx_prev"] = L.lag(volx, 1); F["vol_build"] = roll_mean(L.lag(qv, 1), 3) / np.maximum(roll_mean(L.lag(qv, 4), 24), 1)
    F["taker_1h"] = tbq / np.maximum(qv, 1); F["taker_24h"] = L.S(tbq, 24) / np.maximum(L.S(qv, 24), 1)
    F["trade_size"] = qv / np.maximum(n, 1) / np.maximum(L.S(qv, 24) / np.maximum(L.S(n, 24), 1), 1e-9)
    F["dv24"] = np.log1p(X["dv24"].astype(np.float64)); F["age_d"] = X["age"].astype(np.float64) / 24
    hi30 = pd.DataFrame(c).rolling(720, min_periods=100).max().to_numpy(); F["dd30"] = c / hi30 - 1
    F["rv7d"] = np.sqrt(roll_mean(X["r1"].astype(np.float64) ** 2, 168)); F["f8"] = X["f8"].astype(np.float64)
    F["beta"] = X["beta"].astype(np.float64); F["corr_btc"] = X["corr_btc"].astype(np.float64)
    F["mkt_r1"] = np.repeat(X["mkt_r1"].astype(np.float64), N, 1); F["mkt_r24"] = np.repeat(L.S(X["mkt_r1"].astype(np.float64), 24), N, 1)
    F["breadth"] = np.repeat(np.nanmean(np.where(U, r1 > 0, np.nan), 1, keepdims=True), N, 1)
    F["n_bursting"] = np.repeat(((r1 >= 0.05) & U).sum(1, keepdims=True).astype(float), N, 1)
    F["hour_kst"] = np.repeat((((ts + 32400) % 86400) // 3600)[:, None].astype(float), N, 1)
    F["dow"] = np.repeat((((ts // 86400) + 3) % 7)[:, None].astype(float), N, 1)
    # Korea (research cache)
    up_qv = np.nan_to_num(np.asarray(B30.P("up_qv")).astype(np.float64)); bt_qv = np.nan_to_num(np.asarray(B30.P("bt_qv")).astype(np.float64))
    up_lc = np.asarray(B30.P("up_lc")).astype(np.float64)
    F["upbit_share_1h"] = up_qv / np.maximum(up_qv + qv, 1); F["upbit_share_24h"] = L.S(up_qv, 24) / np.maximum(L.S(up_qv + qv, 24), 1)
    F["upbit_share_chg"] = F["upbit_share_1h"] - F["upbit_share_24h"]; F["bithumb_share_1h"] = bt_qv / np.maximum(bt_qv + qv, 1)
    F["on_upbit"] = (L.S(up_qv, 168) > 0).astype(float); F["kimchi"] = up_lc - lc
    F["kimchi_chg"] = F["kimchi"] - L.lag(F["kimchi"], 24)
    # DB sources (metrics_5m hourly, coinalyze liq, notices)
    from src.data.storage import get_storage
    from oi_drop_short_paper import q
    import ar_ext
    st = get_storage()
    hcol = {c_: j for j, c_ in enumerate(codes)}; row = {int(t): i for i, t in enumerate(ts)}
    m = q(st, "SELECT replace(symbol,'/','') AS code, (ts/3600)*3600 AS h, avg(oi_usd) AS oi, avg(ls_global) AS lsg, avg(ls_top_pos) AS lst, avg(taker_ratio) AS tk FROM metrics_5m GROUP BY 1, 2")
    def grid(frame, col, tcol="h", ccol="code"):
        A = np.full((T, N), np.nan); ii = frame[tcol].map(row); jj = frame[ccol].map(hcol); ok = ii.notna() & jj.notna()
        A[ii[ok].astype(int), jj[ok].astype(int)] = frame.loc[ok, col].astype(float); return A
    for k in ("lsg", "lst", "tk"):
        F[k] = grid(m, k)
    oi = grid(m, "oi"); F["oi_chg_1h"] = oi / L.lag(oi, 1) - 1; F["oi_chg_24h"] = oi / L.lag(oi, 24) - 1
    g = ar_ext.coinalyze_grid(st, ts, codes)
    F["liq_long_1h"] = g["liq_long"] / np.maximum(g["oi_cz"], 1); F["liq_short_1h"] = g["liq_short"] / np.maximum(g["oi_cz"], 1)
    ev = q(st, "SELECT source, kind, ts, symbols FROM exchange_notices WHERE symbols IS NOT NULL AND symbols <> ''")
    notice = np.zeros((T, N)); notice_kind = {}
    for _, e in ev.iterrows():
        i = row.get(int(e.ts) // 3600 * 3600)
        if i is None:
            continue
        for s in str(e.symbols).split(","):
            j = hcol.get(s.strip() + "USDT")
            if j is not None:
                notice[max(0, i - 1):i + 2, j] = 1 if e.source == "upbit" else 0.5
    F["notice"] = notice
    # ---------------- assemble onset table
    ii, jj = np.nonzero(onset)
    df = pd.DataFrame({"ts": ts[ii], "code": np.array(codes)[jj], "up4": up4[ii, jj], "dn4": dn4[ii, jj], "r4": r4[ii, jj]})
    for k, A in F.items():
        if A is not None:
            df[k] = A[ii, jj]
    df["y"] = (df.up4 >= LABEL_UP).astype(int)
    # economic outcome of a 4h long from the onset close with a -3% stop on hourly lows (conservative: stop first)
    df["pnl"] = np.where(df.dn4 <= -STOP, -STOP, df.r4) - COST
    df["hold"] = df.ts >= HOLD_TS
    df.to_parquet(OUT / "onsets.parquet", index=False)
    feats = [k for k in F if F[k] is not None]
    tr, te = df[~df.hold], df[df.hold]
    print("train", len(tr), "pos", int(tr.y.sum()), "| holdout", len(te), "pos", int(te.y.sum()), f"{time.time() - t0:.0f}s", flush=True)
    Lm = ["# B36 - early detection of +15%-in-4h continuations after a volume burst (hourly, 2024-05..2026-09)", "",
          f"Onset = hourly close >= +5% with volume >= 3x prior-24h mean, one per coin per 4h, in-universe. Label = a further >= +15% (high) within 4h. "
          f"Train < 2025-07-01: {len(tr)} onsets, {int(tr.y.sum())} positives ({tr.y.mean():.1%}). HOLDOUT >= 2025-07-01: {len(te)} onsets, {int(te.y.sum())} positives ({te.y.mean():.1%}). "
          f"Economics = long at the onset close, -3% stop on hourly lows, 4h exit, 15 bp cost.", ""]
    # ---- baseline economics
    def econ(x, label):
        if len(x) < 20:
            return f"| {label} | {len(x)} | – | – | – | – |"
        b = np.array([x.pnl.sample(len(x), replace=True).mean() for _ in range(1000)])
        return f"| {label} | {len(x)} | {x.y.mean():.1%} | {x.pnl.mean() * 100:+.2f}% | [{np.percentile(b, 2.5) * 100:+.2f}, {np.percentile(b, 97.5) * 100:+.2f}] | {(x.pnl > 0).mean():.2f} |"
    Lm += ["## Economics (holdout)", "", "| subset | n | P(+15%) | mean 4h net | 95% CI | hit |", "|---|---|---|---|---|---|", econ(te, "all onsets")]
    # ---- single-feature screens on train, reported on holdout (top/bottom quintile)
    from scipy.stats import spearmanr
    scr = []
    for f in feats:
        a = tr[[f, "y", "pnl"]].dropna()
        if len(a) < 200 or a[f].nunique() < 3:
            continue
        rho, p = spearmanr(a[f], a.y)
        b = te[[f, "y", "pnl"]].dropna()
        if len(b) < 100:
            continue
        hi = b[b[f] >= a[f].quantile(0.8)]; lo = b[b[f] <= a[f].quantile(0.2)]
        scr.append((f, rho, p, len(a), hi.y.mean() if len(hi) > 20 else np.nan, lo.y.mean() if len(lo) > 20 else np.nan, hi.pnl.mean() if len(hi) > 20 else np.nan, lo.pnl.mean() if len(lo) > 20 else np.nan, len(hi), len(lo)))
    scr = sorted(scr, key=lambda r: -abs(r[1]))
    Lm += ["", "## Single-feature screens (rank corr. with the label on TRAIN; quintile cut-offs from train, outcomes on HOLDOUT)", "",
           "| feature | rho (train) | p | n train | P(+15%) top quintile | bottom quintile | net top | net bottom | n top / bottom |", "|---|---|---|---|---|---|---|---|---|"]
    for r in scr:
        Lm.append(f"| {r[0]} | {r[1]:+.3f} | {r[2]:.3f} | {r[3]} | {r[4]:.1%} | {r[5]:.1%} | {r[6] * 100:+.2f}% | {r[7] * 100:+.2f}% | {r[8]} / {r[9]} |")
    # ---- LightGBM
    try:
        import lightgbm as lgb
        from sklearn.metrics import roc_auc_score, average_precision_score
        Xtr, Xte = tr[feats].to_numpy(), te[feats].to_numpy()
        mdl = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03, num_leaves=15, min_child_samples=100, subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, verbose=-1, n_jobs=4)
        mdl.fit(Xtr, tr.y)
        pte = mdl.predict_proba(Xte)[:, 1]; ptr = mdl.predict_proba(Xtr)[:, 1]
        auc = roc_auc_score(te.y, pte); ap = average_precision_score(te.y, pte)
        Lm += ["", "## LightGBM (shallow, regularised) trained on TRAIN, scored on HOLDOUT", "",
               f"holdout AUC {auc:.3f} (train {roc_auc_score(tr.y, ptr):.3f}), average precision {ap:.3f} vs base rate {te.y.mean():.3f}", "",
               "| subset by predicted P | n | P(+15%) | mean 4h net | 95% CI | hit |", "|---|---|---|---|---|---|"]
        te2 = te.assign(p=pte)
        for qq in (0.5, 0.8, 0.9, 0.95):
            thr = np.quantile(ptr, qq); Lm.append(econ(te2[te2.p >= thr], f"p >= train q{int(qq * 100)} ({thr:.3f})"))
        Lm.append(econ(te2[te2.p < np.quantile(ptr, 0.5)], "p < train median"))
        imp = sorted(zip(feats, mdl.booster_.feature_importance("gain")), key=lambda x: -x[1])[:15]
        tot = sum(v for _, v in imp) or 1
        Lm += ["", "top features by gain: " + ", ".join(f"{k} {v / tot:.0%}" for k, v in imp)]
        # calendar stability of the top-decile rule on holdout
        thr = np.quantile(ptr, 0.9); x = te2[te2.p >= thr].copy(); x["month"] = pd.to_datetime(x.ts, unit="s").dt.strftime("%Y-%m")
        Lm += ["", "top-decile (train q90) rule by holdout month: n / mean net:", ", ".join(f"{m} {len(g)}/{g.pnl.mean() * 100:+.1f}%" for m, g in x.groupby("month"))]
    except Exception as e:  # noqa: BLE001
        Lm += ["", f"LightGBM step failed: {type(e).__name__}: {e}"]
    # ---- depth-2 tree for a readable rule
    try:
        from sklearn.tree import DecisionTreeClassifier, export_text
        a = tr[feats + ["y"]].fillna(tr[feats].median())
        t = DecisionTreeClassifier(max_depth=2, min_samples_leaf=300, class_weight="balanced", random_state=0).fit(a[feats], a.y)
        leaf_tr = t.apply(a[feats]); leaf_te = t.apply(te[feats].fillna(tr[feats].median()))
        Lm += ["", "## Depth-2 tree (train), leaves scored on holdout", "", "```", export_text(t, feature_names=feats, decimals=3), "```", "",
               "| leaf | n train | P train | n holdout | P holdout | net holdout |", "|---|---|---|---|---|---|"]
        for lf in np.unique(leaf_tr):
            A_ = tr[leaf_tr == lf]; B_ = te[leaf_te == lf]
            Lm.append(f"| {lf} | {len(A_)} | {A_.y.mean():.1%} | {len(B_)} | {B_.y.mean() if len(B_) else float('nan'):.1%} | {B_.pnl.mean() * 100 if len(B_) else float('nan'):+.2f}% |")
    except Exception as e:  # noqa: BLE001
        Lm += ["", f"tree step failed: {type(e).__name__}: {e}"]
    Lm += ["", f"({time.time() - t0:.0f}s)"]
    (ROOT / "research/b36_event_detect.md").write_text("\n".join(Lm))
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
