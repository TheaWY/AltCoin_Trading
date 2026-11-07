"""B17 F13 changepoint / shape triggers at 1 second (registry F13_changepoint_001..009), evaluated against the CLEAN random null.
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f13.py   -> data/reports/b17/f13.json
Windows: 3,713 pump windows (sec1/, row 3900 = onset minute m0) and 10,200 random coin-minutes (sec1_rand/, not selected on outcome).
Search rows 360..3899 (a trigger may fire at most 60 min before m0; +1h exit fits the 7,500-second window). Entry = 1s close 5 s after the fire.
Triggers (first firing):
  bocpd : Adams-MacKay change point on 1s log returns (as F02: Gaussian, hazard 1/200, run length <= 300, sigma = baseline rows 0..299),
          fire when P(run length < 5) > 0.5
  motif : matrix-profile style z-normalised Euclidean distance of the last 60 s of 1s log-price to the nearest of 100 motifs = the 60 s
          leading to the +3% mark of the 100 most recent DISCOVERY pumps; fire when distance < threshold
  dtw   : DTW distance (Sakoe-Chiba band 6) of the last 60 s z-normalised shape to the nearest of 8 k-medoids of discovery pump shapes
Thresholds for motif/dtw chosen on DISCOVERY windows (pump + random; the reference pumps themselves excluded) to maximise net_real at +1h; validation = 2026.
Exits: +15m, +1h, trail3 (3% from the running high, within the window; stands in for the B15_1 hazard exit, as in F02).
Tradability (no outcome-selected negatives): net_real = w * E[net | fire, pump] + (1 - w) * E[net | fire, random],
  w = BR * fire_pump / (BR * fire_pump + (1 - BR) * fire_rand), BR = B15 pumps per eligible coin-hour (dv24 >= $2M) from the hourly panel.
  Day-cluster bootstrap over both components. Also reported: the random-window fires alone. Pass: BH q=0.10 over 9 cells, net_real CI > 0,
  >= 20 validation random fires, MAE p90 < 15%."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numba as nb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f03 import bh  # noqa: E402

B, OUT = ROOT / "data/cache/b17", ROOT / "data/reports/b17"
RNG = np.random.default_rng(1713)
VAL0 = 1767225600
S0, S1, W, LAT = 360, 3900, 60, 5
EXITS = ("t15m", "t1h", "trail3")


@nb.njit(cache=True)
def bocpd_first(r, sigma, start, stop, hazard=1 / 200, maxrl=300):
    P = np.zeros(maxrl + 1); P[0] = 1.0; mu = np.zeros(maxrl + 1); kap = np.ones(maxrl + 1)
    for t in range(stop):
        x = r[t] / sigma; tot = 0.0; cp = 0.0
        Pn = np.zeros(maxrl + 1)
        for i in range(maxrl + 1):
            pv = 1 + 1 / kap[i]; pred = np.exp(-0.5 * (x - mu[i]) ** 2 / pv) / np.sqrt(2 * np.pi * pv)
            g = P[i] * pred * (1 - hazard); cp += P[i] * pred * hazard
            if i < maxrl:
                Pn[i + 1] += g
            else:
                Pn[maxrl] += g
        Pn[0] = cp
        for i in range(maxrl + 1):
            tot += Pn[i]
        if tot > 0:
            for i in range(maxrl + 1):
                P[i] = Pn[i] / tot
        for i in range(maxrl, 0, -1):
            mu[i] = (kap[i - 1] * mu[i - 1] + x) / (kap[i - 1] + 1); kap[i] = kap[i - 1] + 1
        mu[0] = 0.0; kap[0] = 1.0
        if t >= start and P[0] + P[1] + P[2] + P[3] + P[4] > 0.5:
            return t
    return -1


@nb.njit(cache=True)
def dtw(a, b, band):
    n = len(a); D = np.full((n + 1, n + 1), np.inf); D[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(1, i - band), min(n, i + band) + 1):
            c = (a[i - 1] - b[j - 1]) ** 2
            D[i, j] = c + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return np.sqrt(D[n, n])


@nb.njit(cache=True)
def znorm(x):
    s = x.std()
    return (x - x.mean()) / s if s > 1e-12 else x * 0.0


@nb.njit(cache=True)
def dtw_profile(lp, med, start, stop, band):
    """min DTW distance of the z-normalised window [t-60, t) to any medoid, for t in start..stop-1 (stride 1)."""
    out = np.full(stop, np.inf)
    for t in range(start, stop):
        w = znorm(lp[t - 60:t])
        best = np.inf
        for m in range(med.shape[0]):
            d = dtw(w, med[m], band)
            if d < best:
                best = d
        out[t] = best
    return out


def motif_profile(lp, motifs, start, stop):
    win = np.lib.stride_tricks.sliding_window_view(lp, W)[start - W:stop - W]          # windows ending at t-1 for t in start..stop-1
    mu, sd = win.mean(1, keepdims=True), win.std(1, keepdims=True)
    z = np.where(sd > 1e-12, (win - mu) / np.where(sd > 1e-12, sd, 1), 0.0)
    corr = z @ motifs.T / W
    d = np.sqrt(np.clip(2 * W * (1 - corr.max(1)), 0, None))
    out = np.full(stop, np.inf); out[start:stop] = d
    return out


def shape_at_3pct(c):
    base = np.median(c[:300]); j = np.flatnonzero(c[S0:S1] >= base * 1.03)
    if not len(j):
        return None
    s = S0 + j[0]
    return znorm(np.log(c[s - W:s])) if s - W >= 0 else None


def exits(c, h, l, t):
    """long entered at c[t+LAT]; returns dict exit -> (gross, mae)."""
    i = t + LAT
    if i >= len(c) - 2:
        return None
    e = c[i]; out = {}
    for name, j in (("t15m", min(i + 900, len(c) - 1)), ("t1h", min(i + 3600, len(c) - 1))):
        out[name] = (c[j] / e - 1, 1 - np.nanmin(l[i + 1:j + 1]) / e)
    run = np.maximum.accumulate(h[i + 1:]); k = np.flatnonzero(c[i + 1:] <= run * 0.97)
    j = i + 1 + k[0] if len(k) else len(c) - 1
    out["trail3"] = (c[j] / e - 1, 1 - np.nanmin(l[i + 1:j + 1]) / e)
    return out


def windows(which=("pump", "rand")):
    """generator over windows (memory-light)."""
    parts = []
    if "pump" in which:
        parts.append(pd.read_parquet(B / "sample.parquet").assign(src="pump"))
    if "rand" in which:
        parts.append(pd.read_parquet(B / "sample_rand.parquet").assign(src="rand"))
    for r in pd.concat(parts).itertuples(index=False):
        fp = B / (f"sec1/{r.pump_id}.parquet" if r.src == "pump" else f"sec1_rand/{-r.pump_id}.parquet")
        if not fp.exists():
            continue
        d = pd.read_parquet(fp, columns=["c", "h", "l"])
        if len(d) != 7500:
            continue
        c = d["c"].to_numpy(float)
        if not (np.isfinite(c) & (c > 0)).all():
            c = pd.Series(np.where(c > 0, c, np.nan)).ffill().bfill().to_numpy()
        yield dict(src=r.src, pid=int(r.pump_id), ts=int(r.ts), dv24=float(r.dv24), c=c, h=d["h"].to_numpy(float), l=d["l"].to_numpy(float))


def base_rate():
    ts_h, codes, X = L.data()
    P = pd.read_parquet(M.C / "b15/pumps.parquet", columns=["ts"])
    t0, t1 = P["ts"].min(), P["ts"].max(); m = (ts_h >= t0) & (ts_h <= t1)
    ch = float(((X["U"][m]) & (np.nan_to_num(X["dv24"][m]) >= 2e6)).sum())
    return len(P) / ch, ch


GRID = {"bocpd": [0.5], "motif": [2.0, 3.0, 4.0, 5.0, 6.0, 7.0], "dtw": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]}


def kmedoids(Dm, k, iters=30):
    idx = list(RNG.choice(len(Dm), k, replace=False))
    for _ in range(iters):
        lab = np.argmin(Dm[:, idx], 1); new = []
        for j in range(k):
            mem = np.flatnonzero(lab == j)
            new.append(int(mem[np.argmin(Dm[np.ix_(mem, mem)].sum(1))]) if len(mem) else idx[j])
        if new == idx:
            break
        idx = new
    return idx


def references():
    shapes = []
    for w in windows(("pump",)):
        if w["ts"] < VAL0:
            s = shape_at_3pct(w["c"])
            if s is not None:
                shapes.append((w["ts"], s, w["pid"]))
    shapes.sort(key=lambda x: x[0])
    motifs = np.stack([s for _, s, _ in shapes[-100:]])
    pick = RNG.choice(len(shapes), min(1200, len(shapes)), replace=False)
    pool = np.stack([shapes[i][1] for i in pick])
    Dm = np.zeros((len(pool), len(pool)))
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            Dm[i, j] = Dm[j, i] = dtw(pool[i], pool[j], 6)
    mi = kmedoids(Dm, 8); med = pool[mi]
    refs = {p for _, _, p in shapes[-100:]} | {shapes[pick[i]][2] for i in mi}      # reference windows (excluded from threshold selection)
    json.dump(sorted(int(v) for v in refs), open(B / "f13_refs.json", "w"))
    print("reference shapes", len(shapes), "motifs", len(motifs), "medoids", len(med), flush=True)
    return motifs, med


def scan(motifs, med):
    rows = []
    for n_, w in enumerate(windows()):
        c, h, l = w["c"], w["h"], w["l"]
        lp = np.log(c)
        r1 = np.nan_to_num(np.diff(lp, prepend=lp[0])); sig = max(float(np.std(r1[:300])), 1e-5)
        prof = {"motif": motif_profile(lp, motifs, S0, S1), "dtw": dtw_profile(lp, med, S0, S1, 6)}
        fires = {("bocpd", 0.5): bocpd_first(r1, sig, S0, S1)}
        for trig, p in prof.items():
            for thr in GRID[trig]:
                k = np.flatnonzero(p[S0:S1] < thr); fires[(trig, thr)] = S0 + int(k[0]) if len(k) else -1
        cost = 2 * (L.FEE + float(M.slip(np.array([w["dv24"]]))[0]))
        for (trig, thr), t in fires.items():
            if t < 0:
                rows.append((w["src"], w["pid"], w["ts"], trig, thr, 0, None, np.nan, np.nan)); continue
            ex = exits(c, h, l, t)
            if ex is None:
                continue
            for name, (g, mae) in ex.items():
                rows.append((w["src"], w["pid"], w["ts"], trig, thr, 1, name, g - cost, mae))
        if n_ % 1000 == 0:
            print("windows", n_, flush=True)
    T = pd.DataFrame(rows, columns=["src", "pid", "ts", "trig", "thr", "fired", "exit", "net", "mae"])
    T.to_parquet(B / "f13_fires.parquet", index=False)
    return T


def evaluate(T, BR, trig, thr, ex, val, boot=True):
    x = T[(T["trig"] == trig) & (T["thr"] == thr) & ((T["ts"] >= VAL0) == val)]
    if not val and (B / "f13_refs.json").exists():                                    # self-matches would inflate discovery fire rates
        refs = set(json.load(open(B / "f13_refs.json"))); x = x[~((x["src"] == "pump") & x["pid"].isin(refs))]
    win = x.drop_duplicates(["src", "pid"])
    fp = win.loc[win["src"] == "pump", "fired"].mean(); fr = win.loc[win["src"] == "rand", "fired"].mean()
    y = x[(x["fired"] == 1) & (x["exit"] == ex)]; yp, yr = y[y["src"] == "pump"], y[y["src"] == "rand"]
    if len(yp) < 10 or len(yr) < 5 or not (fp + fr) > 0:
        return dict(fire_pump=float(fp), fire_rand=float(fr), n_rand=int(len(yr)), net_real=None, ci=[np.nan, np.nan], p=1.0)
    w = BR * fp / (BR * fp + (1 - BR) * fr) if fr > 0 else 1.0
    nr = w * yp["net"].mean() + (1 - w) * yr["net"].mean()
    out = dict(fire_pump=float(fp), fire_rand=float(fr), w=float(w), n_pump=int(len(yp)), n_rand=int(len(yr)), net_pump=float(yp["net"].mean()),
               net_rand=float(yr["net"].mean()), net_real=float(nr), mae90_rand=float(yr["mae"].quantile(0.9)), mae90_pump=float(yp["mae"].quantile(0.9)))
    if boot:
        gp = {d: g["net"].to_numpy() for d, g in yp.groupby(yp["ts"] // 86400)}; gr = {d: g["net"].to_numpy() for d, g in yr.groupby(yr["ts"] // 86400)}
        dp, dr = list(gp), list(gr); bo = []
        for _ in range(2000):
            a = np.concatenate([gp[d] for d in RNG.choice(dp, len(dp))]); b = np.concatenate([gr[d] for d in RNG.choice(dr, len(dr))])
            bo.append(w * a.mean() + (1 - w) * b.mean())
        bo = np.array(bo); out.update(ci=[float(v) for v in np.percentile(bo, [2.5, 97.5])], p=float((bo <= 0).mean()))
        out["rand_only"] = M.day_ci(yr["net"].to_numpy(), (yr["ts"] // 86400).to_numpy())
    return out


def main():
    BR, ch = base_rate(); print("base rate pumps per eligible coin-hour", BR, "coin-hours", ch, flush=True)
    fp = B / "f13_fires.parquet"
    if fp.exists():
        T = pd.read_parquet(fp)
    else:
        motifs, med = references(); T = scan(motifs, med)
    res = {"base_rate": BR, "chosen_thr": {}, "tests": {}}; pv = {}
    for trig in ("bocpd", "motif", "dtw"):
        thr = max(GRID[trig], key=lambda v: (evaluate(T, BR, trig, v, "t1h", False, boot=False).get("net_real") or -9))
        res["chosen_thr"][trig] = thr
        for ex in EXITS:
            hid = {"bocpd": 1, "motif": 4, "dtw": 7}[trig] + EXITS.index(ex)
            k = f"F13_{hid:03d}|{trig}|{ex}"
            o = evaluate(T, BR, trig, thr, ex, True); o["disc"] = evaluate(T, BR, trig, thr, ex, False, boot=False)
            res["tests"][k] = o; pv[k] = o["p"]
            print(k, json.dumps({a: (round(b, 4) if isinstance(b, float) else b) for a, b in o.items() if a != "disc"}, default=float), flush=True)
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if res["tests"][k]["ci"][0] > 0 and res["tests"][k]["n_rand"] >= 20
                   and max(res["tests"][k]["mae90_rand"], res["tests"][k]["mae90_pump"]) < 0.15]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f13.json", "w"), indent=1, default=float)
    print("F13 BH", res["bh_pass"], "PASS", res["pass"], flush=True)


if __name__ == "__main__":
    main()
