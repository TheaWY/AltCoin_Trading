"""B7_D: does the new-variable set add information jointly? LightGBM LambdaRank, known factors only vs known + B7_A
(+ B7_B pool if present). Quarterly expanding walk-forward, first test quarter 2025-09, 2-day embargo. Tree process (no torch).
Out: data/reports/b7/b7_D.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
from b7_factors import factors  # noqa: E402

OUT = ROOT / "data/reports/b7"
QS = ["2025-09-01", "2025-12-01", "2026-03-01", "2026-06-01", "2026-09-24"]


def panel(feats, rows):
    ts, _, X = L.data()
    U = X["U"][rows]
    r, c = np.nonzero(U)
    df = pd.DataFrame({"row": rows[r], "coin": c, "ts": ts[rows][r]})
    for k, F in feats.items():
        df[k] = L.cs_rank(np.where(U, F[rows], np.nan))[r, c]
    y = L.cs_rank(np.where(U, X["fwd24_res"][rows], np.nan))[r, c]
    df["y"] = y
    df["rel"] = np.clip(((y + 0.5) * 5).astype(int), 0, 4)
    return df.dropna(subset=["y"])


def fit_predict(df, cols, test0, test1):
    tr = df[df["ts"] < test0 - 2 * 86400].sort_values("ts")
    te = df[(df["ts"] >= test0) & (df["ts"] < test1)].sort_values("ts")
    m = lgb.LGBMRanker(objective="lambdarank", n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=200,
                       subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, verbose=-1, n_jobs=8)
    m.fit(tr[cols], tr["rel"], group=tr.groupby("ts").size().to_numpy())
    return te.assign(p=m.predict(te[cols])), m


def daily_ic(te):
    ic = te.groupby("ts").apply(lambda g: g["p"].rank().corr(g["y"].rank()))
    return ic.groupby(ic.index // 86400).mean()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    K = L.known_factors()
    A = factors()
    feats = {**K, **A}
    gp = OUT / "b7_gp_pool.json"
    if gp.exists():
        import b7_gp as G
        B = G.base(False)
        for i, r in enumerate(json.load(open(gp))):
            feats[f"GP{i:02d}"] = r["sign"] * G.ev(G.parse(r["f"]), B)
    rows = L.eval_rows((L.DISC[0], L.HOLD[1]))
    df = panel(feats, rows)
    base_cols = list(K)
    full_cols = [c for c in feats]
    out = {"n_features_full": len(full_cols)}
    ics = {"known": [], "full": []}
    preds = {}
    for q0, q1 in zip(QS[:-1], QS[1:]):
        t0, t1 = pd.Timestamp(q0).timestamp(), pd.Timestamp(q1).timestamp()
        for nm, cols in (("known", base_cols), ("full", full_cols)):
            te, m = fit_predict(df, cols, t0, t1)
            ics[nm].append(daily_ic(te))
            preds.setdefault(nm, []).append(te[["row", "coin", "p"]])
            if nm == "full":
                imp = pd.Series(m.booster_.feature_importance("gain"), index=cols).sort_values(ascending=False)
                out.setdefault("top_gain", {})[q0] = imp.head(10).round(1).to_dict()
        print(q0, {k: round(float(v[-1].mean()), 4) for k, v in ics.items()}, flush=True)
    ik, ifu = pd.concat(ics["known"]), pd.concat(ics["full"])
    diff = (ifu - ik).dropna()
    out.update(ic_known=float(ik.mean()), ic_known_ci=L.boot_ci(ik.dropna().to_numpy()), ic_full=float(ifu.mean()),
               ic_full_ci=L.boot_ci(ifu.dropna().to_numpy()), ic_gain=float(diff.mean()), ic_gain_ci=L.boot_ci(diff.to_numpy()))
    out["pass"] = bool(out["ic_gain"] > 0 and out["ic_gain_ci"][0] > 0)
    # tradability of the full model score (equal-weight quintile L/S, same cost model)
    ts, _, X = L.data()
    for nm in ("known", "full"):
        P = pd.concat(preds[nm])
        F = np.full(X["c"].shape, np.nan, np.float32)
        F[P["row"].to_numpy(), P["coin"].to_numpy()] = P["p"].to_numpy()
        ls = L.long_short(F, L.eval_rows(L.HOLD))
        day = ls.groupby(ls.index // 86400).sum()
        out[f"ls_{nm}"] = dict(net_day=float(day["net"].mean()), ci=L.boot_ci(day["net"].to_numpy()),
                               gross_day=float(day["gross"].mean()), hedged_day=float(day["net_hedged"].mean()),
                               turnover_day=float(day["turnover"].mean()))
        np.save(OUT / f"b7_D_score_{nm}.npy", F)
    print(json.dumps(out, indent=1, default=float))
    json.dump(out, open(OUT / "b7_D.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
