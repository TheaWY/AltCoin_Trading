"""B7_B genetic-programming formula mining with a decorrelated factor pool and cost-aware fitness (research/batch_B7.yaml).

Search uses ONLY discovery data (matrices cropped at 2025-09-01). Fitness = |median of 3 purged fold rank ICs|
- 0.02 x (1 - 24h rank autocorrelation) - 0.001 x size, minus a pool-redundancy penalty (AlphaGen-style pool awareness).
Pool: |rank corr| < 0.5 with every pool member and every known factor; max 15.
Out: data/reports/b7/b7_gp_pool.json, data/reports/b7/b7_gp_trials.csv (every evaluated formula, for the ledger)
Then: python scripts/b7_gp.py holdout   -> evaluates the frozen pool on the holdout (full matrices)."""
from __future__ import annotations

import json
import multiprocessing as mp
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b7"
WINS = (4, 24, 72, 168)
TERMS = ["r1", "lqv", "ofi1", "ofiq1", "lrv", "levy", "corrb", "f8", "ln", "rng"]
UN = ["neg", "abs", "slog", "cs_rank"]
BIN = ["add", "sub", "mul", "div"]
TSU = ["ts_mean", "ts_std", "ts_delta", "ts_z", "ts_max", "ts_min"]
TSB = ["ts_corr"]
POP, GENS, TOUR, PX, PM, MAXD = 400, 30, 5, 0.6, 0.3, 5
G = {}


# ------------------------------------------------------------------ data
def base(crop: bool):
    ts, codes, X = L.data()
    n = np.searchsorted(ts, L.DISC[1] + 25 * 3600) if crop else len(ts)
    qv = np.nan_to_num(X["qv"][:n])
    B = {"r1": X["r1"][:n], "lqv": np.log(qv + 1), "ofi1": L.safe_div(2 * np.nan_to_num(X["tbq"][:n]) - qv, qv),
         "ofiq1": L.safe_div(2 * np.nan_to_num(X["tbq_q"][:n]) - np.nan_to_num(X["qv_q"][:n]), np.nan_to_num(X["qv_q"][:n])),
         "lrv": np.log(np.nan_to_num(X["rv"][:n]) + 1e-10), "levy": X["levy"][:n], "corrb": X["corr_btc"][:n],
         "f8": X["f8"][:n], "ln": np.log(np.nan_to_num(X["n"][:n]) + 1), "rng": L.safe_div(X["h"][:n], X["l"][:n]) - 1}
    return {k: v.astype(np.float32) for k, v in B.items()}


# ------------------------------------------------------------------ formula trees
def rand_tree(d=0):
    if d >= MAXD - 1 or (d > 0 and random.random() < 0.3):
        return random.choice(TERMS)
    k = random.random()
    if k < 0.2:
        return (random.choice(UN), rand_tree(d + 1))
    if k < 0.45:
        return (random.choice(BIN), rand_tree(d + 1), rand_tree(d + 1))
    if k < 0.9:
        return (random.choice(TSU), rand_tree(d + 1), random.choice(WINS))
    return (random.choice(TSB), rand_tree(d + 1), rand_tree(d + 1), random.choice(WINS))


def s(t):
    if isinstance(t, str):
        return t
    if t[0] in TSU:
        return f"{t[0]}({s(t[1])},{t[2]})"
    if t[0] in TSB:
        return f"{t[0]}({s(t[1])},{s(t[2])},{t[3]})"
    return f"{t[0]}({','.join(s(a) for a in t[1:])})"


def size(t):
    return 1 if isinstance(t, str) else 1 + sum(size(a) for a in t[1:] if not isinstance(a, int))


def depth(t):
    return 0 if isinstance(t, str) else 1 + max(depth(a) for a in t[1:] if not isinstance(a, int))


def subtrees(t, path=()):
    yield path, t
    if not isinstance(t, str):
        for i, a in enumerate(t[1:], 1):
            if not isinstance(a, int):
                yield from subtrees(a, path + (i,))


def replace(t, path, new):
    if not path:
        return new
    lst = list(t)
    lst[path[0]] = replace(t[path[0]], path[1:], new)
    return tuple(lst)


def crossover(a, b):
    pa, _ = random.choice(list(subtrees(a)))
    _, sb = random.choice(list(subtrees(b)))
    c = replace(a, pa, sb)
    return c if depth(c) <= MAXD else a


def mutate(t):
    p, sub = random.choice(list(subtrees(t)))
    if not isinstance(sub, str) and sub[0] in TSU + TSB and random.random() < 0.5:
        lst = list(sub)
        lst[-1] = random.choice(WINS)
        return replace(t, p, tuple(lst))
    c = replace(t, p, rand_tree(len(p)))
    return c if depth(c) <= MAXD else t


def ev(t, B):
    if isinstance(t, str):
        return B[t]
    op = t[0]
    if op in UN:
        x = ev(t[1], B)
        if op == "neg":
            return -x
        if op == "abs":
            return np.abs(x)
        if op == "slog":
            return np.sign(x) * np.log1p(np.abs(x))
        return L.cs_rank(x)
    if op in BIN:
        a, b = ev(t[1], B), ev(t[2], B)
        return {"add": a + b, "sub": a - b, "mul": a * b}.get(op) if op != "div" else L.safe_div(a, np.where(np.abs(b) < 1e-9, np.nan, b))
    if op in TSU:
        x, w = ev(t[1], B), t[2]
        if op == "ts_mean":
            return L.M(x, w)
        if op == "ts_std":
            return L.SD(x, w)
        if op == "ts_delta":
            return x - L.lag(x, w)
        if op == "ts_z":
            return L.safe_div(x - L.M(x, w), L.SD(x, w))
        if op == "ts_max":
            return L.MX(x, w)
        return L.MN(x, w)
    return L.corr(ev(t[1], B), ev(t[2], B), t[3])


# ------------------------------------------------------------------ fitness (worker side)
def _init(crop):
    G["B"] = base(crop)
    ts, _, X = L.data()
    rows = L.eval_rows(L.DISC)
    G["rows"] = rows
    G["folds"] = np.array_split(np.arange(len(rows)), 3)
    K = L.known_factors()
    G["Kr"] = {k: L.cs_rank(np.where(X["U"][rows], v[rows], np.nan)) for k, v in K.items()}
    G["pool"] = {}


def rank_corr(A, Bm):
    ok = np.isfinite(A) & np.isfinite(Bm)
    a, b = np.where(ok, A, np.nan), np.where(ok, Bm, np.nan)
    a = a - np.nanmean(a, 1, keepdims=True)
    b = b - np.nanmean(b, 1, keepdims=True)
    c = np.nansum(a * b, 1) / np.sqrt(np.nansum(a * a, 1) * np.nansum(b * b, 1) + 1e-12)
    return float(np.nanmean(c))


def fitness(args):
    t, pool = args
    try:
        F = ev(t, G["B"])
        rows = G["rows"]
        ts, _, X = L.data()
        if not np.isfinite(F[rows])[X["U"][rows]].mean() > 0.8:
            return None
        ic = L.ic_series(F, rows).to_numpy()
        fic = [np.nanmean(ic[f[5:]]) for f in G["folds"]]          # first 5 rows of each fold purged (>24h)
        med = float(np.median(fic))
        sgn = 1.0 if med >= 0 else -1.0
        Fr = L.cs_rank(np.where(X["U"][rows], F[rows], np.nan))
        Fl = L.cs_rank(np.where(X["U"][rows - 24], F[rows - 24], np.nan))
        auto = rank_corr(Fr, Fl)
        known = max(abs(rank_corr(Fr, k)) for k in G["Kr"].values())
        pc = max([abs(rank_corr(Fr, p)) for p in pool.values()] or [0.0])
        fit = abs(med) - 0.02 * (1 - auto) - 0.001 * size(t) - 0.05 * max(0.0, pc - 0.5) - 0.1 * max(0.0, known - 0.5)
        consistent = all(np.sign(x) == sgn for x in fic)
        return dict(f=s(t), fit=fit, med_ic=med, sign=sgn, folds=fic, auto=auto, known_corr=known, pool_corr=pc,
                    size=size(t), consistent=consistent, Fr=Fr.astype(np.float16))
    except Exception:
        return None


def search():
    OUT.mkdir(parents=True, exist_ok=True)
    random.seed(7)
    np.random.seed(7)
    ctx = mp.get_context("fork")
    _init(True)                                  # parent copy for pool bookkeeping
    trials, cache, pool = [], {}, {}
    pop = [rand_tree() for _ in range(POP)]
    with ctx.Pool(8, initializer=_init, initargs=(True,)) as P:
        for gen in range(GENS):
            todo = [t for t in pop if s(t) not in cache]
            poolr = {k: v["Fr"].astype(np.float32) for k, v in pool.items()}
            for t, r in zip(todo, P.map(fitness, [(t, poolr) for t in todo], chunksize=4)):
                cache[s(t)] = r
                if r is not None:
                    trials.append({k: v for k, v in r.items() if k != "Fr"})
            scored = [(cache[s(t)]["fit"] if cache.get(s(t)) else -1.0, t) for t in pop]
            # pool update: best-first, decorrelated from pool and known factors, consistent sign across folds
            cand = sorted([r for r in cache.values() if r and r["consistent"] and r["fit"] > 0 and r["known_corr"] < 0.5],
                          key=lambda r: -r["fit"])
            pool = {}                                        # rebuilt each generation: best-first, weakest drop out
            for r in cand:
                if len(pool) >= 15 or r["f"] in pool:
                    continue
                if all(abs(rank_corr(r["Fr"].astype(np.float32), p["Fr"].astype(np.float32))) < 0.5 for p in pool.values()):
                    pool[r["f"]] = r
            best = max(scored, key=lambda x: x[0])
            print(f"gen {gen} evaluated {len(trials)} best fit {best[0]:.4f} {s(best[1])[:90]} pool {len(pool)}", flush=True)
            new = [t for _, t in sorted(scored, key=lambda x: -x[0])[:20]]            # elitism
            while len(new) < POP:
                a = max(random.sample(scored, TOUR), key=lambda x: x[0])[1]
                if random.random() < PX:
                    b = max(random.sample(scored, TOUR), key=lambda x: x[0])[1]
                    a = crossover(a, b)
                if random.random() < PM:
                    a = mutate(a)
                new.append(a)
            pop = new
    pd.DataFrame(trials).to_csv(OUT / "b7_gp_trials.csv", index=False)
    json.dump([{k: v for k, v in r.items() if k != "Fr"} for r in pool.values()], open(OUT / "b7_gp_pool.json", "w"),
              indent=1, default=float)
    print("done trials", len(trials), "pool", len(pool))


def parse(f):
    """Rebuild a tree from its string."""
    f = f.strip()
    if "(" not in f:
        return f
    op, rest = f.split("(", 1)
    rest = rest[:-1]
    parts, depth_, cur = [], 0, ""
    for ch in rest:
        if ch == "," and depth_ == 0:
            parts.append(cur)
            cur = ""
            continue
        depth_ += ch == "("
        depth_ -= ch == ")"
        cur += ch
    parts.append(cur)
    args = [int(p) if p.isdigit() else parse(p) for p in parts]
    return (op, *args)


def holdout():
    pool = json.load(open(OUT / "b7_gp_pool.json"))
    B = base(False)
    K = L.known_factors()
    rows_out, Z = [], []
    for r in pool:
        F = ev(parse(r["f"]), B)
        res = L.evaluate(F, L.HOLD, sign=r["sign"], K=K)
        rows_out.append(dict(formula=r["f"], disc_med_ic=r["med_ic"], **res))
        Z.append(r["sign"] * L.cs_rank(F))
        print(f"{r['f'][:80]:80s} disc={r['med_ic']:+.4f} hold ic={res['ic']:+.4f} {np.round(res['ic_ci'], 4)} "
              f"nov={res['novel_ic']:+.4f} ls={res['ls_net_day']:+.5f} {np.round(res['ls_net_ci'], 5)}", flush=True)
    combo = np.nanmean(np.stack(Z), 0)
    res = L.evaluate(combo, L.HOLD, K=K)
    rows_out.append(dict(formula="POOL_EQUAL_WEIGHT", **res))
    print("POOL", {k: v for k, v in res.items()})
    json.dump(rows_out, open(OUT / "b7_gp_holdout.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    holdout() if len(sys.argv) > 1 and sys.argv[1] == "holdout" else search()
