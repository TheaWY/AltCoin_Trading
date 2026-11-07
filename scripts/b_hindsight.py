"""B-hindsight (research/hindsight_20261101.md, sections 1-3): oracle portfolios on the forward window, my books,
ex-ante benchmarks, and a per-day gap decomposition of F19 vs each oracle. Same engine as B54 (b54_sweep100.run).
Oracles LOOK AHEAD by construction; rows are labelled. Paper/research only. Writes research/hindsight_results_20261101.md
and data/upbit_db/hindsight_20261101.parquet.
Usage: B51_END=2026-10-31 .venv/bin/python scripts/b_hindsight.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("B51_END", "2026-10-31")
sys.path.insert(0, str(ROOT / "scripts"))
import b54_sweep100 as S  # noqa: E402
import b55_combos as K  # noqa: E402

A0, A1 = pd.Timestamp("2026-10-06"), pd.Timestamp("2026-10-29")   # held days, open t -> open t+1
IDX, C, R, COST = S.IDX, S.C, S.R, S.COST
INA = (IDX >= A0) & (IDX <= A1)
MAJ = S.MAJ
# tradable set for holding day t = U30 membership known at close t-1, plus BTC/ETH
MASK = S.U30.shift(1).fillna(False).astype(bool)
for m in MAJ:
    MASK[m] = True
MASK = MASK & R.notna()


def from_held(H):
    """H: weights HELD on day t (earn R[t]). The engine wants weights decided at close t-1 -> shift back one day."""
    return H.shift(-1).fillna(0.0)


def parts(W):
    W = W.reindex(index=IDX, columns=C.columns).fillna(0.0)
    Wl = W.shift(1).fillna(0.0)
    turn = (Wl - Wl.shift(1).fillna(0.0)).abs()
    gross = (Wl * R.fillna(0.0)).sum(axis=1)
    cost = (turn * COST.shift(1).fillna(0.002)).sum(axis=1)
    return dict(W=W, H=Wl, gross=gross, cost=cost, net=gross - cost, exp=Wl.sum(axis=1), turn=turn.sum(axis=1))


def stats(p, lo=A0, hi=A1):
    m = (IDX >= lo) & (IDX <= hi)
    r = p["net"][m]
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    sh = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan
    return dict(total=eq.iloc[-1] - 1, maxdd=dd, sharpe=sh, rebal=int((p["turn"][m] > 1e-9).sum()),
                cost=p["cost"][m].sum(), exposure=p["exp"][m].mean(), days=int(m.sum()))


def held_one(col, lo=A0, hi=A1):
    H = S.zeros(); H.loc[(IDX >= lo) & (IDX <= hi), col] = 1.0
    return from_held(H)


# ------------------------------------------------------------------ oracles (look-ahead)
def o1_single():
    """buy-and-hold each tradable coin over A; returns {name: W} for all, caller ranks."""
    cols = [c for c in C.columns if MASK.loc[A0, c] and R.loc[INA, c].notna().all()]
    return {c: held_one(c) for c in cols}


def o2_switch():
    Rm = R.where(MASK)
    H = S.zeros()
    for t in IDX[INA]:
        row = Rm.loc[t].dropna()
        if len(row) and row.max() > 0:
            H.loc[t, row.idxmax()] = 1.0        # else cash
    return from_held(H)


def o3_btc_timing():
    H = S.zeros()
    H.loc[INA, "KRW-BTC"] = (R.loc[INA, "KRW-BTC"] > 0).astype(float)
    return from_held(H)


def o4_f19_cash_timing(W19):
    p = parts(W19)
    on = (p["gross"] > 0).astype(float)
    H = p["H"].mul(on, axis=0)
    H[~INA] = 0.0
    return from_held(H)


def o5_max_sharpe(dd_cap=0.10):
    from scipy.optimize import minimize
    cols = [c for c in C.columns if MASK.loc[A0, c] and R.loc[INA, c].notna().all()]
    X = R.loc[INA, cols].to_numpy()
    n = len(cols)

    def obj(w):
        r = X @ w
        sh = -r.mean() / (r.std() + 1e-9)
        eq = np.cumprod(1 + r); dd = (eq / np.maximum.accumulate(eq) - 1).min()
        return sh + 50 * max(0.0, -dd - dd_cap) ** 2 * 1e4

    best = None
    rng = np.random.default_rng(0)
    for _ in range(40):                                     # multistart, long-only, sum <= 1
        w0 = rng.dirichlet(np.ones(n)) * rng.uniform(0.5, 1.0)
        res = minimize(obj, w0, method="SLSQP", bounds=[(0, 1)] * n,
                       constraints=[{"type": "ineq", "fun": lambda w: 1 - w.sum()}], options={"maxiter": 300})
        if best is None or res.fun < best.fun:
            best = res
    w = pd.Series(np.where(best.x < 1e-4, 0, best.x), index=cols)
    H = S.zeros(); H.loc[INA, cols] = w.to_numpy()
    return from_held(H), w[w > 0].sort_values(ascending=False)


def o7_momentum(k, n=20):
    mom = S.CF / S.CF.shift(n) - 1
    sc = mom.where(S.U30)                                   # decided at close t with data through t
    rk = sc.rank(axis=1, ascending=False, method="first")
    return S.ew(rk <= k)


# ------------------------------------------------------------------ gap decomposition (section 3)
def decompose(pO, p19, Rm):
    """per-day: total gap = exposure + selection + cost + residual; concentration reported separately."""
    Rz = R.fillna(0.0)
    eO, e19 = pO["exp"], p19["exp"]
    nO = pO["H"].div(eO.replace(0, np.nan), axis=0).fillna(0.0)
    n19 = p19["H"].div(e19.replace(0, np.nan), axis=0).fillna(0.0)
    rO, r19 = (nO * Rz).sum(axis=1), (n19 * Rz).sum(axis=1)   # return per unit of exposure
    ref = r19.where(e19 > 0, Rm)                                 # F19 in cash -> reference is the market
    exposure = (eO - e19) * ref                                   # same picks as F19, oracle's exposure
    cost = p19["cost"] - pO["cost"]
    total = pO["net"] - p19["net"]
    selection = total - exposure - cost                           # oracle's picks at oracle's exposure (exact split)
    resid = total - exposure - selection - cost                   # 0 by construction, kept as a check
    eqO = S.ew(pO["H"] > 0)
    conc = eO * ((nO - eqO) * Rz).sum(axis=1)
    D = pd.DataFrame(dict(total=total, exposure=exposure, selection=selection, cost=cost, residual=resid,
                          concentration=conc))[INA]
    return D


def fmt(x, pct=True):
    return f"{x:+.1%}" if pct else f"{x:.2f}"


def main():
    t0 = time.time()
    W19 = K.book(K.sat_upvol(), 0.2, K.down_n1(), 1.0, False)
    st = {r[0]: r for r in S.STRATEGIES}
    books = {"F19 (chosen)": W19, "F17": S.W_f17(), "F18b": st["D_brk20_v1.5_own"][3](),
             "BTC hold": held_one("KRW-BTC"), "ETH hold": held_one("KRW-ETH"), "EW30": S.W_ew30(), "cash": S.zeros()}
    for k in (3, 5, 10):
        books[f"O7 mom20 top{k} (ex-ante)"] = o7_momentum(k)
    P = {k: parts(v) for k, v in books.items()}
    Rm = P["EW30"]["gross"]                                 # market = EW30 gross held return

    singles = {k: parts(v) for k, v in o1_single().items()}
    s_tot = pd.Series({k: stats(p)["total"] for k, p in singles.items()}).sort_values(ascending=False)
    oracles = {f"O1 best coin {s_tot.index[0][4:]}": singles[s_tot.index[0]],
               "O2 perfect daily switch": parts(o2_switch()),
               "O3 perfect BTC timing": parts(o3_btc_timing()),
               "O4 F19 perfect cash timing": parts(o4_f19_cash_timing(W19))}
    W5, w5 = o5_max_sharpe()
    oracles["O5 ex-post max-Sharpe (DD<=10%)"] = parts(W5)
    sweep = {}
    for name, bench, desc, fn in S.STRATEGIES:
        try:
            sweep[name] = stats(parts(fn()))["total"]
        except Exception as e:  # noqa: BLE001
            sweep[name] = np.nan; print("skip", name, e)
    sw = pd.Series(sweep).dropna().sort_values(ascending=False)
    oracles[f"O6 best of 100 ({sw.index[0]})"] = parts(st[sw.index[0]][3]())
    print(f"computed in {time.time() - t0:.0f}s")

    # ---- report
    L = [f"# Hindsight results, window A = {A0.date()}..{A1.date()} ({int(INA.sum())} held days), B51_END={os.environ['B51_END']}", "",
         "Engine: b54_sweep100.run (open->open, fee 5bp + slippage tier). Oracles look ahead; everything else is ex-ante.", "",
         "## Scoreboard", "", "| portfolio | side | total | maxDD | Sharpe(ann) | rebal days | cost | avg exposure |", "|---|---|---|---|---|---|---|---|"]
    rows = []
    for name, p in list(oracles.items()) + list(P.items()):
        s = stats(p); side = "hindsight" if name[:2] in ("O1", "O2", "O3", "O4", "O5", "O6") else "ex-ante"
        rows.append(dict(portfolio=name, side=side, **s))
        L.append(f"| {name} | {side} | {fmt(s['total'])} | {fmt(s['maxdd'])} | {s['sharpe']:.2f} | {s['rebal']} | {fmt(s['cost'])} | {s['exposure']:.0%} |")
    L += ["", "## O1 single coins, buy-and-hold over A (top 8 / bottom 5)", ""]
    L += [f"- {k[4:]}: {fmt(v)}" for k, v in s_tot.head(8).items()] + ["- ..."] + [f"- {k[4:]}: {fmt(v)}" for k, v in s_tot.tail(5).items()]
    L += ["", f"Median single coin: {fmt(s_tot.median())}; share of coins positive: {(s_tot > 0).mean():.0%}", ""]
    L += ["## O5 weights", ""] + [f"- {k[4:]}: {v:.1%}" for k, v in w5.items()] + [f"- cash: {1 - w5.sum():.1%}", ""]
    L += ["## O6 the 100 pre-registered B54 strategies on window A", "",
          f"best {sw.index[0]} {fmt(sw.iloc[0])}; median {fmt(sw.median())}; worst {sw.index[-1]} {fmt(sw.iloc[-1])}; positive: {(sw > 0).sum()}/{len(sw)}", "",
          "top 10:"] + [f"- {k}: {fmt(v)}  ({st[k][2]})" for k, v in sw.head(10).items()]
    L += ["", "## Gap decomposition, F19 vs each oracle (sum over A; + means oracle earned more)", "",
          "| oracle | total gap | exposure/timing | selection | cost | residual | (concentration within oracle) |", "|---|---|---|---|---|---|---|"]
    daily = {}
    for name, p in oracles.items():
        D = decompose(p, P["F19 (chosen)"], Rm); daily[name] = D; s = D.sum()
        L.append(f"| {name} | {fmt(s.total)} | {fmt(s.exposure)} | {fmt(s.selection)} | {fmt(s.cost)} | {fmt(s.residual)} | {fmt(s.concentration)} |")
    L += ["", "## Per-day: F19, EW30, BTC, O2 pick", "", "| day | F19 net | F19 exp | EW30 | BTC | O2 pick | O2 ret |", "|---|---|---|---|---|---|---|"]
    H2 = oracles["O2 perfect daily switch"]["H"]
    for t in IDX[INA]:
        pick = H2.loc[t]; pick = pick[pick > 0].index[0][4:] if (pick > 0).any() else "cash"
        L.append(f"| {t.date()} | {fmt(P['F19 (chosen)']['net'][t])} | {P['F19 (chosen)']['exp'][t]:.0%} | {fmt(Rm[t])} | {fmt(R.loc[t, 'KRW-BTC'])} | {pick} | {fmt(oracles['O2 perfect daily switch']['net'][t])} |")
    out = ROOT / "research/hindsight_results_20261101.md"
    out.write_text("\n".join(L) + "\n")
    pd.DataFrame(rows).to_parquet(ROOT / "data/upbit_db/hindsight_20261101.parquet", index=False)
    pd.concat(daily, names=["oracle", "day"]).to_parquet(ROOT / "data/upbit_db/hindsight_gaps_20261101.parquet")
    sw.rename("total_A").to_frame().to_parquet(ROOT / "data/upbit_db/hindsight_sweep_20261101.parquet")
    print("wrote", out, f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
