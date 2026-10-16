"""B57 development evaluation + committed selection (prereg v8). Reads ONLY data/upbit_db/b57/preds (dev, < 2025-07-01).

Usages (weights decided at close of decision day t, engine holds open t+1 -> open t+2, costs as v2):
  U1 (sel preds): F17 total exposure (BTC+ETH) spread equally over top-3 predicted among BTC, ETH and the 8 most liquid
     alts (30d median value, point in time) that have a prediction that day.
  U2 (sel preds): equal-weight top-5 predicted coins, scaled by BTC F15 trend weight (daily).
  U3 (tim preds): F17 BTC/ETH weights, halved while the latest raw prediction for that coin is < 0.
  Selections / signs are refreshed on decision days only (h1 daily, h7 weekly, h28 every 4th week) and held between.
Ensembles per (h, target): ens_tab = tabular models (ridge enet lgbm xgb cat mlp), ens_all = all available models.
  sel: mean of per-day cross-sectional percentile ranks; tim: median of raw predictions (sign used).
Overfitting control: CSCV over ALL configs (S=16 blocks, all C(16,8) splits). PBO = share of splits where the IS-best
  config ranks at or below the OOS median. "PBO-adjusted rank" of a config = its mean OOS percentile rank over all splits.
Selection (fixed here, before any dev result was looked at):
  A = best dev Sharpe among configs with mean OOS percentile rank >= 0.90 (top decile) AND dev maxDD >= F17 maxDD - 0.05.
      If none qualifies there is no candidate A.
  B = ens_all of A's usage and horizon; if no A, the ens_all config with the best dev Sharpe that meets the maxDD rule.
Writes research/b57_dev.md, data/upbit_db/b57/dev_returns.parquet, research/b57_selection.json.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b51_prereg_v2 as B  # noqa: E402
import b54_sweep100 as S  # noqa: E402
import b55_combos as K  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data/upbit_db/b57"
DEV = ("2023-01-01", "2025-07-01")
TAB = ["ridge", "enet", "lgbm", "xgb", "cat", "mlp"]
ALL = TAB + ["lstm", "gru", "transformer"]
MAJ = S.MAJ
F17W = S.W_f17()
F17EXP = F17W[MAJ].sum(axis=1)
BTCTREND = S.BTCW.fillna(0.0)
_age = S.C.notna().cumsum()
LIQRANK = S._medv[S._alts].where(_age[S._alts] >= 180).rank(axis=1, ascending=False, method="first")


def load_preds(split="preds"):
    out = {}
    for f in sorted((DIR / split).glob("*.parquet")):
        m, h, t = f.stem.split("_")
        d = pd.read_parquet(f).dropna(subset=["pred"])
        out[(m, int(h[1:]), t)] = d.pivot(index="day", columns="coin", values="pred")
    for (h, t) in {(h, t) for (_, h, t) in out}:
        for ename, members in (("ens_tab", TAB), ("ens_all", ALL)):
            mats = [out[(m, h, t)] for m in members if (m, h, t) in out]
            if len(mats) < 2:
                continue
            if t == "sel":
                ranks = [x.rank(axis=1, pct=True) for x in mats]
                e = sum(r.fillna(0) for r in ranks) / sum(r.notna().astype(float) for r in ranks).replace(0, np.nan)
            else:
                e = pd.concat(mats, keys=range(len(mats))).groupby(level=1).median()
            out[(ename, h, t)] = e
    return out


def hold(mat):
    """decision-day matrix -> daily, held until next decision (index = engine days)."""
    return mat.reindex(S.IDX).ffill().reindex(columns=S.C.columns)


def W_U1(p):
    W = pd.DataFrame(0.0, index=p.index, columns=S.C.columns)
    for d, row in p.iterrows():
        row = row.dropna()
        if d not in LIQRANK.index:
            continue
        lr = LIQRANK.loc[d]
        liq = lr[lr <= 8].index
        cand = row[[c for c in row.index if c in MAJ or c in liq]]
        top = cand.nlargest(3).index
        W.loc[d, top] = 1.0 / max(len(top), 1)
    W = hold(W).fillna(0.0)
    return W.mul(F17EXP, axis=0)


def W_U2(p):
    W = pd.DataFrame(0.0, index=p.index, columns=S.C.columns)
    for d, row in p.iterrows():
        top = row.dropna().nlargest(5).index
        W.loc[d, top] = 1.0 / max(len(top), 1)
    return hold(W).fillna(0.0).mul(BTCTREND, axis=0)


def W_U3(p):
    W = F17W.copy()
    sig = hold(p)
    for m in MAJ:
        if m in sig:
            W[m] = W[m] * np.where(sig[m].fillna(0.0) < 0, 0.5, 1.0)
    return W


def win(r, a=DEV[0], z=DEV[1]):
    return r[(r.index >= a) & (r.index < z)]


def turnover(W):
    W = W.reindex(index=S.IDX).fillna(0.0)
    return float(win((W - W.shift(1)).abs().sum(axis=1)).mean() * 365)


def ic(key, p):
    f = DIR / "preds" / f"{key[0]}_h{key[1]}_{key[2]}.parquet"
    if not f.exists():
        return np.nan
    d = pd.read_parquet(f).dropna()
    if key[2] == "tim":
        return float(d.pred.rank().corr(d.y.rank()))
    return float(d.groupby("day").apply(lambda g: g.pred.rank().corr(g.y.rank()) if len(g) > 5 else np.nan).mean())


def cscv(M, S_=16):
    """M: T x N returns. Returns PBO and each config's mean OOS percentile rank."""
    T, N = M.shape
    blocks = np.array_split(np.arange(T), S_)
    sr = lambda x: x.mean(0) / (x.std(0, ddof=1) + 1e-12)  # noqa: E731
    lam, oos_rank = [], np.zeros(N); n = 0
    for comb in itertools.combinations(range(S_), S_ // 2):
        isx = np.concatenate([blocks[i] for i in comb])
        osx = np.concatenate([blocks[i] for i in range(S_) if i not in comb])
        s_is, s_os = sr(M[isx]), sr(M[osx])
        pr = (pd.Series(s_os).rank().to_numpy() - 0.5) / N
        oos_rank += pr; n += 1
        w = pr[int(np.argmax(s_is))]
        lam.append(np.log(w / (1 - w)))
    return float(np.mean(np.array(lam) <= 0)), oos_rank / n


def main():
    preds = load_preds("preds")
    f17 = win(S.run(F17W)); f19 = win(S.run(K.book(K.sat_upvol(), 0.2, K.down_n1(), 1.0, False)))
    rets, rows = {}, []
    for key, p in sorted(preds.items(), key=lambda kv: str(kv[0])):
        m, h, t = key
        for u, fn in ((("U1", W_U1), ("U2", W_U2)) if t == "sel" else (("U3", W_U3),)):
            W = fn(p); r = win(S.run(W)); name = f"{u}_{m}_h{h}"
            rets[name] = r
            rows.append(dict(cfg=name, usage=u, model=m, h=h, sharpe=B.sharpe(r), maxdd=B.maxdd(r), cagr=B.cagr(r),
                             turnover=turnover(W), ic=ic(key, p)))
    R = pd.DataFrame(rets).fillna(0.0)
    R.to_parquet(DIR / "dev_returns.parquet")
    pbo, oosr = cscv(R.to_numpy())
    T = pd.DataFrame(rows).set_index("cfg").loc[R.columns]
    T["oos_rank"] = oosr
    m_trials = sum(1 for _ in open(ROOT / "research/trial_ledger.csv")) - 1 + len(T)
    T["dsr"] = [B.dsr(R[c].to_numpy(), m=m_trials) for c in T.index]
    T["p_vs_f17"] = [B.boot_p(R[c].to_numpy(), f17.reindex(R.index).fillna(0).to_numpy(), B.sharpe, 20, draws=2000)
                     for c in T.index]
    f17dd = B.maxdd(f17)
    ok_dd = T.maxdd >= f17dd - 0.05
    elig = T[(T.oos_rank >= 0.90) & ok_dd]
    A = elig.sharpe.idxmax() if len(elig) else None
    if A is not None:
        b = f"{T.loc[A, 'usage']}_ens_all_h{T.loc[A, 'h']}"
        Bc = b if b in T.index else None
    else:
        e = T[(T.model == "ens_all") & ok_dd]
        Bc = e.sharpe.idxmax() if len(e) else None
    sel = dict(candidate_A=A, candidate_B=Bc, pbo=pbo, n_configs=len(T), m_trials=m_trials,
               f17_dev=dict(sharpe=B.sharpe(f17), maxdd=f17dd, cagr=B.cagr(f17)),
               f19_dev=dict(sharpe=B.sharpe(f19), maxdd=B.maxdd(f19), cagr=B.cagr(f19)))
    json.dump(sel, open(ROOT / "research/b57_selection.json", "w"), indent=1, default=float)
    T = T.sort_values("sharpe", ascending=False)
    with open(ROOT / "research/b57_dev.md", "w") as fh:
        fh.write("# B57 development results (2023-01..2025-06, sealed data untouched)\n\n")
        fh.write(f"F17 dev: Sharpe {B.sharpe(f17):.2f}, maxDD {f17dd:.1%}, CAGR {B.cagr(f17):.1%}. "
                 f"F19 dev: Sharpe {B.sharpe(f19):.2f}, maxDD {B.maxdd(f19):.1%}, CAGR {B.cagr(f19):.1%}.\n\n")
        fh.write(f"Configs {len(T)}, CSCV PBO {pbo:.2f}, DSR trials M={m_trials}. "
                 f"Selected A = {A}, B = {Bc}.\n\n")
        fh.write(T.round(3).to_markdown() + "\n")
    print(json.dumps(sel, default=float, indent=1))
    print(T.head(15).round(3).to_string())


if __name__ == "__main__":
    main()
