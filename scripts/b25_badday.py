"""B25_BADDAY (registered 2026-10-01, before running). Can we tell a bad day from a good day before it starts?
B24 found strategy-level win/lose forecasts at AUC ~0.5. Here the target is the DAY itself, with richer market state.

Day d = 00:00..24:00 UTC. Features use only data up to the 00:00 bar of day d.
Targets
  DOWN     equal-weight liquid-alt index return < 0
  CRASH    alt index return < -4%
  BIGMOVE  |alt index return| > 3%
  HIVOL    realised hourly vol of the alt index on day d > its trailing-30d median (known at 00:00)
  FADE     days with >= 3 pumps (+10%/1h): mean 4h follow-through < 0     (the F2 failure mode of 09-28)
  VSHLOSS  the VSH book (B20 K4) loses on day d
Features (~40): alt index and BTC returns 1/3/7/30d, realised vol 1/7/30d and ratios, drawdown from 30d high, 7d skew,
  breadth, dispersion, aggregate OI change 1/7d, mean funding and share of coins with hot funding, long/short ratios,
  taker ratio, kimchi premium level and change, Upbit volume share and change, volume surprise, pump count and follow-through,
  exchange inflow surprise, weekday, and yesterday's value of each target (persistence).
Models: logistic regression (L2) and LightGBM; walk-forward quarterly refits from 2025-01-01, training from 2024-05,
  target day strictly before the test block. Baselines: persistence (yesterday's label) for each target.
Pass (per target): OOS AUC weekly-block bootstrap CI lower bound > 0.55 AND above persistence.
Applications (fixed before running; judged on OOS Sharpe in BOTH 2025 and 2026):
  A1 alt-long risk switch: long the alt index when P(DOWN) < 0.5, else cash
  A2 de-risk: half size on days with P(BIGMOVE or CRASH) above the training median, for alt-long, VSH, and PFOLLOW
  A3 pump gate: PFOLLOW only on days with P(FADE) < 0.5
Output data/reports/b25/badday.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b25"
CD = ROOT / "data/cache/disc"
D = 86400
TRAIN0 = int(pd.Timestamp("2024-05-01").timestamp()) // D
BLOCKS = [int(pd.Timestamp(x).timestamp()) // D for x in ("2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01",
                                                          "2026-04-01", "2026-07-01", "2026-10-01")]
Y2026 = BLOCKS[4]


def P(k):
    return np.load(CD / f"prim_{k}.npy", mmap_mode="r")


def build():
    ts, codes, X = L.data()
    bi = codes.index("BTCUSDT")
    U = (np.nan_to_num(X["dv24"]) >= 5e6) & (X["age"] >= 720)          # no future info
    U[:, bi] = False
    r1 = np.where(U, X["r1"], np.nan)
    idx = pd.Series(np.nan_to_num(np.nanmean(r1, 1)), index=ts)            # alt index hourly log return
    btc = pd.Series(np.nan_to_num(X["r1"][:, bi]), index=ts)
    day = ts // D
    i0 = np.flatnonzero(ts % D == 0)
    cum = idx.cumsum(); bcum = btc.cumsum()
    F = pd.DataFrame(index=day[i0])
    t0 = ts[i0]

    def back(s, h):
        return (s.reindex(t0).to_numpy() - s.reindex(t0 - h * 3600).to_numpy())
    for h, n in ((24, "1d"), (72, "3d"), (168, "7d"), (720, "30d")):
        F[f"alt_{n}"] = back(cum, h); F[f"btc_{n}"] = back(bcum, h)
    for h, n in ((24, "1d"), (168, "7d"), (720, "30d")):
        F[f"vol_{n}"] = idx.rolling(h, min_periods=h // 2).std().reindex(t0).to_numpy()
    F["vol_ratio_1_30"] = F.vol_1d / F.vol_30d; F["vol_ratio_7_30"] = F.vol_7d / F.vol_30d
    F["btc_vol7"] = btc.rolling(168, min_periods=84).std().reindex(t0).to_numpy()
    F["dd_30d"] = (cum - cum.rolling(720, min_periods=360).max()).reindex(t0).to_numpy()
    F["skew_7d"] = idx.rolling(168, min_periods=84).skew().reindex(t0).to_numpy()
    lc = X["lc"]; r24 = lc - L.lag(lc, 24)
    F["breadth"] = np.nanmean(np.where(U, r24 > 0, np.nan), 1)[i0]
    F["disp"] = np.nanstd(np.where(U, r24, np.nan), 1)[i0]
    oi = np.where(U, np.asarray(P("m_oi_usd")), np.nan); oit = pd.Series(np.nansum(oi, 1), index=ts).replace(0, np.nan)
    F["oi_1d"] = np.log(oit.reindex(t0).to_numpy() / oit.reindex(t0 - 24 * 3600).to_numpy())
    F["oi_7d"] = np.log(oit.reindex(t0).to_numpy() / oit.reindex(t0 - 168 * 3600).to_numpy())
    f8 = np.where(U, X["f8"], np.nan)
    F["funding"] = np.nanmean(f8, 1)[i0]; F["funding_hot"] = np.nanmean(np.where(U, X["f8"] > 0.0003, np.nan), 1)[i0]
    for k in ("m_ls_global", "m_ls_top_pos", "m_taker_ratio"):
        a = np.nanmean(np.where(U, np.log(np.asarray(P(k))), np.nan), 1)
        s = pd.Series(a, index=ts).rolling(24, min_periods=6).mean()
        F[k] = s.reindex(t0).to_numpy(); F[f"{k}_chg"] = F[k] - s.reindex(t0 - 168 * 3600).to_numpy()
    kp = np.where(U, np.asarray(P("up_lc")) - lc, np.nan)
    kps = pd.Series(np.nanmedian(kp, 1), index=ts).rolling(24, min_periods=6).mean()
    F["kimchi"] = kps.reindex(t0).to_numpy(); F["kimchi_chg"] = F.kimchi - kps.reindex(t0 - 168 * 3600).to_numpy()
    uq = pd.Series(np.nansum(np.where(U, np.asarray(P("up_qv")), np.nan), 1), index=ts).rolling(24).sum()
    pq = pd.Series(np.nansum(np.where(U, np.nan_to_num(X["qv"]), np.nan), 1), index=ts).rolling(24).sum()
    sh = uq / pq
    F["upbit_share"] = sh.reindex(t0).to_numpy(); F["upbit_share_chg"] = F.upbit_share - sh.reindex(t0 - 168 * 3600).to_numpy()
    F["vol_surprise"] = np.log(pq.reindex(t0).to_numpy() / pq.rolling(720, min_periods=360).mean().reindex(t0).to_numpy())
    inf = pd.Series(np.nansum(np.where(U, np.asarray(P("oc_inflow")), np.nan), 1), index=ts).rolling(24).sum()
    F["inflow_surprise"] = (inf / inf.rolling(720, min_periods=360).mean()).reindex(t0).to_numpy()
    # pumps
    dv = np.nan_to_num(X["dv24"]); c = X["c"]
    trig = (X["r1"] >= np.log(1.10)) & (dv >= 2e6) & (X["age"] >= 72)
    ti, tj = np.nonzero(trig[:-5]); g = c[ti + 4, tj] / c[ti, tj] - 1; ok = np.isfinite(g); ti, tj, g = ti[ok], tj[ok], g[ok]
    pumps = pd.DataFrame({"ts": ts[ti], "g": g, "cost": 2 * (L.FEE + L.slip(dv[ti, tj]))})
    ct = pumps.ts.to_numpy() + 4 * 3600; o = np.argsort(ct); ct, gg = ct[o], pumps.g.to_numpy()[o]; cs = np.concatenate([[0], np.cumsum(gg)])
    hi = np.searchsorted(ct, t0, side="right")
    for h in (24, 72):
        lo = np.searchsorted(ct, t0 - h * 3600, side="left"); n = hi - lo
        F[f"pump_n{h}"] = n; F[f"pump_follow{h}"] = np.where(n >= 3, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
    F["weekday"] = pd.to_datetime(t0, unit="s").dayofweek
    # targets on day d
    dret = idx.groupby(day).sum(); dvol = idx.groupby(day).std()
    T = pd.DataFrame(index=F.index)
    T["ret"] = dret.reindex(T.index)
    T["DOWN"] = (T.ret < 0).astype(float); T["CRASH"] = (T.ret < np.log(0.96)).astype(float); T["BIGMOVE"] = (T.ret.abs() > 0.03).astype(float)
    med30 = dvol.shift(1).rolling(30, min_periods=15).median()
    T["HIVOL"] = (dvol > med30).astype(float).reindex(T.index)
    pd_ = pumps.assign(d=pumps.ts // D).groupby("d").g.agg(["mean", "size"])
    T["FADE"] = np.where(pd_["size"].reindex(T.index).fillna(0) >= 3, (pd_["mean"].reindex(T.index) < 0).astype(float), np.nan)
    S = pd.read_csv(ROOT / "data/reports/b24/strategy_daily.csv", index_col=0)
    T["VSHLOSS"] = (S["VSH"].reindex(T.index) < 0).astype(float).where(S["VSH"].reindex(T.index).notna())
    # persistence features (yesterday's realised labels are known at 00:00)
    for k in ("DOWN", "CRASH", "BIGMOVE", "HIVOL"):
        F[f"prev_{k}"] = T[k].shift(1)
    F["prev_FADE"] = T["FADE"].shift(1)                    # pumps of day d-1 may close up to 4h into d: use d-2 to be safe
    F["prev_FADE"] = T["FADE"].shift(2)
    F["prev_VSHLOSS"] = T["VSHLOSS"].shift(1)
    strat = pd.DataFrame({"ALTLONG": T.ret.apply(np.expm1), "VSH": S["VSH"].reindex(T.index), "PFOLLOW": S["PFOLLOW"].reindex(T.index)})
    keep = (F.index >= TRAIN0) & (F.index < BLOCKS[-1]) & T.ret.notna().to_numpy()
    return F[keep], T[keep], strat[keep]


def block_auc_ci(y, p, days, reps=2000, rng=np.random.default_rng(25)):
    from sklearn.metrics import roc_auc_score
    wk = days // 7; u = np.unique(wk); g = {w: np.flatnonzero(wk == w) for w in u}
    out = []
    for _ in range(reps):
        ix = np.concatenate([g[w] for w in rng.choice(u, len(u))])
        if len(np.unique(y[ix])) == 2:
            out.append(roc_auc_score(y[ix], p[ix]))
    return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))]


def sharpe(r):
    r = pd.Series(r).dropna()
    return float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0


def main():
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    import lightgbm as lgb
    OUT.mkdir(parents=True, exist_ok=True)
    F, T, strat = build()
    days = F.index.to_numpy()
    targets = ["DOWN", "CRASH", "BIGMOVE", "HIVOL", "FADE", "VSHLOSS"]
    pred = {m: pd.DataFrame(index=F.index, columns=targets, dtype=float) for m in ("LOGIT", "LGBM")}
    med = {t: pd.Series(index=F.index, dtype=float) for t in targets}
    for b0, b1 in zip(BLOCKS[:-1], BLOCKS[1:]):
        tr = days < b0; te = (days >= b0) & (days < b1)
        if not te.any():
            continue
        Xtr, Xte = F[tr], F[te]
        mu, sd = Xtr.mean(), Xtr.std().replace(0, 1)
        Ztr, Zte = ((Xtr - mu) / sd).fillna(0), ((Xte - mu) / sd).fillna(0)
        for t in targets:
            y = T[t][tr]; m = y.notna().to_numpy()
            if y[m].nunique() < 2:
                continue
            lr = LogisticRegression(C=0.1, max_iter=2000).fit(Ztr[m], y[m].astype(int))
            pred["LOGIT"].loc[te, t] = lr.predict_proba(Zte)[:, 1]
            gb = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=25, subsample=0.8,
                                    subsample_freq=1, colsample_bytree=0.7, verbose=-1, random_state=25).fit(Xtr[m], y[m].astype(int))
            pred["LGBM"].loc[te, t] = gb.predict_proba(Xte)[:, 1]
            med[t].loc[te] = float(np.median(gb.predict_proba(Xtr[m])[:, 1]))
        print("block", b0, flush=True)
    oos = days >= BLOCKS[0]
    R = {"targets": {}, "base_rates": {}}
    for t in targets:
        y = T[t][oos]; ok = y.notna().to_numpy()
        yy = y[ok].astype(int).to_numpy(); dd = days[oos][ok]
        R["base_rates"][t] = float(yy.mean())
        res = {}
        pp = F[f"prev_{t}"][oos][ok].fillna(0.5).to_numpy()
        res["PERSIST"] = {"auc": float(roc_auc_score(yy, pp + 1e-9 * np.arange(len(pp)))) if len(np.unique(yy)) == 2 else None}
        for m in ("LOGIT", "LGBM"):
            p = pred[m][t][oos][ok].astype(float).to_numpy()
            fin = np.isfinite(p)
            a = roc_auc_score(yy[fin], p[fin])
            ci = block_auc_ci(yy[fin], p[fin], dd[fin])
            a26 = (dd[fin] >= Y2026)
            res[m] = {"auc": float(a), "ci": ci, "auc_2025": float(roc_auc_score(yy[fin][~a26], p[fin][~a26])),
                      "auc_2026": float(roc_auc_score(yy[fin][a26], p[fin][a26]))}
        best = max(("LOGIT", "LGBM"), key=lambda m: res[m]["auc"])
        res["pass"] = bool(res[best]["ci"][0] > 0.55 and res[best]["auc"] > (res["PERSIST"]["auc"] or 0))
        res["best"] = best
        R["targets"][t] = res
        print(t, json.dumps(res), flush=True)
    # applications (LGBM probabilities)
    pg = pred["LGBM"]
    S = strat[oos].copy(); S["ALTLONG"] = S.ALTLONG - 0.0                        # daily-held index, no turnover cost on hold days
    A = {}
    risk = (pg["BIGMOVE"].astype(float) > med["BIGMOVE"]) | (pg["CRASH"].astype(float) > med["CRASH"])
    sw = (pg["DOWN"].astype(float) < 0.5)
    for yr, sel in (("2025", (S.index < Y2026)), ("2026", (S.index >= Y2026))):
        s = S[sel]; rsk = risk[oos][sel]; swy = sw[oos][sel]
        switch_cost = swy.astype(int).diff().abs().fillna(0) * 0.0012               # in/out of the index ~ fee + slippage
        fg = pg["FADE"][oos][sel].astype(float)
        A[yr] = {
            "ALTLONG": sharpe(s.ALTLONG), "A1_switch": sharpe(np.where(swy, s.ALTLONG, 0.0) - switch_cost),
            "A2_altlong": sharpe(np.where(rsk, 0.5, 1.0) * s.ALTLONG), "VSH": sharpe(s.VSH), "A2_vsh": sharpe(np.where(rsk, 0.5, 1.0) * s.VSH),
            "PFOLLOW": sharpe(s.PFOLLOW), "A2_pfollow": sharpe(np.where(rsk, 0.5, 1.0) * s.PFOLLOW),
            "A3_pfollow_gate": sharpe(np.where(fg.fillna(1) < 0.5, s.PFOLLOW, 0.0)),
            "mean_bp": {"ALTLONG": float(s.ALTLONG.mean() * 1e4), "A1_switch": float((np.where(swy, s.ALTLONG, 0.0) - switch_cost).mean() * 1e4),
                        "PFOLLOW": float(s.PFOLLOW.mean() * 1e4), "A3": float(np.where(fg.fillna(1) < 0.5, s.PFOLLOW, 0.0).mean() * 1e4)}}
    A["pass"] = {k: bool(A["2025"][k] > A["2025"][b] and A["2026"][k] > A["2026"][b]) for k, b in
                 (("A1_switch", "ALTLONG"), ("A2_altlong", "ALTLONG"), ("A2_vsh", "VSH"), ("A2_pfollow", "PFOLLOW"), ("A3_pfollow_gate", "PFOLLOW"))}
    R["applications"] = A
    # what drives it: LightGBM gain importance on the full pre-2026 sample for the best target
    imp = {}
    for t in ("BIGMOVE", "HIVOL", "DOWN", "FADE"):
        m = (days < Y2026) & T[t].notna().to_numpy()
        gb = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=25, verbose=-1, random_state=25,
                                importance_type="gain").fit(F[m], T[t][m].astype(int))
        imp[t] = pd.Series(gb.feature_importances_, index=F.columns).sort_values(ascending=False).head(6).round(1).to_dict()
    R["top_features"] = imp
    json.dump(R, open(OUT / "badday.json", "w"), indent=1, default=float)
    Lm = ["# B25: can we tell a bad day before it starts? (OOS 2025-01..2026-09, walk-forward)", "",
          "| target | base rate | persistence AUC | logistic AUC [CI] | LightGBM AUC [CI] | 2025 / 2026 (best) | pass |", "|---|---|---|---|---|---|---|"]
    for t, r in R["targets"].items():
        b = r[r["best"]]
        Lm.append(f"| {t} | {R['base_rates'][t]:.0%} | {r['PERSIST']['auc']:.3f} | {r['LOGIT']['auc']:.3f} [{r['LOGIT']['ci'][0]:.3f}, {r['LOGIT']['ci'][1]:.3f}] | "
                  f"{r['LGBM']['auc']:.3f} [{r['LGBM']['ci'][0]:.3f}, {r['LGBM']['ci'][1]:.3f}] | {b['auc_2025']:.3f} / {b['auc_2026']:.3f} | {'PASS' if r['pass'] else '-'} |")
    Lm += ["", "## Applications (Sharpe; pass = better than the plain version in both 2025 and 2026)", "", "| | 2025 | 2026 | pass |", "|---|---|---|---|"]
    for k, b in (("A1_switch", "ALTLONG"), ("A2_altlong", "ALTLONG"), ("A2_vsh", "VSH"), ("A2_pfollow", "PFOLLOW"), ("A3_pfollow_gate", "PFOLLOW")):
        Lm.append(f"| {k} vs {b} | {A['2025'][k]:.2f} vs {A['2025'][b]:.2f} | {A['2026'][k]:.2f} vs {A['2026'][b]:.2f} | {A['pass'][k]} |")
    Lm += ["", "## Top drivers (LightGBM gain, pre-2026)", ""] + [f"- {t}: {d}" for t, d in imp.items()]
    (OUT / "badday.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
