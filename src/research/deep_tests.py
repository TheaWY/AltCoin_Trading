"""The test battery on the deep panel (src/research/deep_search.py).

Split: train = first 55% of hours, 1 day embargo, test = the rest. Nothing
chosen on test. Significance is deflated for clustering: a pump shows up in
many consecutive hourly rows of the same coin, so the effective sample of a
cell is its number of distinct coin-days, not rows.

  univariate   AUC of every feature for every target (train and test), and
               the hit rate of its top / bottom 10%
  cells        exhaustive 2-, 3- and 4-feature conjunctions of quintile bins:
               pairs over all features, triples over the 36 most informative,
               4-way over the top 20 (+ beam: best 3-way cells extended by
               every other feature). Ranked on train, judged on test
  depth        LightGBM with interaction depth 1 (purely additive) .. 6: does
               allowing features to interact improve out-of-sample ranking?
  k_features   the best model using exactly 1, 2, 3, 4, 5, 8, 12, 20, all
               features, and an exhaustive search of every 3- and 4-feature
               subset of the top 10
  timescale    one model per feature family / time window
  horizons     all of the above for +10% within 1h, 4h and 24h
  stability    the best model's test AUC by month and by market regime
  korea        the Upbit/Bithumb features on the period they exist, with vs
               without them

The economic check uses the trade label: +10% take-profit, -5% stop, else
the close at the horizon, minus 0.3% round trip.
"""

from __future__ import annotations

import itertools
import logging
import math
import time
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy.stats import rankdata

logger = logging.getLogger(__name__)
COST = 0.003
TARGETS = ("up10_24h", "win10_24h", "up10_4h", "win10_4h", "up10_1h", "dn10_24h", "dir_24h")


def add_trade_labels(p: pd.DataFrame) -> pd.DataFrame:
    for H in (1, 4, 24):
        win, mae, ret = p[f"win10_{H}h"], p[f"mae_{H}h"], p[f"ret_{H}h"]
        p[f"trade_{H}h"] = np.where(win == 1, 0.10, np.where(mae <= -0.05, -0.05, ret)) - COST
        p.loc[win.isna(), f"trade_{H}h"] = np.nan
        # direction only: among coins that moved 10% one way, did it go up? (NaN if neither / both)
        u, d = p[f"up10_{H}h"], p[f"dn10_{H}h"]
        p[f"dir_{H}h"] = np.where((u == 1) & (d != 1), 1.0, np.where((d == 1) & (u != 1), 0.0, np.nan))
    return p


def split(p: pd.DataFrame, frac: float = 0.55) -> tuple[np.ndarray, np.ndarray, int]:
    hours = np.sort(p["ts"].unique())
    cut = int(hours[int(len(hours) * frac)])
    tr = (p["ts"] < cut - 86400).to_numpy()
    te = (p["ts"] >= cut).to_numpy()
    return tr, te, cut


def _auc_from_ranks(r: np.ndarray, y: np.ndarray) -> float | None:
    npos = int(y.sum())
    nneg = len(y) - npos
    if npos < 20 or nneg < 20:
        return None
    return float((r[y].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def univariate(p: pd.DataFrame, feats: list[str], tr: np.ndarray, te: np.ndarray) -> list[dict[str, Any]]:
    out = []
    Y = {t: p[t].to_numpy() for t in TARGETS}
    for f in feats:
        x = p[f].to_numpy(np.float64)
        row: dict[str, Any] = {"feature": f, "coverage": float(np.isfinite(x).mean())}
        for part, m in (("train", tr), ("test", te)):
            ok = m & np.isfinite(x)
            if ok.sum() < 1000:
                continue
            r = rankdata(x[ok])
            q = r / len(r)
            for t in TARGETS:
                y = Y[t][ok]
                good = np.isfinite(y)
                if good.sum() < 1000:
                    continue
                yy = y[good] == 1
                rr = rankdata(x[ok][good])
                row[f"auc_{t}_{part}"] = _auc_from_ranks(rr, yy)
                if part == "test":
                    base = yy.mean()
                    qq = q[good]
                    row[f"top10_{t}"] = float(yy[qq >= 0.9].mean() / base) if base else None
                    row[f"bot10_{t}"] = float(yy[qq <= 0.1].mean() / base) if base else None
        out.append(row)
    return out


# ------------------------------------------------------------------ conjunction cells

def quantize(p: pd.DataFrame, feats: list[str], tr: np.ndarray, q: int = 5) -> np.ndarray:
    """uint8 codes 0..q-1 from train quantiles; q = missing."""
    C = np.full((len(p), len(feats)), q, dtype=np.uint8)
    for j, f in enumerate(feats):
        x = p[f].to_numpy(np.float64)
        xt = x[tr & np.isfinite(x)]
        if len(xt) < 1000 or np.unique(xt[:20000]).size < 3:
            continue
        edges = np.unique(np.quantile(xt, np.linspace(0, 1, q + 1)[1:-1]))
        fin = np.isfinite(x)
        C[fin, j] = np.searchsorted(edges, x[fin], side="right").astype(np.uint8)
    return C


def _cells(C: np.ndarray, cols: tuple[int, ...], q: int) -> tuple[np.ndarray, int]:
    base = q + 1
    code = np.zeros(C.shape[0], dtype=np.int64)
    for j in cols:
        code = code * base + C[:, j]
    return code, base ** len(cols)


def _decode(k: int, cols: tuple[int, ...], q: int) -> list[int]:
    base = q + 1
    out = []
    for _ in cols:
        out.append(k % base)
        k //= base
    return out[::-1]


def _wilson_lcb(pos: np.ndarray, n: np.ndarray, z: float = 1.64) -> np.ndarray:
    n = np.maximum(n, 1)
    ph = pos / n
    return (ph + z * z / (2 * n) - z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def cell_scan(C: np.ndarray, feats: list[str], combos: list[tuple[int, ...]], y: np.ndarray, tr: np.ndarray,
              q: int, min_n: int, keep: int = 400) -> list[dict[str, Any]]:
    """Every cell of every combo; returns the `keep` best by train Wilson lower bound."""
    ytr = np.where(tr & np.isfinite(y), y, 0.0)
    ntr_mask = (tr & np.isfinite(y)).astype(np.float64)
    base = ytr.sum() / ntr_mask.sum()
    best: list[tuple[float, tuple[int, ...], int, int, int]] = []
    for cols in combos:
        code, size = _cells(C, cols, q)
        n = np.bincount(code, weights=ntr_mask, minlength=size)
        pos = np.bincount(code, weights=ytr, minlength=size)
        good = n >= min_n
        if not good.any():
            continue
        lcb = np.where(good, _wilson_lcb(pos, n), 0)
        # cells containing a missing bin are skipped
        for k in np.argsort(lcb)[-5:]:
            if lcb[k] <= base * 1.3:
                continue
            bins = _decode(int(k), cols, q)
            if q in bins:
                continue
            best.append((float(lcb[k]), cols, int(k), int(n[k]), int(pos[k])))
    best.sort(key=lambda b: -b[0])
    return [{"cols": b[1], "code": b[2], "train_n": b[3], "train_pos": b[4], "train_rate": b[4] / b[3],
             "train_lift": b[4] / b[3] / base, "train_lcb": b[0]} for b in best[:keep]]


def judge(cells: list[dict[str, Any]], C: np.ndarray, feats: list[str], p: pd.DataFrame, y: np.ndarray, te: np.ndarray,
          trade: np.ndarray, q: int, edges_txt: Callable[[str, int], str]) -> list[dict[str, Any]]:
    """Test-period behaviour of train-selected cells, with coin-day clustering."""
    ok = te & np.isfinite(y)
    base = float(y[ok].mean())
    unit = (p["symbol"].astype("category").cat.codes.to_numpy().astype(np.int64) * 100000
            + (p["ts"].to_numpy() // 86400))
    out = []
    code_cache: dict[tuple[int, ...], np.ndarray] = {}
    for c in cells:
        cols = c["cols"]
        if cols not in code_cache:
            if len(code_cache) > 200:
                code_cache.clear()
            code_cache[cols] = _cells(C, cols, q)[0]
        m = ok & (code_cache[cols] == c["code"])
        n = int(m.sum())
        if n == 0:
            rate, n_eff, z, ev, pos_units = None, 0, None, None, 0
        else:
            rate = float(y[m].mean())
            n_eff = int(np.unique(unit[m]).size)
            pos_units = int(np.unique(unit[m & (y == 1)]).size)
            z = (rate - base) / math.sqrt(base * (1 - base) / max(n_eff, 1)) if base > 0 else None
            tv = trade[m]
            ev = float(np.nanmean(tv)) if np.isfinite(tv).any() else None
        bins = _decode(c["code"], cols, q)
        out.append({**{k: v for k, v in c.items() if k not in ("cols", "code")},
                    "rule": " & ".join(edges_txt(feats[j], b) for j, b in zip(cols, bins)),
                    "features": [feats[j] for j in cols], "test_n": n, "test_units": n_eff, "test_pos_units": pos_units,
                    "test_rate": rate, "test_lift": (rate / base) if rate is not None and base else None,
                    "test_z": z, "test_trade_ev": ev})
    # Benjamini-Hochberg over the judged cells (one-sided)
    from scipy.stats import norm
    zs = np.array([o["test_z"] if o["test_z"] is not None else -9 for o in out])
    pv = 1 - norm.cdf(zs)
    order = np.argsort(pv)
    m_ = len(pv)
    qv = np.empty(m_)
    prev = 1.0
    for rank, i in enumerate(order[::-1]):
        k = m_ - rank
        prev = min(prev, pv[i] * m_ / k)
        qv[i] = prev
    for o, pvi, qvi in zip(out, pv, qv):
        o["test_p"], o["test_q"] = float(pvi), float(qvi)
        o["holds"] = bool(qvi < 0.05 and (o["test_lift"] or 0) > 1.5 and o["test_pos_units"] >= 10)
    return out


# ------------------------------------------------------------------ models

def _lgb():
    import lightgbm as lgb
    return lgb


def fit_eval(p: pd.DataFrame, feats: list[str], target: str, tr: np.ndarray, te: np.ndarray, depth: int = -1,
             leaves: int = 31, rounds: int = 300, sub: int | None = 600_000, seed: int = 0) -> dict[str, Any]:
    lgb = _lgb()
    y = p[target].to_numpy()
    trm = tr & np.isfinite(y)
    tem = te & np.isfinite(y)
    idx = np.where(trm)[0]
    if sub and len(idx) > sub:
        rng = np.random.default_rng(seed)
        idx = np.sort(rng.choice(idx, sub, replace=False))
    Xall, cols_all = _matrix(p)
    ci = [cols_all[f] for f in feats]
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": leaves if depth < 0 else min(leaves, 2 ** depth),
              "max_depth": depth, "min_data_in_leaf": 400, "feature_fraction": 0.8, "bagging_fraction": 0.8,
              "bagging_freq": 1, "lambda_l2": 5.0, "verbose": -1, "seed": seed, "num_threads": 8}
    bst = lgb.train(params, lgb.Dataset(Xall[np.ix_(idx, ci)], y[idx], free_raw_data=True), num_boost_round=rounds)
    s = bst.predict(Xall[np.ix_(np.where(tem)[0], ci)])
    yt = y[tem] == 1
    res = {"target": target, "n_feats": len(feats), "depth": depth, "test_n": int(tem.sum()), "base": float(yt.mean())}
    r = rankdata(s)
    res["auc"] = _auc_from_ranks(r, yt)
    hz = target.split("_")[1]
    tv = p.loc[tem, "trade_" + hz].to_numpy() if target.startswith(("up10", "win10")) else None
    dn = p.loc[tem, "dn10_" + hz].to_numpy()
    for q_ in (0.001, 0.005, 0.01, 0.05):
        cut = np.quantile(s, 1 - q_)
        sel = s >= cut
        res[f"prec_top{q_}"] = float(yt[sel].mean())
        res[f"dn10_top{q_}"] = float(np.nanmean(dn[sel]))   # same picks falling 10%: direction vs just volatility
        res[f"lift_top{q_}"] = float(yt[sel].mean() / yt.mean()) if yt.mean() else None
        if tv is not None:
            res[f"ev_top{q_}"] = float(np.nanmean(tv[sel]))
    res["_score"] = s
    res["_booster"] = bst
    return res


_MX: dict[int, tuple[np.ndarray, dict[str, int]]] = {}


def _matrix(p: pd.DataFrame) -> tuple[np.ndarray, dict[str, int]]:
    key = id(p)
    if key not in _MX:
        _MX.clear()
        num = [c for c in p.columns if c not in ("symbol",) and p[c].dtype != object]
        _MX[key] = (p[num].to_numpy(np.float32), {c: i for i, c in enumerate(num)})
    return _MX[key]


def strip(r: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def importance(bst, feats: list[str]) -> list[tuple[str, float]]:
    g = bst.feature_importance("gain")
    tot = g.sum() or 1
    return sorted(((f, float(v / tot)) for f, v in zip(feats, g)), key=lambda x: -x[1])


FAMILIES = {
    "초단기 (1~15분)": lambda f: any(f.endswith(s) for s in ("_1m", "_5m", "_15m")) or f in ("max1m_60", "min1m_60"),
    "1시간": lambda f: f.endswith(("_60m", "_1h")) or f in ("rsi_1h", "rv_ratio_1h_24h", "nsurge_60m", "tsize_chg", "oi_1h"),
    "4~24시간": lambda f: f.endswith(("_240m", "_1440m", "_24h", "_4h")) or f in ("max5m_1440", "bursts3_1440", "ls_chg24"),
    "3일 이상": lambda f: f.endswith(("_4320m", "_10080m", "_7d", "_30d")) or f == "rv_ratio_24h_7d",
    "파생 (OI·롱숏·펀딩)": lambda f: f.startswith(("oi_", "ls_", "smart", "fut_", "funding")),
    "규모·나이": lambda f: f in ("mcap_log", "dv24_log", "turnover", "age_days", "age_censored", "oi_mcap"),
    "시장 전체·시간": lambda f: f.startswith(("btc_", "mkt_", "breadth", "fng", "hour", "weekday")),
    "순위 (코인간 비교)": lambda f: f.startswith(("rk_", "xs_")),
    "한국": lambda f: f.startswith(("kr_", "kimchi")),
    "공지": lambda f: f.startswith("ev_"),
}


def event_profile(p: pd.DataFrame, feats: list[str], H: int = 1, lags: tuple[int, ...] = (0, 1, 3, 6),
                  log: Callable[[str], None] = logger.info) -> dict[str, Any]:
    """For every +10%-within-H hour (first per coin-day) and every -10% hour:
    where was each feature, as a percentile among all coins at that same hour,
    at the decision time and 1/3/6 hours earlier? A feature worth watching sits
    far from 50 for pumps AND differs from dumps (else it only says 'volatile')."""
    use = [f for f in feats if p[f].notna().mean() > 0.3 and not f.startswith(("hour", "weekday", "btc_", "mkt_", "breadth", "fng"))]
    ranks = p.groupby("ts")[use].rank(pct=True).astype(np.float32)
    key = pd.MultiIndex.from_arrays([p["symbol"].to_numpy(), p["ts"].to_numpy()])
    ranks.index = key
    day = p["ts"] // 86400
    out: dict[str, Any] = {"horizon_h": H, "features": {}}
    ev = {}
    for kind, col in (("up", f"up10_{H}h"), ("down", f"dn10_{H}h")):
        m = p[col] == 1
        first = p[m].assign(day=day[m]).drop_duplicates(["symbol", "day"])
        ev[kind] = first
        out[f"n_{kind}"] = int(len(first))
    for lag in lags:
        for kind in ("up", "down"):
            e = ev[kind]
            idx = pd.MultiIndex.from_arrays([e["symbol"].to_numpy(), (e["ts"] - lag * 3600).to_numpy()])
            r = ranks.reindex(idx)
            for f in use:
                x = r[f].to_numpy()
                x = x[np.isfinite(x)]
                if len(x) < 30:
                    continue
                d = out["features"].setdefault(f, {})
                d[f"{kind}_med_{lag}h"] = float(np.median(x) * 100)
                d[f"{kind}_top10_{lag}h"] = float((x >= 0.9).mean())
    for f, d in out["features"].items():
        # separation: pumps vs dumps at decision time
        d["gap_0h"] = (d.get("up_med_0h", 50) - d.get("down_med_0h", 50))
        d["extreme_0h"] = abs(d.get("up_med_0h", 50) - 50)
    log(f"event profile H={H}: {out['n_up']} pumps, {out['n_down']} dumps")
    return out


def run_all(p: pd.DataFrame, feats: list[str], log: Callable[[str], None] = logger.info) -> dict[str, Any]:
    t0 = time.time()
    p = add_trade_labels(p)
    tr, te, cut = split(p)
    res: dict[str, Any] = {"rows": int(len(p)), "coins": int(p["symbol"].nunique()), "features": len(feats),
                           "from": int(p["ts"].min()), "to": int(p["ts"].max()), "test_from": cut,
                           "base_rates": {t: {"train": float(np.nanmean(p.loc[tr, t])), "test": float(np.nanmean(p.loc[te, t]))}
                                          for t in TARGETS}}
    log(f"panel {p.shape}, split at {cut}")

    # 1. univariate
    uni = univariate(p, feats, tr, te)
    res["univariate"] = uni
    log(f"univariate done ({time.time() - t0:.0f}s)")

    # informativeness ranking for the combinatorial scans: train AUC distance from 0.5
    def info(u):
        return max(abs((u.get(f"auc_{t}_train") or 0.5) - 0.5) for t in ("up10_24h", "win10_24h", "up10_4h"))
    ranked = [u["feature"] for u in sorted(uni, key=info, reverse=True) if u["coverage"] > 0.3]

    # 1b. event profiles: what did +10% hours look like just before
    res["events"] = {f"{H}h": event_profile(p, feats, H, log=log) for H in (1, 4)}

    # 2. conjunction cells
    q = 5
    C = quantize(p, feats, tr, q)
    edges: dict[str, np.ndarray] = {}
    for f in feats:
        x = p.loc[tr, f].to_numpy(np.float64)
        x = x[np.isfinite(x)]
        edges[f] = np.unique(np.quantile(x, np.linspace(0, 1, q + 1)[1:-1])) if len(x) > 1000 else np.array([])

    def txt(f: str, b: int) -> str:
        e = edges[f]
        lo = -np.inf if b == 0 else e[b - 1]
        hi = np.inf if b >= len(e) else e[b]
        fmt = lambda v: f"{v:.3g}"  # noqa: E731
        if b == 0:
            return f"{f} < {fmt(hi)} (하위 20%)"
        if b >= len(e):
            return f"{f} ≥ {fmt(lo)} (상위 20%)"
        return f"{fmt(lo)} ≤ {f} < {fmt(hi)} ({b * 20}~{b * 20 + 20}%)"

    fi = {f: j for j, f in enumerate(feats)}
    top36 = [fi[f] for f in ranked[:36]]
    top20 = [fi[f] for f in ranked[:20]]
    all_idx = [fi[f] for f in ranked]
    res["cells"] = {}
    for target in ("up10_24h", "win10_24h", "up10_4h"):
        y = p[target].to_numpy(np.float64)
        trade = p["trade_" + target.split("_")[1]].to_numpy()
        pairs = list(itertools.combinations(all_idx, 2))
        c2 = cell_scan(C, feats, pairs, y, tr, q, min_n=800)
        log(f"{target}: pairs scanned ({len(pairs)}) {time.time() - t0:.0f}s")
        c3 = cell_scan(C, feats, list(itertools.combinations(top36, 3)), y, tr, q, min_n=400)
        log(f"{target}: triples scanned {time.time() - t0:.0f}s")
        c4 = cell_scan(C, feats, list(itertools.combinations(top20, 4)), y, tr, q, min_n=250)
        # beam: the 60 best 3-way cells extended by one more condition from any feature
        beam = []
        for c in c3[:60]:
            for j in all_idx:
                if j in c["cols"]:
                    continue
                beam.append(tuple(sorted(c["cols"] + (j,))))
        c4b = cell_scan(C, feats, sorted(set(beam)), y, tr, q, min_n=250)
        log(f"{target}: 4-way scanned {time.time() - t0:.0f}s")
        out = {}
        for name, cells in (("2", c2[:150]), ("3", c3[:150]), ("4", c4[:100]), ("4_beam", c4b[:100])):
            judged = judge(cells, C, feats, p, y, te, trade, q, txt)
            out[name] = {"judged": len(judged), "holds": sum(o["holds"] for o in judged),
                         "share_test_lift_1_5": float(np.mean([(o["test_lift"] or 0) > 1.5 for o in judged])) if judged else None,
                         "median_test_lift": float(np.median([o["test_lift"] or 0 for o in judged])) if judged else None,
                         "median_train_lift": float(np.median([o["train_lift"] for o in judged])) if judged else None,
                         "best": sorted(judged, key=lambda o: -(o["test_lift"] or 0) if o["holds"] else 0)[:12],
                         "top_train": judged[:8]}
        res["cells"][target] = out

    # 3. models: interaction depth, k features, families, horizons
    res["models"] = {}
    for target in ("up10_24h", "win10_24h", "up10_4h", "up10_1h", "dn10_24h", "dir_24h"):
        mt: dict[str, Any] = {}
        full = fit_eval(p, feats, target, tr, te, depth=-1)
        imp = importance(full["_booster"], feats)
        mt["full"] = strip(full)
        mt["importance"] = imp[:30]
        order = [f for f, _ in imp]
        mt["depth"] = [strip(fit_eval(p, feats, target, tr, te, depth=d)) for d in (1, 2, 3, 4, 6)]
        mt["k"] = [strip(fit_eval(p, order[:k], target, tr, te, depth=-1)) | {"feats": order[:k]}
                   for k in (1, 2, 3, 4, 5, 8, 12, 20)]
        fam = []
        for name, fn in FAMILIES.items():
            fs = [f for f in feats if fn(f)]
            if fs:
                fam.append(strip(fit_eval(p, fs, target, tr, te, depth=-1)) | {"family": name, "feats": fs})
        mt["families"] = fam
        if target in ("up10_24h", "win10_24h"):
            # stability of the full model on test: by month and by regime
            s = full["_score"]
            tem = te & np.isfinite(p[target].to_numpy())
            sub = p.loc[tem, ["ts", target, "btc_ret_1440m", "fng"]].copy()
            sub["s"] = s
            stab = []
            for lab, g in list(sub.groupby((sub["ts"] // (30 * 86400)))) + [
                    ("BTC 상승일", sub[sub["btc_ret_1440m"] > 0]), ("BTC 하락일", sub[sub["btc_ret_1440m"] <= 0]),
                    ("공포 (FnG<40)", sub[sub["fng"] < 40]), ("탐욕 (FnG>=60)", sub[sub["fng"] >= 60])]:
                yy = g[target].to_numpy() == 1
                if yy.sum() >= 20:
                    stab.append({"part": str(lab) if not isinstance(lab, (int, np.integer)) else
                                 pd.Timestamp(int(lab) * 30 * 86400, unit="s").strftime("%Y-%m"),
                                 "n": int(len(g)), "auc": _auc_from_ranks(rankdata(g["s"].to_numpy()), yy)})
            mt["stability"] = stab
        mt["_order"] = order
        res["models"][target] = mt
        log(f"models {target} done {time.time() - t0:.0f}s (auc {full['auc']})")

    # 4. exhaustive 3- and 4-feature subsets of the top 12 (small, fast models)
    res["subsets"] = {}
    for target in ("up10_24h", "win10_24h"):
        order = res["models"][target]["_order"][:10]
        rows = []
        for k in (3, 4):
            for fs in itertools.combinations(order, k):
                r = fit_eval(p, list(fs), target, tr, te, depth=k, rounds=120, sub=300_000)
                rows.append(strip(r) | {"feats": list(fs), "k": k})
        rows.sort(key=lambda r: -(r["auc"] or 0))
        res["subsets"][target] = {"tried": len(rows), "best3": [r for r in rows if r["k"] == 3][:8],
                                  "best4": [r for r in rows if r["k"] == 4][:8]}
        log(f"subsets {target} done {time.time() - t0:.0f}s")

    # 5. Korea period: with vs without Korean features
    kf = [f for f in feats if f.startswith(("kr_", "kimchi"))]
    if kf and "kr_listed" in p:
        kp = p[p["kr_listed"].notna()].reset_index(drop=True)
        if len(kp) > 50_000:
            ktr, kte, kcut = split(kp, 0.5)
            base_f = [f for f in feats if f not in kf]
            kr = {"rows": int(len(kp)), "from": int(kp["ts"].min()), "test_from": kcut,
                  "univariate": [u for u in univariate(kp, kf, ktr, kte)]}
            for target in ("up10_24h", "up10_4h", "win10_24h"):
                a = fit_eval(kp, base_f, target, ktr, kte)
                b = fit_eval(kp, feats, target, ktr, kte)
                kr[target] = {"without": strip(a), "with": strip(b),
                              "korea_importance": [x for x in importance(b["_booster"], feats) if x[0] in kf]}
            res["korea"] = kr
            log(f"korea done {time.time() - t0:.0f}s")
    for mt in res["models"].values():
        mt.pop("_order", None)
    res["elapsed_s"] = round(time.time() - t0)
    return res
