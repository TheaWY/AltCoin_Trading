"""B16 exit / stop-loss (손절) policy study (research/batch_B15_B16.yaml). Same entries, different exits; parameters fixed on discovery.
Hourly bars from the B7 panel (stop fills at the stop price, or at the bar open if it gapped through; noted deviation from
the next-minute rule for hourly entry sets). LightGBM for the learned exit (no torch).
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b16_exits.py
Out: data/reports/b16/b16.json"""
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
from b7_lib import SD, lag  # noqa: E402

OUT = ROOT / "data/reports/b16"
H_, D_, DISC1 = 3600, 86400, 1756684800
HOLD = 24
RNG = np.random.default_rng(16)


def day_ci(x, days, reps=3000):
    d = pd.DataFrame({"x": x, "d": days}).dropna().groupby("d")["x"].agg(["sum", "count"])
    su, cn = d["sum"].to_numpy(), d["count"].to_numpy()
    b = np.array([su[k].sum() / cn[k].sum() for k in (RNG.integers(0, len(su), len(su)) for _ in range(reps))])
    return [float(v) for v in np.percentile(b, [2.5, 97.5])]


def entries():
    ts, codes, X = L.data()
    lc, r1, U = X["lc"], X["r1"], X["U"]
    T = len(ts)
    rows = np.flatnonzero((ts >= L.DISC[0]) & (ts < L.HOLD[1] - 2 * D_))
    E = {}
    # E1 composite: momentum 28d, short-term reversal 24h, low vol (cross-sectional z), top-5 long at 00 UTC daily
    comp = L.cs_rank(np.where(U, lc - lag(lc, 672), np.nan)) - L.cs_rank(np.where(U, lc - lag(lc, 24), np.nan)) - L.cs_rank(np.where(U, SD(r1, 168), np.nan))
    e1 = []
    for i in rows[ts[rows] % D_ == 0]:
        v = np.where(U[i], comp[i], np.nan)
        if np.isfinite(v).sum() >= 20:
            e1 += [(i, j, 1) for j in np.argsort(-np.nan_to_num(v, nan=-9))[:5]]
    E["E1_composite_long"] = e1
    # E2 pump follow long: hour containing the onset, entry at that hour's close
    P = pd.read_parquet(ROOT / "data/cache/b15/pumps.parquet")
    idx = {c: j for j, c in enumerate(codes)}
    hi = np.searchsorted(ts, (P["ts"].to_numpy() // H_ + 1) * H_)
    E["E2_pump_follow_long"] = [(i, idx[c], 1) for i, c in zip(hi, P["code"]) if c in idx and i < T - HOLD - 1]
    # E3 crash rebound (C1 rule): coin 24h <= -25%, BTC 24h <= -3%, below prior 30d closing low; 24h cooldown
    bi = codes.index("BTCUSDT")
    r24 = lc - lag(lc, 24)
    lo30 = L.MN(X["c"], 720)
    e3, last = [], {}
    for i in rows:
        if r24[i, bi] > np.log(0.97):
            continue
        for j in np.flatnonzero(U[i] & (r24[i] <= np.log(0.75)) & (X["c"][i] <= lag(lo30, 1)[i])):
            if i - last.get(j, -999) >= 24:
                e3.append((i, j, 1))
                last[j] = i
    E["E3_crash_rebound_long"] = e3
    # E5 random control: 5 random universe coins at 00/08/16 UTC
    e5 = []
    for i in rows[ts[rows] % (8 * H_) == 0]:
        cand = np.flatnonzero(U[i])
        if len(cand) >= 20:
            e5 += [(i, j, 1) for j in RNG.choice(cand, 5, replace=False)]
    E["E5_random_long"] = e5
    return E


def paths(E):
    ts, codes, X = L.data()
    c, h, l, f8 = X["c"], X["h"], X["l"], X["f8"]
    vol = SD(X["r1"], 24) * np.sqrt(24)
    ii = np.array([e[0] for e in E])
    jj = np.array([e[1] for e in E])
    k = np.arange(0, HOLD + 1)
    C = c[ii[:, None] + k, jj[:, None]] / c[ii, jj][:, None]
    Hh = h[ii[:, None] + k, jj[:, None]] / c[ii, jj][:, None]
    Ll = l[ii[:, None] + k, jj[:, None]] / c[ii, jj][:, None]
    F = np.nan_to_num(f8[ii[:, None] + k, jj[:, None]]) / 8
    cost = 2 * (L.FEE + L.slip(np.nan_to_num(X["dv24"][ii, jj])))
    return dict(i=ii, j=jj, ts=ts[ii], C=C, H=Hh, L=Ll, F=F, cost=cost, vol=np.nan_to_num(vol[ii, jj], nan=0.05))


def run_policy(p, kind, prm, learned_exit=None):
    """Returns net per trade and exit hour. Long only (all entry sets are long)."""
    C, Hh, Ll = p["C"], p["H"], p["L"]
    n = len(C)
    exit_h = np.full(n, HOLD)
    px = C[:, HOLD].copy()
    if kind in ("stop", "atr", "trail", "combo"):
        maxh = prm.get("maxh", HOLD)
        for r in range(n):
            if not np.isfinite(C[r]).all():
                px[r] = np.nan
                continue
            runhi = 1.0
            for t in range(1, maxh + 1):
                if kind == "stop" or kind == "combo":
                    stop = 1 - prm["k"]
                elif kind == "atr":
                    stop = 1 - prm["k"] * p["vol"][r]
                else:
                    stop = runhi * (1 - prm["k"] * p["vol"][r])
                if Ll[r, t] <= stop:
                    opn = C[r, t - 1]
                    px[r] = stop if opn >= stop else opn
                    exit_h[r] = t
                    break
                runhi = max(runhi, Hh[r, t])
            else:
                px[r] = C[r, maxh]
                exit_h[r] = maxh
    elif kind == "time":
        exit_h[:] = prm["h"]
        px = C[np.arange(n), prm["h"]]
    elif kind == "learned":
        prob, thr = learned_exit
        for r in range(n):
            hit = np.flatnonzero(prob[r, 1:HOLD] > thr)
            if len(hit):
                t = hit[0] + 1
                exit_h[r] = t
                px[r] = C[r, t]
    fund = np.array([p["F"][r, 1:exit_h[r] + 1].sum() for r in range(n)])
    net = px - 1 - p["cost"] - fund
    return net, exit_h


def learned_features(p):
    """Per trade x hour features: unrealised P&L, age, last-hour and 6h return, running drawdown, coin vol."""
    C = p["C"]
    n = len(C)
    feats = []
    for t in range(HOLD + 1):
        r1 = C[:, t] / C[:, max(t - 1, 0)] - 1
        r6 = C[:, t] / C[:, max(t - 6, 0)] - 1
        dd = C[:, t] / np.maximum.accumulate(C[:, :t + 1], 1)[:, -1] - 1
        feats.append(np.column_stack([C[:, t] - 1, np.full(n, t), r1, r6, dd, p["vol"]]))
    return np.stack(feats, 1)                                   # n x 25 x 6


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    E = entries()
    res = {}
    grids = {"time": [dict(h=h) for h in (4, 8, 12, 24)], "stop": [dict(k=k) for k in (0.02, 0.03, 0.05, 0.08)],
             "atr": [dict(k=k) for k in (1.0, 1.5, 2.0)], "trail": [dict(k=k) for k in (1.0, 1.5, 2.0)],
             "combo": [dict(k=k, maxh=mh) for k in (0.03, 0.05, 0.08) for mh in (8, 12)]}
    for name, ev in E.items():
        p = paths(ev)
        ok = np.isfinite(p["C"]).all(1)
        p = {k: (v[ok] if isinstance(v, np.ndarray) and len(v) == len(ok) else v) for k, v in p.items()}
        disc = p["ts"] < DISC1
        day = p["ts"] // D_
        base, _ = run_policy(p, "time", dict(h=HOLD))
        r = {"n_disc": int(disc.sum()), "n_hold": int((~disc).sum()),
             "P0_24h": dict(disc=float(base[disc].mean()), hold=float(base[~disc].mean()), hold_ci=day_ci(base[~disc], day[~disc]),
                            hold_mean_loss=float(base[~disc][base[~disc] < 0].mean()), hold_hit=float((base[~disc] > 0).mean()))}
        for fam, grid in grids.items():
            best = max(grid, key=lambda prm: np.nanmean(run_policy({k: (v[disc] if isinstance(v, np.ndarray) and len(v) == len(disc) else v) for k, v in p.items()}, fam, prm)[0]))
            net, eh = run_policy(p, fam, best)
            d = net[~disc] - base[~disc]
            losers = base[~disc] < 0
            winners = ~losers
            cut_ok = np.mean(net[~disc][losers] >= 0.5 * base[~disc][losers]) if losers.any() else np.nan
            cut_bad = np.mean((eh[~disc][winners] < HOLD) & (net[~disc][winners] < base[~disc][winners])) if winners.any() else np.nan
            r[fam] = dict(params=best, hold=float(np.nanmean(net[~disc])), hold_ci=day_ci(net[~disc], day[~disc]),
                          vs_P0=float(np.nanmean(d)), vs_P0_ci=day_ci(d, day[~disc]), mean_loss=float(net[~disc][net[~disc] < 0].mean()),
                          hit=float((net[~disc] > 0).mean()), avg_hold_h=float(eh[~disc].mean()),
                          loss_cut_efficiency=float(cut_ok - cut_bad))
        # P5 learned exit: P(close at 24h < current close) at each hour, trained on discovery trades
        F = learned_features(p)
        y = (p["C"][:, [HOLD]] < p["C"]).astype(int)            # staying from hour t to 24h loses
        Xtr = F[disc][:, 1:HOLD].reshape(-1, F.shape[2])
        ytr = y[disc][:, 1:HOLD].reshape(-1)
        m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=200, verbose=-1, n_jobs=6).fit(Xtr, ytr)
        prob = m.predict_proba(F.reshape(-1, F.shape[2]))[:, 1].reshape(F.shape[0], F.shape[1])
        pd_ = {k: (v[disc] if isinstance(v, np.ndarray) and len(v) == len(disc) else v) for k, v in p.items()}
        thr = max((0.55, 0.6, 0.65, 0.7, 0.75), key=lambda t: np.nanmean(run_policy(pd_, "learned", {}, (prob[disc], t))[0]))
        net, eh = run_policy(p, "learned", {}, (prob, thr))
        d = net[~disc] - base[~disc]
        losers = base[~disc] < 0
        r["learned"] = dict(threshold=thr, hold=float(np.nanmean(net[~disc])), hold_ci=day_ci(net[~disc], day[~disc]),
                            vs_P0=float(np.nanmean(d)), vs_P0_ci=day_ci(d, day[~disc]), mean_loss=float(net[~disc][net[~disc] < 0].mean()),
                            hit=float((net[~disc] > 0).mean()), avg_hold_h=float(eh[~disc].mean()),
                            loss_cut_efficiency=float(np.mean(net[~disc][losers] >= 0.5 * base[~disc][losers])
                                                      - np.mean((eh[~disc][~losers] < HOLD) & (net[~disc][~losers] < base[~disc][~losers]))))
        res[name] = r
        print(name, json.dumps({k: (v if k.startswith("n_") else {kk: vv for kk, vv in v.items() if kk in ("params", "threshold", "hold", "hold_ci", "vs_P0", "vs_P0_ci", "loss_cut_efficiency")}) for k, v in r.items()}, default=float), flush=True)
    # pass rule
    fams = ["time", "stop", "atr", "trail", "combo", "learned"]
    real = ["E1_composite_long", "E2_pump_follow_long", "E3_crash_rebound_long"]
    res["verdict"] = {}
    for f in fams:
        wins = sum(res[e][f]["vs_P0_ci"][0] > 0 for e in real if e in res)
        ctrl = res["E5_random_long"][f]["vs_P0_ci"][0] > 0
        eff = np.mean([res[e][f]["loss_cut_efficiency"] for e in real if e in res])
        res["verdict"][f] = dict(beats_P0_on=int(wins), beats_on_control=bool(ctrl), mean_loss_cut_eff=float(eff),
                                 pass_=bool(wins >= 2 and not ctrl and eff > 0))
    json.dump(res, open(OUT / "b16.json", "w"), indent=1, default=float)
    print("VERDICT", json.dumps(res["verdict"], default=float))


if __name__ == "__main__":
    main()
