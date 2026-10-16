"""B17 F04 dump shorts with hard stops, F05 squeeze model / abstention, F18 funding-liquidation mechanics. Registry F04/F05/F18.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f04.py f04|f05|f18   -> data/reports/b17/{f04,f05,f18}.json
Entries are conditional on an OBSERVED pump (+10%/60m already happened), so no null-window weighting is needed here.
Data: B15 minute paths (ret since m0, log vol ratio, taker share, range), pump context (OI 24h, funding, Korean, notice, size, liq), tick onset
concentration for the 2,700 tick-sampled pumps (whale filter), metrics5m (5-min OI) where downloaded, Postgres liquidations (from 2026-07-17).
F04 triggers (first minute after onset where it holds, min 3; short at the next minute close):
  dd3 / dd5: 3% / 5% below the running high   |   taker_flip: first 5-min block with taker share < 0.40   |   oi_drop3: 5-min OI 3% below its post-onset high
  funding_hi: funding at onset > 0.1%/8h (enter at m0+1: no minute funding)   |   tps_fade: minute volume < 25% of the onset-minute volume (after minute 10)
  ask_stack, spread3x: not runnable (no minute book / best bid-ask) -> not_run
Stops s5 / s10 / atr(1.5x pre-onset 1h realised vol, 2-30%); exits 'retrace50 or 24h' and 't4h'. Filters all / whale-driven / size >= 25%.
Pass: validation mean net > 0 with day-clustered CI > 0 AND MAE p90 < 15%; BH q=0.10 within family.
F05: LightGBM P(MAE > 20% within h) for the dd3 short, features at trigger time; abstain when P > thr (fixed on discovery at the top 30%);
     conformal variant: abstain when the conformal upper bound of MAE (90%) > 20%. Filtered short net vs unfiltered. GRU variant not run (noted).
F18: statements implemented with the data at hand; those needing minute funding or a liquidation map are marked not_run."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b15_models as M  # noqa: E402
from b17_f03 import bh, boot_diff, leg, load  # noqa: E402

C, OUT, B = M.C, ROOT / "data/reports/b17", ROOT / "data/cache/b17"
RNG = np.random.default_rng(1704)
VAL0 = 1767225600
M0 = 60
OI_LAG = 300          # seconds; see oi_series()


def whale_flag(P):
    """top tercile of onset concentration (mean rank of big_ratio and gini) for the tick-sampled pumps; NaN elsewhere."""
    fs = [B.parent / "b15/tick_features.parquet", B.parent / "b15/tick_features_confirm.parquet"]
    T = pd.concat([pd.read_parquet(f) for f in fs if f.exists()]).drop_duplicates("pump_id")
    if T.empty:
        return pd.Series(np.nan, index=P.index)
    r = T[["big_ratio", "gini"]].rank(pct=True).mean(1)
    conc = pd.Series(r.to_numpy(), index=T["pump_id"].to_numpy())
    q = conc.quantile(2 / 3)
    return P["pump_id"].map(conc).ge(q).where(P["pump_id"].isin(conc.index))


def oi_series(P):
    """5-min OI around each pump from metrics5m: returns dict pump_id -> (times, oi) or None."""
    out = {}
    for code, g in P.groupby("code"):
        fp = B / f"metrics5m/{code}.parquet"
        if not fp.exists():
            continue
        d = pd.read_parquet(fp, columns=["create_time", "sum_open_interest"]).sort_values("create_time")
        # Vision metrics: the row stamped create_time T holds the OI measured at ~T+5min (verified 2026-09-29: it equals REST
        # openInterestHist at T+300s exactly, and that matches live OI taken 0-60 s before its own stamp). Shift to the time it is KNOWN.
        t, oi = d["create_time"].to_numpy() + OI_LAG, d["sum_open_interest"].to_numpy(float)
        for pid, ts in zip(g["pump_id"], g["ts"]):
            i0, i1 = np.searchsorted(t, ts - 3600), np.searchsorted(t, ts + 86400)
            if i1 - i0 > 12:
                out[int(pid)] = (t[i0:i1] - ts, oi[i0:i1])
    return out


def f04():
    P, A = load()
    n = len(P); day = (P["ts"] // 86400).to_numpy(); cost = P["cost"].to_numpy(); val = P["ts"].to_numpy() >= VAL0
    r = np.asarray(A[:, :, 0], np.float32); vr = np.asarray(A[:, :, 1], np.float32); tk = np.asarray(A[:, :, 2], np.float32)
    rv = np.nanstd(np.diff(r[:, :M0], axis=1), 1) * np.sqrt(60)
    whale = whale_flag(P).to_numpy(); big = (P["pump_size"] >= 0.25).to_numpy()
    OI = oi_series(P)
    trig = {k: np.full(n, -1, int) for k in ("dd3", "dd5", "taker_flip", "oi_drop3", "funding_hi", "tps_fade")}
    fund = P["funding"].to_numpy()
    for i in range(n):
        x = r[i, M0:]; run = np.maximum.accumulate(x); dd = (1 + x) / (1 + run) - 1
        for k, th in (("dd3", -0.03), ("dd5", -0.05)):
            j = np.flatnonzero(dd <= th); j = j[j >= 3]
            if len(j) and j[0] + 2 < 1440:
                trig[k][i] = int(j[0]) + 1
        tk5 = np.nanmean(tk[i, M0:M0 + 1440].reshape(-1, 5), 1)
        j = np.flatnonzero(tk5 < 0.40); j = j[j >= 1]
        if len(j) and j[0] * 5 + 6 < 1440:
            trig["taker_flip"][i] = int(j[0] * 5 + 5)
        v = np.exp(vr[i, M0:M0 + 1440]); j = np.flatnonzero(v[10:] < 0.25 * v[0]) + 10
        if len(j) and j[0] + 2 < 1440:
            trig["tps_fade"][i] = int(j[0]) + 1
        if np.isfinite(fund[i]) and fund[i] > 0.001:
            trig["funding_hi"][i] = 1
        if i in OI or int(P["pump_id"].iloc[i]) in OI:
            t, oi = OI[int(P["pump_id"].iloc[i])]
            post = t >= 0
            if post.sum() > 2:
                o = oi[post]; tt = t[post]; runo = np.maximum.accumulate(o); j = np.flatnonzero(o <= 0.97 * runo)
                if len(j) and tt[j[0]] // 60 + 2 < 1440:
                    trig["oi_drop3"][i] = int(tt[j[0]] // 60) + 1
    res = {"tests": {}, "fire_share": {k: float((v >= 0).mean()) for k, v in trig.items()}, "not_run": ["ask_stack", "spread3x"]}
    pv = {}
    STOPS = {"s5": lambda i: 0.05, "s10": lambda i: 0.10, "atr": lambda i: float(np.clip(1.5 * rv[i], 0.02, 0.30))}
    EX = {"retrace50_24h": "t24h", "t4h": "t4h"}
    FILT = {"all": np.ones(n, bool), "whale": whale == True, "size25": big}  # noqa: E712
    base_r = r  # for retrace level
    for tname, tv in trig.items():
        for sname, sf in STOPS.items():
            for ename, ex in EX.items():
                rows = []
                for i in range(n):
                    e = tv[i]
                    if e < 0:
                        continue
                    g, mae, k_out = leg(A, i, e, ex, sf(i), -1)
                    if ename == "retrace50_24h" and np.isfinite(g):
                        # take profit when price retraces 50% of the pump (base = m0-60 close) before the stop/horizon
                        x = base_r[i]; lvl = x[0] + 0.5 * (np.max(x[M0:M0 + e + 1]) - x[0]); pe = x[M0 + e]
                        fut = x[M0 + e + 1:M0 + 1441]; hit = np.flatnonzero(fut <= lvl)
                        if len(hit) and hit[0] < k_out:
                            g = -((1 + lvl) / (1 + pe) - 1)
                    rows.append((i, g - cost[i], mae))
                E = pd.DataFrame(rows, columns=["i", "net", "mae"]).dropna()
                for fname, fm in FILT.items():
                    key = f"{tname}|{sname}|{ename}|{fname}"; out = {}
                    Ef = E[fm[E["i"]]]
                    for per, sel in (("discovery", ~val[Ef["i"]]), ("validation", val[Ef["i"]])):
                        g_ = Ef[sel]
                        if len(g_) < 30:
                            continue
                        b = pd.DataFrame({"x": g_["net"].to_numpy(), "d": day[g_["i"]]}).groupby("d")["x"].agg(["sum", "count"])
                        bo = np.array([b["sum"].to_numpy()[k].sum() / b["count"].to_numpy()[k].sum() for k in (RNG.integers(0, len(b), len(b)) for _ in range(2000))])
                        out[per] = dict(n=int(len(g_)), mean=float(g_["net"].mean()), ci=M.day_ci(g_["net"].to_numpy(), day[g_["i"]]), p=float((bo <= 0).mean()),
                                        win=float((g_["net"] > 0).mean()), mae90=float(g_["mae"].quantile(0.9)), mae99=float(g_["mae"].quantile(0.99)))
                    if "validation" in out:
                        res["tests"][key] = out; pv[key] = out["validation"]["p"]
        print(tname, "done", flush=True)
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["validation"]["ci"][0] > 0 and res["tests"][k]["validation"]["mae90"] < 0.15]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f04.json", "w"), indent=1, default=float)
    print("F04 cells", len(res["tests"]), "BH", len(res["bh_pass"]), "PASS", res["pass"], res["fire_share"])
    for k, v in sorted(res["tests"].items(), key=lambda kv: -kv[1]["validation"]["mean"])[:12]:
        x = v["validation"]; print(k, x["n"], round(x["mean"], 4), [round(a, 4) for a in x["ci"]], "win", round(x["win"], 2), "mae90", round(x["mae90"], 3), "disc", round(v.get("discovery", {}).get("mean", np.nan), 4))


def f05():
    import lightgbm as lgb
    P, A = load()
    n = len(P); day = (P["ts"] // 86400).to_numpy(); cost = P["cost"].to_numpy(); ts = P["ts"].to_numpy(); val = ts >= VAL0
    r = np.asarray(A[:, :, 0], np.float32); vr = np.asarray(A[:, :, 1], np.float32); tk = np.asarray(A[:, :, 2], np.float32)
    rows = []
    for i in range(n):
        x = r[i, M0:]; run = np.maximum.accumulate(x); dd = (1 + x) / (1 + run) - 1
        j = np.flatnonzero(dd <= -0.03); j = j[j >= 3]
        if not len(j) or j[0] + 2 >= 1440:
            continue
        e = int(j[0]) + 1; pe = x[e]
        feats = dict(trig_min=e, gain_at_trig=float(run[e]), pump_size=P["pump_size"].iloc[i], vol_ratio_0_5=P["vol_ratio_0_5"].iloc[i], taker_0_5=P["taker_0_5"].iloc[i],
                     taker_last5=float(np.nanmean(tk[i, M0 + max(0, e - 5):M0 + e + 1])), vol_last5=float(np.nanmean(vr[i, M0 + max(0, e - 5):M0 + e + 1])),
                     oi_chg_24h=P["oi_chg_24h"].iloc[i], funding=P["funding"].iloc[i], korean=P["korean_listed"].iloc[i], ldv=np.log(P["dv24"].iloc[i]), state=P["state"].iloc[i])
        for hz in (60, 240, 1440):
            fut = x[e + 1:e + 1 + hz]
            if len(fut) < 10:
                continue
            mae = float(np.nanmax((1 + fut) / (1 + pe) - 1)); net = -((1 + fut[-1]) / (1 + pe) - 1) - cost[i]
            rows.append(dict(i=i, hz=hz, mae=mae, y=int(mae > 0.20), net=net, **feats))
    E = pd.DataFrame(rows); F = [c for c in E.columns if c not in ("i", "hz", "mae", "y", "net")]
    res = {"tests": {}, "not_run": ["gru variants (torch) - lgbm + conformal only"]}; pv = {}
    for hz, g in E.groupby("hz"):
        g = g.reset_index(drop=True); tsg = ts[g["i"]]; d_ = day[g["i"]]; v = tsg >= VAL0
        D = g[~v]; H = g[v]
        if len(D) < 200 or len(H) < 100:
            continue
        clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=100, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5, verbose=-1, n_jobs=6)
        clf.fit(D[F].fillna(0), D["y"])
        pD = clf.predict_proba(D[F].fillna(0))[:, 1]; thr = float(np.quantile(pD, 0.70)); pH = clf.predict_proba(H[F].fillna(0))[:, 1]
        from sklearn.metrics import roc_auc_score
        auc = float(roc_auc_score(H["y"], pH)) if H["y"].nunique() > 1 else None
        # conformal: split-conformal 90% upper bound on MAE from a quantile regressor
        qr = lgb.LGBMRegressor(objective="quantile", alpha=0.9, n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=100, verbose=-1, n_jobs=6)
        cal_mask = RNG.random(len(D)) < 0.3; qr.fit(D[F][~cal_mask].fillna(0), D["mae"][~cal_mask])
        resid = D["mae"][cal_mask].to_numpy() - qr.predict(D[F][cal_mask].fillna(0)); qhat = float(np.quantile(resid, 0.9)); ub = qr.predict(H[F].fillna(0)) + qhat
        for name, keep in (("lgbm_abstain", pH <= thr), ("conformal_abstain", ub <= 0.20)):
            a, b = H[keep], H[~keep]
            if len(a) < 30:
                continue
            ci, p = boot_diff(a["net"].to_numpy(), d_[v][keep], H["net"].to_numpy(), d_[v])
            key = f"{name}|{hz}m"
            res["tests"][key] = dict(auc=auc, n_keep=int(len(a)), keep_share=float(keep.mean()), net_keep=float(a["net"].mean()), net_all=float(H["net"].mean()),
                                     ci_keep=M.day_ci(a["net"].to_numpy(), d_[v][keep]), diff_ci=ci, p=p, mae90_keep=float(a["mae"].quantile(0.9)), mae90_all=float(H["mae"].quantile(0.9)))
            pv[key] = p
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["ci_keep"][0] > 0]
    json.dump(res, open(OUT / "f05.json", "w"), indent=1, default=float)
    print("F05 PASS", res["pass"])
    for k, v in res["tests"].items():
        print(k, "auc", v["auc"], "keep", round(v["keep_share"], 2), "net_keep", round(v["net_keep"], 4), [round(a, 4) for a in v["ci_keep"]], "net_all", round(v["net_all"], 4), "mae90", round(v["mae90_keep"], 3), round(v["mae90_all"], 3))


def f18():
    P, A = load()
    n = len(P); day = (P["ts"] // 86400).to_numpy(); cost = P["cost"].to_numpy(); ts = P["ts"].to_numpy(); val = ts >= VAL0
    r = np.asarray(A[:, :, 0], np.float32)
    OI = oi_series(P)
    # liquidations (Binance, from 2026-07-17): forced-buy (short liq) share in the first 5 minutes after onset
    try:
        from src.data.storage import get_storage
        with get_storage()._connect() as con:
            L = pd.DataFrame(con.raw.execute("select symbol, timestamp, side, notional from liquidations where exchange='binance'").fetchall())
        L["code"] = L["symbol"].str.replace("/", "", regex=False)
        LQ = {c: (g["timestamp"].to_numpy(), g["side"].to_numpy(), g["notional"].to_numpy()) for c, g in L.groupby("code")}
    except Exception as e:  # noqa: BLE001
        print("liquidations unavailable", e); LQ = {}
    liq_share = np.full(n, np.nan); casc_end = np.full(n, -1, int)
    for i, (code, t0) in enumerate(zip(P["code"], ts)):
        if code not in LQ:
            continue
        t, side, notl = LQ[code]; a, b = np.searchsorted(t, t0), np.searchsorted(t, t0 + 300)
        if b > a:
            s = notl[a:b][side[a:b] == "short"].sum(); liq_share[i] = s / max(notl[a:b].sum(), 1e-9)
        # cascade end: >= 5 short liquidations in 60s, then 3 minutes with none -> long at that minute
        w = t[(t >= t0) & (t < t0 + 3600)]; sw = side[(t >= t0) & (t < t0 + 3600)]; w = w[sw == "short"]
        if len(w) >= 5:
            for k in range(len(w) - 4):
                if w[k + 4] - w[k] <= 60:
                    end = w[k + 4]; nxt = w[w > end]
                    if not len(nxt) or nxt[0] - end > 180:
                        m = int((end - t0) // 60) + 3
                        if m + 2 < 1440:
                            casc_end[i] = m
                        break
    oi_up10 = np.full(n, np.nan); oi_div = np.full(n, np.nan)
    for i in range(n):
        pid = int(P["pump_id"].iloc[i])
        if pid in OI:
            t, oi = OI[pid]; pre = oi[(t >= -900) & (t <= 0)]; post = oi[(t > 0) & (t <= 3600)]
            if len(pre) and len(post):
                oi_up10[i] = post.max() / pre[-1] - 1; oi_div[i] = post[-1] / pre[-1] - 1
    hod_min = (ts % 28800) // 60                                      # minutes into the 8h funding period
    ret_e = lambda e, h, side: side * ((1 + r[:, M0 + e + h]) / (1 + r[:, M0 + e]) - 1) - cost   # simple returns
    STAT = {
        "pre_settlement_short_at_settlement": (hod_min >= 465, lambda: -((1 + r[:, M0 + 480]) / (1 + r[:, M0 + 15]) - 1) - cost, "pump < 15 min before funding settlement: short at settlement, exit +8h"),
        "short_liq_share>30%_continuation_long": (liq_share > 0.30, lambda: ret_e(5, 60, 1), "forced buying > 30% of first-5m liquidations: long m0+5 -> +1h"),
        "short_liq_share>30%_is_squeeze_fade": (liq_share > 0.30, lambda: ret_e(5, 240, -1), "same set, short m0+5 -> +4h (squeeze, not demand)"),
        "oi_falls_while_price_rises_fade": (oi_div < -0.02, lambda: ret_e(60, 240, -1), "OI down 2% over the pump hour (short covering): short m0+60 -> +4h"),
        "oi_up>10%_deeper_dump_short": (oi_up10 > 0.10, lambda: ret_e(60, 1380, -1), "OI +10% during the pump (new longs): short m0+60 -> 24h"),
        "cascade_end_long": (casc_end >= 0, None, "long at the end of a short-liquidation cascade -> +1h"),
        "funding>0.1%_at_onset_short": (P["funding"].to_numpy() > 0.001, lambda: ret_e(1, 480, -1), "funding > 0.1%/8h at onset: short m0+1 -> +8h (carry + crowding)"),
    }
    res = {"tests": {}, "not_run": ["liquidation cluster map", "funding flip during pump (no minute funding)", "cross-exchange OI divergence (coinalyze hourly not joined here)", "predicted funding at peak"]}
    pv = {}
    for name, (mask, fn, desc) in STAT.items():
        if fn is None:
            g = np.full(n, np.nan)
            for i in np.flatnonzero(mask):
                e = casc_end[i]; g[i] = (1 + r[i, M0 + e + 60]) / (1 + r[i, M0 + e]) - 1 - cost[i]
        else:
            g = fn()
        m = np.nan_to_num(mask.astype(float)).astype(bool) & np.isfinite(g)
        out = {"statement": desc, "n_eligible": int(m.sum())}
        for per, sel in (("discovery", ~val), ("validation", val)):
            a = sel & m; b = sel & ~np.nan_to_num(mask.astype(float)).astype(bool) & np.isfinite(g)
            if a.sum() < 30:
                continue
            d = dict(n=int(a.sum()), mean=float(g[a].mean()), ci=M.day_ci(g[a], day[a]))
            bo = pd.DataFrame({"x": g[a], "d": day[a]}).groupby("d")["x"].agg(["sum", "count"])
            bb = np.array([bo["sum"].to_numpy()[k].sum() / bo["count"].to_numpy()[k].sum() for k in (RNG.integers(0, len(bo), len(bo)) for _ in range(2000))])
            d["p"] = float((bb <= 0).mean())
            if b.sum() >= 30:
                d["diff_ci"], d["p_vs_rest"] = boot_diff(g[a], day[a], g[b], day[b]); d["mean_rest"] = float(g[b].mean())
            out[per] = d
        res["tests"][name] = out
        if "validation" in out:
            pv[name] = out["validation"]["p"]
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["validation"]["ci"][0] > 0]
    json.dump(res, open(OUT / "f18.json", "w"), indent=1, default=float)
    print("F18 PASS", res["pass"])
    for k, v in res["tests"].items():
        x = v.get("validation"); print(k, v["n_eligible"], None if not x else (x["n"], round(x["mean"], 4), [round(a, 4) for a in x["ci"]], "rest", round(x.get("mean_rest", np.nan), 4)))


if __name__ == "__main__":
    {"f04": f04, "f05": f05, "f18": f18}[sys.argv[1]]()
