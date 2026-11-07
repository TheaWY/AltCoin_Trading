"""B17 F14: learned exit policies for the m0+1 pump long (registry F14_rl_exit_001..004).  Batch (offline) RL on the B15 minute paths.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f14.py   -> data/reports/b17/f14.json
Method: fitted-Q / Longstaff-Schwartz optimal stopping. Long at m0+1 close; decision every 2 minutes k=1..239; exit at k sells at the next
minute close (as B15_1); forced exit at +240m. Continuation value Q_hold(s_k) is regressed (LightGBM) on DISCOVERY pumps only
(2024-04..2025-12) with pathwise targets, 3 backward passes; the policy exits when Q_hold < current gain. Validation = 2026 pumps only
(no reward leakage: models never see validation rewards).
  001 'cql'  : conservative continuation = mean - 0.5 x residual std of its k-bucket (penalises uncertain holding)
  002 'iql'  : upper-expectile proxy = LightGBM quantile(0.7) continuation (IQL-style optimistic in-sample value)
  003 'cvar' : squeeze-averse = 0.5 x mean + 0.5 x quantile(0.1) continuation (CVaR-10 flavoured)
  004 'joint': 'cql' exit + entry filter decided at k=1: enter at the k=1 close only if Q_hold(s_1) - gain(s_1) > cost
Baseline: best fixed exit (15m/60m/4h) chosen on discovery (the registry says 'vs hazard exit'; B15_1's hazard exit did not beat the best
fixed exit on holdout, so the fixed exit is the stronger comparator). Paired day-clustered bootstrap of policy - baseline on validation.
Features at k (all known at k): k, gain, 5-min return, volume decay, taker share, range, drawdown from high since entry, pump size at onset,
log dv24, funding at onset. HMM state omitted (day-level filtered state has intraday look-ahead).
Pass: validation mean net CI > 0 AND policy - baseline CI > 0; BH q=0.10 over the 4."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b15_models as M  # noqa: E402
from b17_f03 import bh, load  # noqa: E402

OUT = ROOT / "data/reports/b17"
RNG = np.random.default_rng(1714)
VAL0 = 1767225600
K = np.arange(1, 240, 2)
FEATS = ["k", "gain", "r5", "vol_decay", "taker5", "range5", "dd", "pump_size", "ldv", "funding"]


def build():
    P, A = load()
    a = np.asarray(A[:, 50:310, :], np.float32)            # index 50..309 -> local 0..259 ; m0 = 60 -> local 10 ; entry m0+1 -> local 11
    cr, vr, tk, rg = a[..., 0].astype(np.float64), a[..., 1], a[..., 2], a[..., 3]
    E = 11; e = cr[:, E]
    n, J = len(P), len(K)
    X = np.zeros((n, J, len(FEATS)), np.float32); G = np.zeros((n, J)); R = np.zeros((n, J))
    px = (1 + cr) / (1 + e[:, None]) - 1                   # return since entry at every local index
    runmax = np.maximum.accumulate(np.where(np.arange(px.shape[1]) >= E, px, -np.inf), axis=1)
    v0 = vr[:, E:E + 5].mean(1)
    for j, k in enumerate(K):
        c = E + k
        G[:, j] = px[:, c]; R[:, j] = px[:, c + 1]
        X[:, j, 0] = k; X[:, j, 1] = px[:, c]; X[:, j, 2] = cr[:, c] - cr[:, c - 5]
        X[:, j, 3] = vr[:, c - 4:c + 1].mean(1) - v0; X[:, j, 4] = tk[:, c - 4:c + 1].mean(1); X[:, j, 5] = rg[:, c - 4:c + 1].mean(1)
        X[:, j, 6] = runmax[:, c] - px[:, c]
    X[:, :, 7] = P["pump_size"].to_numpy()[:, None]; X[:, :, 8] = np.log(P["dv24"].to_numpy())[:, None]
    X[:, :, 9] = P["funding"].fillna(0).to_numpy()[:, None]
    Rf = px[:, E + 240]
    fixed = {m: px[:, E + m] for m in (15, 60, 240)}
    ok = np.isfinite(Rf) & np.isfinite(G).all(1) & np.isfinite(R).all(1)
    return P[ok].reset_index(drop=True), X[ok], G[ok], R[ok], Rf[ok], {m: v[ok] for m, v in fixed.items()}


def continuation(exit_, R, Rf):
    """value of HOLDING at decision j = value of the current policy from decision j+1 on (Rf after the last decision)."""
    n, J = R.shape; C = np.empty((n, J)); nxt = Rf.copy()
    for j in range(J - 1, -1, -1):
        C[:, j] = nxt
        nxt = np.where(exit_[:, j], R[:, j], nxt)
    return C


def realise(exit_, R, Rf):
    first = np.where(exit_.any(1), exit_.argmax(1), -1)
    val = np.where(first >= 0, R[np.arange(len(R)), np.clip(first, 0, None)], Rf)
    held = np.where(first >= 0, K[np.clip(first, 0, None)] + 1, 240)
    return val, held


def fit(Xf, y, obj="l2", alpha=None):
    import lightgbm as lgb
    kw = dict(n_estimators=250, learning_rate=0.05, num_leaves=63, min_child_samples=200, subsample=0.5, subsample_freq=1,
              colsample_bytree=0.9, reg_lambda=5, verbose=-1, n_jobs=6)
    if obj == "quantile":
        kw.update(objective="quantile", alpha=alpha)
    return lgb.LGBMRegressor(**kw).fit(Xf, y)


def fqi(variant, X, G, R, Rf, disc, passes=3):
    n, J, F = X.shape; flat = X.reshape(-1, F); dmask = np.repeat(disc, J)
    sub = np.flatnonzero(dmask); sub = RNG.choice(sub, min(len(sub), 900_000), replace=False)
    exit_ = np.zeros((n, J), bool)
    for _ in range(passes):
        C = continuation(exit_, R, Rf).reshape(-1)
        if variant in ("cql", "joint"):
            m = fit(flat[sub], C[sub]); q = m.predict(flat).reshape(n, J)
            res = (C[sub] - m.predict(flat[sub])); kb = (flat[sub, 0] // 20).astype(int)
            sd = pd.Series(res).groupby(kb).std(); q = q - 0.5 * sd.reindex((X[:, :, 0] // 20).astype(int).ravel()).to_numpy().reshape(n, J)
        elif variant == "iql":
            q = fit(flat[sub], C[sub], "quantile", 0.7).predict(flat).reshape(n, J)
        elif variant == "cvar":
            q = 0.5 * fit(flat[sub], C[sub]).predict(flat).reshape(n, J) + 0.5 * fit(flat[sub], C[sub], "quantile", 0.1).predict(flat).reshape(n, J)
        exit_ = q < G
    return exit_, q


def stats(x, day):
    x = np.asarray(x, float)
    return dict(n=int(len(x)), mean=float(x.mean()), ci=M.day_ci(x, day))


def boot_p(d, day, reps=2000):
    b = pd.DataFrame({"x": d, "d": day}).groupby("d")["x"].agg(["sum", "count"]); su, cn = b["sum"].to_numpy(), b["count"].to_numpy()
    bo = np.array([su[k].sum() / cn[k].sum() for k in (RNG.integers(0, len(su), len(su)) for _ in range(reps))])
    return float((bo <= 0).mean())


def main():
    P, X, G, R, Rf, fixed = build()
    cost = P["cost"].to_numpy(); day = (P["ts"] // 86400).to_numpy(); val = P["ts"].to_numpy() >= VAL0; disc = ~val
    print("pumps", len(P), "validation", int(val.sum()), flush=True)
    best = max(fixed, key=lambda m: float((fixed[m] - cost)[disc].mean()))
    base = fixed[best] - cost
    res = {"baseline": f"fixed {best}m (best on discovery)", "baseline_val": stats(base[val], day[val]),
           "fixed_val": {f"{m}m": stats((v - cost)[val], day[val]) for m, v in fixed.items()}, "tests": {}}
    pv = {}
    for hid, variant in (("001_cql", "cql"), ("002_iql", "iql"), ("003_cvar", "cvar"), ("004_joint", "joint")):
        ex, q = fqi(variant, X, G, R, Rf, disc)
        v, held = realise(ex, R, Rf); net = v - cost
        if variant == "joint":
            # entry is decided from the state at k=1 (one minute after m0+1), so the trade is entered at THAT price (G[:, 0]),
            # not at m0+1 (fixed 2026-09-29: the first run booked the k=1 minute's move as profit -> look-ahead)
            take = q[:, 0] - G[:, 0] - cost > 0                 # expected continuation over the price at decision time
            net = (1 + v) / (1 + G[:, 0]) - 1 - cost
            net_t = np.where(take, net, 0.0)                    # per-pump P&L incl. skipped (0) for the paired comparison
            d = net_t[val] - base[val]
            out = dict(take_rate_val=float(take[val].mean()), taken_val=stats(net[val & take], day[val & take]) if (val & take).sum() > 30 else None,
                       per_pump_val=stats(net_t[val], day[val]))
            main_ci = out["taken_val"]["ci"] if out["taken_val"] else [np.nan, np.nan]
        else:
            d = net[val] - base[val]
            out = dict(val=stats(net[val], day[val]), disc=stats(net[disc], day[disc]), held_median_val=float(np.median(held[val])))
            main_ci = out["val"]["ci"]
        out["minus_baseline_val"] = stats(d, day[val]); out["p"] = boot_p(d, day[val]); pv[hid] = out["p"]
        out["main_ci"] = main_ci
        res["tests"][hid] = out
        print(hid, json.dumps({k: (v if not isinstance(v, dict) else {a: (round(b, 4) if isinstance(b, float) else b) for a, b in v.items()}) for k, v in out.items()}, default=float), flush=True)
    res["bh_pass"] = bh(pv)
    res["pass"] = [h for h in res["bh_pass"] if res["tests"][h]["main_ci"][0] > 0 and res["tests"][h]["minus_baseline_val"]["ci"][0] > 0]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f14.json", "w"), indent=1, default=float)
    print("baseline", res["baseline"], res["baseline_val"], "\nF14 BH", res["bh_pass"], "PASS", res["pass"], flush=True)


if __name__ == "__main__":
    main()
