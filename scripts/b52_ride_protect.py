"""B52: run prereg v3 (research/prereg_v3_ride_protect.md, commit cae2f8f) exactly as written.
R = surge ride (20d closing-high breakout + 2x volume + BTC trend regime, Donchian-10 exit, 10 slots x 10%),
P = loss protection overlay on F15/H1 (crash brake + volatility scaling). Upbit KRW daily, long only, paper research.
Output research/b52_ride_protect.md, data/upbit_db/b52_trades.parquet.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402

K = 2
STABLE = {"KRW-USDT", "KRW-USDC"}
MAJORS = ["KRW-BTC", "KRW-ETH"]


def costs(V):
    return B.FEE + pd.DataFrame(B.slip(V.rolling(30, min_periods=10).median().fillna(0).to_numpy()), index=V.index, columns=V.columns)


def membership(C, V):
    """universe at day t uses data through t-1: BTC, ETH + top 20 others by 30d median value with >= 365d history."""
    age = C.notna().cumsum().shift(1)
    medv = V.rolling(30, min_periods=20).median().shift(1)
    others = [c for c in C.columns if c not in MAJORS and c not in STABLE]
    m = medv[others].where(age[others] >= 365)
    rk = m.rank(axis=1, ascending=False, method="first")
    mem = (rk <= 20).reindex(columns=C.columns, fill_value=False)
    for c in MAJORS:
        mem[c] = age[c] >= 200
    return mem.fillna(False)


def exit_idx(Cn, j, i_entry_sig, T):
    """index of the CLOSE that triggers the exit (sell at next open); Donchian-10 on closes or 60 days."""
    for k in range(i_entry_sig + 1, min(i_entry_sig + 61, T - 1)):
        lo = np.nanmin(Cn[max(k - 10, 0):k, j])
        if Cn[k, j] < lo:
            return k
    return min(i_entry_sig + 60, T - 2)


def run_R(O, C, V, regime_df=None):
    """regime_df: optional bool DataFrame (day x coin) replacing the v3 BTC regime (used by B53 G1)."""
    Cf = C.ffill()
    mem = membership(C, V)
    btc_w = B.trend_w(Cf["KRW-BTC"])
    regime = (btc_w >= 0.5)
    if regime_df is not None:
        mem = mem & regime_df.reindex_like(mem).fillna(False).astype(bool)
        regime = pd.Series(True, index=C.index)
    brk = Cf > Cf.shift(1).rolling(20, min_periods=20).max()
    vratio = V / V.shift(1).rolling(30, min_periods=20).median()
    sig = brk & (vratio >= 2) & mem
    sig = sig.mul(regime, axis=0).astype(bool)
    Cn, On = Cf.to_numpy(), O.to_numpy(); cost = costs(V).to_numpy()
    T, cols = len(C), list(C.columns)
    open_pos, trades = {}, []                     # j -> (entry signal idx, exit close idx)
    S, VR = sig.to_numpy(), vratio.to_numpy()
    for i in range(T - 2):
        for j in [j for j, (_, x) in open_pos.items() if x <= i]:
            del open_pos[j]                         # exit already booked; slot frees after its exit close
        cand = [j for j in np.flatnonzero(S[i]) if j not in open_pos]
        cand.sort(key=lambda j: -np.nan_to_num(VR[i, j]))
        for j in cand[: max(10 - len(open_pos), 0)]:
            x = exit_idx(Cn, j, i, T)
            pe, px = On[i + 1, j], On[x + 1, j]
            if not (np.isfinite(pe) and np.isfinite(px) and pe > 0):
                continue
            g = px / pe - 1
            net = g - cost[i, j] - cost[x, j]
            trades.append((cols[j], C.index[i + 1], C.index[x + 1], i, x, j, g, net))
            open_pos[j] = (i, x)
    Tr = pd.DataFrame(trades, columns=["coin", "entry", "exit", "i", "x", "j", "gross", "net"])
    # portfolio: 10% per position, open-to-open returns from entry open (i+1) to exit open (x+1)
    R = (O.shift(-1) / O - 1).to_numpy()
    port = np.zeros(T)
    for r in Tr.itertuples():
        port[r.i + 1:r.x + 1] += 0.10 * np.nan_to_num(R[r.i + 1:r.x + 1, r.j])
        port[r.i + 1] -= 0.10 * cost[r.i, r.j]; port[r.x + 1] -= 0.10 * cost[r.x, r.j]
    return Tr, pd.Series(port, index=C.index), (mem.mul(regime, axis=0).astype(bool)).to_numpy(), Cn, On, cost


def control_R(Tr, eligible, Cn, On, cost, reps=200, seed=11):
    rng = np.random.default_rng(seed); T = len(Cn)
    ii, jj = np.nonzero(eligible[: T - 70])
    means = []
    for _ in range(reps):
        pick = rng.integers(len(ii), size=len(Tr)); nets = []
        for i, j in zip(ii[pick], jj[pick]):
            x = exit_idx(Cn, j, i, T); pe, px = On[i + 1, j], On[x + 1, j]
            if np.isfinite(pe) and np.isfinite(px) and pe > 0:
                nets.append(px / pe - 1 - cost[i, j] - cost[x, j])
        means.append(np.mean(nets))
    return np.array(means)


def day_ci(net, day, n=4000, seed=5):
    rng = np.random.default_rng(seed); u = np.unique(day); g = {k: net[day == k] for k in u}
    m = [np.concatenate([g[k] for k in rng.choice(u, len(u))]).mean() for _ in range(n)]
    return np.percentile(m, [2.5, 97.5])


def p_weight(c):
    """P overlay weight for one coin, all inputs known at the close of day t."""
    w = B.trend_w(c)
    cn = c.to_numpy(); sma20 = c.rolling(20, min_periods=20).mean().to_numpy()
    hi20 = c.rolling(21, min_periods=21).max().to_numpy()       # max(close t-20..t)
    brake = np.zeros(len(c), bool); on = False
    for t in range(1, len(c)):
        crash = (cn[t] <= 0.90 * cn[t - 1]) or (np.isfinite(hi20[t]) and cn[t] <= 0.85 * hi20[t])
        if crash:
            on = True
        elif on and np.isfinite(sma20[t]) and cn[t] > sma20[t]:
            on = False
        brake[t] = on
    lr = np.log(c).diff()
    rv = lr.rolling(20, min_periods=20).std()
    s = (rv.rolling(365, min_periods=120).median() / rv).clip(upper=1.0)
    return (w.where(~pd.Series(brake, index=c.index), 0.0) * s).where(w.notna())


def sleeve_w(o, w, v):
    R = o.shift(-1) / o - 1
    cost = B.FEE + pd.Series(B.slip(v.rolling(30, min_periods=10).median().fillna(0).to_numpy()), index=v.index)
    wl = w.shift(1); turn = (wl - wl.shift(1)).abs().fillna(wl.abs())
    return (wl * R - turn * cost.shift(1)).where(wl.notna())


def run_P(O, C, V):
    H, P = [], []
    for m in MAJORS:
        c = C[m].ffill()
        H.append(sleeve_w(O[m], B.trend_w(c), V[m])); P.append(sleeve_w(O[m], p_weight(c), V[m]))
    h = pd.concat(H, axis=1).mean(axis=1, skipna=False); p = pd.concat(P, axis=1).mean(axis=1, skipna=False)
    ok = h.notna() & p.notna()
    return h[ok], p[ok]


def worst30(r):
    eq = (1 + r.fillna(0)).cumprod()
    return float((eq / eq.shift(30) - 1).min())


def main():
    t0 = time.time()
    O, C, V = B.panel()
    L = ["# B52: prereg v3 surge ride (R) and loss protection (P)", "",
         f"Prereg research/prereg_v3_ride_protect.md (commit cae2f8f, before running). Run {time.strftime('%Y-%m-%d %H:%M')}. "
         f"Upbit KRW daily {C.index[0]:%Y-%m-%d}..{C.index[-1]:%Y-%m-%d}, {C.shape[1]} current markets (survivorship: alts biased up). "
         f"Family K={K}, one-sided alpha {0.05 / K}.", ""]
    # ---------------- R
    Tr, port, elig, Cn, On, cost = run_R(O, C, V)
    Tr["day"] = Tr.entry.dt.floor("D")
    lo, hi = day_ci(Tr.net.to_numpy(), Tr.day.to_numpy())
    ctrl = control_R(Tr, elig, Cn, On, cost)
    p_ctrl = float(np.mean(ctrl >= Tr.net.mean()))
    L += ["## R: surge ride", "", "| era | trades | mean net | win | median hold d | best | worst |", "|---|---|---|---|---|---|---|"]
    eras_ok = eras_n = 0
    for en, a, z in B.ERAS:
        x = Tr[(Tr.entry >= a) & (Tr.entry < z)]
        if len(x) < 10:
            L.append(f"| {en} | {len(x)} | - | - | - | - | - |"); continue
        eras_n += 1; eras_ok += x.net.mean() > 0
        L.append(f"| {en} | {len(x)} | {x.net.mean():+.2%} | {(x.net > 0).mean():.0%} | {(x.exit - x.entry).dt.days.median():.0f} | {x.net.max():+.0%} | {x.net.min():+.0%} |")
    okR = lo > 0 and p_ctrl < 0.05 / K and eras_ok >= 4
    L += ["", f"All {len(Tr)} trades: mean net {Tr.net.mean():+.2%} (day-clustered 95% CI [{lo:+.2%}, {hi:+.2%}]), win {(Tr.net > 0).mean():.0%}, "
          f"median {Tr.net.median():+.2%}. Random-entry control (same universe, BTC regime on, same exit, same trade count, 200 reps): "
          f"mean {ctrl.mean():+.2%} (5-95% {np.percentile(ctrl, 5):+.2%}..{np.percentile(ctrl, 95):+.2%}); p(control >= real) = {p_ctrl:.3f}. "
          f"Eras positive {eras_ok}/{eras_n}. **{'PASS' if okR else 'FAIL'}**", ""]
    h, p = run_P(O, C, V)
    port = port.reindex(h.index).fillna(0)
    L += ["Portfolio view (information only):", "", "| book | Sharpe | CAGR | maxDD |", "|---|---|---|---|",
          f"| F15 (H1) alone | {B.sharpe(h):.2f} | {B.cagr(h):+.0%} | {B.maxdd(h):.0%} |",
          f"| R alone (10 x 10% slots) | {B.sharpe(port):.2f} | {B.cagr(port):+.0%} | {B.maxdd(port):.0%} |",
          f"| 50% F15 + 50% R | {B.sharpe(0.5 * h + 0.5 * port):.2f} | {B.cagr(0.5 * h + 0.5 * port):+.0%} | {B.maxdd(0.5 * h + 0.5 * port):.0%} |", ""]
    # ---------------- P
    L += ["## P: loss protection overlay on F15", ""]
    wins = B.era_table("P (S) vs F15/H1 (B)", p, h, L)
    _, p_worse = B.boot_p(h.to_numpy(), p.to_numpy(), lambda x: B.sharpe(x), 20)
    cal = lambda r: B.cagr(r) / abs(B.maxdd(r))  # noqa: E731
    okP = wins >= 4 and cal(p) > cal(h) and p_worse > 0.05 / K and p.min() >= h.min() and worst30(p) >= worst30(h)
    L += [f"Full sample: Sharpe P {B.sharpe(p):.2f} vs F15 {B.sharpe(h):.2f} (p that F15 is better = {p_worse:.3f}); "
          f"Calmar {cal(p):.2f} vs {cal(h):.2f}; maxDD {B.maxdd(p):.0%} vs {B.maxdd(h):.0%}; worst day {p.min():.1%} vs {h.min():.1%}; "
          f"worst 30d {worst30(p):.1%} vs {worst30(h):.1%}; lower maxDD in {wins}/5 eras. **{'PASS' if okP else 'FAIL'}**", "",
          "## Verdicts", "", f"| R surge ride | **{'PASS' if okR else 'FAIL'}** |", "|---|---|", f"| P loss protection | **{'PASS' if okP else 'FAIL'}** |",
          "", f"Runtime {time.time() - t0:.0f}s."]
    (B.ROOT / "research/b52_ride_protect.md").write_text("\n".join(L) + "\n")
    Tr.to_parquet(B.ROOT / "data/upbit_db/b52_trades.parquet", index=False)
    print("\n".join(L[-12:]))


if __name__ == "__main__":
    main()
