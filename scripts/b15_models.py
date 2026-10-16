"""B15_1 discrete-time peak hazard + exit rule, B15_5 post-peak dump model (research/batch_B15_B16.yaml). LightGBM, no torch.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b15_models.py 1|5
Out: data/reports/b15/b15_{1,5}.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

C = ROOT / "data/cache"
OUT = ROOT / "data/reports/b15"
D_, DISC1, CAL0 = 86400, 1756684800, 1756684800 - 60 * 86400
RNG = np.random.default_rng(15)
PARAMS = dict(n_estimators=500, learning_rate=0.03, num_leaves=63, min_child_samples=300, subsample=0.8, subsample_freq=1,
              colsample_bytree=0.8, reg_lambda=10.0, verbose=-1, n_jobs=6)


def slip(dv):
    return np.select([dv > 1e8, dv > 2e7, dv > 5e6], [0.0002, 0.0005, 0.0010], 0.0020)


def day_ci(x, days, reps=4000):
    d = pd.DataFrame({"x": x, "d": days}).dropna().groupby("d")["x"].agg(["sum", "count"])
    su, cn = d["sum"].to_numpy(), d["count"].to_numpy()
    b = np.array([su[k].sum() / cn[k].sum() for k in (RNG.integers(0, len(su), len(su)) for _ in range(reps))])
    return [float(v) for v in np.percentile(b, [2.5, 97.5])]


def pump_context():
    """Pump table + context from the B14 4h tensor (nearest earlier row for the coin) + regime + size/liquidity/cause slices."""
    P = pd.read_parquet(C / "b15/pumps.parquet")
    T = pd.read_parquet(C / "b14/tab.parquet", columns=["ts", "code", "oi_chg_24h", "spot_share_6h", "funding", "korean_listed", "korea_surge_6h",
                                                          "h_since_upbit_listing", "h_since_binance_listing", "ldv"])
    P = P.sort_values("ts")
    T = T.sort_values("ts")
    P = pd.merge_asof(P, T, on="ts", by="code", direction="backward", tolerance=4 * 3600)
    R = pd.read_parquet(C / "b14/regime.parquet")
    P["day"] = P["ts"] // D_
    P = P.merge(R[["day", "state"]], on="day", how="left")
    P["state"] = P["state"].fillna(-1).astype(int)
    P["size_bucket"] = pd.cut(P["pump_size"], [0.0999, 0.15, 0.25, 99], labels=["10-15", "15-25", "25+"]).astype(str)
    P["cause"] = np.where((P["h_since_upbit_listing"] < 24) | (P["h_since_binance_listing"] < 24), "notice", "none")
    P["liq"] = pd.qcut(P["dv24"].rank(method="first"), 3, labels=["low", "mid", "high"]).astype(str)
    P["kr"] = (P["korean_listed"] > 0).astype(int)
    P["hold"] = P["ts"] >= DISC1
    P["cost"] = 2 * (L.FEE + slip(P["dv24"].to_numpy()))
    return P.sort_values("pump_id").reset_index(drop=True)


def slices(df, col_net="net"):
    out = {}
    for name in ("state", "size_bucket", "liq", "kr", "cause"):
        out[name] = {str(k): dict(n=int(len(g)), mean=float(g[col_net].mean()), ci=day_ci(g[col_net].to_numpy(), g["day"].to_numpy()))
                     for k, g in df.groupby(name) if len(g) >= 30 and k != -1}
    return out


# ------------------------------------------------------------------ B15_1
def exp1():
    P = pump_context()
    paths = np.load(C / "b15/paths.npy", mmap_mode="r")
    K = np.arange(1, 241, 2)                          # evaluate every 2 minutes, minutes 1..239 after onset
    rows = []
    for p in P.itertuples():
        a = np.asarray(paths[p.pump_id], np.float32)  # index 60 = m0
        cr, vr, tk, rg = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
        for k in K:
            if k >= p.min_to_peak + 5:                # after the peak window the event has happened (censor)
                break
            m = 60 + k
            rows.append((p.pump_id, k, cr[m], cr[m] - cr[m - 5], vr[m - 4:m + 1].mean() - vr[61:66].mean(), tk[m - 4:m + 1].mean(),
                         rg[m - 4:m + 1].mean(), cr[60 + 1:m + 1].max() - cr[m], int(p.min_to_peak > k and p.min_to_peak <= k + 5)))
    H = pd.DataFrame(rows, columns=["pump_id", "k", "gain", "r5", "vol_decay", "taker5", "range5", "dd_from_high", "y"])
    ctx = P[["pump_id", "pump_size", "dv24", "oi_chg_24h", "spot_share_6h", "funding", "korean_listed", "state", "hold", "ts"]]
    H = H.merge(ctx, on="pump_id")
    feats = ["k", "gain", "r5", "vol_decay", "taker5", "range5", "dd_from_high", "pump_size", "dv24", "oi_chg_24h", "spot_share_6h",
             "funding", "korean_listed", "state"]
    tr, ca, te = (~H["hold"]) & (H["ts"] < CAL0), (~H["hold"]) & (H["ts"] >= CAL0), H["hold"]
    m = lgb.LGBMClassifier(**PARAMS).fit(H[tr][feats], H[tr]["y"])
    iso = IsotonicRegression(out_of_bounds="clip").fit(m.predict_proba(H[ca][feats])[:, 1], H[ca]["y"])
    H["h"] = iso.predict(m.predict_proba(H[feats])[:, 1])
    res = {"n_pumps": int(len(P)), "rows": int(len(H))}
    for hz, lo, hi in (("5m", 1, 15), ("15m", 15, 60), ("60m", 60, 240)):
        s = H[te & (H["k"] >= lo) & (H["k"] < hi)]
        res[f"auc_{hz}"] = float(roc_auc_score(s["y"], s["h"])) if s["y"].nunique() > 1 else None
    # exit simulation on minute paths: long at m0+1, exit at first k where hazard > thr (next-minute price), else at +240m
    paths_f = {pid: np.asarray(paths[pid][:, 0], np.float32) for pid in P["pump_id"]}

    def simulate(pids, thr):
        out = []
        g = H[H["pump_id"].isin(pids)]
        first = g[g["h"] > thr].groupby("pump_id")["k"].min()
        for pid in pids:
            cr = paths_f[pid]
            e = cr[61]
            k = int(first.get(pid, 240))
            x = cr[60 + min(k + 1, 240)]
            out.append((pid, (1 + x) / (1 + e) - 1))
        return pd.DataFrame(out, columns=["pump_id", "gross"])
    disc_ids = P.loc[~P["hold"], "pump_id"].to_numpy()
    hold_ids = P.loc[P["hold"], "pump_id"].to_numpy()
    grid = np.quantile(H.loc[tr, "h"], [0.5, 0.7, 0.8, 0.9, 0.95, 0.98])
    best = max(grid, key=lambda thr: (simulate(disc_ids, thr).merge(P, on="pump_id").eval("gross - cost")).mean())
    S = simulate(hold_ids, best).merge(P, on="pump_id")
    S["net"] = S["gross"] - S["cost"]
    fixed = {}
    for nm, mins in (("15m", 15), ("60m", 60), ("4h", 240), ("24h", 1440)):
        g = [(pid, (1 + paths_f[pid][60 + mins]) / (1 + paths_f[pid][61]) - 1) for pid in hold_ids]
        F = pd.DataFrame(g, columns=["pump_id", "gross"]).merge(P, on="pump_id")
        F["net"] = F["gross"] - F["cost"]
        fixed[nm] = dict(mean=float(F["net"].mean()), ci=day_ci(F["net"].to_numpy(), F["day"].to_numpy()))
        disc_F = [(1 + paths_f[pid][60 + mins]) / (1 + paths_f[pid][61]) - 1 for pid in disc_ids]
        fixed[nm]["disc_mean_gross"] = float(np.mean(disc_F))
    best_fixed = max(fixed, key=lambda k: fixed[k]["disc_mean_gross"])      # best fixed exit chosen on discovery
    Fb = pd.DataFrame([(pid, (1 + paths_f[pid][60 + {"15m": 15, "60m": 60, "4h": 240, "24h": 1440}[best_fixed]]) / (1 + paths_f[pid][61]) - 1)
                       for pid in hold_ids], columns=["pump_id", "gross"]).merge(P, on="pump_id")
    Fb["net"] = Fb["gross"] - Fb["cost"]
    diff = S.set_index("pump_id")["net"] - Fb.set_index("pump_id")["net"]
    res.update(threshold=float(best), hazard_exit=dict(mean=float(S["net"].mean()), ci=day_ci(S["net"].to_numpy(), S["day"].to_numpy())),
               fixed_exits=fixed, best_fixed_on_discovery=best_fixed,
               hazard_minus_best_fixed=dict(mean=float(diff.mean()), ci=day_ci(diff.to_numpy(), P.set_index("pump_id").loc[diff.index, "day"].to_numpy())),
               slices=slices(S))
    reg_pos = sum(v["mean"] > 0 for v in res["slices"]["state"].values())
    size_ok = all(v["ci"][1] >= 0 for v in res["slices"]["size_bucket"].values())
    res["pass"] = bool(res["hazard_exit"]["ci"][0] > 0 and res["hazard_minus_best_fixed"]["ci"][0] > 0 and reg_pos >= 2 and size_ok)
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT / "b15_1.json", "w"), indent=1, default=float)
    print(json.dumps({k: v for k, v in res.items() if k != "slices"}, default=float, indent=1))


# ------------------------------------------------------------------ B15_5
def exp5():
    P = pump_context()
    paths = np.load(C / "b15/paths.npy", mmap_mode="r")
    rows = []
    for p in P.itertuples():
        cr = np.asarray(paths[p.pump_id][:, 0], np.float64)
        vr = np.asarray(paths[p.pump_id][:, 1], np.float64)
        base = cr[0]                                            # price 60 min before onset (relative to m0)
        runmax = np.maximum.accumulate(cr[60:])
        dd3 = np.flatnonzero(((1 + cr[60:]) / (1 + runmax) - 1) <= -0.03)
        dd3 = dd3[dd3 >= 3]
        if len(dd3) == 0 or dd3[0] + 1 >= len(cr) - 61:
            continue
        tk = dd3[0]                                             # trigger minute after onset
        hi = runmax[tk]
        level = base + 0.5 * (hi - base)
        fut = cr[60 + tk + 1:]
        hit = np.flatnonzero(fut <= level)
        entry = cr[60 + tk + 1]
        exit_px = level if len(hit) else fut[-1]
        rows.append(dict(pump_id=p.pump_id, trig_min=int(tk), gain_at_trig=float(hi), pump_total=float(hi - base),
                         vol_at_trig=float(vr[60 + max(tk - 4, 0):60 + tk + 1].mean()), y=int(len(hit) > 0),
                         gross_short=float(-((1 + exit_px) / (1 + entry) - 1))))
    E = pd.DataFrame(rows).merge(P, on="pump_id")
    E["taker_late"] = np.where(E["trig_min"] > 60, E["taker_20_60"], np.nan)
    feats = ["trig_min", "gain_at_trig", "pump_total", "vol_at_trig", "pump_size", "taker_0_5", "taker_late", "vol_ratio_0_5", "dv24",
             "oi_chg_24h", "funding", "spot_share_6h", "korean_listed", "korea_surge_6h", "state"]
    E["notice"] = (E["cause"] == "notice").astype(int)
    feats.append("notice")
    tr, ca, te = (~E["hold"]) & (E["ts"] < CAL0), (~E["hold"]) & (E["ts"] >= CAL0), E["hold"]
    m = lgb.LGBMClassifier(**PARAMS).fit(E[tr][feats], E[tr]["y"])
    iso = IsotonicRegression(out_of_bounds="clip").fit(m.predict_proba(E[ca][feats])[:, 1], E[ca]["y"])
    E["p"] = iso.predict(m.predict_proba(E[feats])[:, 1])
    E["net"] = E["gross_short"] - E["cost"]
    disc = E[~E["hold"]]
    grid = np.quantile(disc["p"], [0.3, 0.5, 0.6, 0.7, 0.8, 0.9])
    thr = max(grid, key=lambda t: disc.loc[disc["p"] > t, "net"].mean())
    H = E[te]
    s = H[H["p"] > thr]
    aucs = []
    days = H["day"].unique()
    grp = {d: g for d, g in H.groupby("day")}
    for _ in range(500):
        b = pd.concat([grp[d] for d in RNG.choice(days, len(days))])
        if b["y"].nunique() > 1:
            aucs.append(roc_auc_score(b["y"], b["p"]))
    res = dict(n_trig_disc=int((~E["hold"]).sum()), n_trig_hold=int(len(H)), base_rate_hold=float(H["y"].mean()),
               auc=float(roc_auc_score(H["y"], H["p"])), auc_ci=[float(v) for v in np.percentile(aucs, [2.5, 97.5])],
               threshold=float(thr), n_trades=int(len(s)), short_all_mean=float(H["net"].mean()),
               short_filtered=dict(mean=float(s["net"].mean()), ci=day_ci(s["net"].to_numpy(), s["day"].to_numpy())),
               importance=pd.Series(m.booster_.feature_importance("gain"), index=feats).sort_values(ascending=False).head(8).round(0).to_dict(),
               slices=slices(s))
    reg_pos = sum(v["mean"] > 0 for v in res["slices"]["state"].values())
    res["pass"] = bool(res["auc"] > 0.6 and res["auc_ci"][0] > 0.55 and res["short_filtered"]["ci"][0] > 0 and reg_pos >= 2)
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT / "b15_5.json", "w"), indent=1, default=float)
    print(json.dumps({k: v for k, v in res.items() if k != "slices"}, default=float, indent=1))


if __name__ == "__main__":
    {"1": exp1, "5": exp5}[sys.argv[1]]()
