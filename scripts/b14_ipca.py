"""B14_6 IPCA (Kelly-Pruitt-Su) with K=2,3,4 on 25 characteristics, weekly rebalance, yearly walk-forward re-estimation.
Benchmark: observable 4-factor model (market, size, momentum, reversal) with rolling betas.
Metrics: OOS total R2, predictive R2, tangency-portfolio net Sharpe. Out: data/reports/b14/b14_6.json"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import b14_lib as B  # noqa: E402
from b14_lib import boot_ci  # noqa: E402

CHARS = ["ret_24h", "ret_7d", "ret_28d", "rv_24h", "rv_7d", "range_6h", "vsurge_6h", "taker_24h", "ldv", "dlo30", "dhi30", "corr_btc_24h",
         "oi_chg_24h", "oi_to_vol", "ls_top", "ls_global", "taker_ratio_6h", "funding", "funding_chg", "korea_surge_6h", "krw_premium",
         "spot_share_6h", "basis", "log_mcap", "turnover"]


def ipca_fit(Z, r, K, iters=50):
    """Z: list of (n_t x L) char matrices, r: list of (n_t,) next-period returns. Returns Gamma (L x K), factors (T x K)."""
    L_ = Z[0].shape[1]
    # init Gamma from PCA of managed portfolios x_t = Z_t' r_t / n_t
    Xm = np.stack([z.T @ y / len(y) for z, y in zip(Z, r)])
    _, _, vt = np.linalg.svd(Xm - Xm.mean(0), full_matrices=False)
    G = vt[:K].T
    F = np.zeros((len(Z), K))
    for _ in range(iters):
        for t, (z, y) in enumerate(zip(Z, r)):
            b = z @ G
            F[t] = np.linalg.lstsq(b, y, rcond=None)[0]
        num = sum(np.kron(np.outer(f, f), z.T @ z) for z, f in zip(Z, F))
        den = sum(np.kron(f, z.T @ y) for z, y, f in zip(Z, r, F))
        G = np.linalg.solve(num + 1e-6 * np.eye(num.shape[0]), den).reshape(K, L_).T
        q, _ = np.linalg.qr(G)
        G = q
    return G, F


def main():
    T, _ = B.load_tab()
    T = T[T["ts"] % (7 * B.D_) == 4 * B.D_]                          # weekly rows (Thursdays 00:00)
    T["y"] = T.groupby("j")["y_raw24"].shift(0)                      # use 24h fwd as the weekly proxy return? No: build 7d forward from lc
    # 7-day forward raw return from the hourly panel
    import b7_lib as L
    ts, codes, X = L.data()
    lc = X["lc"]
    fwd7 = L.lag(lc, -168) - lc
    T["y7"] = fwd7[T["i"].to_numpy(), T["j"].to_numpy()]
    T = T.dropna(subset=["y7"])
    T = T[np.isfinite(T["y7"])]
    Zc = T[CHARS].copy()
    Zc = Zc.groupby(T["ts"]).rank(pct=True) - 0.5                    # cross-sectional rank transform, NaN -> 0 (median)
    Zc = Zc.fillna(0.0)
    Zc["const"] = 1.0
    T[list(Zc.columns)] = Zc.to_numpy()
    L_cols = list(Zc.columns)
    weeks = sorted(T["ts"].unique())
    res = {}
    for K in (2, 3, 4):
        tot_num = tot_den = pred_num = pred_den = 0.0
        port = []
        for yr0 in (B.DISC1, B.DISC1 + 182 * B.D_):                  # two half-year OOS blocks in the holdout, refit before each
            tr_w = [w for w in weeks if w < yr0 - 8 * B.D_]
            te_w = [w for w in weeks if yr0 <= w < min(yr0 + 182 * B.D_, B.HOLD1)]
            Z = [T[T["ts"] == w][L_cols].to_numpy() for w in tr_w]
            r = [T[T["ts"] == w]["y7"].to_numpy() for w in tr_w]
            G, F = ipca_fit(Z, r, K)
            lam = F.mean(0)                                          # factor risk premia
            Sig = np.cov(F.T) + 1e-6 * np.eye(K)
            w_tan = np.linalg.solve(Sig, lam)
            for w in te_w:
                g = T[T["ts"] == w]
                z, y = g[L_cols].to_numpy(), g["y7"].to_numpy()
                beta = z @ G
                f_hat = np.linalg.lstsq(beta, y, rcond=None)[0]     # realised factor (contemporaneous fit)
                tot_num += ((y - beta @ f_hat) ** 2).sum()
                tot_den += (y ** 2).sum()
                pred_num += ((y - beta @ lam) ** 2).sum()
                pred_den += (y ** 2).sum()
                # tangency portfolio of managed portfolios: weights on coins = z @ G @ w_tan, dollar-neutral, gross 1
                wc = beta @ w_tan
                wc = wc - wc.mean()
                wc = wc / (np.abs(wc).sum() + 1e-12)
                cost = (np.abs(wc) * g["cost"].to_numpy()).sum()      # full turnover assumed weekly
                port.append((w, float((wc * np.expm1(y)).sum() - cost - (wc * g["fund24"].to_numpy() * 7).sum())))
        P = pd.Series(dict(port))
        res[f"K{K}"] = dict(total_R2=1 - tot_num / tot_den, pred_R2=1 - pred_num / pred_den, weeks=int(len(P)),
                            tangency_weekly_net=float(P.mean()), ci=boot_ci(P.to_numpy()),
                            sharpe=float(P.mean() / (P.std() + 1e-12) * np.sqrt(52)))
        print(K, res[f"K{K}"], flush=True)
    # observable 4-factor benchmark: market (ew alt), size (ldv), momentum (ret_28d), reversal (ret_24h) managed portfolios
    port, pn, pd_ = [], 0.0, 0.0
    tr_w = [w for w in weeks if w < B.DISC1 - 8 * B.D_]
    fac_tr = []
    for w in tr_w:
        g = T[T["ts"] == w]
        y = g["y7"].to_numpy()
        fac_tr.append([y.mean()] + [float((g[c].to_numpy() * y).mean()) for c in ("ldv", "ret_28d", "ret_24h")])
    Fb = np.array(fac_tr)
    lam = Fb.mean(0)
    w_tan = np.linalg.solve(np.cov(Fb.T) + 1e-6 * np.eye(4), lam)
    for w in [w for w in weeks if B.DISC1 <= w < B.HOLD1]:
        g = T[T["ts"] == w]
        y = g["y7"].to_numpy()
        beta = np.column_stack([np.ones(len(g))] + [g[c].to_numpy() for c in ("ldv", "ret_28d", "ret_24h")])
        pn += ((y - beta @ lam) ** 2).sum()
        pd_ += (y ** 2).sum()
        wc = beta @ w_tan
        wc = wc - wc.mean()
        wc = wc / (np.abs(wc).sum() + 1e-12)
        port.append((w, float((wc * np.expm1(y)).sum() - (np.abs(wc) * g["cost"].to_numpy()).sum())))
    P = pd.Series(dict(port))
    res["benchmark_obs4"] = dict(pred_R2=1 - pn / pd_, tangency_weekly_net=float(P.mean()), ci=boot_ci(P.to_numpy()),
                                 sharpe=float(P.mean() / (P.std() + 1e-12) * np.sqrt(52)))
    best = max((res[f"K{K}"] for K in (2, 3, 4)), key=lambda r: r["sharpe"])
    res["pass"] = bool(best["pred_R2"] > res["benchmark_obs4"]["pred_R2"] and best["sharpe"] > 1.0 and best["ci"][0] > 0)
    json.dump(res, open(B.OUT / "b14_6.json", "w"), indent=1, default=float)
    print("B14_6", json.dumps(res, default=float))


if __name__ == "__main__":
    main()
