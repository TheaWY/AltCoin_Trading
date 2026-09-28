"""B17 families F03 (continuation filters), F15 (pump stops), F06 (multi-day) on the B15 pump paths. Registry: research/batch_B17_registry.yaml.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f03.py f03|f15|f06      -> data/reports/b17/{fam}.json
Validation = onsets from 2026-01-01; discovery reported for reference. Day-clustered bootstrap CIs; BH q=0.10 within family.
Deviations from the registry text, fixed before running: (1) 'hazard exit' is not reproducible from a saved model, replaced by a 3% trailing stop
from the running high (the B15_5 peak-confirmation rule) - labelled trail3; (2) 'OI up since onset' uses the 4h-tensor OI change (no minute OI);
(3) the depth-refill filter has no minute-level book data and is skipped (marked not_run); (4) F15 'precursor long' entry is dropped because F01 failed.
F03 control = the complement (unfiltered) set on the same entry; test = filtered mean minus complement mean.
F15: same entries, stop vs no-stop, paired; LCE = share of losers whose loss the stop cut by >= 50% minus share of eventual winners it stopped out.
F06 uses the hourly panel (b7) for horizons beyond 24h."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402

C, OUT = M.C, ROOT / "data/reports/b17"
RNG = np.random.default_rng(1703)
VAL0 = 1767225600
M0 = 60


def boot_diff(a, da, b, db, reps=4000):
    """day-clustered bootstrap of mean(a)-mean(b); returns ci, one-sided p(diff<=0)."""
    A = pd.DataFrame({"x": a, "d": da}).groupby("d")["x"].agg(["sum", "count"]); B = pd.DataFrame({"x": b, "d": db}).groupby("d")["x"].agg(["sum", "count"])
    out = []
    for _ in range(reps):
        ka = RNG.integers(0, len(A), len(A)); kb = RNG.integers(0, len(B), len(B))
        out.append(A["sum"].to_numpy()[ka].sum() / A["count"].to_numpy()[ka].sum() - B["sum"].to_numpy()[kb].sum() / B["count"].to_numpy()[kb].sum())
    out = np.array(out)
    return [float(x) for x in np.percentile(out, [2.5, 97.5])], float((out <= 0).mean())


def bh(pv, q=0.10):
    names = sorted(pv, key=pv.get)
    last = max([k for k, nm in enumerate(names) if pv[nm] <= q * (k + 1) / len(names)], default=-1)
    return [nm for k, nm in enumerate(names) if k <= last]


def load():
    P = M.pump_context()
    A = np.load(C / "b15/paths.npy", mmap_mode="r")
    return P, A


def leg(A, pid, e_min, exit_, stop=None, side=1):
    """gross return and MAE for a long (side=1) / short (-1) entered at minute e_min after m0 (close), given exit rule and optional stop."""
    r = np.asarray(A[pid][:, 0], np.float64)
    e = r[M0 + e_min]
    path = (1 + r[M0 + e_min + 1:]) / (1 + e) - 1          # minute returns from entry
    if len(path) == 0 or not np.isfinite(e):
        return np.nan, np.nan, np.nan
    horizon = {"t1h": 60, "t4h": 240, "t24h": 1440 - e_min, "trail3": 1440 - e_min}[exit_]
    path = path[:horizon] * side
    mae = -np.nanmin(np.r_[0, path])
    out_i = len(path) - 1; out = path[-1]
    if exit_ == "trail3":
        run = np.maximum.accumulate(np.r_[0, path])[1:]
        k = np.flatnonzero((1 + path) / (1 + run) - 1 <= -0.03)
        if len(k):
            out_i = k[0]; out = path[k[0]]
    if stop is not None:
        k = np.flatnonzero(path <= -stop)
        if len(k) and k[0] <= out_i:
            out_i = k[0]; out = max(path[k[0]], -stop - 0.002)   # slippage past the stop
    return float(out), float(mae), int(out_i)


def f03():
    P, A = load()
    n = len(P)
    r = np.asarray(A[:, :, 0], np.float32); vr = np.asarray(A[:, :, 1], np.float32); tk = np.asarray(A[:, :, 2], np.float32)
    acc = (r[:, M0 + 5] - r[:, M0 + 3]) - (r[:, M0 + 3] - r[:, M0 + 1])
    vol_decay = np.exp(np.nanmean(vr[:, M0 + 3:M0 + 6], 1)) / np.exp(np.nanmean(vr[:, M0:M0 + 2], 1))
    taker_late = np.nanmean(tk[:, M0 + 2:M0 + 6], 1) > np.nanmean(tk[:, M0:M0 + 2], 1)
    last = P.sort_values(["code", "ts"]).groupby("code")["ts"].diff().reindex(P.index)
    day = P["ts"] // 86400
    first_of_day = ~pd.Series(list(zip(P["code"], day))).duplicated(keep="first").to_numpy()
    tsv = np.sort(P["ts"].to_numpy()); burst = np.searchsorted(tsv, P["ts"] + 180, "right") - np.searchsorted(tsv, P["ts"] - 180) - 1
    # BTC 1h return before onset from the hourly panel
    ts_h, codes, X = L.data(); bi = codes.index("BTCUSDT")
    hi = np.clip(np.searchsorted(ts_h, P["ts"].to_numpy(), "right") - 1, 1, len(ts_h) - 1)
    btc1h = X["lc"][hi, bi] - X["lc"][hi - 1, bi]
    # thin-book precursor: depth/volume at the hour before onset, bottom quintile across pumps
    d2v = np.full(n, np.nan)
    for c, g in P.groupby("code"):
        fp = C / f"depth1h/{c}.parquet"
        if fp.exists():
            d = pd.read_parquet(fp, columns=["ts", "bid_1", "ask_1"]).sort_values("ts")
            k = np.searchsorted(d["ts"].to_numpy(), g["ts"].to_numpy(), "right") - 1
            ok = k >= 0
            d2v[g.index[ok]] = (d["bid_1"].to_numpy()[k[ok]] + d["ask_1"].to_numpy()[k[ok]]) / g["dv24"].to_numpy()[ok]
    R = pd.read_parquet(C / "b14/regime.parquet")
    bull = int(R.groupby("state")["mkt_ret"].mean().idxmax()) if "mkt_ret" in R else int(R["state"].mode()[0])
    FILT = {
        "vol_decay>0.6": vol_decay > 0.6, "taker_share_rising": taker_late, "oi_up_24h": P["oi_chg_24h"].to_numpy() > 0,
        "spot_share_high": P["spot_share_6h"].to_numpy() > np.nanmedian(P["spot_share_6h"]), "korea_surge>0": P["korea_surge_6h"].to_numpy() > 0,
        "depth_refill": None, "acceleration>0": acc > 0, "no_notice_24h": (P["cause"] == "none").to_numpy(),
        "last_pump>30d": (last.isna() | (last > 30 * 86400)).to_numpy(), "btc_1h>0": btc1h > 0, "hmm_bull": (P["state"] == bull).to_numpy(),
        "funding<0.01%": P["funding"].to_numpy() < 0.0001, "thin_book_p20": d2v < np.nanquantile(d2v, 0.2), "first_pump_of_day": first_of_day,
        "no_burst<3": burst < 3,
    }
    res = {"bull_state": bull, "tests": {}}
    legs = {}
    for ex in ("t1h", "t24h", "trail3"):
        for st in (None, 0.05):
            g, mae = zip(*[leg(A, i, 5, ex, st)[:2] for i in range(n)])
            legs[(ex, st)] = (np.array(g) - P["cost"].to_numpy(), np.array(mae))
    pv = {}
    for fname, mask in FILT.items():
        if mask is None:
            res["tests"][fname] = {"not_run": "no minute-level book data"}; continue
        mask = np.nan_to_num(mask.astype(float)).astype(bool)
        for (ex, st), (net, mae) in legs.items():
            key = f"{fname}|{ex}|{'s5' if st else 'nostop'}"
            out = {}
            for per, sel in (("discovery", ~P["hold"].to_numpy() | (P["ts"].to_numpy() < VAL0)), ("validation", P["ts"].to_numpy() >= VAL0)):
                a, b = sel & mask & np.isfinite(net), sel & ~mask & np.isfinite(net)
                if a.sum() < 30 or b.sum() < 30:
                    continue
                ci, p = boot_diff(net[a], day[a], net[b], day[b])
                out[per] = dict(n_filt=int(a.sum()), n_comp=int(b.sum()), mean_filt=float(net[a].mean()), mean_comp=float(net[b].mean()),
                                ci_filt=M.day_ci(net[a], day[a].to_numpy()), diff_ci=ci, p=p, mae90_filt=float(np.nanquantile(mae[a], 0.9)))
            if "validation" in out:
                pv[key] = out["validation"]["p"]; res["tests"][key] = out
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["validation"]["ci_filt"][0] > 0 and res["tests"][k]["validation"]["mae90_filt"] < 0.15]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f03.json", "w"), indent=1, default=float)
    print("F03 BH", res["bh_pass"], "PASS", res["pass"])
    top = sorted(((k, v["validation"]) for k, v in res["tests"].items() if "validation" in v), key=lambda kv: -kv[1]["mean_filt"])[:8]
    for k, v in top:
        print(k, v["n_filt"], round(v["mean_filt"], 4), round(v["mean_comp"], 4), [round(x, 4) for x in v["diff_ci"]], round(v["p"], 3))


def f15():
    P, A = load()
    n = len(P); day = (P["ts"] // 86400).to_numpy(); cost = P["cost"].to_numpy()
    rv = np.nanstd(np.diff(np.asarray(A[:, :M0, 0], np.float32), axis=1), 1) * np.sqrt(60)   # 1h realised vol before onset
    ENT = {"m0+1_long": (1, 1), "post_peak_short": (None, -1)}
    STOPS = {"s5": lambda i: 0.05, "s10": lambda i: 0.10, "atr": lambda i: float(np.clip(1.5 * rv[i], 0.02, 0.30)), "time4h+s10": lambda i: 0.10, "trail+s10": lambda i: 0.10}
    res = {"tests": {}}; pv = {}
    for ename, (emin, side) in ENT.items():
        base = []
        for i in range(n):
            if emin is None:   # post-peak short trigger: first 3% dd from running high after onset (B15_5), enter next minute
                r = np.asarray(A[i][:, 0], np.float64); run = np.maximum.accumulate(r[M0:])
                k = np.flatnonzero((1 + r[M0:]) / (1 + run) - 1 <= -0.03); k = k[k >= 3]
                e = int(k[0]) + 1 if len(k) and k[0] + 2 < 1440 else None
            else:
                e = emin
            base.append(e)
        for sname, sfun in STOPS.items():
            ex = "t4h" if sname.startswith("time") else ("trail3" if sname.startswith("trail") else "t24h")
            rows = []
            for i, e in enumerate(base):
                if e is None:
                    continue
                g0, mae0, _ = leg(A, i, e, "t24h", None, side)
                g1, mae1, _ = leg(A, i, e, ex, sfun(i), side)
                rows.append((i, g0 - cost[i], g1 - cost[i]))
            E = pd.DataFrame(rows, columns=["i", "net0", "net1"]).dropna()
            E["day"] = day[E["i"]]; E["val"] = P["ts"].to_numpy()[E["i"]] >= VAL0
            out = {}
            for per, sel in (("discovery", ~E["val"]), ("validation", E["val"])):
                g = E[sel]
                if len(g) < 30:
                    continue
                losers = g[g["net0"] < 0]; winners = g[g["net0"] > 0]
                lce = float((losers["net1"] >= 0.5 * losers["net0"]).mean() - (winners["net1"] < 0.5 * winners["net0"]).mean())
                d = (g["net1"] - g["net0"]).to_numpy()
                ci = M.day_ci(d, g["day"].to_numpy())
                b = pd.DataFrame({"x": d, "d": g["day"]}).groupby("d")["x"].agg(["sum", "count"])
                bo = np.array([b["sum"].to_numpy()[k].sum() / b["count"].to_numpy()[k].sum() for k in (RNG.integers(0, len(b), len(b)) for _ in range(4000))])
                out[per] = dict(n=int(len(g)), mean_nostop=float(g["net0"].mean()), mean_stop=float(g["net1"].mean()), diff_ci=ci, p=float((bo <= 0).mean()), lce=lce,
                                avg_loss_nostop=float(losers["net0"].mean()), avg_loss_stop=float(g.loc[g["net1"] < 0, "net1"].mean()))
            key = f"{ename}|{sname}"; res["tests"][key] = out
            if "validation" in out:
                pv[key] = out["validation"]["p"]
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["validation"]["lce"] > 0 and res["tests"][k]["validation"]["diff_ci"][0] > 0]
    json.dump(res, open(OUT / "f15.json", "w"), indent=1, default=float)
    print("F15 BH", res["bh_pass"], "PASS", res["pass"])
    for k, v in res["tests"].items():
        if "validation" in v:
            x = v["validation"]; print(k, x["n"], round(x["mean_nostop"], 4), round(x["mean_stop"], 4), [round(a, 4) for a in x["diff_ci"]], "lce", round(x["lce"], 3))


def f06():
    """Multi-day statements on the hourly panel. Each = (condition mask over pumps, entry hour offset from onset hour, side, horizon hours)."""
    P, A = load()
    ts_h, codes, X = L.data(); lc = X["lc"]; ci_ = {c: j for j, c in enumerate(codes)}
    P = P[P["code"].isin(ci_)].reset_index(drop=True)
    j = P["code"].map(ci_).to_numpy(); h0 = np.searchsorted(ts_h, P["ts"].to_numpy(), "right")     # first hourly row closing after m0
    day = (P["ts"] // 86400).to_numpy(); cost = P["cost"].to_numpy(); val = P["ts"].to_numpy() >= VAL0
    hod = ((P["ts"] // 3600) % 24).to_numpy(); kst = (hod + 9) % 24; dow = ((P["ts"] // 86400 + 4) % 7).to_numpy()   # 1970-01-01 = Thursday
    srt = P.sort_values(["code", "ts"]); prev = srt.groupby("code")["ts"].shift()
    P["prev_gap"] = (srt["ts"] - prev).reindex(P.index)
    ser = np.zeros(len(P), bool)
    for c, g in P.groupby("code"):
        t = g["ts"].to_numpy(); ser[g.index] = (np.searchsorted(t, t) - np.searchsorted(t, t - 30 * 86400)) >= 2   # >= 2 earlier pumps in the prior 30d
    P["serial"] = ser
    retrace100 = np.asarray(A[:len(P), M0 + 1440, 0], np.float32) <= 0
    unl = C / "unlocks_live"
    STAT = {
        "serial_pumper_next_onset_long": (P["serial"].to_numpy(), 0, 1, 24),
        "day2_3_drift_short_at_24h": (np.ones(len(P), bool), 24, -1, 48),
        "post_retrace_long_at_72h": (retrace100, 72, 1, 96),
        "listing_notice_pump_long_m0+5m": ((P["cause"] == "notice").to_numpy(), 0, 1, 24),
        "unlock_week_short_post_peak": ((P["h_since_binance_listing"] < 24 * 7).to_numpy(), 1, -1, 24),   # proxy: young perp; unlock calendar join left for F09/F18
        "weekend_pump_short_post_peak": (np.isin(dow, [5, 6]), 1, -1, 24),
        "kst_open_09_12_long": ((kst >= 9) & (kst < 12), 0, 1, 24),
        "us_session_14_21_short_post_peak": ((hod >= 14) & (hod < 21), 1, -1, 24),
        "second_pump_within_6h_long": ((P["prev_gap"] < 6 * 3600).to_numpy(), 0, 1, 24),
        "falling_volume_7d_long": ((X["dv24"][np.clip(h0 - 1, 0, len(ts_h) - 1), j] < 0.7 * X["dv24"][np.clip(h0 - 169, 0, len(ts_h) - 1), j]), 0, 1, 24),
    }
    res = {"tests": {}}; pv = {}
    for name, (mask, off, side, hz) in STAT.items():
        e = h0 + off; ok = (e + hz < len(ts_h)) & np.isfinite(lc[np.clip(e, 0, len(ts_h) - 1), j])
        g = side * (lc[np.clip(e + hz, 0, len(ts_h) - 1), j] - lc[np.clip(e, 0, len(ts_h) - 1), j]) - cost
        out = {}
        for per, sel in (("discovery", ~val), ("validation", val)):
            a = sel & ok & np.nan_to_num(mask.astype(float)).astype(bool); b = sel & ok & ~np.nan_to_num(mask.astype(float)).astype(bool)
            if a.sum() < 30:
                continue
            d = dict(n=int(a.sum()), mean=float(np.nanmean(g[a])), ci=M.day_ci(g[a], day[a]))
            if b.sum() >= 30:
                d["diff_ci"], d["p_vs_rest"] = boot_diff(g[a], day[a], g[b], day[b]); d["mean_rest"] = float(np.nanmean(g[b]))
            bo = pd.DataFrame({"x": g[a], "d": day[a]}).dropna().groupby("d")["x"].agg(["sum", "count"])
            bb = np.array([bo["sum"].to_numpy()[k].sum() / bo["count"].to_numpy()[k].sum() for k in (RNG.integers(0, len(bo), len(bo)) for _ in range(4000))])
            d["p"] = float((bb <= 0).mean()); out[per] = d
        res["tests"][name] = out
        if "validation" in out:
            pv[name] = out["validation"]["p"]
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["validation"]["ci"][0] > 0]
    json.dump(res, open(OUT / "f06.json", "w"), indent=1, default=float)
    print("F06 BH", res["bh_pass"], "PASS", res["pass"])
    for k, v in res["tests"].items():
        if "validation" in v:
            x = v["validation"]; print(k, x["n"], round(x["mean"], 4), [round(a, 4) for a in x["ci"]], "rest", round(x.get("mean_rest", np.nan), 4))


if __name__ == "__main__":
    {"f03": f03, "f15": f15, "f06": f06}[sys.argv[1]]()
