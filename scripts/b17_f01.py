"""B17 family F01: precursor anticipation long (enter BEFORE the pump). research/batch_B17_registry.yaml F01_*.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f01.py logit|lgbm     (gru = separate torch process, later)
Features (hourly, 22): the 20 B8 precursor panels (data/cache/b8) + depth-to-volume and 6h book imbalance from depth1h (B13 winners).
Label y_h: a pump onset (data/cache/b15/pumps.parquet) for this coin within the next h hours, h in 1,3,6,12,24.
Universe: dv24 >= $5M, listed >= 30d. Walk-forward quarterly folds, 2-day embargo, model refit each fold on all prior data.
Trade: at the close of hour t when P > thr (thr = the training-fold quantile giving the top 0.5% of coin-hours), long at the
next-hour open, exits tp10 (first hour whose high >= +10%: fill at +10%, else close at +24h) | t24h | trail2 (2x 24h ATR
from the running high, checked hourly). Cost = 2x(fee + slip(dv24)). Max 10 concurrent (equal weight) for the portfolio line.
Control: random coin-hours matched on hour-of-day x liquidity tercile x month, same count. Verdict rows use validation
2026-01..2026-09 only; discovery folds reported for reference. BH q=0.10 within the family over the 15 (h x exit) tests per model.
Out: data/reports/b17/f01_{model}.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402

C = ROOT / "data/cache"
OUT = ROOT / "data/reports/b17"
RNG = np.random.default_rng(1701)
H_ = 3600
HZ = [1, 3, 6, 12, 24]
VAL0 = 1767225600          # 2026-01-01
EXITS = ["tp10", "t24h", "trail2"]
FEATS = ["D1_oi_change_6h", "D2_oi_change_24h", "D3_oi_vs_price_6h", "D4_top_pos_ls_change_24h", "D5_global_ls_change_24h", "D6_taker_ratio_6h",
         "D7_oi_to_volume", "F1_funding_level", "F2_taker_imbalance_6h", "F3_quarter_hour_flow_6h", "K1_upbit_surge_6h", "K2_upbit_vs_binance_surge",
         "K3_krw_premium", "K4_krw_premium_change_6h", "K5_upbit_share_24h_change", "N1_listing_notice_prior_24h", "P1_ret_6h", "P2_ret_24h",
         "P3_volume_surge_6h", "P4_range_6h"]


def panel():
    ts, codes, X = L.data()
    F = {k: np.load(C / f"b8/{k}.npy", mmap_mode="r") for k in FEATS}
    T, N = len(ts), len(codes)
    d2v, imb = np.full((T, N), np.nan, np.float32), np.full((T, N), np.nan, np.float32)
    tidx = {int(t): i for i, t in enumerate(ts)}
    for j, c in enumerate(codes):
        fp = C / f"depth1h/{c}.parquet"
        if not fp.exists():
            continue
        d = pd.read_parquet(fp, columns=["ts", "bid_1", "ask_1", "imb_1"])
        ii = np.array([tidx.get(int(t), -1) for t in d["ts"]])
        ok = ii >= 0
        d2v[ii[ok], j] = (d["bid_1"] + d["ask_1"]).to_numpy(np.float32)[ok]
        imb[ii[ok], j] = d["imb_1"].to_numpy(np.float32)[ok]
    F["DEPTH1_to_volume"] = np.log(L.safe_div(d2v, X["dv24"]) + 1e-9)
    F["IMB1_6h"] = L.M(imb, 6)
    return ts, codes, X, F


def labels(ts, codes):
    P = pd.read_parquet(C / "b15/pumps.parquet", columns=["code", "ts"])
    ci = {c: j for j, c in enumerate(codes)}
    onset = np.zeros((len(ts), len(codes)), bool)
    t0 = int(ts[0])
    for c, t in zip(P["code"], P["ts"]):
        i = (int(t) - t0) // H_
        if c in ci and 0 <= i < len(ts):
            onset[i, ci[c]] = True
    Y = {}
    for h in HZ:
        y = np.zeros_like(onset)
        for k in range(1, h + 1):
            y[:-k] |= onset[k:]
        Y[h] = y
    return onset, Y


def simulate(entries, X, exit_):
    """entries: DataFrame(t, j). Returns gross return per trade and MAE (adverse excursion)."""
    o, h, l, c = X["o"], X["h"], X["l"], X["c"]
    atr = L.M(np.log(L.safe_div(h, l)), 24)
    g, mae = [], []
    for t, j in zip(entries["t"], entries["j"]):
        if t + 25 >= len(c):
            g.append(np.nan); mae.append(np.nan); continue
        e = o[t + 1, j]
        hh, ll, cc = h[t + 1:t + 25, j], l[t + 1:t + 25, j], c[t + 1:t + 25, j]
        if not np.isfinite(e) or not np.isfinite(cc[-1]):
            g.append(np.nan); mae.append(np.nan); continue
        mae.append(1 - np.nanmin(ll) / e)
        if exit_ == "t24h":
            g.append(cc[-1] / e - 1)
        elif exit_ == "tp10":
            k = np.flatnonzero(hh >= e * 1.10)
            g.append(0.10 if len(k) else cc[-1] / e - 1)
        else:
            stop_w = 2 * (atr[t, j] if np.isfinite(atr[t, j]) else 0.05)
            run, out = e, None
            for k in range(24):
                run = max(run, hh[k])
                if ll[k] <= run * np.exp(-stop_w):
                    out = run * np.exp(-stop_w) / e - 1; break
            g.append(out if out is not None else cc[-1] / e - 1)
    return np.array(g), np.array(mae)


def main(model):
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    ts, codes, X, F = panel()
    onset, Y = labels(ts, codes)
    U = (X["dv24"] >= 5e6) & (X["age"] >= 720)
    R = pd.read_parquet(C / "b14/regime.parquet").set_index("day")["state"]
    T, N = U.shape
    feat = np.stack([np.nan_to_num(np.asarray(F[k], np.float32), nan=0.0, posinf=0, neginf=0) for k in FEATS + ["DEPTH1_to_volume", "IMB1_6h"]], -1)
    days = ts // 86400
    q_edges = np.arange(int(ts[0]), int(ts[-1]) + 1, 91 * 86400)
    res = {"model": model, "n_features": feat.shape[-1], "tests": {}}
    ledger = []
    for h in HZ:
        y = Y[h]
        P_all = np.full((T, N), np.nan, np.float32)
        thr_row = np.full(T, np.nan)
        thr_by_fold = []
        for q0, q1 in zip(q_edges[1:], list(q_edges[2:]) + [int(ts[-1]) + H_]):
            tr = (ts < q0 - 2 * 86400 - h * H_) & U.any(1)
            te = (ts >= q0) & (ts < q1)
            itr = np.flatnonzero(tr); ite = np.flatnonzero(te)
            if len(ite) == 0:
                continue
            m_tr = U[itr]
            Xtr, ytr = feat[itr][m_tr], y[itr][m_tr]
            if ytr.sum() < 50:
                continue
            if len(Xtr) > 1_500_000:
                pick = RNG.choice(len(Xtr), 1_500_000, replace=False); Xtr, ytr = Xtr[pick], ytr[pick]
            if model == "logit":
                clf = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=300, class_weight="balanced"))
            else:
                clf = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.03, num_leaves=63, min_child_samples=500, subsample=0.7, subsample_freq=1,
                                         colsample_bytree=0.8, reg_lambda=10, verbose=-1, n_jobs=6)
            clf.fit(Xtr, ytr)
            p_tr = clf.predict_proba(Xtr[RNG.choice(len(Xtr), min(400_000, len(Xtr)), replace=False)])[:, 1]
            thr = float(np.quantile(p_tr, 0.995)); thr_by_fold.append(thr)
            m_te = U[ite]
            p = np.full(m_te.shape, np.nan, np.float32); p[m_te] = clf.predict_proba(feat[ite][m_te])[:, 1]
            P_all[ite] = np.where(m_te, p, np.nan)
            thr_row[ite] = thr
        sel = np.isfinite(P_all) & (P_all > thr_row[:, None]) & U
        rows = pd.DataFrame({"t": np.nonzero(sel)[0], "j": np.nonzero(sel)[1]})
        # control: same count per (hour-of-day, liq tercile, month) from U rows not selected
        liq = pd.DataFrame(X["dv24"]).rank(axis=1, pct=True).to_numpy()
        ctrl = []
        for (hod, mo), g in rows.groupby([(ts[rows["t"]] // H_) % 24, ts[rows["t"]] // (30 * 86400)]):
            pool_t = np.flatnonzero(((ts // H_) % 24 == hod) & (ts // (30 * 86400) == mo))
            cand = np.argwhere(U[pool_t] & ~sel[pool_t])
            if len(cand):
                pick = cand[RNG.choice(len(cand), min(len(g), len(cand)), replace=False)]
                ctrl.append(pd.DataFrame({"t": pool_t[pick[:, 0]], "j": pick[:, 1]}))
        ctrl = pd.concat(ctrl) if ctrl else rows.iloc[:0]
        for ex in EXITS:
            for name, E in (("signal", rows), ("control", ctrl)):
                g, mae = simulate(E, X, ex)
                E = E.assign(gross=g, mae=mae, cost=2 * (L.FEE + M.slip(X["dv24"][E["t"], E["j"]])), ts=ts[E["t"]], day=days[E["t"]])
                E["net"] = E["gross"] - E["cost"]
                E["val"] = E["ts"] >= VAL0
                E["state"] = R.reindex(E["day"]).fillna(-1).astype(int).to_numpy()
                E["liq"] = pd.cut(liq[E["t"], E["j"]], [0, 1 / 3, 2 / 3, 1.01], labels=["low", "mid", "high"]).astype(str)
                E["kr"] = np.isfinite(np.asarray(F["K1_upbit_surge_6h"])[E["t"], E["j"]]).astype(int)
                E["third"] = pd.cut(E["ts"], [0, 1735689600, 1751328000, 9e9], labels=["24-25H1", "25H2", "26"]).astype(str)
                E = E.dropna(subset=["net"])
                key = f"h{h}_{ex}"
                res["tests"].setdefault(key, {})[name] = {}
                for per, g_ in (("discovery", E[~E["val"]]), ("validation", E[E["val"]])):
                    if len(g_) < 20:
                        continue
                    d = dict(n=int(len(g_)), mean=float(g_["net"].mean()), ci=M.day_ci(g_["net"].to_numpy(), g_["day"].to_numpy()),
                             win=float((g_["net"] > 0).mean()), mae_p90=float(g_["mae"].quantile(0.9)), mae_p99=float(g_["mae"].quantile(0.99)))
                    if name == "signal" and per == "validation":
                        d["slices"] = {s: {str(k): dict(n=int(len(v)), mean=float(v["net"].mean()), ci=M.day_ci(v["net"].to_numpy(), v["day"].to_numpy()))
                                            for k, v in g_.groupby(s) if len(v) >= 20} for s in ("state", "liq", "kr", "third")}
                        # portfolio line: max 10 concurrent, equal weight, daily
                        dd = g_.groupby("day")["net"].agg(["mean", "count"])
                        d["portfolio_daily_mean"] = float((dd["mean"] * np.minimum(dd["count"], 10) / 10).mean())
                    res["tests"][key][name][per] = d
            s, c_ = res["tests"][key]["signal"].get("validation"), res["tests"][key]["control"].get("validation")
            if s and c_:
                res["tests"][key]["beats_control"] = bool(s["ci"][0] > c_["mean"] and s["ci"][0] > 0)
        # model quality on validation
        v = (ts >= VAL0)
        m = v[:, None] & U & np.isfinite(P_all)
        if y[m].sum() > 0:
            res["tests"][f"h{h}_auc"] = dict(auc=float(roc_auc_score(y[m], P_all[m])), base_rate=float(y[m].mean()),
                                              prec_top=float(y[m & sel].mean()) if (m & sel).sum() else None, n_flags=int((m & sel).sum()))
        print(h, {k: v_ for k, v_ in res["tests"].items() if k.startswith(f"h{h}_") and k.endswith("auc")}, flush=True)
    # BH within family on validation p-values (one-sided, day-cluster bootstrap)
    pv = {}
    for key, r in res["tests"].items():
        s = r.get("signal", {}).get("validation") if isinstance(r, dict) else None
        if s:
            lo, hi = s["ci"]; se = (hi - lo) / 3.92 or 1e-9
            from scipy.stats import norm
            pv[key] = float(1 - norm.cdf(s["mean"] / se))
    names = sorted(pv, key=pv.get)
    last = max([k for k, nm in enumerate(names) if pv[nm] <= 0.10 * (k + 1) / len(names)], default=-1)
    res["bh_pass"] = {nm: (k <= last) for k, nm in enumerate(names)}
    res["pass"] = [nm for nm in names if res["bh_pass"][nm] and res["tests"][nm].get("beats_control") and res["tests"][nm]["signal"]["validation"]["mae_p90"] < 0.15]
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT / f"f01_{model}.json", "w"), indent=1, default=float)
    print("PASS:", res["pass"])
    print({k: (v["signal"].get("validation", {}).get("mean"), v["control"].get("validation", {}).get("mean")) for k, v in res["tests"].items() if "signal" in v})


if __name__ == "__main__":
    main(sys.argv[1])
