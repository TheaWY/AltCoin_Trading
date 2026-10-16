"""Shared B14 helpers: data, folds, slices, cost-aware P&L and bootstrap. No torch / lightgbm imports here."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
B14 = ROOT / "data/cache/b14"
OUT = ROOT / "data/reports/b14"
D_, H_ = 86400, 3600
DISC0, DISC1, HOLD1 = 1711929600, 1756684800, 1790208000          # 2024-04-01, 2025-09-01, 2026-09-24
QS = [1756684800, 1764547200, 1772323200, 1780272000, HOLD1]      # 2025-09-01, 2025-12-01, 2026-03-01, 2026-06-01, end
RNG = np.random.default_rng(14)


def load_tab():
    T = pd.read_parquet(B14 / "tab.parquet")
    G = json.load(open(B14 / "groups.json"))
    R = pd.read_parquet(B14 / "regime.parquet")
    T["day"] = T["ts"] // D_
    T = T.merge(R[["day", "state"]], on="day", how="left")
    T["state"] = T["state"].fillna(-1).astype(int)
    T["liq_tercile"] = T.groupby("ts")["ldv"].transform(lambda x: pd.qcut(x.rank(method="first"), 3, labels=False)).astype(int)
    T["kr"] = (T["korean_listed"] > 0).astype(int)
    T["third"] = np.where(T["ts"] < 1767225600, "2025H2", np.where(T["ts"] < 1782864000, "2026H1", "2026Q3"))   # 2026-01-01, 2026-07-01
    return T, G


def feature_sets(G):
    a = G["price_volume"] + G["market_state"]
    b = a + G["positioning"]
    c = b + G["korean"] + G["spot"]
    d = c + G["onchain"] + G["depth"] + G["calendar"] + G["size"]
    return {"a_price": a, "b_positioning": b, "c_korean_spot": c, "d_full": d}


def wf_folds(T):
    """Quarterly expanding walk-forward over the holdout; train = everything before test0 - 2d, val = last 30d of train."""
    for q0, q1 in zip(QS[:-1], QS[1:]):
        tr = (T["ts"] >= DISC0) & (T["ts"] < q0 - 2 * D_ - 30 * D_)
        va = (T["ts"] >= q0 - 2 * D_ - 30 * D_) & (T["ts"] < q0 - 2 * D_)
        te = (T["ts"] >= q0) & (T["ts"] < q1)
        yield q0, tr.to_numpy(), va.to_numpy(), te.to_numpy()


def boot_ci(x, reps=4000):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 5:
        return [float("nan"), float("nan")]
    b = [x[RNG.integers(0, len(x), len(x))].mean() for _ in range(reps)]
    return [float(v) for v in np.percentile(b, [2.5, 97.5])]


def daily_ic(df, score="p", target="y_res24"):
    ic = df.groupby("ts").apply(lambda g: g[score].rank().corr(g[target].rank()) if len(g) >= 20 else np.nan)
    return ic.groupby(ic.index // D_).mean().dropna()


def net_returns(df, side):
    """Per-row net return of a 24h position: side * raw - cost - side * funding."""
    return side * (np.expm1(df["y_raw24"].to_numpy())) - df["cost"].to_numpy() - side * df["fund24"].to_numpy()


def quintile_ls(df, score="p"):
    """Equal-weight top-quintile long / bottom-quintile short per timestamp, 24h hold, net; daily series."""
    out = []
    for t, g in df.groupby("ts"):
        if len(g) < 20:
            continue
        lo, hi = g[score].quantile([0.2, 0.8])
        L_, S_ = g[g[score] >= hi], g[g[score] <= lo]
        out.append((t, 0.5 * net_returns(L_, 1).mean() + 0.5 * net_returns(S_, -1).mean()))
    s = pd.Series(dict(out))
    return s.groupby(s.index // D_).mean()


def topk_long(df, score="p", k=10):
    out = []
    for t, g in df.groupby("ts"):
        if len(g) < k:
            continue
        out.append((t, net_returns(g.nlargest(k, score), 1).mean()))
    s = pd.Series(dict(out))
    return s.groupby(s.index // D_).mean()


def slice_table(df, daily_fn, score="p"):
    """Apply a daily-P&L function to the whole holdout and to each registered slice."""
    res = {"pooled": summ(daily_fn(df, score))}
    for name, col in (("regime", "state"), ("liq", "liq_tercile"), ("korean", "kr"), ("third", "third")):
        res[name] = {}
        for v, g in df.groupby(col):
            if v == -1 or len(g) < 500:
                continue
            res[name][str(v)] = summ(daily_fn(g, score))
    return res


def summ(s):
    s = s.dropna()
    if len(s) == 0:
        return dict(n=0)
    ci = boot_ci(s.to_numpy())
    return dict(days=int(len(s)), mean=float(s.mean()), ci=ci, sharpe=float(s.mean() / (s.std() + 1e-12) * np.sqrt(365)))


def slice_rules(st):
    """Registered per-slice rules: regime >= 2 of 3 positive; no liquidity tercile with CI entirely < 0; year thirds >= 2 of 3 positive."""
    reg = [v["mean"] > 0 for v in st.get("regime", {}).values() if v.get("days", 0) > 10]
    liq_ok = all(not (v["ci"][1] < 0) for v in st.get("liq", {}).values() if v.get("days", 0) > 10)
    thirds = [v["mean"] > 0 for v in st.get("third", {}).values() if v.get("days", 0) > 10]
    return dict(regime_ok=bool(sum(reg) >= 2), liq_ok=bool(liq_ok), third_ok=bool(sum(thirds) >= 2),
                all_ok=bool(sum(reg) >= 2 and liq_ok and sum(thirds) >= 2))
