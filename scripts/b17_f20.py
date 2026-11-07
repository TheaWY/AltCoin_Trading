"""B17 F20 on-chain (registry F20_onchain_001, 002, 005, 006, 008). Not runnable: 003 (no stablecoin flows in the cache), 004 (no holder
snapshots), 007 (no bridge flows) -> not_run.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f20.py   -> data/reports/b17/f20.json
Data: data/cache/onchain/flows_YYYY-MM.parquet = hourly CEX inflow/outflow of 160 ERC-20 perps (B10, BigQuery public Ethereum, 2024-03..);
flows are turned into per-coin z-scores vs the trailing 7 days of hours (unit-free, no look-ahead: hour h uses hours h-168..h-1).
B15 pumps (minute paths), b7 hourly panel, depth1h. Discovery 2024-04..2025-12 selects, validation 2026 confirms. Simple returns, costs 2x(fee+slip).
  001 inflow spike 1-6h BEFORE the onset hour (max z > 3): dumps deeper -> (a) dd_24h of flagged vs other pumps, (b) short after peak =
      F04 dd3 trigger, 5% stop, exit +4h; flagged minus other (day-cluster bootstrap)
  002 inflow z > 3 in the pump hour itself: same two measures
  005 low inflow (6h mean z < -1) + thin book (depth1h top-of-book / dv24 bottom quintile, thresholds from discovery) as a precursor:
      P(B15 onset within the next 6h) vs all eligible coin-hours of the 160 coins; trade long at the hour close, exit +6h/+24h vs all hours
  006 net outflow in hours 1-6 after onset (sum outflow z > sum inflow z): no rebound -> long at m0+6h, exit m0+24h; flagged minus other
  008 token age < 90 days (panel age) and thin book at onset: larger pumps -> long m0+1, exit +1h/+24h; flagged minus other
Pass: validation flagged net (in the stated direction) CI > 0 AND flagged-minus-other CI in the stated direction; BH q=0.10 over tests.
Caveat: the F04 short leg checks its stop on minute closes (optimistic, see B17_F04-3); it is used here only for flagged-vs-other contrasts."""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f03 import bh, leg, load  # noqa: E402

C, OUT = M.C, ROOT / "data/reports/b17"
RNG = np.random.default_rng(1720)
VAL0, H = 1767225600, 3600


def flows_z():
    o = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(C / "onchain/flows_*.parquet")))]).sort_values(["code", "ts"])
    out = {}
    for code, g in o.groupby("code"):
        g = g.set_index("ts")[["inflow", "outflow"]]
        idx = np.arange(g.index.min(), g.index.max() + H, H); g = g.reindex(idx, fill_value=0.0)
        z = {}
        for col in ("inflow", "outflow"):
            x = np.log1p(g[col]); m = x.shift(1).rolling(168, min_periods=72).mean(); s = x.shift(1).rolling(168, min_periods=72).std()
            z[col] = ((x - m) / s.replace(0, np.nan)).to_numpy()
        out[code] = (idx, z["inflow"], z["outflow"])
    return out


def at(Z, code, t, lo, hi):
    """z-scores of the hours whose START is in [t+lo*H, t+hi*H) relative to hour floor(t)."""
    if code not in Z:
        return None
    idx, zi, zo = Z[code]; h0 = (t // H) * H
    a, b = np.searchsorted(idx, h0 + lo * H), np.searchsorted(idx, h0 + hi * H)
    return (zi[a:b], zo[a:b]) if b > a else None


def contrast(v, flag, day, val, sign=1, reps=1000):
    """flagged stats and flagged-minus-other (x sign) on validation, day-cluster bootstrap."""
    ok = np.isfinite(v) & val
    d = pd.DataFrame({"x": sign * v[ok], "f": flag[ok], "d": day[ok]})
    if d["f"].sum() < 20 or (~d["f"]).sum() < 20:
        return dict(n_flag=int(d["f"].sum()), note="too few"), 1.0
    g = {k: x for k, x in d.groupby("d")}; days = list(g); bf, bd = [], []
    for _ in range(reps):
        s = pd.concat([g[k] for k in RNG.choice(days, len(days))])
        bf.append(s.loc[s["f"], "x"].mean()); bd.append(s.loc[s["f"], "x"].mean() - s.loc[~s["f"], "x"].mean())
    bf, bd = np.array(bf), np.array(bd)
    out = dict(n_flag=int(d["f"].sum()), n_other=int((~d["f"]).sum()), flagged=float(d.loc[d["f"], "x"].mean()), other=float(d.loc[~d["f"], "x"].mean()),
               flagged_ci=[float(a) for a in np.nanpercentile(bf, [2.5, 97.5])], diff_ci=[float(a) for a in np.nanpercentile(bd, [2.5, 97.5])])
    return out, float(max((bf <= 0).mean(), (bd <= 0).mean()))


def depth_ratio(code, ts, dv24):
    fp = C / f"depth1h/{code}.parquet"
    if not fp.exists():
        return np.full(len(ts), np.nan)
    d = pd.read_parquet(fp, columns=["ts", "bid_1", "ask_1"]).sort_values("ts")
    k = np.searchsorted(d["ts"].to_numpy(), np.asarray(ts), "right") - 1; ok = k >= 0
    out = np.full(len(ts), np.nan)
    out[ok] = (d["bid_1"].to_numpy()[k[ok]] + d["ask_1"].to_numpy()[k[ok]]) / np.asarray(dv24)[ok]
    return out


def pump_tests(P, A, Z, res, pv):
    n = len(P); t = P["ts"].to_numpy(); code = P["code"].to_numpy(); day = t // 86400; val = t >= VAL0; disc = ~val
    cr = np.asarray(A[:, :, 0], np.float64); cost = P["cost"].to_numpy()
    covered = np.array([c in Z for c in code]); res["pumps_with_flows"] = int(covered.sum())
    pre, hour, post = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    for i in np.flatnonzero(covered):
        a = at(Z, code[i], t[i], -6, 0); b = at(Z, code[i], t[i], 0, 1); c = at(Z, code[i], t[i], 1, 7)
        if a is not None and np.isfinite(a[0]).any():
            pre[i] = np.nanmax(a[0])
        if b is not None and np.isfinite(b[0]).any():
            hour[i] = np.nanmax(b[0])
        if c is not None and np.isfinite(c[0]).any() and np.isfinite(c[1]).any():
            post[i] = np.nansum(c[1]) - np.nansum(c[0])
    # F04-style dd3 short, 5% stop, +4h
    short = np.full(n, np.nan)
    for i in np.flatnonzero(covered):
        x = cr[i, 60:]; run = np.maximum.accumulate(x); dd = (1 + x) / (1 + run) - 1; j = np.flatnonzero(dd <= -0.03); j = j[j >= 3]
        if len(j) and j[0] + 242 < 1440:
            g, _, _ = leg(A, i, int(j[0]) + 1, "t4h", 0.05, -1); short[i] = g - cost[i]
    dd24 = P["dd_24h"].to_numpy()
    for hid, flag in (("001", pre > 3), ("002", hour > 3)):
        m = covered & np.isfinite(pre if hid == "001" else hour)
        o1, p1 = contrast(np.where(m, -dd24, np.nan), flag, day, val)            # deeper dump = larger -dd24
        o2, p2 = contrast(np.where(m, short, np.nan), flag, day, val)
        res[hid] = {"share_flagged": float(flag[m].mean()) if m.any() else None, "dump_depth": o1, "short_after_peak": o2}; pv[hid] = max(p1, p2)
    # 006: net outflow after onset -> weaker rebound: long m0+6h -> m0+24h, stated direction = flagged WORSE
    gross = (1 + cr[:, 1500]) / (1 + cr[:, 420]) - 1
    m = covered & np.isfinite(post)
    o, p = contrast(np.where(m, -gross - cost, np.nan), post > 0, day, val)        # SHORT the would-be rebound (pays cost too)
    res["006"] = {"share_flagged": float((post[m] > 0).mean()), "rebound_long_negated": o,
                  "rebound_long_val_flagged": float(np.nanmean((gross - cost)[m & val & (post > 0)])), "rebound_long_val_other": float(np.nanmean((gross - cost)[m & val & ~(post > 0)]))}; pv["006"] = p
    # 008: young (< 90 days) and thin book at onset -> larger pumps; long m0+1
    ts_h, codes, X = L.data(); ci = {c: k for k, c in enumerate(codes)}
    hi = np.clip(np.searchsorted(ts_h, t, "right") - 1, 0, len(ts_h) - 1)
    age = np.array([X["age"][hi[i], ci[code[i]]] if code[i] in ci else np.nan for i in range(n)])
    res["age_units_max"] = float(np.nanmax(age))
    days_listed = age / 24 if np.nanmax(age) > 5000 else age                       # panel age is in hours if values exceed ~5000
    d2v = np.full(n, np.nan)
    for cde, g in P.groupby("code"):
        d2v[g.index] = depth_ratio(cde, g["ts"].to_numpy(), g["dv24"].to_numpy())
    thin = d2v < np.nanquantile(d2v[disc], 0.2)
    flag = (days_listed < 90) & thin
    for hz, col in (("1h", 121), ("24h", 1500)):
        r = (1 + cr[:, col]) / (1 + cr[:, 61]) - 1 - cost
        o, p = contrast(r, flag, day, val); res.setdefault("008", {})[hz] = o; pv[f"008|{hz}"] = p
    res["008"]["share_flagged"] = float(flag.mean())
    return ts_h, codes, X


def precursor(P, Z, ts_h, codes, X, res, pv):
    ci = {c: k for k, c in enumerate(codes)}; on = {c: np.sort(g["ts"].to_numpy()) for c, g in P.groupby("code")}
    rows = []
    for code in Z:
        if code not in ci:
            continue
        j = ci[code]; idx, zi, _ = Z[code]
        ok = X["U"][:, j] & (np.nan_to_num(X["dv24"][:, j]) >= 2e6); ok[-25:] = False
        hs = np.flatnonzero(ok)
        if not len(hs):
            continue
        tt = ts_h[hs]                                                                # bar closing times
        zs = pd.Series(zi, index=idx).rolling(6, min_periods=4).mean()               # mean z of the 6 hours ending at each hour start
        z6 = zs.reindex(tt - H).to_numpy()                                           # hours starting tt-6h .. tt-1h (all closed at tt)
        d2v = depth_ratio(code, tt, X["dv24"][hs, j])
        o = on.get(code, np.array([], np.int64))
        y = (np.searchsorted(o, tt + 6 * H, "right") - np.searchsorted(o, tt, "right")) > 0
        c6 = np.exp(X["lc"][hs + 6, j] - X["lc"][hs, j]) - 1; c24 = np.exp(X["lc"][hs + 24, j] - X["lc"][hs, j]) - 1
        cost = 2 * (L.FEE + M.slip(X["dv24"][hs, j]))
        rows.append(pd.DataFrame({"code": code, "ts": tt, "z6": z6, "d2v": d2v, "y": y, "n6": c6 - cost, "n24": c24 - cost}))
    D = pd.concat(rows, ignore_index=True).dropna(subset=["z6", "d2v"]); D["day"] = D["ts"] // 86400; val = (D["ts"] >= VAL0).to_numpy()
    thr = np.nanquantile(D.loc[~val, "d2v"], 0.2); flag = ((D["z6"] < -1) & (D["d2v"] < thr)).to_numpy()
    out = {"coin_hours": int(len(D)), "share_flagged": float(flag.mean()),
           "p_pump_flagged_val": float(D.loc[val & flag, "y"].mean()), "p_pump_other_val": float(D.loc[val & ~flag, "y"].mean()),
           "p_pump_flagged_disc": float(D.loc[~val & flag, "y"].mean()), "p_pump_other_disc": float(D.loc[~val & ~flag, "y"].mean())}
    day = D["day"].to_numpy()
    for hz in ("n6", "n24"):
        o, p = contrast(D[hz].to_numpy(), flag, day, val); out[hz] = o; pv[f"005|{hz}"] = p
    res["005"] = out


def main():
    P, A = load(); Z = flows_z(); print("coins with flows", len(Z), flush=True)
    res = {"not_run": {"003": "no stablecoin flows in the cache", "004": "no token-holder snapshots", "007": "no bridge flows"}}; pv = {}
    ts_h, codes, X = pump_tests(P, A, Z, res, pv); print("pump tests done", flush=True)
    precursor(P, Z, ts_h, codes, X, res, pv)
    res["bh_pass"] = bh(pv)
    def good(o):
        return isinstance(o, dict) and "flagged_ci" in o and o["flagged_ci"][0] > 0 and o["diff_ci"][0] > 0
    passes = []
    for k in res["bh_pass"]:
        h, _, hz = k.partition("|")
        if h in ("001", "002") and good(res[h]["short_after_peak"]):
            passes.append(k)
        if h == "006" and good(res["006"]["rebound_long_negated"]):
            passes.append(k)
        if h in ("005", "008") and good(res[h][hz]):
            passes.append(k)
    res["pass"] = passes
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f20.json", "w"), indent=1, default=float)
    print(json.dumps({k: v for k, v in res.items()}, default=float)[:3000], "\nF20 BH", res["bh_pass"], "PASS", res["pass"], flush=True)


if __name__ == "__main__":
    main()
