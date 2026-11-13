"""B7_A pre-registered mechanism factors (research/batch_B7.yaml). Sign fixed on discovery, then one holdout pass.
Out: data/reports/b7/b7_A.json and .csv"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
from b7_lib import MN, MX, M, S, SD, corr, cs_rank, lag, safe_div  # noqa: E402

OUT = ROOT / "data/reports/b7"


def factors():
    ts, codes, X = L.data()
    qv, tbq, n = np.nan_to_num(X["qv"]), np.nan_to_num(X["tbq"]), np.nan_to_num(X["n"])
    qvq, tbqq = np.nan_to_num(X["qv_q"]), np.nan_to_num(X["tbq_q"])
    lc, r1, h, l, o, c = X["lc"], X["r1"], X["h"], X["l"], X["o"], X["c"]
    flow = 2 * tbq - qv
    ofi24 = safe_div(S(flow, 24), S(qv, 24))
    ret24, ret7 = lc - lag(lc, 24), lc - lag(lc, 168)
    beta = np.nan_to_num(X["beta"])
    res1 = r1 - beta * X["btc_r1"]
    F = {}
    F["A01_ofi_q24"] = safe_div(S(2 * tbqq - qvq, 24), S(qvq, 24))
    F["A02_ofi_q_excess"] = F["A01_ofi_q24"] - safe_div(S(flow - (2 * tbqq - qvq), 24), S(qv - qvq, 24))
    F["A03_ofi24_rel"] = ofi24 - np.nanmedian(np.where(X["U"], ofi24, np.nan), 1, keepdims=True)
    F["A04_flow_price_gap"] = cs_rank(ofi24) - cs_rank(ret24)
    F["A05_ofi_persist"] = safe_div(S(flow, 168) - S(flow, 24), S(qv, 168) - S(qv, 24))
    m = X["mkt_r1"]
    mlag = lag(m, 1)
    mm = M(mlag, 720)
    lagb = safe_div(M(r1 * mlag, 720) - M(r1, 720) * mm, M(mlag * mlag, 720) - mm * mm)
    F["A06_lag_beta_mkt"] = lagb * m
    btc_lc = lc[:, [codes.index("BTCUSDT")]]
    F["A07_levy_lead"] = M(X["levy"], 168) * (btc_lc - lag(btc_lc, 4))
    F["A08_corr_break"] = M(X["corr_btc"], 24) - M(X["corr_btc"], 720)
    F["A09_trade_size"] = np.log(safe_div(S(qv, 24), S(n, 24))) - np.log(safe_div(S(qv, 720), S(n, 720)))
    F["A10_trade_count_surge"] = np.log(safe_div(S(n, 24), S(n, 720) / 30))
    mu, s2, s3 = M(r1, 168), M(r1 ** 2, 168), M(r1 ** 3, 168)
    var = s2 - mu ** 2
    F["A11_rskew_7d"] = safe_div(s3 - 3 * mu * s2 + 2 * mu ** 3, np.maximum(var, 1e-12) ** 1.5)
    rv = np.nan_to_num(X["rv"])
    F["A12_squeeze"] = np.sqrt(safe_div(S(rv, 24), S(rv, 168) / 7))
    F["A13_funding_change"] = X["f8"] - lag(X["f8"], 24)
    F["A14_funding_price_gap"] = cs_rank(X["f8"]) - cs_rank(ret7)
    F["A15_close_loc_24h"] = safe_div(c - MN(l, 24), MX(h, 24) - MN(l, 24))
    F["A16_close_loc_7d"] = safe_div(c - MN(l, 168), MX(h, 168) - MN(l, 168))
    lq = np.log(qv + 1)
    F["A17_vol_price_corr"] = corr(r1, lq - lag(lq, 1), 168)
    am = safe_div(np.abs(r1), qv)
    F["A18_amihud_change"] = np.log(safe_div(S(am, 24), S(am, 720) / 30))
    F["A19_upwick_24h"] = safe_div(S(h - np.fmax(o, c), 24), S(h - l, 24))
    F["A20_resid_mom"] = S(res1, 168) - S(res1, 24)
    F["A21_idio_vol"] = SD(res1, 168)
    hod = ((ts // 3600) % 24)[:, None]
    sett = np.isin(hod, (22, 23, 0, 6, 7, 8, 14, 15, 16))
    F["A22_settle_flow"] = safe_div(S(flow * sett, 24), S(qv * sett, 24))
    F["A23_q_share"] = safe_div(S(qvq, 24), S(qv, 24)) - safe_div(S(qvq, 720), S(qv, 720))
    F["A24_intraday_noise"] = safe_div(S(rv, 24), S(np.nan_to_num(r1) ** 2, 24))
    return {k: v.astype(np.float32) for k, v in F.items()}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    K = L.known_factors()
    rows = []
    for name, F in factors().items():
        d = L.evaluate(F, L.DISC, K=K)
        sign = 1.0 if d["ic"] >= 0 else -1.0
        hres = L.evaluate(F, L.HOLD, sign=sign, K=K)
        z = hres["ic"] / ((hres["ic_ci"][1] - hres["ic_ci"][0]) / 3.92)
        rows.append(dict(factor=name, sign=sign, disc_ic=d["ic"], disc_novel=d["novel_ic"], disc_ls=d["ls_net_day"],
                         hold_ic=hres["ic"], hold_ic_ci=hres["ic_ci"], hold_novel=hres["novel_ic"], hold_novel_ci=hres["novel_ci"],
                         hold_ls_net=hres["ls_net_day"], hold_ls_ci=hres["ls_net_ci"], hold_ls_hedged=hres["ls_hedged_day"],
                         hold_ls_hedged_ci=hres["ls_hedged_ci"], hold_gross=hres["ls_gross_day"], turnover=hres["turnover_day"],
                         hold_z=z))
        r = rows[-1]
        print(f"{name:24s} s={sign:+.0f} disc ic={d['ic']:+.4f} nov={d['novel_ic']:+.4f} | hold ic={r['hold_ic']:+.4f} "
              f"ci=[{r['hold_ic_ci'][0]:+.4f},{r['hold_ic_ci'][1]:+.4f}] nov={r['hold_novel']:+.4f} "
              f"ls={r['hold_ls_net']:+.5f}/d ci=[{r['hold_ls_ci'][0]:+.5f},{r['hold_ls_ci'][1]:+.5f}] "
              f"hedged={r['hold_ls_hedged']:+.5f} gross={r['hold_gross']:+.5f} to={r['turnover']:.2f}", flush=True)
    D = pd.DataFrame(rows)
    D.to_csv(OUT / "b7_A.csv", index=False)
    json.dump(rows, open(OUT / "b7_A.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
