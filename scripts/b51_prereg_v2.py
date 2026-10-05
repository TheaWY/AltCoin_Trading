"""B51: run pre-registered v2 hypotheses H1-H3 exactly as written in research/prereg_v2_longonly.md (committed 751343c).
Long-only Upbit allocation on daily candles 2017+. Nothing here is tuned; see the prereg for every parameter.
Output research/b51_prereg_v2.md, data/upbit_db/b51_daily.parquet. Paper research only.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
D1 = ROOT / "data/upbit_db/d1"
FEE = 0.0005
LOOKS = (20, 50, 100, 200)
ERAS = [("E1 2017-19", "2017-10-01", "2020-01-01"), ("E2 2020-21", "2020-01-01", "2022-01-01"),
        ("E3 2022", "2022-01-01", "2023-01-01"), ("E4 2023-24", "2023-01-01", "2025-01-01"),
        ("E5 2025-26", "2025-01-01", "2026-10-05")]
K, M_PRIOR = 3, 1100
EXCL = {"KRW-USDT", "KRW-USDC"}
END = __import__("os").environ.get("B51_END", "2026-10-05")


def panel():
    O, C, V = {}, {}, {}
    for f in sorted(D1.glob("*.parquet")):
        if f.stem in EXCL:
            continue
        g = pd.read_parquet(f)
        g = g[g.ts < pd.Timestamp(END, tz="UTC").timestamp()]   # prereg end (env B51_END for forward use); drops unfinished candle
        if len(g) < 30:
            continue
        g = g.set_index(pd.to_datetime(g.ts, unit="s")).sort_index()
        O[f.stem], C[f.stem], V[f.stem] = g.o, g.c, g.v
    O, C, V = (pd.DataFrame(x).sort_index() for x in (O, C, V))
    idx = pd.date_range(C.index[0], C.index[-1], freq="D")
    O, C, V = O.reindex(idx), C.reindex(idx), V.reindex(idx).fillna(0)
    return O, C, V


def slip(medv):
    return np.where(medv >= 1e11, 0.0002, np.where(medv >= 1e10, 0.0005, np.where(medv >= 1e9, 0.0010, 0.0020)))


def trend_w(c):
    """ensemble trend weight known at close of day t (uses closes <= t only)."""
    w = sum((c > c.rolling(L, min_periods=L).mean()).astype(float) for L in LOOKS) / len(LOOKS)
    ok = c.rolling(max(LOOKS), min_periods=max(LOOKS)).count() >= max(LOOKS)
    return w.where(ok)


def sharpe(r, per=365):
    r = np.asarray(r, float); r = r[np.isfinite(r)]
    return r.mean() / r.std(ddof=1) * np.sqrt(per) if len(r) > 2 and r.std() > 0 else np.nan


def maxdd(r):
    eq = np.cumprod(1 + np.nan_to_num(np.asarray(r, float)))
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def cagr(r, per=365):
    r = np.nan_to_num(np.asarray(r, float))
    return float(np.prod(1 + r) ** (per / max(len(r), 1)) - 1)


def stat_boot(n, mean_block, rng):
    """Politis-Romano stationary bootstrap indices."""
    new = rng.random(n) < 1 / mean_block; new[0] = True
    pos = np.flatnonzero(new); bid = np.cumsum(new) - 1
    starts = rng.integers(n, size=len(pos))
    return (starts[bid] + np.arange(n) - pos[bid]) % n


def boot_p(a, b, stat, mean_block, draws=5000, seed=7):
    """one-sided p for stat(a) - stat(b) > 0, centred stationary bootstrap on paired observations."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    obs = stat(a) - stat(b); rng = np.random.default_rng(seed); ds = np.empty(draws)
    for k in range(draws):
        ix = stat_boot(len(a), mean_block, rng)
        ds[k] = stat(a[ix]) - stat(b[ix])
    return obs, float(np.mean(ds - obs >= obs))


def dsr(r, m=M_PRIOR):
    """Deflated Sharpe ratio (Bailey & Lopez de Prado 2014), per-period SR, null variance 1/T."""
    r = np.asarray(r, float); r = r[np.isfinite(r)]; T = len(r)
    sr = r.mean() / r.std(ddof=1); g3 = pd.Series(r).skew(); g4 = pd.Series(r).kurt() + 3
    em = 0.5772156649
    sr0 = np.sqrt(1 / T) * ((1 - em) * norm.ppf(1 - 1 / m) + em * norm.ppf(1 - 1 / (m * np.e)))
    return float(norm.cdf((sr - sr0) * np.sqrt(T - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)))


def era_table(name, s, b, L):
    L += [f"### {name}: per era (strategy vs benchmark)", "",
          "| era | days | Sharpe S | Sharpe B | CAGR S | CAGR B | maxDD S | maxDD B |", "|---|---|---|---|---|---|---|---|"]
    wins = 0
    for en, a, z in ERAS:
        m = (s.index >= a) & (s.index < z)
        x, y = s[m].dropna(), b[m].reindex(s[m].dropna().index)
        if len(x) < 30:
            L.append(f"| {en} | {len(x)} | - | - | - | - | - | - |"); continue
        dd_s, dd_b = maxdd(x), maxdd(y)
        wins += dd_s > dd_b
        L.append(f"| {en} | {len(x)} | {sharpe(x):.2f} | {sharpe(y):.2f} | {cagr(x):+.0%} | {cagr(y):+.0%} | {dd_s:.0%} | {dd_b:.0%} |")
    L.append("")
    return wins


def sleeve(o, c, v):
    """one coin trend sleeve: daily open-to-open return with weight decided at the previous close."""
    R = o.shift(-1) / o - 1                       # return earned from open t to open t+1, held with w[t-1]
    w = trend_w(c.ffill())
    cost = FEE + pd.Series(slip(v.rolling(30, min_periods=10).median().fillna(0).to_numpy()), index=v.index)
    wl = w.shift(1)                                 # decided at close t-1, traded at open t
    turn = (wl - wl.shift(1)).abs().fillna(wl.abs())
    s = wl * R - turn * cost.shift(1)
    return s.where(wl.notna()), R


def h1(O, C, V, L):
    S, B = [], []
    for m in ("KRW-BTC", "KRW-ETH"):
        s, R = sleeve(O[m], C[m], V[m]); S.append(s); B.append(R)
    s = pd.concat(S, axis=1).mean(axis=1, skipna=False)
    b = pd.concat(B, axis=1).mean(axis=1, skipna=False).reindex(s.index)
    ok = s.notna() & b.notna(); s, b = s[ok], b[ok]
    return report("H1 trend allocation BTC+ETH", s, b, 20, L, dd_rule=True)


def universe(C, V, day):
    """top-30 coins by 30d median value with >= 180d history, using data up to the day before `day`."""
    hist = C.loc[:day - pd.Timedelta(days=1)]
    if len(hist) < 180:
        return []
    age = hist.notna().sum(); medv = V.loc[:day - pd.Timedelta(days=1)].iloc[-30:].median()
    elig = medv[(age >= 180) & (medv >= 1e9) & hist.iloc[-1].notna()]
    return list(elig.sort_values(ascending=False).index[:30])


def mondays(C):
    return [d for d in C.index if d.weekday() == 0 and d >= C.index[0] + pd.Timedelta(days=200)]


def h2(O, C, V, L):
    mon = mondays(C); rows = []; prev_s, prev_b = set(), set()
    medv_all = V.rolling(30, min_periods=10).median()
    for d in mon:
        nxt = d + pd.Timedelta(days=7)
        if nxt not in O.index:
            break
        u = universe(C, V, d)
        if len(u) < 15:
            continue
        sun, start = d - pd.Timedelta(days=1), d - pd.Timedelta(days=22)
        mom = (C.loc[sun, u] / C.loc[start, u] - 1).dropna()
        top = list(mom.sort_values(ascending=False).index[: max(len(mom) // 3, 3)])
        r = (O.loc[nxt, u] / O.loc[d, u] - 1)
        cost = pd.Series(FEE + slip(medv_all.loc[sun, u].fillna(0).to_numpy()), index=u)
        def legcost(cur, prev):
            ch = len(cur ^ prev) / max(len(cur), 1) if prev else 1.0
            return ch * cost[list(cur)].mean()
        s = r[top].mean() - legcost(set(top), prev_s); b = r.mean() - legcost(set(u), prev_b)
        rows.append((d, s, b)); prev_s, prev_b = set(top), set(u)
    W = pd.DataFrame(rows, columns=["d", "s", "b"]).set_index("d").dropna()
    edge = W.s - W.b
    L += ["## H2 weekly 3-week momentum, top tercile of top-30 (long only) vs EW top-30", ""]
    obs, p = boot_p(W.s.to_numpy(), W.b.to_numpy(), np.mean, 4)
    eras_pos = 0
    L += ["| era | weeks | mean edge/wk | strategy mean/wk | bench mean/wk |", "|---|---|---|---|---|"]
    for en, a, z in ERAS:
        e = edge[(edge.index >= a) & (edge.index < z)]
        if len(e) < 8:
            L.append(f"| {en} | {len(e)} | - | - | - |"); continue
        eras_pos += e.mean() > 0
        L.append(f"| {en} | {len(e)} | {e.mean():+.2%} | {W.s[e.index].mean():+.2%} | {W.b[e.index].mean():+.2%} |")
    ok = obs > 0 and p < 0.05 / K and eras_pos >= 4
    L += ["", f"Weeks {len(W)}; mean edge {obs:+.3%}/wk; stationary-bootstrap one-sided p = {p:.4f} (need < {0.05 / K:.4f}); "
          f"eras with positive edge {eras_pos}/5; DSR(strategy, M={M_PRIOR}) = {dsr(W.s.to_numpy()):.2f}. "
          f"Strategy Sharpe {sharpe(W.s, 52):.2f} vs bench {sharpe(W.b, 52):.2f}. **{'PASS' if ok else 'FAIL'}**", ""]
    return ("H2", ok, obs, p), W


def h3(O, C, V, L):
    mon = mondays(C); U = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    for d in mon:
        u = universe(C, V, d)
        if len(u) >= 15:
            U.loc[d:d + pd.Timedelta(days=6), :] = 0.0
            U.loc[d:d + pd.Timedelta(days=6), u] = 1.0 / len(u)
    R = O.shift(-1) / O - 1
    TW = pd.DataFrame({m: trend_w(C[m].ffill()) for m in C.columns})
    W = U * TW.shift(1).fillna(0)                      # trend decided at previous close
    cost = FEE + pd.DataFrame(slip(V.rolling(30, min_periods=10).median().fillna(0).to_numpy()), index=V.index, columns=V.columns)
    turn = (W - W.shift(1)).abs()
    s = (W * R.fillna(0)).sum(axis=1) - (turn * cost.shift(1).fillna(0.002)).sum(axis=1)
    b = (U * R.fillna(0)).sum(axis=1)
    live = U.sum(axis=1) > 0
    s, b = s[live], b[live]
    return report("H3 trend-gated top-30 altcoin basket", s, b, 20, L, dd_rule=True)


def report(name, s, b, mb, L, dd_rule):
    L += [f"## {name}", ""]
    wins = era_table(name, s, b, L)
    obs, p = boot_p(s.to_numpy(), b.to_numpy(), lambda x: sharpe(x), mb)
    ok = obs > 0 and p < 0.05 / K and (wins >= 4 if dd_rule else True)
    L += [f"Days {len(s)} ({s.index[0]:%Y-%m-%d}..{s.index[-1]:%Y-%m-%d}). Full-sample Sharpe {sharpe(s):.2f} vs {sharpe(b):.2f}, "
          f"CAGR {cagr(s):+.0%} vs {cagr(b):+.0%}, maxDD {maxdd(s):.0%} vs {maxdd(b):.0%}. Sharpe diff {obs:+.2f}, "
          f"stationary-bootstrap one-sided p = {p:.4f} (need < {0.05 / K:.4f}); lower maxDD in {wins}/5 eras (need 4); "
          f"DSR(M={M_PRIOR}) = {dsr(s.to_numpy()):.2f}. **{'PASS' if ok else 'FAIL'}**", ""]
    return (name.split()[0], ok, obs, p), pd.DataFrame({"s": s, "b": b})


def main():
    t0 = time.time()
    O, C, V = panel()
    L = ["# B51: pre-registered v2 long-only allocation tests (H1-H3)", "",
         f"Prereg: research/prereg_v2_longonly.md (commit 751343c, written before data). Run {time.strftime('%Y-%m-%d %H:%M')}. "
         f"{C.shape[1]} Upbit KRW markets (current listings only: survivorship warning for H2/H3), daily {C.index[0]:%Y-%m-%d}..{C.index[-1]:%Y-%m-%d}. "
         "Benchmarks are daily-rebalanced and cost-free (slightly favours the benchmark).", ""]
    res = []
    r1, d1 = h1(O, C, V, L); res.append(r1)
    r2, d2 = h2(O, C, V, L); res.append(r2)
    r3, d3 = h3(O, C, V, L); res.append(r3)
    L += ["## Verdicts (Bonferroni K=3)", "", "| hypothesis | stat diff | p | verdict |", "|---|---|---|---|"]
    for n, ok, obs, p in res:
        L.append(f"| {n} | {obs:+.4f} | {p:.4f} | **{'PASS' if ok else 'FAIL'}** |")
    L += ["", f"Runtime {time.time() - t0:.0f}s."]
    (ROOT / "research/b51_prereg_v2.md").write_text("\n".join(L) + "\n")
    pd.concat({"H1": d1, "H3": d3}).to_parquet(ROOT / "data/upbit_db/b51_daily.parquet")
    d2.to_parquet(ROOT / "data/upbit_db/b51_h2_weekly.parquet")
    print("\n".join(L[-8:]))


if __name__ == "__main__":
    main()
