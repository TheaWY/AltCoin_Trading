"""B14_5 regime HMM: 3-state Gaussian HMM on daily market-state features, fitted walk-forward (refit monthly on past data
only, first fit after 120 days), state for day d assigned by forward filtering on data up to d (no smoothing = no look-ahead).
Out: data/cache/b14/regime.parquet (day, state, p0..p2), data/reports/b14/b14_5.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b14"
D_ = 86400
COLS = ["btc_ret_1h", "btc_rv_7d", "breadth", "mean_funding", "agg_oi_chg_24h", "mean_krw_premium", "upbit_vol_share", "alt_resid_mean"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    M = pd.read_parquet(ROOT / "data/cache/b14/market_daily.parquet")[COLS].replace([np.inf, -np.inf], np.nan)
    M = M.ffill().dropna()
    days = M.index.to_numpy()
    Xall = M.to_numpy(np.float64)
    states = np.full(len(M), -1)
    probs = np.full((len(M), 3), np.nan)
    model, ref_means = None, None
    for d in range(120, len(M)):
        if model is None or (d - 120) % 30 == 0:
            tr = Xall[:d]
            mu, sd = tr.mean(0), tr.std(0) + 1e-9
            Z = (tr - mu) / sd
            best = None
            for seed in range(3):
                m = GaussianHMM(3, covariance_type="diag", n_iter=200, random_state=seed)
                m.fit(Z)
                s = m.score(Z)
                if best is None or s > best[0]:
                    best = (s, m)
            model = best[1]
            # canonical state order: sort by mean alt residual return (state 0 = worst alts, 2 = best alts)
            order = np.argsort(model.means_[:, COLS.index("alt_resid_mean")])
            ref_means = (mu, sd, order)
        mu, sd, order = ref_means
        Z = (Xall[:d + 1] - mu) / sd
        post = model.predict_proba(Z)[-1]                      # filtered posterior for the last day (forward algorithm)
        p = post[order]
        probs[d] = p
        states[d] = int(np.argmax(p))
    R = pd.DataFrame({"day": days, "state": states, "p0": probs[:, 0], "p1": probs[:, 1], "p2": probs[:, 2]})
    R = R[R["state"] >= 0]
    R.to_parquet(ROOT / "data/cache/b14/regime.parquet", index=False)
    # diagnostics: per-state stats over discovery and holdout
    M2 = M.copy()
    M2["state"] = pd.Series(states, index=M.index)
    M2 = M2[M2["state"] >= 0]
    per = np.where(M2.index * D_ >= L.HOLD[0], "hold", np.where(M2.index * D_ >= L.DISC[0], "disc", "pre"))
    res = {"n_days": int(len(M2)), "state_share": M2["state"].value_counts(normalize=True).round(3).to_dict()}
    for pp in ("disc", "hold"):
        sub = M2[per == pp]
        res[pp] = {int(s): dict(days=int((sub["state"] == s).sum()),
                                alt_resid_mean_daily=float(sub.loc[sub["state"] == s, "alt_resid_mean"].mean()),
                                btc_ret_daily=float(sub.loc[sub["state"] == s, "btc_ret_1h"].mean()),
                                mean_funding=float(sub.loc[sub["state"] == s, "mean_funding"].mean()),
                                breadth=float(sub.loc[sub["state"] == s, "breadth"].mean())) for s in range(3)}
    # persistence
    res["mean_run_length_days"] = float(np.mean([len(list(g)) for _, g in __import__("itertools").groupby(M2["state"])]))
    json.dump(res, open(OUT / "b14_5.json", "w"), indent=1, default=float)
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
