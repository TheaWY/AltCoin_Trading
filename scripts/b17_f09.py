"""B17 F09 coordination (registry F09_coordination_001, 004, 005, 006). 002/003 need Telegram pump-call data (not available -> not_run).
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f09.py   -> data/reports/b17/f09.json
Data: B15 pump onsets (21,360; +10%/60m, dv24 >= $2M, 6h cooldown per coin) with minute paths; b7 hourly panel (row t = bar closing at ts[t]);
Binance product tags (data/cache/cg_categories.json) as sectors. Discovery 2024-04..2025-12 selects, validation 2026 confirms.
  001 Hawkes excitation: at each onset, excitation = sum over OTHER coins' onsets in the past 6h of exp(-dt/tau); tau in {15, 60, 240} min
      and the top-tercile threshold chosen on discovery (max 1h net spread). Long the pump at m0+1, exit +1h and +24h (2 cells);
      pass = validation high-excitation net CI > 0 AND high - rest CI > 0.
  004 sector sympathy: (a) probability a same-sector coin has an onset within 15 min after a pump vs a random other coin (rate ratio, CI by
      day bootstrap); (b) trade: at the first hourly close after an onset, long every same-sector coin without an onset in the past 6h,
      exit +1h / +24h; control = the same number of random non-sector coins at the same hour (paired). pass = net CI > 0 and beats control.
  005 coordinated burst: hours with >= 10 onsets; at that hour's close long an equal-weight basket of the pumped coins, short BTC beta-hedge
      (60-day beta from the panel), exit +1h (and +4h reported). pass = validation net CI > 0 (discovery must agree in sign).
  006 minute-of-hour clustering: chi-square of onset minute (mod 60) vs uniform on discovery; the 5 most over-represented minutes chosen
      on discovery; pass = their validation share exceeds 5/60 with bootstrap CI above it. (Descriptive; no trade.)
Costs: 2 x (0.05% fee + dv24 slippage) per leg (b15_models.slip); simple returns."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f03 import bh, load  # noqa: E402

OUT = ROOT / "data/reports/b17"
RNG = np.random.default_rng(1709)
VAL0 = 1767225600
H = 3600


def cl_boot(x, day, reps=2000):
    b = pd.DataFrame({"x": x, "d": day}).groupby("d")["x"].agg(["sum", "count"]); su, cn = b["sum"].to_numpy(), b["count"].to_numpy()
    return np.array([su[k].sum() / cn[k].sum() for k in (RNG.integers(0, len(su), len(su)) for _ in range(reps))])


def summ(x, day):
    x = np.asarray(x, float); day = np.asarray(day)
    if len(x) < 30:
        return dict(n=int(len(x)), mean=float(np.mean(x)) if len(x) else None, ci=[np.nan, np.nan], p=np.nan)
    bo = cl_boot(x, day)
    return dict(n=int(len(x)), mean=float(x.mean()), ci=[float(v) for v in np.percentile(bo, [2.5, 97.5])], p=float((bo <= 0).mean()))


def f001(P, A, res, pv):
    cr = np.asarray(A[:, :, 0], np.float64); e = cr[:, 61]
    net = {"1h": (1 + cr[:, 121]) / (1 + e) - 1 - P["cost"].to_numpy(), "24h": (1 + cr[:, 1500]) / (1 + e) - 1 - P["cost"].to_numpy()}
    t = P["ts"].to_numpy(); code = P["code"].to_numpy(); order = np.argsort(t); ts_s, cs = t[order], code[order]
    exc = {}
    for tau in (15, 60, 240):
        x = np.zeros(len(P))
        for r, i in enumerate(order):
            lo = np.searchsorted(ts_s, t[i] - 6 * H); w = slice(lo, r)
            dt = t[i] - ts_s[w]; m = (cs[w] != code[i]) & (dt > 0)
            x[i] = np.exp(-dt[m] / (60 * tau)).sum()
        exc[tau] = x
    val = t >= VAL0; disc = ~val; day = t // 86400
    def spread(tau):
        thr = np.quantile(exc[tau][disc], 2 / 3); hi = exc[tau] > thr
        return np.nanmean(net["1h"][disc & hi]) - np.nanmean(net["1h"][disc & ~hi]), thr
    tau = max(exc, key=lambda k: spread(k)[0]); thr = spread(tau)[1]; hi = exc[tau] > thr
    res["001"] = {"tau_min": tau, "threshold": float(thr), "share_high": float(hi.mean())}
    for hz, v in net.items():
        ok = np.isfinite(v)
        high = summ(v[val & hi & ok], day[val & hi & ok]); rest = summ(v[val & ~hi & ok], day[val & ~hi & ok])
        # high minus rest: difference of day-clustered means via bootstrap over days
        dd = pd.DataFrame({"x": v[val & ok], "h": hi[val & ok], "d": day[val & ok]}); days = dd["d"].unique(); g = {d: x for d, x in dd.groupby("d")}
        bo = []
        for _ in range(1000):
            s = pd.concat([g[d] for d in RNG.choice(days, len(days))])
            bo.append(s.loc[s["h"], "x"].mean() - s.loc[~s["h"], "x"].mean())
        diff = dict(mean=float(high["mean"] - rest["mean"]), ci=[float(a) for a in np.nanpercentile(bo, [2.5, 97.5])], p=float((np.array(bo) <= 0).mean()))
        res["001"][hz] = dict(high=high, rest=rest, high_minus_rest=diff, disc_high=summ(v[disc & hi & ok], day[disc & hi & ok]))
        pv[f"001|{hz}"] = max(high["p"], diff["p"])
    print("001", json.dumps(res["001"], default=float)[:900], flush=True)


def panel():
    ts_h, codes, X = L.data()
    return ts_h, list(codes), X


def hcost(dv):
    return 2 * (L.FEE + M.slip(np.asarray(dv, float)))


def f004(P, res, pv, ts_h, codes, X):
    tags = json.load(open(ROOT / "data/cache/cg_categories.json"))
    freq = pd.Series([t for v in tags.values() for t in v]).value_counts()
    keep = set(freq[(freq >= 3) & (freq <= 150)].index)                           # drop generic/programme tags (e.g. HODLer)
    sect = {c: set(v) & keep for c, v in tags.items()}
    ci = {c: i for i, c in enumerate(codes)}
    t = P["ts"].to_numpy(); code = P["code"].to_numpy(); val = t >= VAL0
    on = {c: np.sort(g["ts"].to_numpy()) for c, g in P.groupby("code")}
    def onset_in(c, a, b):
        o = on.get(c); return o is not None and np.searchsorted(o, b, "right") - np.searchsorted(o, a, "right") > 0
    rows, prob = [], []
    for i in range(len(P)):
        s = sect.get(code[i], set())
        if not s:
            continue
        hi = int(np.searchsorted(ts_h, t[i] + 60))                                 # first hourly bar closing at/after entry
        if hi + 24 >= len(ts_h):
            continue
        univ = [c for c in codes if c != code[i] and X["U"][hi, ci[c]]]
        peers = [c for c in univ if sect.get(c, set()) & s and not onset_in(c, t[i] - 6 * H, t[i])]
        others = [c for c in univ if not (sect.get(c, set()) & s) and not onset_in(c, t[i] - 6 * H, t[i])]
        if not peers or len(others) < len(peers):
            continue
        ctrl = list(RNG.choice(others, len(peers), replace=False))
        prob.append((t[i], np.mean([onset_in(c, t[i], t[i] + 15 * 60) for c in peers]), np.mean([onset_in(c, t[i], t[i] + 15 * 60) for c in ctrl])))
        for hz, k in (("1h", 1), ("24h", 24)):
            def leg(cs):
                j = [ci[c] for c in cs]
                r = np.exp(X["lc"][hi + k, j] - X["lc"][hi, j]) - 1 - hcost(X["dv24"][hi, j])
                return float(np.nanmean(r))
            rows.append((t[i], hz, leg(peers), leg(ctrl), len(peers)))
    Pr = pd.DataFrame(prob, columns=["ts", "sector", "random"]); Pr["day"] = Pr["ts"] // 86400
    Tr = pd.DataFrame(rows, columns=["ts", "hz", "peer", "ctrl", "n_peers"]); Tr["day"] = Tr["ts"] // 86400
    out = {"a_probability": {}}
    for per, s in (("disc", Pr["ts"] < VAL0), ("val", Pr["ts"] >= VAL0)):
        x = Pr[s]; out["a_probability"][per] = dict(sector=float(x["sector"].mean()), random=float(x["random"].mean()),
                                                    diff=summ((x["sector"] - x["random"]).to_numpy(), x["day"].to_numpy()))
    for hz in ("1h", "24h"):
        x = Tr[(Tr["hz"] == hz) & (Tr["ts"] >= VAL0)].dropna(); xd = Tr[(Tr["hz"] == hz) & (Tr["ts"] < VAL0)].dropna()
        out[hz] = dict(peer_val=summ(x["peer"], x["day"]), minus_ctrl_val=summ(x["peer"] - x["ctrl"], x["day"]),
                       peer_disc=summ(xd["peer"], xd["day"]), median_peers=float(x["n_peers"].median()))
        pv[f"004|{hz}"] = max(out[hz]["peer_val"]["p"], out[hz]["minus_ctrl_val"]["p"])
    res["004"] = out
    print("004", json.dumps(out, default=float)[:900], flush=True)


def f005(P, res, pv, ts_h, codes, X):
    ci = {c: i for i, c in enumerate(codes)}; bi = ci["BTCUSDT"]
    P = P.assign(hb=P["ts"] // H)
    rows = []
    for hb, g in P.groupby("hb"):
        n = g["code"].nunique()
        hi = int(np.searchsorted(ts_h, (hb + 1) * H))                              # bar closing at the end of that hour
        if hi + 4 >= len(ts_h) or ts_h[hi] != (hb + 1) * H:
            continue
        j = [ci[c] for c in g["code"].unique() if c in ci]
        if not j:
            continue
        beta = np.nan_to_num(X["beta"][hi, j], nan=1.0)
        for hz, k in (("1h", 1), ("4h", 4)):
            r = np.exp(X["lc"][hi + k, j] - X["lc"][hi, j]) - 1
            rb = float(np.exp(X["lc"][hi + k, bi] - X["lc"][hi, bi]) - 1)
            c = hcost(X["dv24"][hi, j]) + np.abs(beta) * 2 * (L.FEE + 0.0002)
            rows.append((int(ts_h[hi]), hz, n, float(np.nanmean(r - beta * rb - c))))
    T = pd.DataFrame(rows, columns=["ts", "hz", "n", "net"]); T["day"] = T["ts"] // 86400
    out = {}
    for hz in ("1h", "4h"):
        for lab, s in (("burst>=10", T["n"] >= 10), ("3-9", T["n"].between(3, 9))):
            x = T[(T["hz"] == hz) & s]
            out[f"{lab}|{hz}"] = dict(val=summ(x.loc[x["ts"] >= VAL0, "net"], x.loc[x["ts"] >= VAL0, "day"]),
                                      disc=summ(x.loc[x["ts"] < VAL0, "net"], x.loc[x["ts"] < VAL0, "day"]))
    res["005"] = out
    for hz in ("1h",):
        o = out[f"burst>=10|{hz}"]; pv[f"005|{hz}"] = o["val"]["p"] if np.isfinite(o["val"]["p"]) else 1.0
    print("005", json.dumps(out, default=float)[:700], flush=True)


def f006(P, res):
    from scipy.stats import chisquare
    mnt = ((P["ts"] // 60) % 60).to_numpy(); val = P["ts"].to_numpy() >= VAL0; day = (P["ts"] // 86400).to_numpy()
    cd = np.bincount(mnt[~val], minlength=60); chi = chisquare(cd)
    top = list(np.argsort(-cd)[:5])
    x = np.isin(mnt[val], top).astype(float)
    s = summ(x, day[val])
    res["006"] = dict(disc_chi2=float(chi.statistic), disc_p=float(chi.pvalue), top5_minutes=[int(v) for v in top],
                      top5_share_disc=float(np.isin(mnt[~val], top).mean()), top5_share_val=s, uniform=5 / 60,
                      pass_=bool(s["ci"][0] > 5 / 60), val_counts=np.bincount(mnt[val], minlength=60).tolist())
    print("006", json.dumps({k: v for k, v in res["006"].items() if k != "val_counts"}, default=float), flush=True)


def main():
    P, A = load()
    res = {"not_run": {"002": "needs Telegram pump-call timestamps", "003": "needs Telegram pump labels"}}; pv = {}
    f006(P, res)
    f001(P, A, res, pv)
    ts_h, codes, X = panel()
    f005(P, res, pv, ts_h, codes, X)
    f004(P, res, pv, ts_h, codes, X)
    res["bh_pass"] = bh(pv)
    passes = []
    for k in res["bh_pass"]:
        h, hz = k.split("|")
        if h == "001" and res["001"][hz]["high"]["ci"][0] > 0 and res["001"][hz]["high_minus_rest"]["ci"][0] > 0:
            passes.append(k)
        if h == "004" and res["004"][hz]["peer_val"]["ci"][0] > 0 and res["004"][hz]["minus_ctrl_val"]["ci"][0] > 0:
            passes.append(k)
        if h == "005" and res["005"][f"burst>=10|{hz}"]["val"]["ci"][0] > 0 and (res["005"][f"burst>=10|{hz}"]["disc"]["mean"] or 0) > 0:
            passes.append(k)
    res["pass"] = passes + (["006"] if res["006"]["pass_"] else [])
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f09.json", "w"), indent=1, default=float)
    print("F09 BH", res["bh_pass"], "PASS", res["pass"], flush=True)


if __name__ == "__main__":
    main()
