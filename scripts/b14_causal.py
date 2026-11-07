"""B14_7 causal lead-lag: PCMCI+ (ParCorr) on hourly aggregate series, max lag 24h, fitted on discovery. Every discovered lagged
link INTO alt residual return becomes a registered signal (sign from discovery); holdout: long/short the liquid alt basket
when the source series exceeds +-1 sd (rolling, past-only); BH across links. Out: data/reports/b14/b14_7.json"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from tigramite import data_processing as pp
from tigramite.independence_tests.parcorr import ParCorr
from tigramite.pcmci import PCMCI

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import b14_lib as B  # noqa: E402
from b14_lib import boot_ci  # noqa: E402

COLS = ["mean_krw_premium", "upbit_vol_share", "agg_oi_chg_24h", "mean_funding", "breadth", "btc_ret_1h", "alt_resid_mean"]


def main():
    M = pd.read_parquet(B.B14 / "market_hourly.parquet")[COLS].replace([np.inf, -np.inf], np.nan).ffill().dropna()
    # make series stationary-ish: differences of slow levels
    D = M.copy()
    for c in ("mean_krw_premium", "upbit_vol_share", "agg_oi_chg_24h", "mean_funding", "breadth"):
        D[c] = M[c].diff()
    D = D.dropna()
    disc = D[(D.index >= B.DISC0) & (D.index < B.DISC1)]
    Z = (disc - disc.mean()) / disc.std()
    df = pp.DataFrame(Z.to_numpy(), var_names=COLS)
    pc = PCMCI(dataframe=df, cond_ind_test=ParCorr(significance="analytic"), verbosity=0)
    out = pc.run_pcmciplus(tau_min=1, tau_max=24, pc_alpha=0.01)
    tgt = COLS.index("alt_resid_mean")
    links = []
    for src in range(len(COLS)):
        for lag in range(1, 25):
            p = out["p_matrix"][src, tgt, lag]
            v = out["val_matrix"][src, tgt, lag]
            if p < 0.01 and out["graph"][src, tgt, lag] != "":
                links.append(dict(source=COLS[src], lag=int(lag), val=float(v), p=float(p), sign=int(np.sign(v))))
    print("links into alt_resid_mean:", links, flush=True)
    # trade test on holdout: liquid alt basket (equal weight, universe dv24 >= $20M) 1h return after the signal
    import b7_lib as L
    ts, codes, X = L.data()
    liq = X["U"] & (X["dv24"] >= 2e7)
    bi = codes.index("BTCUSDT")
    basket = np.nanmean(np.where(liq, X["r1"] - np.nan_to_num(X["beta"]) * X["r1"][:, [bi]], np.nan), 1)
    bk = pd.Series(basket, index=ts)
    res = {"links": links, "tests": {}}
    ps = {}
    for lk in links:
        s = D[lk["source"]]
        z = (s - s.rolling(720, min_periods=240).mean()) / s.rolling(720, min_periods=240).std()
        sig = np.sign(z.where(z.abs() >= 1.0, 0.0)) * lk["sign"]
        hold = sig[(sig.index >= B.DISC1) & (sig.index < B.HOLD1)]
        # position taken at hour t affects the basket return at t+lag; cost 0.05%+0.02% per flip, funding ignored (1h holds)
        r = (hold.shift(lk["lag"] - 1) * bk.reindex(hold.index).shift(-1)).dropna()
        flips = hold.diff().abs().fillna(0) / 2
        net = r - flips.reindex(r.index) * 0.0007
        d = net.groupby(net.index // B.D_).sum()
        ci = boot_ci(d.to_numpy())
        p_boot = float(np.mean(np.array([d.to_numpy()[B.RNG.integers(0, len(d), len(d))].mean() for _ in range(2000)]) <= 0))
        key = f"{lk['source']}@{lk['lag']}"
        res["tests"][key] = dict(days=int(len(d)), net_day=float(d.mean()), ci=ci, p=p_boot, active_share=float((hold != 0).mean()))
        ps[key] = p_boot
        print(key, res["tests"][key], flush=True)
    items = sorted(ps.items(), key=lambda x: x[1])
    keep = set()
    for r_, (k, p) in enumerate(items, 1):
        if p <= 0.10 * r_ / len(items):
            keep = {kk for kk, _ in items[:r_]}
    res["passed"] = [k for k in keep if res["tests"][k]["ci"][0] > 0]
    res["pass"] = bool(res["passed"])
    json.dump(res, open(B.OUT / "b14_7.json", "w"), indent=1, default=float)
    print("B14_7", res["passed"])


if __name__ == "__main__":
    main()
