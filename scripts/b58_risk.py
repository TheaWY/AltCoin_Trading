"""B58 (prereg v9): predict risk, not return. BTC/ETH sleeve = F15 trend weight x risk overlay.
Forecasts are walk-forward (refit each January on rows whose 7d target ended before it). No tuning (fixed params).
Selection in discovery 2019-2023 only; the holdout 2024-01..2026-10-04 is evaluated for the selected rule."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402
import b52_ride_protect as P  # noqa: E402
import b54_sweep100 as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MAJ = S.MAJ
DISC = ("2019-01-01", "2024-01-01")
HOLD = ("2024-01-01", "2026-10-05")
FEATS = ["lrv1", "lrv7", "lrv30", "lrv90", "lsemi30", "r1", "r7", "r30", "dd60", "dsma50", "dsma200", "vsurge",
         "corr30", "is_eth"]
GBM = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50, subsample=0.8,
           subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=7)


def brake(c):
    cn = c.to_numpy(); sma20 = c.rolling(20, min_periods=20).mean().to_numpy()
    hi20 = c.rolling(21, min_periods=21).max().to_numpy(); out = np.zeros(len(c), bool); on = False
    for t in range(1, len(c)):
        if (cn[t] <= 0.90 * cn[t - 1]) or (np.isfinite(hi20[t]) and cn[t] <= 0.85 * hi20[t]):
            on = True
        elif on and np.isfinite(sma20[t]) and cn[t] > sma20[t]:
            on = False
        out[t] = on
    return pd.Series(out, index=c.index)


def features():
    lrb = np.log(S.CF[MAJ[0]]).diff(); lre = np.log(S.CF[MAJ[1]]).diff()
    corr = lrb.rolling(30, min_periods=30).corr(lre)
    rows = []
    for m in MAJ:
        c = S.CF[m]; lr = np.log(c).diff(); v = S.V[m]
        rv = lambda n: np.sqrt((lr ** 2).rolling(n, min_periods=n).mean())  # noqa: E731
        d = pd.DataFrame(index=c.index)
        d["lrv1"] = np.log(lr.abs() + 1e-4); d["lrv7"] = np.log(rv(7)); d["lrv30"] = np.log(rv(30))
        d["lrv90"] = np.log(rv(90))
        d["lsemi30"] = np.log(np.sqrt((lr.clip(upper=0) ** 2).rolling(30, min_periods=30).mean()) + 1e-5)
        d["r1"], d["r7"], d["r30"] = c.pct_change(), c.pct_change(7), c.pct_change(30)
        d["dd60"] = c / c.rolling(60, min_periods=60).max() - 1
        d["dsma50"] = c / c.rolling(50).mean() - 1; d["dsma200"] = c / c.rolling(200).mean() - 1
        d["vsurge"] = np.log(v / v.rolling(30, min_periods=20).median())
        d["corr30"] = corr; d["is_eth"] = float(m == MAJ[1])
        fwd = pd.concat([lr.shift(-k) for k in range(1, 8)], axis=1)
        d["y_lvol"] = np.log(np.sqrt((fwd ** 2).mean(axis=1, skipna=False)))
        d["y_crash"] = (c.shift(-7) / c - 1 < -0.10).astype(float).where(c.shift(-7).notna())
        d["rv20"] = rv(20); d["coin"] = m; d.index.name = "day"
        rows.append(d.reset_index())
    return pd.concat(rows, ignore_index=True)


def walk_forward(D):
    import lightgbm as lgb
    from sklearn.linear_model import LinearRegression, LogisticRegression
    from sklearn.preprocessing import StandardScaler
    D = D.copy()
    for k in ("V1", "V2", "V3", "V4"):
        D[k] = np.nan
    ok = D[FEATS].notna().all(axis=1)
    for yr in range(2019, 2027):
        start = pd.Timestamp(f"{yr}-01-01")
        tr = ok & (D.day + pd.Timedelta(days=8) < start) & D.y_lvol.notna()
        te = ok & (D.day >= start) & (D.day < pd.Timestamp(f"{yr + 1}-01-01"))
        if not te.any():
            continue
        har = ["lrv1", "lrv7", "lrv30"]
        D.loc[te, "V1"] = LinearRegression().fit(D.loc[tr, har], D.loc[tr, "y_lvol"]).predict(D.loc[te, har])
        D.loc[te, "V2"] = lgb.LGBMRegressor(**GBM).fit(D.loc[tr, FEATS], D.loc[tr, "y_lvol"]).predict(D.loc[te, FEATS])
        trc = tr & D.y_crash.notna()
        sc = StandardScaler().fit(D.loc[trc, FEATS])
        D.loc[te, "V3"] = LogisticRegression(C=0.5, max_iter=2000).fit(
            sc.transform(D.loc[trc, FEATS]), D.loc[trc, "y_crash"]).predict_proba(sc.transform(D.loc[te, FEATS]))[:, 1]
        D.loc[te, "V4"] = lgb.LGBMClassifier(**GBM).fit(
            D.loc[trc, FEATS], D.loc[trc, "y_crash"]).predict_proba(D.loc[te, FEATS])[:, 1]
        print(time.strftime("%T"), "fit", yr, int(tr.sum()), int(te.sum()), flush=True)
    return D


def quality(D, a, z):
    from sklearn.metrics import brier_score_loss, roc_auc_score
    d = D[(D.day >= a) & (D.day < z) & D.y_lvol.notna() & D.V1.notna()]
    s2 = np.exp(2 * d.y_lvol)
    out = {}
    for k, f in (("V0_rv20", np.log(d.rv20)), ("V1_HAR", d.V1), ("V2_LGBM", d.V2)):
        f2 = np.exp(2 * f); q = s2 / f2
        out[k] = dict(qlike=float((q - np.log(q) - 1).mean()),
                      r2=float(1 - ((d.y_lvol - f) ** 2).sum() / ((d.y_lvol - d.y_lvol.mean()) ** 2).sum()))
    c = d[d.y_crash.notna()]
    for k in ("V3", "V4"):
        out[k] = dict(auc=float(roc_auc_score(c.y_crash, c[k])), brier=float(brier_score_loss(c.y_crash, c[k])),
                      base_rate=float(c.y_crash.mean()))
    return out


def overlay_vol(f_log, c):
    f = np.exp(f_log)
    s = (f.rolling(365, min_periods=30).median() / f).clip(upper=1.0)
    w = B.trend_w(c)
    return (w.where(~brake(c), 0.0) * s).where(w.notna() & f.notna())


def cut(base, p):
    thr = p.rolling(365, min_periods=30).quantile(0.8)
    return base * np.where(p > thr, 0.5, 1.0)


def books(D):
    W = {k: S.zeros() for k in ("F17", "R1", "R2", "R3", "R4", "R5")}
    for m in MAJ:
        c = S.CF[m]; d = D[D.coin == m].set_index("day").reindex(c.index)
        f17 = P.p_weight(c)
        r1 = overlay_vol(d.V1, c); r2 = overlay_vol(d.V2, c)
        live = d.V1.notna()
        W["F17"][m] = 0.5 * f17.where(live).fillna(0.0)
        W["R1"][m] = 0.5 * r1.fillna(0.0)
        W["R2"][m] = 0.5 * r2.fillna(0.0)
        W["R3"][m] = 0.5 * cut(f17, d.V3).where(live).fillna(0.0)
        W["R4"][m] = 0.5 * cut(f17, d.V4).where(live).fillna(0.0)
        W["R5"][m] = 0.5 * cut(r1, d.V3).fillna(0.0)
    return W


def stats(r):
    return dict(sharpe=B.sharpe(r), maxdd=B.maxdd(r), cagr=B.cagr(r), worst30=P.worst30(r))


def main():
    t0 = time.time()
    D = walk_forward(features())
    D.to_parquet(ROOT / "data/upbit_db/b58_forecasts.parquet")
    q_dev, q_hold = quality(D, *DISC), quality(D, *HOLD)
    W = books(D)
    R = {k: S.run(w) for k, w in W.items()}
    win = lambda r, a, z: r[(r.index >= a) & (r.index < z)]  # noqa: E731
    dev = {k: stats(win(r, *DISC)) for k, r in R.items()}
    f17dd = dev["F17"]["maxdd"]
    elig = {k: v for k, v in dev.items() if k != "F17" and v["maxdd"] >= f17dd}
    sel = max(elig, key=lambda k: elig[k]["sharpe"]) if elig else None
    hold = {k: stats(win(r, *HOLD)) for k, r in R.items()}
    verdict = None
    if sel:
        a, b = win(R[sel], *HOLD).to_numpy(), win(R["F17"], *HOLD).to_numpy()
        diff, p = B.boot_p(a, b, B.sharpe, 20, draws=5000)
        h, f = hold[sel], hold["F17"]
        branch1 = diff > 0 and p < 0.025
        branch2 = (h["maxdd"] - f["maxdd"] >= 0.03) and (h["worst30"] > f["worst30"]) and (diff > -0.10)
        verdict = dict(rule=sel, sharpe_diff=float(diff), p=p, branch1=bool(branch1), branch2=bool(branch2),
                       result="PASS" if (branch1 or branch2) else "FAIL")
    out = dict(selected=sel, verdict=verdict, quality_dev=q_dev, quality_hold=q_hold, dev=dev, hold=hold)
    json.dump(out, open(ROOT / "research/b58_results.json", "w"), indent=1, default=float)
    with open(ROOT / "research/trial_ledger.csv", "a") as fh:
        for k in ("R1", "R2", "R3", "R4", "R5"):
            tag = "selected" if k == sel else "not selected"
            res = verdict["result"] if (verdict and k == sel) else "n/a"
            fh.write(f"2026-10-05T23:40:00,B58_{k},risk forecast overlay (prereg v9),risk,disc 2019-2023 / hold 2024-2026,"
                     f"{dev[k]['sharpe']:.3f},,,dev maxDD {dev[k]['maxdd']:.3f} hold Sharpe {hold[k]['sharpe']:.3f}; {tag}; {res}\n")
    print(json.dumps(out, indent=1, default=float))
    print(f"done {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
