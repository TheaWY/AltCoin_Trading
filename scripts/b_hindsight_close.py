"""B-hindsight section 4: closability test. Does any signal known at close t-1 predict where the money went?
Window B = 2024-01-01..2026-10-29 (ex-ante only, U30 cross-section, next-day open->open return). Also window A.
Decision rule (fixed in research/hindsight_20261101.md): closable iff mean-IC t > 2.5 on B AND net long-only top-quintile
spread > 0 in >= 60% of calendar months. Writes research/hindsight_close_20261101.md. Research only.
Usage: B51_END=2026-10-31 .venv/bin/python scripts/b_hindsight_close.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("B51_END", "2026-10-31")
sys.path.insert(0, str(ROOT / "scripts"))
import b54_sweep100 as S  # noqa: E402

IDX, CF, V, R, COST = S.IDX, S.CF, S.V, S.R, S.COST
B0, B1 = pd.Timestamp("2024-01-01"), pd.Timestamp("2026-10-29")
A0, A1 = pd.Timestamp("2026-10-06"), pd.Timestamp("2026-10-29")
lr = np.log(CF).diff()
BTC = CF["KRW-BTC"]


def signals():
    """all computed from candles complete at close t; used to predict R[t+1] (held t+1 -> t+2 open)."""
    sg = {}
    for n in (1, 5, 20, 60):
        sg[f"ret{n}d"] = CF / CF.shift(n) - 1
    sg["vol20d"] = lr.rolling(20, min_periods=15).std()
    mv = np.log(V.replace(0, np.nan))
    sg["value_z20"] = (mv - mv.rolling(20, min_periods=15).mean()) / mv.rolling(20, min_periods=15).std()
    sg["dist_hi20"] = CF / CF.rolling(20, min_periods=20).max() - 1
    sg["sma_stack"] = pd.DataFrame({m: S.trendw(CF[m]) for m in CF.columns})
    sg["max5d"] = lr.rolling(5, min_periods=5).max()
    return sg


def ic_stats(sig, lo, hi):
    """daily Spearman IC between signal at close t (U30 members) and next-day return R[t+1]."""
    Y = R.shift(-1).where(S.U30)
    X = sig.where(S.U30)
    m = (IDX >= lo) & (IDX <= hi)
    ics = []
    for t in IDX[m]:
        x, y = X.loc[t].dropna(), Y.loc[t].dropna()
        j = x.index.intersection(y.index)
        if len(j) >= 10:
            ics.append(pd.Series(x[j]).corr(y[j], method="spearman"))
    ic = pd.Series(ics)
    return dict(n=len(ic), ic=ic.mean(), t=ic.mean() / ic.std() * np.sqrt(len(ic)) if len(ic) > 2 else np.nan)


def quintile(sig, lo, hi, low=False):
    """long-only top (or bottom) quintile of U30 by signal, daily rebalance, net of costs, minus EW30 net."""
    W = S.topq(sig, S.U30, frac=0.2, low=low)
    r = S.run(W) - S.run(S.ew(S.U30))
    m = (IDX >= lo) & (IDX <= hi)
    r = r[m]
    mo = r.groupby(r.index.to_period("M")).sum()
    return dict(spread_ann=r.mean() * 365, pos_months=(mo > 0).mean(), n_months=len(mo))


def market_timing():
    """does anything at close t predict next-day EW30 return? OLS with HAC-free t-stats (daily, ~1000 obs)."""
    y = S.run(S.ew(S.U30)).shift(-1)                        # EW30 net return held t+1
    X = pd.DataFrame({"btc_ret5d": BTC / BTC.shift(5) - 1, "btc_ret20d": BTC / BTC.shift(20) - 1,
                      "ew_vol20d": S.run(S.ew(S.U30)).rolling(20).std(),
                      "f17_overlay": S.W_f17().sum(axis=1),
                      "alt_ret5d": S.alt_index().pct_change(5)})
    m = (IDX >= B0) & (IDX <= B1)
    D = pd.concat([y.rename("y"), X], axis=1)[m].dropna()
    Xm = np.column_stack([np.ones(len(D)), D.drop(columns="y").to_numpy()])
    beta, *_ = np.linalg.lstsq(Xm, D["y"].to_numpy(), rcond=None)
    res = D["y"].to_numpy() - Xm @ beta
    cov = res.var(ddof=Xm.shape[1]) * np.linalg.inv(Xm.T @ Xm)
    t = beta / np.sqrt(np.diag(cov))
    return pd.DataFrame({"beta": beta, "t": t}, index=["const"] + list(X.columns)), len(D)


def main():
    sg = signals()
    L = ["# Closability test (section 4), ex-ante only", "",
         f"Window B = {B0.date()}..{B1.date()}, window A = {A0.date()}..{A1.date()}. Universe U30 (top-30 alts by 30d value).",
         "IC = daily Spearman(signal at close t, next-day open->open return). Quintile = long-only top 20% by signal, daily",
         "rebalance, net of engine costs, minus EW30 net. Rule: closable iff |t_B| > 2.5 AND positive months >= 60%.", "",
         "| signal | dir | IC_B | t_B | IC_A | t_A | spread/yr B | +months B | spread/yr A | verdict |", "|---|---|---|---|---|---|---|---|---|---|"]
    rows = []
    for name, sig in sg.items():
        b, a = ic_stats(sig, B0, B1), ic_stats(sig, A0, A1)
        low = b["ic"] < 0                                   # choose the long side by the sign of the B-window IC
        qb, qa = quintile(sig, B0, B1, low=low), quintile(sig, A0, A1, low=low)
        ok = abs(b["t"]) > 2.5 and qb["pos_months"] >= 0.60 and qb["spread_ann"] > 0
        v = "closable" if ok else "not knowable"
        rows.append(dict(signal=name, low=low, **{f"{k}_B": x for k, x in b.items()}, **{f"{k}_A": x for k, x in a.items()},
                         **{f"{k}_B": x for k, x in qb.items()}, **{f"{k}_A": x for k, x in qa.items()}, verdict=v))
        L.append(f"| {name} | {'low' if low else 'high'} | {b['ic']:+.3f} | {b['t']:+.1f} | {a['ic']:+.3f} | {a['t']:+.1f} | "
                 f"{qb['spread_ann']:+.0%} | {qb['pos_months']:.0%} ({qb['n_months']}) | {qa['spread_ann']:+.0%} | {v} |")
    T, n = market_timing()
    L += ["", f"## Market timing, next-day EW30 net return on window B (n={n})", "", "| regressor | beta | t |", "|---|---|---|"]
    L += [f"| {k} | {r.beta:+.4f} | {r.t:+.1f} |" for k, r in T.iterrows()]
    L += ["", "Any |t| > 2 on window B counts as a timing signal worth a pre-registered test; otherwise timing is labelled luck."]
    out = ROOT / "research/hindsight_close_20261101.md"
    out.write_text("\n".join(L) + "\n")
    pd.DataFrame(rows).to_parquet(ROOT / "data/upbit_db/hindsight_close_20261101.parquet", index=False)
    print("wrote", out)


if __name__ == "__main__":
    main()
