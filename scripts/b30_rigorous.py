"""B30_RIGOROUS (registered 2026-10-01, BEFORE running). Claude's own study, designed like a journal paper.

Why it differs from B29: every hypothesis has an A PRIORI sign from the literature (no sign fitted on our data), the
family is small (M=13 cross-sectional + 12 time-series), and the decisive tests use a HOLDOUT that no family-level
choice touched: 2025-07-01 .. 2026-09-23. 2024-05 .. 2025-06 is the "in-sample" period, reported for comparison only.

PART 1 - cross-section of perp excess returns (price return - funding), daily rebalance, next-day return
  (weekly non-overlapping rebalance as robustness). Universe/data rules as B29 (point-in-time, $5M volume floor, 30 days
  listed, no stablecoins/TradFi, delisted -> last price, 1%/99% winsorising, 1-hour implementation-lag variant).
  H1  CARRY       -funding_7d (mean 8h funding over 7 days)           Schmeling-Schrimpf-Todorov 2023: crowding / carry
  H2  KOREA       -Upbit volume share (24h)                           retail attention; our B29 survivor (confirmatory)
  H3  FLOW        +taker-buy share 24h - 0.5                          order-flow persistence (Chordia-Subrahmanyam 2004)
  H4  MAX         -max hourly return over 7 days                      lottery demand (Bali-Cakici-Whitelaw 2011)
  H5  IVOL        -idiosyncratic vol vs BTC, 7 days                   Ang-Hodrick-Xing-Zhang 2006
  H6  BAB         -beta to BTC, 30 days                               Frazzini-Pedersen 2014
  H7  REV         -return 1 day                                       Jegadeesh 1990 short-term reversal
  H8  MOM3W       +return 21 days (skipping none)                     Liu-Tsyvinski-Wu 2022 CMOM
  H9  VOLSHOCK    +log(dv24 / 30-day ADV)                             Gervais-Kaniel-Mingelgrin 2001
  H10 SKEW        -realised skewness of hourly returns, 7 days        Amaya-Christoffersen-Jacobs-Vasquez 2015
  H11 OIGROW      -open-interest growth 7 days                        leverage build-up (perp analogue of issuance)
  H12 ILLIQ       +Amihud illiquidity 7 days                          Amihud 2002
  H13 COMPOSITE   Lewellen (2015) rolling Fama-MacBeth forecast: trailing 180-day average FM slopes on H1-H12
                  characteristics x today's characteristics (1-day embargo), i.e. no in-sample fitting of weights
  Battery per signal: quintile sorts EW/VW + NW t, Patton-Timmermann MR, FM slope with controls (size, beta, rv) NW t,
  size-tercile double sort, LTW 3-factor alpha + GRS, turnover / net / break-even / band net, subperiods, 1h lag.
  Family on HOLDOUT: BHY + Holm on FM p-values, |t| >= 3, Hansen SPA + StepM on net band returns, DSR, PBO (CSCV).
  Verdict "confirmed" = holdout: FM t >= 2 with the a-priori sign AND BHY AND alpha t >= 2 AND band net > 0;
  "strong" adds |FM t| >= 3 and SPA/StepM.

PART 2 - time series: can the next day of the alt index (equal-weight, liquid) be forecast?
  Predictors (lagged, known at 00:00 UTC): TSMOM 1d/7d/28d (Liu-Tsyvinski 2021), BTC 7d, funding level, OI growth 7d,
  Deribit DVOL, DVOL - realised vol, breadth, Upbit share, kimchi premium, volume surprise.
  Design: Goyal-Welch recursive OLS, one predictor at a time + Rapach-Strauss-Zhou mean combination; OOS from 2025-07-01.
  Tests: Campbell-Thompson R2_OS (vs recursive mean), Clark-West (one-sided), Pesaran-Timmermann directional test,
  economic value = annualised certainty-equivalent gain for a mean-variance investor (gamma=3, weight in [0, 1.5],
  60-day variance) vs the historical-mean investor; BHY across the 12 + 1.
  Also the intraday question: does the alt return 00:00-16:00 UTC forecast 16:00-24:00 UTC (F8)? same tests.
Output data/reports/b30/rigorous.{json,md}, battery_xs.csv, forecast_ts.csv
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

OUT = ROOT / "data/reports/b30"
D = 86400
START, END = int(pd.Timestamp("2024-05-01").timestamp()), int(pd.Timestamp("2026-09-23").timestamp())
HOLD = int(pd.Timestamp("2025-07-01").timestamp()) // D
CD = ROOT / "data/cache/disc"


def P(k):
    return np.load(CD / f"prim_{k}.npy", mmap_mode="r")


def roll(a, w, fn):
    return getattr(pd.DataFrame(a).rolling(w, min_periods=w // 2), fn)().to_numpy()


def build_xs():
    from b29_battery import crypto_mask
    ts, codes, X = L.data()
    bi = codes.index("BTCUSDT")
    i0 = np.flatnonzero((ts % D == 0) & (ts >= START) & (ts < END)); i0 = i0[i0 + 25 < len(ts)]
    day = ts[i0] // D
    c = X["c"]; cf = pd.DataFrame(c).ffill().to_numpy(); listed = np.isfinite(c[i0])
    f8 = np.nan_to_num(X["f8"])
    fund = np.stack([f8[i + 1:i + 25].sum(0) / 8.0 for i in i0])
    R = np.where(listed, cf[i0 + 24] / c[i0] - 1 - fund, np.nan)
    R1 = np.where(np.isfinite(c[i0 + 1]), cf[i0 + 25] / c[i0 + 1] - 1 - fund, np.nan)
    dv = np.nan_to_num(X["dv24"]); adv30 = roll(dv, 720, "mean")
    mask = (dv[i0] >= 5e6) & (X["age"][i0] >= 720) & listed & crypto_mask(codes)[None, :]
    cost = L.FEE + L.slip(dv[i0])
    r1 = X["r1"]; lc = X["lc"]
    beta = X["beta"]
    resid = r1 - np.nan_to_num(beta) * r1[:, [bi]]
    qv = np.nan_to_num(X["qv"]); tb = np.nan_to_num(X["tbq"])
    oi = np.asarray(P("m_oi_usd"))
    upq = np.asarray(P("up_qv"))
    ch = {
        "H1_CARRY": -roll(X["f8"], 168, "mean")[i0],
        "H2_KOREA": -(pd.DataFrame(np.nan_to_num(upq)).rolling(24, min_periods=12).sum().to_numpy() /
                      np.maximum(pd.DataFrame(qv).rolling(24, min_periods=12).sum().to_numpy(), 1))[i0],
        "H3_FLOW": (L.S(tb, 24) / np.maximum(L.S(qv, 24), 1) - 0.5)[i0],
        "H4_MAX": -L.MX(r1, 168)[i0],
        "H5_IVOL": -L.SD(resid, 168)[i0],
        "H6_BAB": -beta[i0],
        "H7_REV": -(lc - L.lag(lc, 24))[i0],
        "H8_MOM3W": (lc - L.lag(lc, 504))[i0],
        "H9_VOLSHOCK": np.log((dv + 1) / (adv30 + 1))[i0],
        "H10_SKEW": -roll(r1, 168, "skew")[i0],
        "H11_OIGROW": -np.log(np.where(oi > 0, oi, np.nan) / np.where(L.lag(oi, 168) > 0, L.lag(oi, 168), np.nan))[i0],
        "H12_ILLIQ": roll(np.abs(r1) / np.maximum(qv, 1), 168, "mean")[i0],
    }
    ch["H2_KOREA"] = np.where(np.asarray(P("up_qv"))[i0] > 0, ch["H2_KOREA"], np.nan)    # only coins listed on Upbit
    size = np.log(adv30[i0] + 1)
    controls = {"size": size, "beta": beta[i0], "rv7": L.SD(r1, 168)[i0]}
    return dict(day=day, R=R, R1=R1, mask=mask, cost=cost, ch=ch, size=size, adv30=adv30[i0], controls=controls,
                mom21=(lc - L.lag(lc, 504))[i0])


def lewellen(ch, R, mask, window=180):
    """rolling FM composite: forecast_t = sum_k mean(slope_k over days t-window-1 .. t-2) * rank(char_k,t)."""
    names = list(ch); T = R.shape[0]
    Z = {k: A.winsor_rows(np.where(mask, ch[k], np.nan)) for k in names}
    Zr = {k: np.vstack([pd.Series(Z[k][t]).rank(pct=True).to_numpy() - 0.5 for t in range(T)]) for k in names}
    G, _ = A.fama_macbeth(R, {k: Zr[k] for k in names}, mask=mask, rank=False)
    S = np.full(R.shape, np.nan)
    for t in range(window + 2, T):
        w = G.iloc[t - window - 1:t - 1].mean().to_numpy()                # slopes known by day t (target t-2 realised)
        if not np.isfinite(w).all():
            w = np.nan_to_num(w)
        S[t] = sum(w[j] * np.nan_to_num(Zr[k][t]) for j, k in enumerate(names))
        S[t][~mask[t]] = np.nan
    return S


def battery(name, S, d, F, sel_rows):
    R, R1, mask, cost = d["R"][sel_rows], d["R1"][sel_rows], d["mask"][sel_rows], d["cost"][sel_rows]
    S = A.winsor_rows(np.where(mask, S[sel_rows], np.nan))
    Fs = F.iloc[np.flatnonzero(sel_rows)].reset_index(drop=True)
    P5 = A.sort_portfolios(S, R, W=d["adv30"][sel_rows], n=5, mask=mask)
    hml, hvw = A.long_short(P5["EW"]), A.long_short(P5["VW"])
    m_ew, t_ew = A.nw_t(hml); m_vw, t_vw = A.nw_t(hvw)
    _, p_mr = A.mr_test(P5["EW"], B=500)
    ctrl = {k: v[sel_rows] for k, v in d["controls"].items()}
    _, fm = A.fama_macbeth(R, {"sig": S, **ctrl}, mask=mask)
    fm_m, fm_t = fm["sig"]
    within = []
    sz = d["size"][sel_rows]
    for t in range(len(R)):
        m = mask[t] & np.isfinite(S[t]) & np.isfinite(R[t]) & np.isfinite(sz[t])
        if m.sum() < 45:
            within.append(np.nan); continue
        g = np.digitize(sz[t][m], np.quantile(sz[t][m], [1 / 3, 2 / 3])); s, r = S[t][m], R[t][m]; h = []
        for k in range(3):
            lo, hi = np.quantile(s[g == k], [0.2, 0.8]); h.append(r[g == k][s[g == k] >= hi].mean() - r[g == k][s[g == k] <= lo].mean())
        within.append(np.nanmean(h))
    _, ds_t = A.nw_t(within)
    al = A.alpha(hml, Fs)
    g_stat, g_p = A.grs(P5["EW"], Fs)
    to, cs = A.turnover_cost(P5["q"], 4, 0, cost, R=np.nan_to_num(R))
    net = hml - cs
    gb, nb, tob = A.band_long_short(S, R, cost, mask)
    P1 = A.sort_portfolios(S, R1, n=5, mask=mask); _, lag_t = A.nw_t(A.long_short(P1["EW"]))
    # weekly non-overlapping rebalance (every 7th day, 7-day compounded return)
    wk = np.arange(len(R)) % 7 == 0
    R7 = np.full(R.shape, np.nan)
    for t in np.flatnonzero(wk):
        seg = R[t:t + 7]
        if len(seg) == 7:
            R7[t] = np.prod(1 + np.nan_to_num(seg), 0) - 1
    Pw = A.sort_portfolios(S[wk], R7[wk], n=5, mask=mask[wk]); mw, tw = A.nw_t(A.long_short(Pw["EW"]))
    return {"signal": name, "hml_bp": m_ew * 1e4, "t_ew": t_ew, "hml_vw_bp": m_vw * 1e4, "t_vw": t_vw, "mr_p": p_mr,
            "fm_bp": fm_m * 1e4, "fm_t": fm_t, "dsort_t": ds_t, "alpha_bp": al["alpha"] * 1e4, "alpha_t": al["t_alpha"],
            "grs_p": g_p, "turnover": float(to.mean()), "net_bp": float(net.mean() * 1e4), "net_t": A.nw_t(net)[1],
            "breakeven_bp": float(hml.mean() / max(to.mean(), 1e-9) / 2 * 1e4), "band_net_bp": float(np.nanmean(nb) * 1e4),
            "band_net_t": A.nw_t(nb)[1], "lag1h_t": lag_t, "weekly_bp": mw * 1e4, "weekly_t": tw,
            "sharpe": A.sharpe(hml, 365), "n_days": int(len(R))}, nb.to_numpy()


def part1():
    d = build_xs()
    T = len(d["day"])
    F = A.ltw_factors(d["R"], d["size"], d["mom21"], d["mask"], mkt_w=d["adv30"])
    ch = dict(d["ch"])
    ch["H13_COMPOSITE"] = lewellen(d["ch"], d["R"], d["mask"])
    sets = {"insample": d["day"] < HOLD, "holdout": d["day"] >= HOLD}
    rows, nets = [], {}
    for name, S in ch.items():
        for tag, sel in sets.items():
            if name == "H13_COMPOSITE" and tag == "insample":
                sel = sel & (np.arange(T) >= 185)
            r, nb = battery(name, S, d, F, sel)
            r["set"] = tag; rows.append(r)
            if tag == "holdout":
                nets[name] = nb
            print(name, tag, f"FM t {r['fm_t']:+.2f} HML t {r['t_ew']:+.2f} alpha t {r['alpha_t']:+.2f} band net {r['band_net_bp']:+.1f}bp", flush=True)
    Rt = pd.DataFrame(rows)
    H = Rt[Rt.set == "holdout"].copy()
    p_one = 1 - stats.norm.cdf(H.fm_t.fillna(-9))                       # one-sided in the a-priori direction
    H["fm_p1"] = p_one; H["bhy"] = A.bhy(p_one.to_numpy()); H["holm"] = A.holm(p_one.to_numpy())
    BN = pd.DataFrame(nets).fillna(0.0)
    spa = A.spa_stepm(np.zeros(len(BN)), -BN, reps=2000)
    srs = BN.mean() / BN.std(); best = srs.idxmax()
    dsr, sr0 = A.deflated_sharpe(BN[best], len(BN.columns), float(srs.var()))
    pbo, lam = A.pbo_cscv(BN, S=16)
    H["confirmed"] = (H.fm_t >= 2) & H.bhy & (H.alpha_t >= 2) & (H.band_net_bp > 0)
    H["strong"] = H.confirmed & (H.fm_t >= 3) & H.signal.isin(spa["stepm_superior"])
    fam = {"M": int(len(H)), "spa": spa, "best": best, "best_sharpe": float(srs[best] * np.sqrt(365)), "dsr": dsr, "pbo": pbo,
           "factors_holdout": {k: A.nw_t(F[k][d["day"] >= HOLD])[1] for k in F.columns}}
    return Rt, H, fam


def part2():
    import b25_badday as B25
    Fx, Tt, _ = B25.build()
    dvol = pd.read_parquet(ROOT / "data/cache/deribit_dvol_btc_1d.parquet")
    dv = pd.Series(dvol.c.to_numpy(), index=(dvol.ts // 1000 // D).to_numpy()).shift(1)
    y = Tt["ret"]
    pr = pd.DataFrame(index=Fx.index)
    pr["TSMOM_1d"] = Fx["alt_1d"]; pr["TSMOM_7d"] = Fx["alt_7d"]; pr["TSMOM_28d"] = Fx["alt_30d"]; pr["BTC_7d"] = Fx["btc_7d"]
    pr["FUNDING"] = Fx["funding"]; pr["OI_7d"] = Fx["oi_7d"]; pr["DVOL"] = np.log(dv.reindex(Fx.index))
    pr["DVOL_minus_RV"] = np.log(dv.reindex(Fx.index) / 100 / np.sqrt(365)) - np.log(Fx["vol_7d"] * np.sqrt(24) + 1e-9)
    pr["BREADTH"] = Fx["breadth"]; pr["UPBIT_SHARE"] = Fx["upbit_share"]; pr["KIMCHI"] = Fx["kimchi"]; pr["VOL_SURPRISE"] = Fx["vol_surprise"]
    idx = Fx.index.to_numpy(); start = int(np.searchsorted(idx, HOLD))
    rows, fc = [], {}
    var60 = y.shift(1).rolling(60, min_periods=30).var()

    def cer(f, hm):
        def u(w):
            r = w * y
            return r.mean() - 1.5 * r.var()
        wf = (f / (3 * var60)).clip(0, 1.5); wh = (hm / (3 * var60)).clip(0, 1.5)
        ok = f.notna() & hm.notna() & var60.notna()
        return float((u(wf[ok]) - u(wh[ok])) * 365 * 1e4)                    # bp per year
    for k in pr.columns:
        f, hm = A.recursive_ols_forecast(y, pr[k], start=start, min_obs=120)
        fc[k] = f
        o = f.notna() & hm.notna() & y.notna()
        cw_s, cw_p = A.clark_west(y[o], hm[o], f[o])
        hit, S, ptp = A.pesaran_timmermann(y[o], f[o])
        rows.append({"predictor": k, "r2_os_pct": A.r2_os(y[o], f[o], hm[o]) * 100, "cw_stat": cw_s, "cw_p": cw_p, "hit": hit, "pt_p": ptp,
                     "cer_gain_bp_yr": cer(f, hm), "n": int(o.sum())})
    comb = pd.DataFrame(fc).mean(1)
    f, hm = comb, A.recursive_ols_forecast(y, pr["TSMOM_1d"], start=start, min_obs=120)[1]
    o = f.notna() & hm.notna() & y.notna()
    rows.append({"predictor": "RSZ_MEAN_COMBINATION", "r2_os_pct": A.r2_os(y[o], f[o], hm[o]) * 100, "cw_stat": A.clark_west(y[o], hm[o], f[o])[0],
                 "cw_p": A.clark_west(y[o], hm[o], f[o])[1], "hit": A.pesaran_timmermann(y[o], f[o])[0], "pt_p": A.pesaran_timmermann(y[o], f[o])[2],
                 "cer_gain_bp_yr": cer(f, hm), "n": int(o.sum())})
    # intraday: alt return 00-16 UTC -> 16-24 UTC
    ts, codes, X = L.data()
    bi = codes.index("BTCUSDT")
    U = (np.nan_to_num(X["dv24"]) >= 5e6) & (X["age"] >= 720); U[:, bi] = False
    idx_h = pd.Series(np.nan_to_num(np.nanmean(np.where(U, X["r1"], np.nan), 1)), index=ts)
    hh = (idx_h.index % D) // 3600; dd = idx_h.index // D
    early = idx_h[hh < 16].groupby(dd[hh < 16]).sum(); late = idx_h[hh >= 16].groupby(dd[hh >= 16]).sum()
    J = pd.DataFrame({"e": early, "l": late}).dropna(); J = J[(J.index >= START // D) & (J.index < END // D)]
    st = int(np.searchsorted(J.index.to_numpy(), HOLD))
    f, hm = A.recursive_ols_forecast(J.l, J.e, start=st, min_obs=120)
    o = f.notna() & hm.notna()
    rows.append({"predictor": "INTRADAY_00-16_to_16-24", "r2_os_pct": A.r2_os(J.l[o], f[o], hm[o]) * 100, "cw_stat": A.clark_west(J.l[o], hm[o], f[o])[0],
                 "cw_p": A.clark_west(J.l[o], hm[o], f[o])[1], "hit": A.pesaran_timmermann(J.l[o], f[o])[0],
                 "pt_p": A.pesaran_timmermann(J.l[o], f[o])[2], "cer_gain_bp_yr": np.nan, "n": int(o.sum()),
                 "insample_corr": float(J[J.index < HOLD].corr().iloc[0, 1]), "holdout_corr": float(J[J.index >= HOLD].corr().iloc[0, 1])})
    TS = pd.DataFrame(rows)
    TS["bhy_cw"] = A.bhy(TS.cw_p.to_numpy())
    return TS


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    Rt, H, fam = part1()
    Rt.to_csv(OUT / "battery_xs.csv", index=False)
    TS = part2()
    TS.to_csv(OUT / "forecast_ts.csv", index=False)
    json.dump({"family": fam, "holdout": H.to_dict("records"), "ts": TS.to_dict("records")}, open(OUT / "rigorous.json", "w"), indent=1, default=float)
    f = lambda v: f"{v:+.2f}" if isinstance(v, (float, np.floating)) and np.isfinite(v) else str(v)  # noqa: E731
    Lm = ["# B30: Claude's own pre-registered study (a-priori signs, holdout 2025-07-01..2026-09-23)", "",
          "## Part 1: cross-section, HOLDOUT (decisive)", "",
          "| hypothesis | HML bp/day | NW t | VW t | MR p | FM t | size-dsort t | 3F alpha t | GRS p | turnover | net bp | break-even bp | band net bp | 1h-lag t | weekly t | BHY | confirmed | strong |",
          "|---|" + "---|" * 17]
    for r in H.itertuples():
        Lm.append(f"| {r.signal} | {f(r.hml_bp)} | {f(r.t_ew)} | {f(r.t_vw)} | {r.mr_p:.2f} | {f(r.fm_t)} | {f(r.dsort_t)} | {f(r.alpha_t)} | {r.grs_p:.2f} | "
                  f"{r.turnover:.2f} | {f(r.net_bp)} | {f(r.breakeven_bp)} | {f(r.band_net_bp)} | {f(r.lag1h_t)} | {f(r.weekly_t)} | {r.bhy} | {r.confirmed} | {r.strong} |")
    ins = Rt[Rt.set == "insample"].set_index("signal")
    Lm += ["", "In-sample (2024-05..2025-06) FM t for comparison: " + ", ".join(f"{k} {v:+.2f}" for k, v in ins.fm_t.items()), "",
           f"Family on holdout (M={fam['M']}): SPA p {fam['spa']['spa_p_consistent']:.3f}, StepM superior {fam['spa']['stepm_superior'] or 'none'}, "
           f"best {fam['best']} (Sharpe {fam['best_sharpe']:.2f}), Deflated Sharpe {fam['dsr']:.3f}, PBO {fam['pbo']:.2f}. "
           f"LTW factor t on holdout: " + ", ".join(f"{k} {v:+.2f}" for k, v in fam["factors_holdout"].items()), "",
           "## Part 2: time series - forecasting the next day of the alt index (OOS from 2025-07-01)", "",
           "| predictor | R2_OS % | Clark-West p | hit rate | Pesaran-Timmermann p | CER gain bp/yr | BHY | n |", "|---|---|---|---|---|---|---|---|"]
    for r in TS.itertuples():
        Lm.append(f"| {r.predictor} | {r.r2_os_pct:+.2f} | {r.cw_p:.3f} | {r.hit:.1%} | {r.pt_p:.3f} | {f(r.cer_gain_bp_yr)} | {r.bhy_cw} | {r.n} |")
    (OUT / "rigorous.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
