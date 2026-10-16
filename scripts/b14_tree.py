"""B14_1 (ranker with new sources), B14_2 (meta-labeling v2), B14_3 (quantile magnitude). LightGBM process, no torch.
  OMP_NUM_THREADS=8 .venv/bin/python -W ignore scripts/b14_tree.py 1|2|3
Out: data/reports/b14/b14_{1,2,3}.json, OOS predictions data/cache/b14/pred_*.parquet"""
from __future__ import annotations

import json
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import b14_lib as B  # noqa: E402
from b14_lib import boot_ci, daily_ic, quintile_ls, slice_rules, slice_table, summ, topk_long, wf_folds  # noqa: E402

PARAMS = dict(learning_rate=0.03, num_leaves=63, min_child_samples=300, subsample=0.8, subsample_freq=1, colsample_bytree=0.7,
              reg_lambda=10.0, verbose=-1, n_jobs=8)


# ------------------------------------------------------------------ B14_1
def exp1():
    T, G = B.load_tab()
    sets = B.feature_sets(G)
    T["rel"] = T.groupby("ts")["y_res24"].rank(pct=True).mul(4.999).astype(int)
    res, preds = {}, {}
    for name, cols in sets.items():
        ics, parts, gain = [], [], {}
        for q0, tr, va, te in wf_folds(T):
            trd = T[tr | va].sort_values("ts")
            m = lgb.LGBMRanker(objective="lambdarank", n_estimators=400, **PARAMS)
            m.fit(trd[cols], trd["rel"], group=trd.groupby("ts").size().to_numpy())
            ted = T[te].copy()
            ted["p"] = m.predict(ted[cols])
            parts.append(ted[["ts", "j", "p"]])
            ics.append(daily_ic(ted))
            imp = pd.Series(m.booster_.feature_importance("gain"), index=cols)
            for gname, gcols in G.items():
                gain[gname] = gain.get(gname, 0.0) + float(imp.reindex(gcols).fillna(0).sum())
            print(name, q0, "ic", round(float(ics[-1].mean()), 4), flush=True)
        P = pd.concat(parts)
        preds[name] = P
        te_all = T[T["ts"] >= B.QS[0]].merge(P, on=["ts", "j"])
        ic = pd.concat(ics)
        tot = sum(gain.values()) or 1.0
        res[name] = dict(ic=float(ic.mean()), ic_ci=boot_ci(ic.to_numpy()), ic_days=int(len(ic)),
                         gain_share={k: round(v / tot, 3) for k, v in gain.items()},
                         ls=slice_table(te_all, quintile_ls), top10=slice_table(te_all, topk_long))
        res[name]["ls_rules"] = slice_rules(res[name]["ls"])
        print(name, json.dumps({k: res[name][k] for k in ("ic", "ic_ci", "gain_share")}), "ls", res[name]["ls"]["pooled"], flush=True)
    a, d = preds["a_price"], preds["d_full"]
    ia, idd = daily_ic(T.merge(a, on=["ts", "j"])), daily_ic(T.merge(d, on=["ts", "j"]))
    diff = (idd - ia).dropna()
    res["ic_gain_full_vs_price"] = dict(mean=float(diff.mean()), ci=boot_ci(diff.to_numpy()))
    lsd = res["d_full"]["ls"]["pooled"]
    res["pass"] = bool(res["ic_gain_full_vs_price"]["ci"][0] > 0 and lsd["ci"][0] > 0 and res["d_full"]["ls_rules"]["all_ok"])
    preds["d_full"].to_parquet(B.B14 / "pred_ranker_full.parquet", index=False)
    json.dump(res, open(B.OUT / "b14_1.json", "w"), indent=1, default=float)
    print("B14_1 pass", res["pass"], res["ic_gain_full_vs_price"])


# ------------------------------------------------------------------ B14_2
def primaries(T):
    """Event rows for the 5 primaries, defined on the tensor itself (4h cadence)."""
    ev = {"pump_fade_S": (T["ret_1h"] >= np.log1p(0.10), -1), "pump_follow_L": (T["ret_1h"] >= np.log1p(0.10), 1),
          "breakout_L": ((T["dhi30"] > 0) & (T["ret_24h"] > np.log1p(0.05)), 1), "crash_rebound_L": (T["ret_24h"] <= np.log1p(-0.25), 1),
          "spot_led_pump_S": ((T["ret_1h"] >= np.log1p(0.10)) & (T["spot_share_6h"] >= 0.01999), -1)}
    return {k: (T[m].copy(), side) for k, (m, side) in ev.items()}


def exp2():
    T, G = B.load_tab()
    cols = B.feature_sets(G)["d_full"]
    res, passes = {}, 0
    for name, (E, side) in primaries(T).items():
        E["net"] = B.net_returns(E, side)
        E["loss"] = (E["net"] < 0).astype(int)
        parts = []
        for q0, tr, va, te in wf_folds(E):
            if tr.sum() < 500 or te.sum() < 20:
                continue
            m = lgb.LGBMClassifier(n_estimators=300, **PARAMS)
            m.fit(E[tr][cols], E[tr]["loss"])
            iso = IsotonicRegression(out_of_bounds="clip").fit(m.predict_proba(E[va][cols])[:, 1], E[va]["loss"]) if va.sum() > 50 else None
            p = m.predict_proba(E[te][cols])[:, 1]
            ted = E[te].copy()
            ted["p"] = iso.predict(p) if iso else p
            parts.append(ted)
        if not parts:
            res[name] = dict(n=0)
            continue
        H = pd.concat(parts)
        disc = E[(E["ts"] >= B.DISC0) & (E["ts"] < B.DISC1)]
        med = float(np.median(H["p"]))          # median of OOS scores (no discovery-fitted model exists; note in report)
        keep = H["p"] <= med
        imp = H.loc[keep, "net"].mean() - H["net"].mean()
        d_all = H.groupby("day")["net"].mean()
        d_keep = H[keep].groupby("day")["net"].mean()
        diff = (d_keep - d_all).dropna()
        from sklearn.metrics import roc_auc_score
        r = dict(n=int(len(H)), auc=float(roc_auc_score(H["loss"], H["p"])) if H["loss"].nunique() > 1 else None,
                 mean_all=float(H["net"].mean()), mean_kept=float(H.loc[keep, "net"].mean()), improvement=float(imp),
                 improvement_ci=boot_ci(diff.to_numpy()), n_disc_events=int(len(disc)))
        r["regime"] = {str(s): float(H[(H["state"] == s) & keep]["net"].mean() - H[H["state"] == s]["net"].mean())
                       for s in (0, 1, 2) if (H["state"] == s).sum() > 30}
        r["pass"] = bool(r["improvement_ci"][0] > 0 and sum(v > 0 for v in r["regime"].values()) >= 2)
        passes += r["pass"]
        res[name] = r
        print(name, json.dumps(r, default=float), flush=True)
    res["pass"] = passes >= 3
    json.dump(res, open(B.OUT / "b14_2.json", "w"), indent=1, default=float)
    print("B14_2 pass", res["pass"])


# ------------------------------------------------------------------ B14_3
def exp3():
    T, G = B.load_tab()
    cols = B.feature_sets(G)["d_full"]
    parts, pin = [], []
    for q0, tr, va, te in wf_folds(T):
        trd, ted = T[tr | va], T[te].copy()
        for q in (0.1, 0.5, 0.9):
            m = lgb.LGBMRegressor(objective="quantile", alpha=q, n_estimators=400, **PARAMS)
            m.fit(trd[cols], trd["y_res24"])
            ted[f"q{int(q * 100)}"] = m.predict(ted[cols])
            base = float(np.quantile(trd["y_res24"], q))
            y = ted["y_res24"].to_numpy()

            def pinball(pred):
                e = y - pred
                return float(np.mean(np.maximum(q * e, (q - 1) * e)))
            pin.append(dict(q0=int(q0), q=q, model=pinball(ted[f"q{int(q * 100)}"].to_numpy()), base=pinball(np.full(len(y), base))))
        parts.append(ted)
        print("fold", q0, [f"{p['q']}: {p['model']:.5f} vs {p['base']:.5f}" for p in pin[-3:]], flush=True)
    H = pd.concat(parts)
    H["width"] = (H["q90"] - H["q10"]).clip(lower=1e-3)
    H["cover"] = ((H["y_res24"] >= H["q10"]) & (H["y_res24"] <= H["q90"])).astype(float)
    # asymmetric-confidence rule, inverse-width sizing, gross capped at 1 per timestamp
    H["side"] = np.where(H["q10"] > H["cost"], 1, np.where(H["q90"] < -H["cost"], -1, 0))
    H["w"] = np.where(H["side"] != 0, 1.0 / H["width"], 0.0)
    H["w"] = H["w"] / H.groupby("ts")["w"].transform("sum").replace(0, np.nan)
    H["pnl"] = H["w"] * B.net_returns(H, H["side"].to_numpy())
    H["p"] = H["q50"]

    def daily_pnl(df, score="p"):
        s = df.groupby("ts")["pnl"].sum()
        return s.groupby(s.index // B.D_).mean()
    st = slice_table(H, daily_pnl)
    res = dict(pinball=pin, pinball_improved_quarters=int(sum(1 for q0 in set(p["q0"] for p in pin)
                                                            if all(p["model"] < p["base"] for p in pin if p["q0"] == q0))),
               coverage_10_90=float(H["cover"].mean()), n_signals_per_ts=float((H["side"] != 0).groupby(H["ts"]).sum().mean()),
               trade=st, rules=slice_rules(st), median_ic=dict(mean=float(daily_ic(H).mean()), ci=boot_ci(daily_ic(H).to_numpy())))
    res["pass"] = bool(st["pooled"].get("ci", [0])[0] > 0 and res["pinball_improved_quarters"] >= 3 and res["rules"]["all_ok"])
    H[["ts", "j", "q10", "q50", "q90"]].to_parquet(B.B14 / "pred_quantiles.parquet", index=False)
    json.dump(res, open(B.OUT / "b14_3.json", "w"), indent=1, default=float)
    print("B14_3", json.dumps({k: res[k] for k in ("pinball_improved_quarters", "coverage_10_90", "n_signals_per_ts", "median_ic", "pass")}, default=float), st["pooled"])


if __name__ == "__main__":
    B.OUT.mkdir(parents=True, exist_ok=True)
    {"1": exp1, "2": exp2, "3": exp3}[sys.argv[1]]()
