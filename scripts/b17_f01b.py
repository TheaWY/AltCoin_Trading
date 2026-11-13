"""B17 F01b + F01c: what happens AFTER a precursor flag, and the model sweep. Registry: research/batch_B17_registry.yaml F01b_*, F01c_*.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f01b.py fit   logit|lgbm|catboost|mlp     -> data/cache/b17/p_{model}.npy (P(pump<6h), walk-forward)
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f01b.py trade lgbm                        -> data/reports/b17/f01b_{model}.json
Tabular models here; gru/tcn/transformer in b17_f01_seq.py (torch, separate process) write the same p_{model}.npy so `trade` works for all.
Path study (per flag, validation): return path o[t+1..t+24]/o[t+1]-1 mean/median per hour; time to first onset; max drawdown before onset.
Entries (all paired by flag; a flag with no qualifying entry = no trade): immediate | dip d% within w h | re-break of flag-hour high within w h | onset within 24h.
Flag = top q of coin-hours by P on the training fold (q in 0.1%, 0.5%, 2%); minimum gap 24h between flags on the same coin."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f01 import EXITS, FEATS, VAL0, labels, panel, simulate  # noqa: E402

C = ROOT / "data/cache"
B = C / "b17"
OUT = ROOT / "data/reports/b17"
RNG = np.random.default_rng(1702)
H_ = 3600
HZ = 6
QS = {"q01": 0.999, "q05": 0.995, "q2": 0.98}


def fit(model):
    ts, codes, X, F = panel()
    onset, Y = labels(ts, codes)
    y = Y[HZ]
    U = (X["dv24"] >= 5e6) & (X["age"] >= 720)
    T, N = U.shape
    feat = np.stack([np.nan_to_num(np.asarray(F[k], np.float32), nan=0.0, posinf=0, neginf=0) for k in FEATS + ["DEPTH1_to_volume", "IMB1_6h"]], -1)
    P_all = np.full((T, N), np.nan, np.float32)
    thr = {q: np.full(T, np.nan, np.float32) for q in QS}
    q_edges = np.arange(int(ts[0]), int(ts[-1]) + 1, 91 * 86400)
    for q0, q1 in zip(q_edges[1:], list(q_edges[2:]) + [int(ts[-1]) + H_]):
        itr = np.flatnonzero((ts < q0 - 2 * 86400 - HZ * H_)); ite = np.flatnonzero((ts >= q0) & (ts < q1))
        if len(ite) == 0:
            continue
        m_tr = U[itr]; Xtr, ytr = feat[itr][m_tr], y[itr][m_tr]
        if ytr.sum() < 50:
            continue
        if len(Xtr) > 1_500_000:
            pick = RNG.choice(len(Xtr), 1_500_000, replace=False); Xtr, ytr = Xtr[pick], ytr[pick]
        clf = make_model(model)
        clf.fit(Xtr, ytr)
        p_tr = clf.predict_proba(Xtr[RNG.choice(len(Xtr), min(400_000, len(Xtr)), replace=False)])[:, 1]
        for q, qq in QS.items():
            thr[q][ite] = np.quantile(p_tr, qq)
        m_te = U[ite]; p = np.full(m_te.shape, np.nan, np.float32); p[m_te] = clf.predict_proba(feat[ite][m_te])[:, 1]
        P_all[ite] = p
        print(model, pd.to_datetime(q0, unit="s").date(), "trained", len(Xtr), flush=True)
    np.save(B / f"p_{model}.npy", P_all)
    np.save(B / f"thr_{model}.npy", np.stack([thr[q] for q in QS], 1))
    v = (ts >= VAL0)[:, None] & U & np.isfinite(P_all)
    print(model, "validation AUC", roc_auc_score(y[v], P_all[v]))


def make_model(model):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if model == "logit":
        from sklearn.linear_model import LogisticRegression
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=300, class_weight="balanced"))
    if model == "lgbm":
        import lightgbm as lgb
        return lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03, num_leaves=63, min_child_samples=500, subsample=0.7, subsample_freq=1,
                                  colsample_bytree=0.8, reg_lambda=10, verbose=-1, n_jobs=6)
    if model == "catboost":
        from catboost import CatBoostClassifier
        return CatBoostClassifier(iterations=600, learning_rate=0.05, depth=7, l2_leaf_reg=5, verbose=0, thread_count=6, auto_class_weights="SqrtBalanced")
    if model == "mlp":
        from sklearn.neural_network import MLPClassifier
        return make_pipeline(StandardScaler(), MLPClassifier((128, 64), alpha=1e-3, batch_size=2048, max_iter=30, early_stopping=True, random_state=1))
    raise ValueError(model)


def flags(ts, U, P, thr_col, gap=24):
    sel = np.isfinite(P) & (P > thr_col[:, None]) & U
    rows = pd.DataFrame({"t": np.nonzero(sel)[0], "j": np.nonzero(sel)[1]}).sort_values(["j", "t"])
    keep, last = [], {}
    for t, j in zip(rows["t"], rows["j"]):
        if t - last.get(j, -10**9) >= gap:
            keep.append((t, j)); last[j] = t
    return pd.DataFrame(keep, columns=["t", "j"])


def entry_time(fl, X, onset, kind, d=None, w=None):
    """Return entry hour index per flag (or -1)."""
    o, h, l, c = X["o"], X["h"], X["l"], X["c"]
    T = len(o)
    out = np.full(len(fl), -1, int)
    for i, (t, j) in enumerate(zip(fl["t"], fl["j"])):
        if kind == "immediate":
            out[i] = t; continue
        if t + w + 1 >= T:
            continue
        ref = o[t + 1, j]
        if kind == "dip":
            k = np.flatnonzero(l[t + 1:t + 1 + w, j] <= ref * (1 - d))
        elif kind == "rebreak":
            hi = h[t, j]
            lows = l[t + 1:t + 1 + w, j]
            k = np.flatnonzero((c[t + 1:t + 1 + w, j] > hi) & (np.minimum.accumulate(lows) < ref * 0.98))
        elif kind == "onset":
            k = np.flatnonzero(onset[t + 1:t + 1 + w, j])
        if len(k):
            out[i] = t + 1 + k[0]     # signal seen at close of that hour; simulate() enters at the next open
    return out


def path_study(fl, X, onset):
    o, l = X["o"], X["l"]
    paths, tto, ddb = [], [], []
    for t, j in zip(fl["t"], fl["j"]):
        if t + 25 >= len(o):
            continue
        e = o[t + 1, j]
        if not np.isfinite(e):
            continue
        paths.append(o[t + 1:t + 25, j] / e - 1)
        k = np.flatnonzero(onset[t + 1:t + 25, j])
        tto.append(k[0] + 1 if len(k) else np.nan)
        ddb.append(1 - np.nanmin(l[t + 1:t + 1 + (k[0] + 1 if len(k) else 24), j]) / e)
    P = np.array(paths)
    return dict(n=len(P), mean_path=np.nanmean(P, 0).round(4).tolist(), median_path=np.nanmedian(P, 0).round(4).tolist(),
                share_onset_24h=float(np.mean(np.isfinite(tto))), time_to_onset_median_h=float(np.nanmedian(tto)),
                drawdown_before_onset_p50=float(np.nanmedian(ddb)), drawdown_before_onset_p75=float(np.nanpercentile(ddb, 75)))


def trade(model):
    ts, codes, X, F = panel()
    onset, Y = labels(ts, codes)
    y = Y[HZ]
    U = (X["dv24"] >= 5e6) & (X["age"] >= 720)
    P = np.load(B / f"p_{model}.npy"); THR = np.load(B / f"thr_{model}.npy")
    R = pd.read_parquet(C / "b14/regime.parquet").set_index("day")["state"]
    days = ts // 86400
    v = (ts >= VAL0)[:, None] & U & np.isfinite(P)
    res = {"model": model, "auc_validation": float(roc_auc_score(y[v], P[v])), "thresholds": {}, "tests": {}}
    ENTRIES = {"immediate": ("immediate", None, None)}
    for d in (0.03, 0.05, 0.08):
        for w in (6, 12, 24):
            ENTRIES[f"dip{int(d*100)}_w{w}"] = ("dip", d, w)
    for w in (6, 12, 24):
        ENTRIES[f"rebreak_w{w}"] = ("rebreak", None, w)
    ENTRIES["onset_w24"] = ("onset", None, 24)
    for qi, q in enumerate(QS):
        fl = flags(ts, U, P, THR[:, qi])
        flv = fl[ts[fl["t"]] >= VAL0]
        res["thresholds"][q] = dict(n_flags_val=int(len(flv)), precision_6h=float(y[flv["t"], flv["j"]].mean()) if len(flv) else None,
                                    path=path_study(flv, X, onset) if len(flv) > 30 else None)
        # control flags: random U coin-hours matched on hour-of-day and month, same count
        ctrl = []
        for (hod, mo), g in fl.groupby([(ts[fl["t"]] // H_) % 24, ts[fl["t"]] // (30 * 86400)]):
            pool = np.flatnonzero(((ts // H_) % 24 == hod) & (ts // (30 * 86400) == mo))
            cand = np.argwhere(U[pool])
            if len(cand):
                pick = cand[RNG.choice(len(cand), min(len(g), len(cand)), replace=False)]
                ctrl.append(pd.DataFrame({"t": pool[pick[:, 0]], "j": pick[:, 1]}))
        ctrl = pd.concat(ctrl)
        for ename, (kind, d, w) in ENTRIES.items():
            for ex in EXITS:
                key = f"{q}_{ename}_{ex}"
                out = {}
                for nm, FL in (("signal", fl), ("control", ctrl)):
                    et = entry_time(FL, X, onset, kind, d, w)
                    E = pd.DataFrame({"t": et, "j": FL["j"].to_numpy(), "flag_t": FL["t"].to_numpy()})
                    E = E[E["t"] >= 0]
                    if len(E) < 20:
                        continue
                    g, mae = simulate(E, X, ex)
                    E = E.assign(gross=g, mae=mae, cost=2 * (L.FEE + M.slip(X["dv24"][E["t"], E["j"]])), ts=ts[E["t"]], day=days[E["t"]])
                    E["net"] = E["gross"] - E["cost"]; E = E.dropna(subset=["net"])
                    E["val"] = E["ts"] >= VAL0
                    E["state"] = R.reindex(E["day"]).fillna(-1).astype(int).to_numpy()
                    for per, gg in (("discovery", E[~E["val"]]), ("validation", E[E["val"]])):
                        if len(gg) < 20:
                            continue
                        dct = dict(n=int(len(gg)), fill_rate=float(len(gg) / max(1, (FL["t"] >= 0).sum() if per == "discovery" else len(FL[ts[FL["t"]] >= VAL0]))),
                                   mean=float(gg["net"].mean()), ci=M.day_ci(gg["net"].to_numpy(), gg["day"].to_numpy()), win=float((gg["net"] > 0).mean()),
                                   mae_p90=float(gg["mae"].quantile(0.9)), mae_p99=float(gg["mae"].quantile(0.99)))
                        if nm == "signal" and per == "validation":
                            dct["slices_state"] = {str(k): dict(n=int(len(x)), mean=float(x["net"].mean())) for k, x in gg.groupby("state") if len(x) >= 20}
                        out.setdefault(nm, {})[per] = dct
                s, c_ = out.get("signal", {}).get("validation"), out.get("control", {}).get("validation")
                if s:
                    se = (s["ci"][1] - s["ci"][0]) / 3.92 or 1e-9
                    out["p_one_sided"] = float(1 - norm.cdf(s["mean"] / se))
                    out["beats_control"] = bool(c_ and s["ci"][0] > max(0.0, c_["mean"]))
                    res["tests"][key] = out
    pv = {k: v["p_one_sided"] for k, v in res["tests"].items()}
    names = sorted(pv, key=pv.get)
    last = max([k for k, nm in enumerate(names) if pv[nm] <= 0.10 * (k + 1) / len(names)], default=-1)
    res["bh_pass"] = [nm for k, nm in enumerate(names) if k <= last]
    res["pass"] = [nm for nm in res["bh_pass"] if res["tests"][nm]["beats_control"] and res["tests"][nm]["signal"]["validation"]["mae_p90"] < 0.15]
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT / f"f01b_{model}.json", "w"), indent=1, default=float)
    print("AUC", res["auc_validation"], "PASS", res["pass"])
    best = sorted(res["tests"].items(), key=lambda kv: -kv[1]["signal"]["validation"]["mean"])[:8]
    for k, v_ in best:
        s = v_["signal"]["validation"]; print(k, s["n"], round(s["mean"], 4), [round(x, 4) for x in s["ci"]], "ctrl", round(v_.get("control", {}).get("validation", {}).get("mean", np.nan), 4))


if __name__ == "__main__":
    {"fit": fit, "trade": trade}[sys.argv[1]](sys.argv[2])
