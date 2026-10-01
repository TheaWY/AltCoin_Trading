"""B24_REGIME (registered 2026-10-01, before running). 유리's idea: predict whether the next day/week is bad for each
strategy and pick the strategy (or cash) from that prediction.

Strategies (daily net returns on the b7 panel, costs + funding included, 2024-04 .. 2026-09-24):
  BTC       BTC perp long, 1x
  MOM       quintile L/S on 7d return (b7_lib.long_short, 3 staggered 24h books)
  REV       quintile L/S on -24h return
  LOWVOL    quintile L/S on -7d realised vol
  VSH       -Upbit volume share, liquid universe, wide band, 3-day smoothing (B20 K4)
  PFOLLOW   every +10% 1h pump (dv24 >= $2M, >= 72h listed): long 4h, 10% notional per trade
  PFADE     same triggers, short 4h
  CASH      0
Regime features, known at 00:00 UTC of the day: BTC 1d/7d/30d return, BTC 7d vol, alt breadth (share up 24h),
  cross-sectional dispersion of 24h returns, equal-weight alt 7d return, mean funding, pump count and pump follow-through
  (mean 4h return of pumps that closed in the last 72h), each strategy's own trailing 7d and 30d return.
Selectors, walk-forward (quarterly refits from 2025-01-01, expanding window from 2024-05, rows purged so the target
period ends before the test block starts), horizon 1 day (daily rebalance) and 7 days (weekly rebalance):
  EW      equal weight all 7 strategies                         (baseline, no prediction)
  WINNER  last 30 days' best strategy, cash if it lost           (baseline, simple regime following)
  RIDGE   per-strategy ridge forecast of next-period return, hold the argmax if forecast > 0 else cash
  LGBM    same with LightGBM (small trees)
  GATE    LightGBM classifier P(EW loses next period); hold EW if P < 0.5 else cash
Pass = RIDGE or LGBM or GATE: OOS daily-net bootstrap CI > 0 AND OOS Sharpe above both EW and WINNER, at either horizon.
Output data/reports/b24/regime.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b24"
D = 86400
OOS0 = int(pd.Timestamp("2025-01-01").timestamp())
TRAIN0 = int(pd.Timestamp("2024-05-01").timestamp())


def strategies(ts, codes, X):
    K = L.known_factors()
    days = {}
    rows8 = np.flatnonzero((ts % (8 * 3600) == 0) & (ts >= ts[0] + 30 * D))
    for name, F in (("MOM", K["ret_7d"]), ("REV", -K["ret_24h"]), ("LOWVOL", -K["rv_7d"])):
        ls = L.long_short(F, rows8)
        days[name] = ls.groupby(ls.index // D)["net"].sum()
        print("strategy", name, flush=True)
    import b20_costaware as B20
    import disc_engine as DE
    vs = None
    for n, _, f in DE.variables():
        if n == "vshare_up":
            vs = -np.asarray(f); break
    XX = dict(X); XX["ts"] = ts
    o = pd.DataFrame(B20.run(vs, rows8, XX, ent=0.1, ext=0.5, liquid=True, smooth=9), columns=["ts", "net", "turn"])
    days["VSH"] = o.groupby(o.ts // D).net.sum()
    print("strategy VSH", flush=True)
    bi = codes.index("BTCUSDT")
    c = X["c"]
    btc = pd.Series(c[:, bi], index=ts)
    f8 = pd.Series(np.nan_to_num(X["f8"][:, bi]), index=ts)
    bd = btc[btc.index % D == 0]
    r = bd.pct_change().shift(-1)                                     # day d: close(d+1 00:00)/close(d 00:00)-1
    fund = f8.groupby(f8.index // D).sum() / 8.0
    days["BTC"] = pd.Series(r.to_numpy(), index=bd.index // D) - fund.reindex(bd.index // D).fillna(0).to_numpy()
    # pumps
    r1 = X["r1"]; dv = np.nan_to_num(X["dv24"]); age = X["age"]
    trig = (r1 >= np.log(1.10)) & (dv >= 2e6) & (age >= 72)
    ti, tj = np.nonzero(trig[:-5])
    g = c[ti + 4, tj] / c[ti, tj] - 1
    ok = np.isfinite(g)
    ti, tj, g = ti[ok], tj[ok], g[ok]
    cost = 2 * (L.FEE + L.slip(dv[ti, tj]))
    pumps = pd.DataFrame({"ts": ts[ti], "g": g, "cost": cost})
    days["PFOLLOW"] = (0.1 * (pumps.g - pumps.cost)).groupby(pumps.ts // D).sum()
    days["PFADE"] = (0.1 * (-pumps.g - pumps.cost)).groupby(pumps.ts // D).sum()
    S = pd.DataFrame(days).sort_index()
    S = S[(S.index >= TRAIN0 // D - 40) & (S.index < int(pd.Timestamp("2026-09-24").timestamp()) // D)]
    S[["PFOLLOW", "PFADE"]] = S[["PFOLLOW", "PFADE"]].fillna(0.0)
    return S.fillna(0.0), pumps


def features(ts, codes, X, S, pumps):
    bi = codes.index("BTCUSDT"); lc = X["lc"]; U = X["U"]
    i0 = np.flatnonzero(ts % D == 0)
    day = ts[i0] // D
    lb = lc[:, bi]
    r24 = lc - L.lag(lc, 24)
    F = pd.DataFrame(index=day)
    F["btc_1d"] = (lb - L.lag(lb[:, None], 24)[:, 0])[i0]
    F["btc_7d"] = (lb - L.lag(lb[:, None], 168)[:, 0])[i0]
    F["btc_30d"] = (lb - L.lag(lb[:, None], 720)[:, 0])[i0]
    F["btc_vol7"] = L.SD(X["r1"][:, [bi]], 168)[i0, 0]
    up = np.where(U, r24 > 0, np.nan)
    F["breadth"] = np.nanmean(up, 1)[i0]
    F["disp"] = np.nanstd(np.where(U, r24, np.nan), 1)[i0]
    F["alt_7d"] = np.nanmean(np.where(U, lc - L.lag(lc, 168), np.nan), 1)[i0]
    F["funding"] = np.nanmean(np.where(U, X["f8"], np.nan), 1)[i0]
    close_t = pumps.ts.to_numpy() + 4 * 3600
    order = np.argsort(close_t); ct, gg = close_t[order], pumps.g.to_numpy()[order]
    cs = np.concatenate([[0], np.cumsum(gg)])
    t0 = ts[i0]
    hi = np.searchsorted(ct, t0, side="right"); lo = np.searchsorted(ct, t0 - 72 * 3600, side="left")
    n = hi - lo
    F["pump_n72"] = n
    F["pump_follow72"] = np.where(n >= 3, (cs[hi] - cs[lo]) / np.maximum(n, 1), 0.0)
    for k in S.columns:
        s = S[k].reindex(F.index).fillna(0)
        sh = 2 if k.startswith("P") else 1          # pump trades of day d-1 can close up to 4h into day d
        s = s.shift(sh - 1)
        F[f"{k}_7d"] = s.shift(1).rolling(7, min_periods=3).sum()        # trailing, ends the day before
        F[f"{k}_30d"] = s.shift(1).rolling(30, min_periods=10).sum()
    return F.reindex(S.index)


def boot(x):
    x = np.asarray(x, float)
    return [float(v) for v in L.boot_ci(x)] if len(x) > 20 else [None, None]


def stats(r):
    r = pd.Series(r).dropna()
    return {"bp_day": float(r.mean() * 1e4), "ci_bp": [v * 1e4 if v is not None else None for v in boot(r.to_numpy())],
            "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0, "days": int(len(r)), "total": float(r.sum())}


def run(S, F, H):
    from sklearn.linear_model import Ridge
    import lightgbm as lgb
    strat = list(S.columns)
    Y = {k: S[k].rolling(H).sum().shift(-(H - 1)) for k in strat}               # return over days d..d+H-1
    EWy = S.mean(1).rolling(H).sum().shift(-(H - 1))
    days = S.index.to_numpy()
    blocks = [int(pd.Timestamp(x).timestamp()) // D for x in ("2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01",
                                                              "2026-04-01", "2026-07-01", "2026-10-01")]
    reb = days[(days >= blocks[0]) & ((days - blocks[0]) % H == 0)]
    Fx = F.fillna(0.0)
    picks = {m: pd.Series(index=reb, dtype=object) for m in ("RIDGE", "LGBM")}
    gate = pd.Series(index=reb, dtype=float)
    for b0, b1 in zip(blocks[:-1], blocks[1:]):
        tr = (days >= TRAIN0 // D) & (days + H - 1 < b0)
        te = reb[(reb >= b0) & (reb < b1)]
        if not len(te):
            continue
        Xtr, Xte = Fx.loc[days[tr]], Fx.loc[te]
        mu, sd = Xtr.mean(), Xtr.std().replace(0, 1)
        pr, pl = {}, {}
        for k in strat:
            y = Y[k].loc[days[tr]]; m = y.notna()
            rg = Ridge(alpha=10.0).fit(((Xtr - mu) / sd)[m], y[m]); pr[k] = rg.predict((Xte - mu) / sd)
            gb = lgb.LGBMRegressor(n_estimators=150, learning_rate=0.03, num_leaves=7, min_child_samples=20, subsample=0.8,
                                   subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=24).fit(Xtr[m], y[m])
            pl[k] = gb.predict(Xte)
        for m_, P in (("RIDGE", pr), ("LGBM", pl)):
            M = pd.DataFrame(P, index=te)
            best = M.idxmax(1); picks[m_].loc[te] = np.where(M.max(1) > 0, best, "CASH")
        yb = (EWy.loc[days[tr]] < 0); m = EWy.loc[days[tr]].notna()
        cl = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=7, min_child_samples=20, verbose=-1,
                                random_state=24).fit(Xtr[m], yb[m].astype(int))
        gate.loc[te] = cl.predict_proba(Xte)[:, 1]
    # realise daily returns: hold the pick for H days
    S2 = S.assign(CASH=0.0)
    out = {}

    def realise(sel):
        r = pd.Series(0.0, index=days[days >= blocks[0]])
        for d0, k in sel.items():
            span = r.index[(r.index >= d0) & (r.index < d0 + H)]
            r.loc[span] = S2.loc[span, k] if k in S2.columns else 0.0
        return r
    out["EW"] = S.loc[days >= blocks[0]].mean(1)
    win = pd.Series({d0: (F.loc[d0, [f"{k}_30d" for k in strat]].astype(float).idxmax().replace("_30d", "")
                          if F.loc[d0, [f"{k}_30d" for k in strat]].max() > 0 else "CASH") for d0 in reb})
    out["WINNER"] = realise(win)
    out["RIDGE"] = realise(picks["RIDGE"]); out["LGBM"] = realise(picks["LGBM"])
    ew = S.mean(1)
    gr = pd.Series(0.0, index=days[days >= blocks[0]])
    for d0, p in gate.items():
        span = gr.index[(gr.index >= d0) & (gr.index < d0 + H)]
        gr.loc[span] = ew.loc[span] if p < 0.5 else 0.0
    out["GATE"] = gr
    res = {k: {**stats(v), "y2026": stats(v[v.index >= blocks[4]])} for k, v in out.items()}
    res["single"] = {k: stats(S.loc[days >= blocks[0], k]) for k in strat}
    res["pick_counts"] = {m: picks[m].value_counts().to_dict() for m in picks}
    res["gate_cash_share"] = float((gate >= 0.5).mean())
    # how well were bad periods predicted? AUC of the gate
    from sklearn.metrics import roc_auc_score
    yy = (EWy.loc[gate.index] < 0).astype(int); mm = EWy.loc[gate.index].notna()
    res["gate_auc"] = float(roc_auc_score(yy[mm], gate[mm])) if yy[mm].nunique() == 2 else None
    for m_ in ("RIDGE", "LGBM", "GATE"):
        r = res[m_]
        res[m_]["pass"] = bool(r["ci_bp"][0] is not None and r["ci_bp"][0] > 0 and r["sharpe"] > res["EW"]["sharpe"] and r["sharpe"] > res["WINNER"]["sharpe"])
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    S, pumps = strategies(ts, codes, X)
    F = features(ts, codes, X, S, pumps)
    S.to_csv(OUT / "strategy_daily.csv")
    R = {f"H{H}": run(S, F, H) for H in (1, 7)}
    json.dump(R, open(OUT / "regime.json", "w"), indent=1, default=str)
    Lm = ["# B24_REGIME: predict bad days/weeks and pick the strategy (OOS 2025-01..2026-09)", ""]
    for h, r in R.items():
        Lm += [f"## {h} ({'daily' if h == 'H1' else 'weekly'} rebalance)", "", "| selector | bp/day [CI] | Sharpe | 2026 bp/day | 2026 Sharpe | pass |", "|---|---|---|---|---|---|"]
        for k in ("EW", "WINNER", "RIDGE", "LGBM", "GATE"):
            v = r[k]
            Lm.append(f"| {k} | {v['bp_day']:+.1f} [{v['ci_bp'][0]:+.1f}, {v['ci_bp'][1]:+.1f}] | {v['sharpe']:.2f} | {v['y2026']['bp_day']:+.1f} | {v['y2026']['sharpe']:.2f} | {v.get('pass', '')} |")
        Lm += ["", "Single strategies: " + ", ".join(f"{k} {v['bp_day']:+.1f}bp (S {v['sharpe']:.2f})" for k, v in r["single"].items()),
               f"Picks: {r['pick_counts']}", f"Gate: cash {r['gate_cash_share']:.0%} of periods, AUC for 'EW loses next period' {r['gate_auc']}", ""]
    (OUT / "regime.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
