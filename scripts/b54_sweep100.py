"""B54: prereg v5 (research/prereg_v5_sweep100.md) — 100 long-only rules on Upbit daily data, discovery 2018-2023 with BHY,
holdout 2024-2026 for survivors only. STRATEGIES below is the registered list. Paper research only.
Output research/b54_sweep100.md, data/upbit_db/b54_results.parquet."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402
import b52_ride_protect as P  # noqa: E402

DISC = ("2018-01-01", "2024-01-01")
HOLD = ("2024-01-01", "2026-10-05")
MAJ = ["KRW-BTC", "KRW-ETH"]
STABLE = {"KRW-USDT", "KRW-USDC"}

# ------------------------------------------------------------------ data + engine
O, C, V = B.panel()
CF = C.ffill()
R = (O.shift(-1) / O - 1)                                   # open t -> open t+1
COST = B.FEE + pd.DataFrame(B.slip(V.rolling(30, min_periods=10).median().fillna(0).to_numpy()), index=V.index, columns=V.columns)
IDX = C.index


def run(W):
    """W: target weights decided at close t (index = days). Returns daily net returns."""
    W = W.reindex(index=IDX, columns=C.columns).fillna(0.0)
    Wl = W.shift(1).fillna(0.0)
    turn = (Wl - Wl.shift(1).fillna(0.0)).abs()
    return (Wl * R.fillna(0.0)).sum(axis=1) - (turn * COST.shift(1).fillna(0.002)).sum(axis=1)


def zeros():
    return pd.DataFrame(0.0, index=IDX, columns=C.columns)


def trendw(x, looks=B.LOOKS):
    w = sum((x > x.rolling(L, min_periods=L).mean()).astype(float) for L in looks) / len(looks)
    return w.where(x.rolling(max(looks), min_periods=max(looks)).count() >= max(looks))


# universe: top-30 alts (no BTC/ETH/stables) by 30d median value, >= 180d history, ranked on data through t
_age = C.notna().cumsum()
_medv = V.rolling(30, min_periods=20).median()
_alts = [c for c in C.columns if c not in MAJ and c not in STABLE]
_rk = _medv[_alts].where(_age[_alts] >= 180).rank(axis=1, ascending=False, method="first")
U30 = (_rk <= 30).reindex(columns=C.columns, fill_value=False).fillna(False)
MON = IDX.weekday == 0


def weekly(Wd):
    """sample a daily target at each Monday's decision (Sunday close = index day Sunday), hold for the week."""
    Wd = Wd.copy()
    keep = pd.Series(IDX.weekday == 6, index=IDX)              # decide at Sunday close -> trade Monday open
    Wd[~keep.to_numpy()] = np.nan
    return Wd.ffill().fillna(0.0)


def ew(mask):
    m = mask.astype(float)
    return m.div(m.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def topq(score, mask, frac=1 / 3, low=False):
    s = score.where(mask)
    r = s.rank(axis=1, ascending=low, pct=True)
    return ew((r <= frac) & mask)


# ------------------------------------------------------------------ benchmarks
def W_f17():
    W = zeros()
    for m in MAJ:
        W[m] = 0.5 * P.p_weight(CF[m]).fillna(0.0)
    return W


def W_f15():
    W = zeros()
    for m in MAJ:
        W[m] = 0.5 * trendw(CF[m]).fillna(0.0)
    return W


def W_ew30():
    return ew(U30)


# ------------------------------------------------------------------ helpers
def pair(fn):
    W = zeros()
    for m in MAJ:
        W[m] = 0.5 * fn(CF[m]).astype(float).clip(0, 1).fillna(0.0)
    return W


def ema(x, n):
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(x, n):
    d = x.diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def state(enter, exit_):
    """1 after an enter signal until an exit signal (both known at close t)."""
    e, x = enter.fillna(False).to_numpy(), exit_.fillna(False).to_numpy(); s = np.zeros(len(e)); on = 0.0
    for t in range(len(e)):
        if on and x[t]:
            on = 0.0
        elif not on and e[t]:
            on = 1.0
        s[t] = on
    return pd.Series(s, index=enter.index)


def donch(c, n_in, n_out):
    return state(c > c.shift(1).rolling(n_in, min_periods=n_in).max(), c < c.shift(1).rolling(n_out, min_periods=n_out).min())


def eq_brake(Wbase, x):
    r = run(Wbase); eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).shift(1)   # r[t] ends at open t+1: lag it
    return Wbase.mul(np.where(dd < -x, 0.5, 1.0), axis=0)


def dual(n):
    W = zeros(); mom = CF[MAJ] / CF[MAJ].shift(n) - 1
    best = mom.idxmax(axis=1); ok = mom.max(axis=1) > 0
    for m in MAJ:
        W[m] = ((best == m) & ok).astype(float)
    return W


def voltgt(tgt):
    W = W_f15()
    for m in MAJ:
        rv = np.log(CF[m]).diff().rolling(20, min_periods=20).std() * np.sqrt(365)
        W[m] = W[m] * (tgt / rv).clip(upper=1).fillna(0)
    return W


def fam_A():
    S = []
    for L in (10, 30, 150, 300):
        S.append((f"A_sma{L}", "F17", f"BTC/ETH hold when close > SMA{L}", lambda L=L: pair(lambda c: c > c.rolling(L, min_periods=L).mean())))
    for f, s in ((12, 26), (20, 100), (50, 200)):
        S.append((f"A_ema{f}_{s}", "F17", f"EMA{f} > EMA{s}", lambda f=f, s=s: pair(lambda c: ema(c, f) > ema(c, s))))
    for a, b in ((20, 10), (55, 20), (100, 50), (200, 100)):
        S.append((f"A_donch{a}_{b}", "F17", f"Donchian enter {a}d high, exit {b}d low", lambda a=a, b=b: pair(lambda c: donch(c, a, b))))
    for n in (7, 30, 90, 180):
        S.append((f"A_tsmom{n}", "F17", f"{n}d return > 0", lambda n=n: pair(lambda c: c / c.shift(n) - 1 > 0)))
    for n in (30, 90, 180):
        S.append((f"A_dual{n}", "F17", f"dual momentum BTC vs ETH {n}d, cash if both < 0", lambda n=n: dual(n)))
    for tg in (0.4, 0.6, 0.8):
        S.append((f"A_vt{int(tg * 100)}", "F17", f"F15 x vol target {tg:.0%}", lambda tg=tg: voltgt(tg)))
    S.append(("A_sma200", "F17", "close > SMA200", lambda: pair(lambda c: c > c.rolling(200, min_periods=200).mean())))
    S.append(("A_sma200_rsi50", "F17", "close > SMA200 and RSI14 > 50", lambda: pair(lambda c: (c > c.rolling(200, min_periods=200).mean()) & (rsi(c, 14) > 50))))
    S.append(("A_dip_uptrend", "F17", "above SMA200: enter RSI14<40, exit RSI14>60", lambda: pair(
        lambda c: state((c > c.rolling(200, min_periods=200).mean()) & (rsi(c, 14) < 40), rsi(c, 14) > 60))))
    S.append(("A_rsi50", "F17", "RSI14 > 50", lambda: pair(lambda c: rsi(c, 14) > 50)))
    S.append(("A_rsi2_mr", "F17", "above SMA200: enter RSI2<10, exit close>SMA5", lambda: pair(
        lambda c: state((c > c.rolling(200, min_periods=200).mean()) & (rsi(c, 2) < 10), c > c.rolling(5).mean()))))
    S.append(("A_macd", "F17", "MACD(12,26) > signal(9)", lambda: pair(lambda c: (ema(c, 12) - ema(c, 26)) > (ema(c, 12) - ema(c, 26)).ewm(span=9, adjust=False).mean())))
    for x in (0.10, 0.15, 0.20):
        S.append((f"A_ddbrake{int(x * 100)}", "F17", f"F17 halved while its equity is > {x:.0%} below peak", lambda x=x: eq_brake(W_f17(), x)))
    return S


def mom(n, skip=0):
    return CF.shift(skip) / CF.shift(n) - 1


def alt_index():
    r = (ew(U30).shift(1).fillna(0) * R.fillna(0)).sum(axis=1)
    return (1 + r).cumprod().shift(1)            # level known at close t (uses returns through open t)


def fam_B():
    S = []
    for n in (7, 14, 60, 90):
        S.append((f"B_mom{n}", "EW30", f"weekly top tercile {n}d return", lambda n=n: weekly(topq(mom(n), U30))))
    S.append(("B_skipmom28_7", "EW30", "weekly top tercile return t-28..t-7", lambda: weekly(topq(mom(28, 7), U30))))
    S.append(("B_skipmom91_7", "EW30", "weekly top tercile return t-91..t-7", lambda: weekly(topq(mom(91, 7), U30))))
    for n in (3, 7, 14):
        S.append((f"B_rev{n}", "EW30", f"weekly bottom tercile {n}d return (reversal)", lambda n=n: weekly(topq(mom(n), U30, low=True))))
    S.append(("B_rev1_daily", "EW30", "daily bottom tercile 1d return", lambda: topq(mom(1), U30, low=True)))
    lr = np.log(CF).diff()
    for n in (30, 90):
        S.append((f"B_lowvol{n}", "EW30", f"weekly lowest tercile {n}d vol", lambda n=n: weekly(topq(lr.rolling(n, min_periods=n // 2).std(), U30, low=True))))
    for n in (7, 30):
        S.append((f"B_lowmax{n}", "EW30", f"weekly lowest tercile max daily return {n}d", lambda n=n: weekly(topq(lr.rolling(n, min_periods=n // 2).max(), U30, low=True))))
    S.append(("B_small", "EW30", "weekly smallest tercile by 30d value", lambda: weekly(topq(_medv, U30, low=True))))
    S.append(("B_large", "EW30", "weekly largest tercile by 30d value", lambda: weekly(topq(_medv, U30))))
    S.append(("B_52wh", "EW30", "weekly top tercile close / 365d high", lambda: weekly(topq(CF / CF.rolling(365, min_periods=120).max(), U30))))
    S.append(("B_90dh", "EW30", "weekly top tercile close / 90d high", lambda: weekly(topq(CF / CF.rolling(90, min_periods=60).max(), U30))))
    vt = V.rolling(7, min_periods=5).median() / V.rolling(90, min_periods=45).median()
    S.append(("B_volup", "EW30", "weekly top tercile value trend 7d/90d", lambda: weekly(topq(vt, U30))))
    S.append(("B_voldown", "EW30", "weekly bottom tercile value trend 7d/90d", lambda: weekly(topq(vt, U30, low=True))))
    btcw = trendw(CF["KRW-BTC"])
    S.append(("B_gate_btc05", "EW30", "EW30 only when BTC trend weight >= 0.5", lambda: ew(U30).mul((btcw >= 0.5).astype(float), axis=0)))
    S.append(("B_gate_btc1", "EW30", "EW30 only when BTC trend weight = 1", lambda: ew(U30).mul((btcw == 1).astype(float), axis=0)))
    S.append(("B_gate_altidx", "EW30", "EW30 only when alt index trend weight >= 0.5", lambda: ew(U30).mul((trendw(alt_index()) >= 0.5).astype(float), axis=0)))
    S.append(("B_own_trend", "EW30", "EW of universe coins whose own trend weight >= 0.75", lambda: ew(U30 & (pd.DataFrame({m: trendw(CF[m]) for m in C.columns}) >= 0.75))))
    S.append(("B_mom28_btcgate", "EW30", "weekly top tercile 28d return, only when BTC trend >= 0.5", lambda: weekly(topq(mom(28), U30)).mul((btcw >= 0.5).astype(float), axis=0)))
    return S


def season(on, mix=1.0):
    """alt-season switch: when `on` (known at close t) hold `mix` x EW30 + (1-mix) x F17, else F17."""
    on = on.reindex(IDX).fillna(False).astype(float)
    return W_ew30().mul(on * mix, axis=0) + W_f17().mul(1 - on * mix, axis=0)


def fam_C():
    ratio = alt_index() / CF["KRW-BTC"]
    btcw = trendw(CF["KRW-BTC"])
    S = []
    for th in (0.5, 0.75, 1.0):
        S.append((f"C_ratio_tw{int(th * 100)}", "F17", f"alts when alt/BTC trend weight >= {th}", lambda th=th: season(trendw(ratio) >= th)))
    for n in (30, 90, 180):
        S.append((f"C_ratio_sma{n}", "F17", f"alts when alt/BTC > its SMA{n}", lambda n=n: season(ratio > ratio.rolling(n, min_periods=n).mean())))
    S.append(("C_ratio90_btc", "F17", "alts when alt/BTC > SMA90 and BTC trend >= 0.5", lambda: season((ratio > ratio.rolling(90, min_periods=90).mean()) & (btcw >= 0.5))))
    S.append(("C_ratio30_btc", "F17", "alts when alt/BTC > SMA30 and BTC trend >= 0.5", lambda: season((ratio > ratio.rolling(30, min_periods=30).mean()) & (btcw >= 0.5))))
    S.append(("C_half_tw50", "F17", "50% alts when alt/BTC trend weight >= 0.5", lambda: season(trendw(ratio) >= 0.5, 0.5)))
    S.append(("C_half_sma90", "F17", "50% alts when alt/BTC > SMA90", lambda: season(ratio > ratio.rolling(90, min_periods=90).mean(), 0.5)))
    return S


MEM = P.membership(C, V)
BTCW = trendw(CF["KRW-BTC"])
VR = (V / V.shift(1).rolling(30, min_periods=20).median()).to_numpy()


def breakout(win, vmult, exit_kind="donch10", regime=True, slots=10, own=False):
    sig = (CF > CF.shift(1).rolling(win, min_periods=win).max()) & (V / V.shift(1).rolling(30, min_periods=20).median() >= vmult) & MEM
    if regime:
        sig = sig.mul(BTCW >= 0.5, axis=0)
    if own:
        sig = sig & (pd.DataFrame({m: trendw(CF[m]) for m in C.columns}) >= 0.75)
    S, Cn = sig.fillna(False).astype(bool).to_numpy(), CF.to_numpy()
    T = len(IDX); W = np.zeros(Cn.shape); held = {}
    for i in range(T - 1):
        for j in [j for j, x in held.items() if x <= i]:
            del held[j]
        cand = sorted([j for j in np.flatnonzero(S[i]) if j not in held], key=lambda j: -np.nan_to_num(VR[i, j]))
        for j in cand[: max(slots - len(held), 0)]:
            x = min(i + 60, T - 1); pk = Cn[i, j]
            for k in range(i + 1, min(i + 61, T - 1)):
                pk = max(pk, Cn[k, j])
                if exit_kind == "donch10":
                    if Cn[k, j] < np.nanmin(Cn[max(k - 10, 0):k, j]):
                        x = k; break
                elif Cn[k, j] < 0.8 * pk:
                    x = k; break
            W[i:x, j] = 1.0 / slots; held[j] = x
    return pd.DataFrame(W, index=IDX, columns=C.columns)


def fam_D():
    S = []
    for win in (10, 20, 55, 100):
        for vm in (1.5, 2, 3):
            if (win, vm) == (20, 2):
                continue                                   # = B52 R, already tested
            S.append((f"D_brk{win}_v{vm}", "EW30", f"{win}d high + value >= {vm}x, Donchian-10 exit, BTC gate", lambda win=win, vm=vm: breakout(win, vm)))
    for win in (20, 55):
        for vm in (1.5, 3):
            S.append((f"D_brk{win}_v{vm}_trail", "EW30", f"{win}d high + {vm}x value, 20% trailing exit, BTC gate", lambda win=win, vm=vm: breakout(win, vm, "trail20")))
    for win in (20, 55):
        S.append((f"D_brk{win}_v2_nogate", "EW30", f"{win}d high + 2x value, no regime gate", lambda win=win: breakout(win, 2, regime=False)))
        S.append((f"D_brk{win}_v2_5slots", "EW30", f"{win}d high + 2x value, 5 slots x 20%", lambda win=win: breakout(win, 2, slots=5)))
    S.append(("D_brk20_v1.5_own", "EW30", "20d high + 1.5x value, BTC gate + own trend >= 0.75", lambda: breakout(20, 1.5, own=True)))
    return S


def cal(keep_fn):
    """F17, but weight 0 for holding days where keep_fn(holding_date) is False. Holding day = decision day + 1."""
    hold_day = IDX + pd.Timedelta(days=1)
    keep = pd.Series([bool(keep_fn(d)) for d in hold_day], index=IDX).astype(float)
    return W_f17().mul(keep, axis=0)


def fam_E():
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    S = [(f"E_no_{names[k]}", "F17", f"F17 but flat on {names[k]}", lambda k=k: cal(lambda d: d.weekday() != k)) for k in range(7)]
    S += [("E_weekends_only", "F17", "F17 only Sat/Sun", lambda: cal(lambda d: d.weekday() >= 5)),
          ("E_weekdays_only", "F17", "F17 only Mon-Fri", lambda: cal(lambda d: d.weekday() < 5)),
          ("E_tom_only", "F17", "F17 only days 25..31 and 1..5 (turn of month)", lambda: cal(lambda d: d.day >= 25 or d.day <= 5)),
          ("E_no_tom", "F17", "F17 except turn of month", lambda: cal(lambda d: 6 <= d.day <= 24)),
          ("E_no_sep", "F17", "F17 except September", lambda: cal(lambda d: d.month != 9)),
          ("E_no_jun_sep", "F17", "F17 except June-September", lambda: cal(lambda d: d.month not in (6, 7, 8, 9))),
          ("E_oct_mar_only", "F17", "F17 only October-March", lambda: cal(lambda d: d.month in (10, 11, 12, 1, 2, 3))),
          ("E_no_first_week", "F17", "F17 except days 1-7", lambda: cal(lambda d: d.day > 7))]
    return S


STRATEGIES = fam_A() + fam_B() + fam_C() + fam_D() + fam_E()


def window(r, a, z):
    return r[(r.index >= a) & (r.index < z)]


def bhy(p, q=0.10):
    p = np.asarray(p); m = len(p); cm = np.sum(1 / np.arange(1, m + 1)); o = np.argsort(p)
    thr = q * np.arange(1, m + 1) / (m * cm); passed = p[o] <= thr
    k = np.max(np.flatnonzero(passed)) + 1 if passed.any() else 0
    out = np.zeros(m, bool); out[o[:k]] = True
    return out


def main():
    t0 = time.time()
    assert len(STRATEGIES) == 100, len(STRATEGIES)
    bench = {"F17": run(W_f17()), "EW30": run(W_ew30())}
    rows = []
    for sid, bn, desc, fn in STRATEGIES:
        W = fn(); s, b = run(W), bench[bn]
        d_s, d_b = window(s, *DISC), window(b, *DISC)
        h_s, h_b = window(s, *HOLD), window(b, *HOLD)
        obs, p = B.boot_p(d_s.to_numpy(), d_b.to_numpy(), lambda x: B.sharpe(x), 20, draws=2000)
        hobs, hp = B.boot_p(h_s.to_numpy(), h_b.to_numpy(), lambda x: B.sharpe(x), 20, draws=2000)
        rows.append(dict(id=sid, fam=sid[0], bench=bn, desc=desc, d_sh=B.sharpe(d_s), d_bsh=B.sharpe(d_b), d_diff=obs, d_p=p,
                         d_dd=B.maxdd(d_s), d_bdd=B.maxdd(d_b), d_cagr=B.cagr(d_s), d_bcagr=B.cagr(d_b),
                         h_sh=B.sharpe(h_s), h_bsh=B.sharpe(h_b), h_diff=hobs, h_p=hp, h_dd=B.maxdd(h_s), h_bdd=B.maxdd(h_b),
                         h_cagr=B.cagr(h_s), h_bcagr=B.cagr(h_b), inmkt=float((W.sum(axis=1) > 0).mean())))
        print(time.strftime("%T"), sid, f"disc {obs:+.2f} p={p:.3f} | hold {hobs:+.2f}", flush=True)
    D = pd.DataFrame(rows)
    D["bhy"] = bhy(D.d_p.to_numpy())
    D["survivor"] = D.bhy & (D.d_diff > 0) & (D.d_dd >= D.d_bdd - 0.10)
    ns = int(D.survivor.sum())
    D["PASS"] = D.survivor & (D.h_diff > 0) & (D.h_p < 0.05 / max(ns, 1)) & (D.h_dd >= D.h_bdd - 0.10)
    D.to_parquet(B.ROOT / "data/upbit_db/b54_results.parquet", index=False)
    report(D, ns, time.time() - t0)


def report(D, ns, secs):
    L = ["# B54: 100-rule long-only sweep (prereg v5)", "",
         f"Prereg research/prereg_v5_sweep100.md + STRATEGIES in scripts/b54_sweep100.py (commit before run). Run {time.strftime('%Y-%m-%d %H:%M')}, {secs:.0f}s. "
         f"Discovery {DISC[0]}..{DISC[1]}, holdout {HOLD[0]}..{HOLD[1]}. BHY q<=0.10 across 100 -> {ns} survivors; "
         f"holdout PASS needs p < {0.05 / max(ns, 1):.4f}.", "",
         "Benchmarks (discovery / holdout Sharpe): " + ", ".join(
             f"{b} {D[D.bench == b].d_bsh.iloc[0]:.2f} / {D[D.bench == b].h_bsh.iloc[0]:.2f}" for b in ("F17", "EW30")), "",
         "## Survivors and verdicts", "", "| id | rule | disc Sharpe diff | disc p | hold Sharpe diff | hold p | hold maxDD vs bench | verdict |",
         "|---|---|---|---|---|---|---|---|"]
    for r in D[D.survivor].itertuples():
        L.append(f"| {r.id} | {r.desc} | {r.d_diff:+.2f} | {r.d_p:.4f} | {r.h_diff:+.2f} | {r.h_p:.4f} | {r.h_dd:.0%} vs {r.h_bdd:.0%} | **{'PASS' if r.PASS else 'FAIL'}** |")
    if not ns:
        L.append("| (none) | | | | | | | |")
    L += ["", "## All 100 (sorted by discovery p). Holdout columns for non-survivors are information only.", "",
          "| id | bench | disc Sh | bench Sh | diff | p | maxDD vs bench | hold diff | hold p | in market |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in D.sort_values("d_p").itertuples():
        L.append(f"| {r.id} | {r.bench} | {r.d_sh:.2f} | {r.d_bsh:.2f} | {r.d_diff:+.2f} | {r.d_p:.3f} | {r.d_dd:.0%} vs {r.d_bdd:.0%} | "
                 f"{r.h_diff:+.2f} | {r.h_p:.3f} | {r.inmkt:.0%} |")
    L += ["", "## Family summary (count with positive discovery diff / positive holdout diff)", ""]
    for f, g in D.groupby("fam"):
        L.append(f"- {f}: {len(g)} rules, discovery diff > 0: {(g.d_diff > 0).sum()}, holdout diff > 0: {(g.h_diff > 0).sum()}, "
                 f"both > 0: {((g.d_diff > 0) & (g.h_diff > 0)).sum()}")
    (B.ROOT / "research/b54_sweep100.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:20]))


if __name__ == "__main__":
    main()
