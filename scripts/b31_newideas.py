"""B31_NEWIDEAS (registered 2026-10-01, BEFORE running). New, literature-motivated hypotheses we have NOT tested yet,
with the B30 protocol: a-priori signs, in-sample 2024-05..2025-06 shown, HOLDOUT 2025-07-01..2026-09-23 decides,
BHY across the family (M=9), aphx library.

A  FUNDING SETTLEMENT (perp-specific microstructure). Binance settles funding at 00/08/16 UTC. Longs facing high funding
   have an incentive to close just before settlement and re-open after (and vice versa).
   A1  hour BEFORE settlement: return negatively related to the funding rate (rank of the last settled rate, known)
   A2  2 hours AFTER settlement: positively related (rebound)
   Test: Fama-MacBeth per settlement (T ~ 2,600), NW t; quintile HML gross (1-hour holds are cost-bound: break-even shown)
B  INTRADAY PERIODICITY (Heston, Korajczyk & Sadka 2010): a coin's return in the same hour of day over the last 5 days
   predicts its return this hour, cross-sectionally. FM per hour (T ~ 20,000), NW t; HML gross and break-even.
C  CALENDAR / TIME-OF-DAY (Aharon & Qadan 2019): hour-of-day and weekday means of the alt index differ from each other.
   Test: OLS with hour and weekday dummies, HAC Wald test; stability = correlation of the 24 hourly means in-sample vs holdout.
D  EVENTS, calendar-time portfolios (Mitchell & Stafford 2000) with NW t, market-adjusted (minus alt index):
   D1  new Binance perp listings: days 1..30 after listing underperform (supply overhang, lottery demand fades) -> a-priori negative
   D2  Upbit KRW listing of a coin already on Binance >= 30 days: days 1..10 after the Upbit listing day (day 0 = the pump,
       not tradable) -> a-priori negative (post-listing fade)
E  KOREA SHARE, three pre-specified refinements of the only near-survivor (B29/B30 H2), battery from b30:
   E1  weekly rebalance (B30 holdout weekly t 3.72 suggested it)        E2  largest-tercile coins only (capacity)
   E3  coin-level kimchi premium (Upbit price / Binance price - 1): high premium -> lower future perp return
Verdict per hypothesis: holdout NW/FM t >= 2 in the a-priori direction AND BHY over the 9 holdout p-values; tradability
reported separately (net of 12bp round trip, break-even).
Output data/reports/b31/newideas.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import aphx as A  # noqa: E402
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b31"
D = 86400
START, END = int(pd.Timestamp("2024-05-01").timestamp()), int(pd.Timestamp("2026-09-23").timestamp())
HOLD = int(pd.Timestamp("2025-07-01").timestamp())


def xs_slope(Y, Xr, M):
    """per-row cross-sectional OLS slope of Y on Xr (both T x N) using rows' masks; vectorised."""
    Y = np.where(M, Y, np.nan); Xr = np.where(M & np.isfinite(Y), Xr, np.nan); Y = np.where(np.isfinite(Xr), Y, np.nan)
    n = np.isfinite(Y).sum(1)
    xm = np.nanmean(Xr, 1, keepdims=True); ym = np.nanmean(Y, 1, keepdims=True)
    cov = np.nansum((Xr - xm) * (Y - ym), 1); var = np.nansum((Xr - xm) ** 2, 1)
    b = np.where((n >= 30) & (var > 0), cov / np.where(var > 0, var, 1), np.nan)
    return b


def rank_rows(A_):
    return pd.DataFrame(A_).rank(axis=1, pct=True).to_numpy() - 0.5


def hml_rows(S, Y, M, q=0.2):
    S = np.where(M & np.isfinite(Y), S, np.nan)
    lo = np.nanquantile(S, q, axis=1, keepdims=True); hi = np.nanquantile(S, 1 - q, axis=1, keepdims=True)
    top = np.where(S >= hi, Y, np.nan); bot = np.where(S <= lo, Y, np.nan)
    return np.nanmean(top, 1) - np.nanmean(bot, 1)


def summarize(name, series_t, ts_rows, sign, extra=None):
    s = pd.Series(series_t, index=ts_rows).dropna()
    out = {"hypothesis": name, "sign": sign}
    for tag, sel in (("insample", s.index < HOLD), ("holdout", s.index >= HOLD)):
        m, t = A.nw_t(s[sel]); out[f"{tag}_mean"] = m; out[f"{tag}_t"] = t; out[f"{tag}_n"] = int(sel.sum())
    out["holdout_t_signed"] = sign * out["holdout_t"]
    out["p_one"] = float(1 - stats.norm.cdf(out["holdout_t_signed"]))
    if extra:
        out.update(extra)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    from b29_battery import crypto_mask
    cm = crypto_mask(codes)
    bi = codes.index("BTCUSDT")
    lc = X["lc"].astype(np.float64); dv = np.nan_to_num(X["dv24"])
    U = (dv >= 5e6) & (X["age"] >= 720) & cm[None, :] & np.isfinite(lc)
    f8 = X["f8"]
    rows_out = []
    # ---------------- A: funding settlement
    hrs = np.flatnonzero((ts % (8 * 3600) == 0) & (ts >= START + 3 * D) & (ts < END))
    s = hrs[(hrs >= 30) & (hrs + 3 < len(ts))]
    fr = rank_rows(f8[s - 2])                                     # last settled rate, known 2 bars before settlement
    rev = rank_rows(lc[s - 2] - lc[s - 26])
    Mset = U[s - 2] & np.isfinite(f8[s - 2])
    r_pre = lc[s - 1] - lc[s - 2]                                 # hour ending at settlement (bar s-2 closes at s-1h.. see b7 bar convention)
    r_post = lc[s + 1] - lc[s - 1]                                # two hours after settlement
    for nm, Y, sg in (("A1_funding_pre_settlement", r_pre, -1), ("A2_funding_post_settlement", r_post, +1)):
        b = xs_slope(Y, fr, Mset)
        h = hml_rows(f8[s - 2], Y, Mset)
        rows_out.append(summarize(nm, b, ts[s], sg, {"hml_holdout_bp": float(np.nanmean(h[ts[s] >= HOLD]) * 1e4 * sg),
                                                     "hml_holdout_t": A.nw_t(sg * h[ts[s] >= HOLD])[1],
                                                     "note": "gross per 1-2h hold; round trip ~12bp"}))
        print(nm, rows_out[-1]["holdout_t_signed"], flush=True)
    # ---------------- B: intraday periodicity (same hour, last 5 days)
    r1 = np.vstack([np.full((1, lc.shape[1]), np.nan), np.diff(lc, axis=0)])
    per = np.nanmean(np.stack([L.lag(r1, 24 * k) for k in range(1, 6)]), 0)
    hh = np.flatnonzero((ts >= START) & (ts < END))
    Mh = U[hh - 1] & np.isfinite(per[hh])
    b = xs_slope(r1[hh], rank_rows(per[hh]), Mh)
    h = hml_rows(per[hh], r1[hh], Mh)
    rows_out.append(summarize("B_intraday_periodicity", b, ts[hh], +1, {"hml_holdout_bp": float(np.nanmean(h[ts[hh] >= HOLD]) * 1e4),
                                                                       "hml_holdout_t": A.nw_t(h[ts[hh] >= HOLD])[1],
                                                                       "note": "gross per 1h hold"}))
    print("B", rows_out[-1]["holdout_t_signed"], flush=True)
    # ---------------- C: calendar effects on the alt index
    Ux = U.copy(); Ux[:, bi] = False
    idx = pd.Series(np.nanmean(np.where(Ux, r1, np.nan), 1), index=ts)
    idx = idx[(idx.index >= START) & (idx.index < END)].dropna()
    dfc = pd.DataFrame({"r": idx.to_numpy(), "h": (idx.index % D) // 3600, "w": ((idx.index // D) + 3) % 7}, index=idx.index)
    import statsmodels.formula.api as smf
    res = {}
    for tag, sel in (("insample", dfc.index < HOLD), ("holdout", dfc.index >= HOLD)):
        mdl = smf.ols("r ~ C(h) + C(w)", data=dfc[sel]).fit(cov_type="HAC", cov_kwds={"maxlags": 24})
        names = [n for n in mdl.params.index if n != "Intercept"]
        R_ = np.zeros((len(names), len(mdl.params))); [R_.__setitem__((i, list(mdl.params.index).index(n)), 1) for i, n in enumerate(names)]
        wt = mdl.wald_test(R_, scalar=True)
        res[tag] = {"wald_p": float(wt.pvalue), "hour_means_bp": (dfc[sel].groupby("h").r.mean() * 1e4).round(2).tolist(),
                    "weekday_means_bp": (dfc[sel].groupby("w").r.mean() * 1e4).round(2).tolist()}
    stab = float(np.corrcoef(res["insample"]["hour_means_bp"], res["holdout"]["hour_means_bp"])[0, 1])
    rows_out.append({"hypothesis": "C_calendar_hour_weekday", "sign": 0, "insample_t": np.nan, "holdout_t": np.nan,
                     "holdout_t_signed": float(stats.norm.ppf(1 - res["holdout"]["wald_p"])), "p_one": res["holdout"]["wald_p"],
                     "insample_wald_p": res["insample"]["wald_p"], "hour_mean_stability_corr": stab, "detail": res})
    print("C", res["insample"]["wald_p"], res["holdout"]["wald_p"], stab, flush=True)
    # ---------------- D: events, calendar-time portfolios (daily, market-adjusted)
    i0 = np.flatnonzero((ts % D == 0) & (ts >= START) & (ts < END)); i0 = i0[i0 + 24 < len(ts)]
    cf = pd.DataFrame(X["c"]).ffill().to_numpy()
    dret = np.where(np.isfinite(X["c"][i0]), cf[i0 + 24] / X["c"][i0] - 1, np.nan)
    mkt = np.nanmean(np.where(Ux[i0], dret, np.nan), 1, keepdims=True)
    ar = dret - mkt
    age_d = X["age"][i0] / 24.0
    listed_in_sample = (X["age"][i0[0]] == 0) | (~np.isfinite(X["c"][i0[0]]))
    D1 = (age_d >= 1) & (age_d <= 30) & listed_in_sample[None, :] & cm[None, :] & (dv[i0] >= 2e6)
    port1 = np.nanmean(np.where(D1, ar, np.nan), 1)
    rows_out.append(summarize("D1_new_binance_listing_days1-30", port1, ts[i0], -1,
                              {"avg_coins": float(D1.sum(1).mean()), "holdout_mean_bp": float(np.nanmean(port1[ts[i0] >= HOLD]) * 1e4)}))
    upq = np.asarray(np.load(ROOT / "data/cache/disc/prim_up_qv.npy", mmap_mode="r"))
    upd = pd.DataFrame(np.nan_to_num(upq)).rolling(24, min_periods=1).sum().to_numpy()[i0] > 0
    first = np.full(len(codes), -1)
    for j in range(len(codes)):
        w = np.flatnonzero(upd[:, j])
        if len(w) and w[0] > 0:                                      # listed on Upbit during the sample (not on day 0)
            first[j] = w[0]
    D2 = np.zeros_like(upd)
    for j in np.flatnonzero(first > 0):
        f0 = first[j]
        if age_d[f0, j] >= 30:
            D2[f0 + 1:f0 + 11, j] = True
    port2 = np.nanmean(np.where(D2, ar, np.nan), 1)
    rows_out.append(summarize("D2_upbit_listing_days1-10", port2, ts[i0], -1,
                              {"n_events": int((first > 0).sum()), "holdout_mean_bp": float(np.nanmean(port2[ts[i0] >= HOLD]) * 1e4)}))
    print("D", rows_out[-2]["holdout_t_signed"], rows_out[-1]["holdout_t_signed"], flush=True)
    # ---------------- E: Korea refinements via the B30 battery
    import b30_rigorous as B30
    d = B30.build_xs()
    T = len(d["day"])
    F = A.ltw_factors(d["R"], d["size"], d["mom21"], d["mask"], mkt_w=d["adv30"])
    hold = d["day"] * D >= HOLD
    K = d["ch"]["H2_KOREA"]
    wk = np.arange(T) % 7 == 0
    R7 = np.full(d["R"].shape, np.nan)
    for t in np.flatnonzero(wk):
        seg = d["R"][t:t + 7]
        if len(seg) == 7:
            R7[t] = np.prod(1 + np.nan_to_num(seg), 0) - 1
    for tag, sel in (("insample", ~hold), ("holdout", hold)):
        pass
    S = A.winsor_rows(np.where(d["mask"], K, np.nan))
    Pw = A.sort_portfolios(S[wk], R7[wk], n=5, mask=d["mask"][wk]); hw = A.long_short(Pw["EW"])
    days_w = d["day"][wk] * D
    rows_out.append(summarize("E1_korea_weekly", hw.to_numpy(), days_w, +1, {"holdout_mean_bp_per_week": float(hw[days_w >= HOLD].mean() * 1e4)}))
    big = np.zeros_like(d["mask"])
    for t in range(T):
        m = d["mask"][t] & np.isfinite(d["size"][t])
        if m.sum() > 30:
            big[t] = m & (d["size"][t] >= np.quantile(d["size"][t][m], 2 / 3))
    _, fm_big = A.fama_macbeth(d["R"], {"sig": np.where(big, S, np.nan)}, mask=big)
    Gb, _ = A.fama_macbeth(d["R"], {"sig": np.where(big, S, np.nan)}, mask=big)
    rows_out.append(summarize("E2_korea_large_tercile", Gb["sig"].to_numpy(), d["day"] * D, +1))
    ts_, codes_, X_ = ts, codes, X
    kp = np.asarray(np.load(ROOT / "data/cache/disc/prim_up_lc.npy", mmap_mode="r")) - X_["lc"]
    i0b = np.flatnonzero((ts % D == 0) & (ts >= START) & (ts < END)); i0b = i0b[i0b + 25 < len(ts)]
    kprem = -pd.DataFrame(kp).rolling(24, min_periods=6).mean().to_numpy()[i0b]
    kprem = kprem - np.nanmedian(kprem, 1, keepdims=True)                 # relative to the day's typical premium (FX, Korea-wide)
    Gk, _ = A.fama_macbeth(d["R"], {"sig": np.where(d["mask"], kprem, np.nan), "size": d["size"], "beta": d["controls"]["beta"]}, mask=d["mask"])
    rows_out.append(summarize("E3_coin_kimchi_premium", Gk["sig"].to_numpy(), d["day"] * D, +1))
    print("E", [r["holdout_t_signed"] for r in rows_out[-3:]], flush=True)
    Rt = pd.DataFrame(rows_out)
    Rt["bhy"] = A.bhy(Rt.p_one.fillna(1).to_numpy())
    Rt["verdict"] = np.where((Rt.holdout_t_signed >= 2) & Rt.bhy, "CONFIRMED", np.where(Rt.holdout_t_signed >= 2, "promising (fails BHY)", "no"))
    json.dump(Rt.to_dict("records"), open(OUT / "newideas.json", "w"), indent=1, default=float)
    cols = ["hypothesis", "sign", "insample_t", "holdout_t", "holdout_t_signed", "p_one", "bhy", "verdict"]
    Lm = ["# B31: new hypotheses (a-priori signs, holdout 2025-07-01..2026-09-23, BHY over 9)", "",
          "| " + " | ".join(cols) + " | extra |", "|" + "---|" * (len(cols) + 1)]
    for r in Rt.to_dict("records"):
        extra = {k: v for k, v in r.items() if k not in cols and k not in ("detail", "insample_mean", "holdout_mean", "insample_n", "holdout_n")}
        Lm.append("| " + " | ".join(f"{r[c]:+.2f}" if isinstance(r[c], float) else str(r[c]) for c in cols) + " | " +
                  ", ".join(f"{k} {v:.3g}" if isinstance(v, float) else f"{k} {v}" for k, v in extra.items()) + " |")
    Lm += ["", "Calendar detail (bp/hour, alt index): in-sample hour means " + str(res["insample"]["hour_means_bp"]),
           "holdout hour means " + str(res["holdout"]["hour_means_bp"])]
    (OUT / "newideas.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
