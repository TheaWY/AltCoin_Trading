"""B29_BATTERY (registered 2026-10-01, before running). Re-test every cross-sectional candidate with the referee-grade
battery used in the crypto asset-pricing literature (Liu-Tsyvinski-Wu 2022 JF; Fieberg et al. 2024 JFQA; Novy-Marx &
Velikov 2016; Harvey-Liu-Zhu 2016; Hansen 2005; Bailey & Lopez de Prado 2014/2017). Library: scripts/aphx.py.

Data (follows the papers' rules, adapted to perps)
  panel      Binance USDT perps, daily at 00:00 UTC, 2024-05-01 .. 2026-09-23 (b7 hourly cache)
  universe   point-in-time: 24h quote volume >= $5M, listed >= 30 days, crypto only (TradFi tokens and stablecoins out);
             no future information in the filter (unlike b7 U)
  return     next-day perp excess return = price return (delisted coins settle at last traded price) - funding paid
  size       30-day average dollar volume (ADV30) - no market cap for perps; VW weights = ADV30
  signals    winsorised 1%/99% per day; known at the 00:00 bar close; 1-hour implementation-lag variant reported
Signals (the family, M counted in full)
  controls/known: ret_1d, ret_7d, ret_21d (LTW 3-week momentum), ret_28d, rv_7d, size (ADV30), beta_30d, max_1h_7d,
                  funding_8h, illiquidity (|ret|/dollar volume, Amihud)
  DISC1: every variable from disc_engine.variables() (venue, Korea, positioning, on-chain, depth)
  ML: B19 H013 (CatBoost xs24) and H033 (CatBoost xs72) walk-forward predictions
Per signal
  1 quintile sorts EW and VW, high-minus-low mean (bp/day), Newey-West t (NW94 lag), Sharpe, monotonicity p (Patton-
    Timmermann, stationary bootstrap, Politis-White block)
  2 Fama-MacBeth slope with controls (size, ret_1d, ret_21d, rv_7d, beta) - NW t
  3 dependent 3x5 double sort on size: average HML within size terciles (NW t) and within the large tercile
  4 alpha of HML on LTW-style factors CMKT (ADV-weighted), CSMB (30/40/30 on ADV30), CMOM (21d, within size halves);
    GRS on the 5 quintiles
  5 costs: one-way turnover, net HML (taker fee 5bp + slippage by dv24, per side), break-even one-way cost,
    buy/hold band (enter 20%, exit 40%) net
  6 robustness: subperiods (2024H2, 2025, 2026), 1-hour implementation lag
Family-level (all M signals)
  BHY and Holm on the FM p-values; Harvey-Liu-Zhu |t| >= 3; Hansen SPA + Romano-Wolf StepM on net band returns vs cash;
  Deflated Sharpe of the best net strategy; PBO via CSCV (S=16) over the net band returns.
"Survivor" = FM |t| >= 3 AND BHY-significant AND same sign in all 3 subperiods AND alpha t >= 2 AND band net > 0.
Output data/reports/b29/battery.{json,csv,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import aphx as A  # noqa: E402
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b29"
D = 86400
START, END = int(pd.Timestamp("2024-05-01").timestamp()), int(pd.Timestamp("2026-09-23").timestamp())
SUB = {"2024H2": ("2024-05-01", "2025-01-01"), "2025": ("2025-01-01", "2026-01-01"), "2026": ("2026-01-01", "2026-09-23")}
STABLE = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT", "BUSDUSDT", "USDEUSDT", "PYUSDUSDT", "EURUSDT", "USD1USDT"}


def crypto_mask(codes):
    try:
        info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20).json()["symbols"]
        tradfi = {x["symbol"] for x in info if x.get("underlyingType", "COIN") != "COIN"}
    except Exception:  # noqa: BLE001
        tradfi = set()
    return np.array([(c not in tradfi) and (c not in STABLE) for c in codes])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    N = len(codes); bi = codes.index("BTCUSDT")
    i0 = np.flatnonzero((ts % D == 0) & (ts >= START) & (ts < END))
    i0 = i0[i0 + 25 < len(ts)]
    T = len(i0); day = ts[i0] // D
    c = X["c"]; cf = pd.DataFrame(c).ffill().to_numpy()
    listed_now = np.isfinite(c[i0])
    ret = cf[i0 + 24] / c[i0] - 1
    ret_lag = cf[i0 + 25] / c[i0 + 1] - 1
    f8 = np.nan_to_num(X["f8"])
    fund = np.stack([f8[i + 1:i + 25].sum(0) / 8.0 for i in i0])            # f8 carried hourly: 3 settlements per day
    R = np.where(listed_now, ret - fund, np.nan)
    R1 = np.where(np.isfinite(c[i0 + 1]), ret_lag - fund, np.nan)
    dv = np.nan_to_num(X["dv24"])
    adv30 = pd.DataFrame(dv).rolling(720, min_periods=240).mean().to_numpy()[i0]
    mask = (dv[i0] >= 5e6) & (X["age"][i0] >= 720) & listed_now & crypto_mask(codes)[None, :]
    mask[:, bi] = mask[:, bi]                                                 # BTC stays in the cross-section
    cost = L.FEE + L.slip(dv[i0])
    K = L.known_factors()
    lc = X["lc"]
    sig = {"ret_1d": K["ret_24h"][i0], "ret_7d": K["ret_7d"][i0], "ret_21d": (lc - L.lag(lc, 504))[i0], "ret_28d": K["ret_28d"][i0],
           "rv_7d": K["rv_7d"][i0], "size_adv30": np.log(adv30 + 1), "beta_30d": X["beta"][i0], "max_1h_7d": K["max_1h_7d"][i0],
           "funding_8h": X["f8"][i0],
           "amihud_7d": pd.DataFrame(np.abs(X["r1"]) / np.maximum(np.nan_to_num(X["qv"]), 1)).rolling(168, min_periods=84).mean().to_numpy()[i0]}
    controls = {k: sig[k] for k in ("size_adv30", "ret_1d", "ret_21d", "rv_7d", "beta_30d")}
    import disc_engine as DE
    for n, fam, f in DE.variables():
        sig[f"d1_{n}"] = np.asarray(f)[i0].astype(np.float32)
    print("signals", len(sig), flush=True)
    R8 = np.flatnonzero((ts % (8 * 3600) == 0) & (ts >= int(pd.Timestamp("2024-03-15").timestamp())))
    for hid in ("H013", "H033"):
        P = np.load(ROOT / f"data/cache/b19/{hid}.npy").astype(np.float32)
        full = np.full(c.shape, np.nan, np.float32); full[R8] = P
        sig[f"ml_{hid}"] = full[i0]
    # factors
    F = A.ltw_factors(R, sig["size_adv30"], sig["ret_21d"], mask, mkt_w=adv30)
    sub_idx = {k: (day >= pd.Timestamp(a).timestamp() // D) & (day < pd.Timestamp(b).timestamp() // D) for k, (a, b) in SUB.items()}
    rows, band_net = [], {}
    for name, S in sig.items():
        S = A.winsor_rows(np.where(mask, S, np.nan))
        if np.isfinite(S).sum() < 1000:
            continue
        P = A.sort_portfolios(S, R, W=adv30, n=5, mask=mask)
        hml_ew, hml_vw = A.long_short(P["EW"]), A.long_short(P["VW"])
        m_ew, t_ew = A.nw_t(hml_ew); m_vw, t_vw = A.nw_t(hml_vw)
        sgn = 1.0 if m_ew >= 0 else -1.0
        Pm = P["EW"] if sgn > 0 else P["EW"].iloc[:, ::-1]
        J, p_mr = A.mr_test(Pm.set_axis(range(5), axis=1), B=500)
        G, fm = A.fama_macbeth(R, {"sig": S, **{k: v for k, v in controls.items() if k != name}}, mask=mask)
        fm_m, fm_t = fm["sig"]
        # dependent double sort on size terciles
        within = []
        for t in range(T):
            m = mask[t] & np.isfinite(S[t]) & np.isfinite(R[t]) & np.isfinite(sig["size_adv30"][t])
            if m.sum() < 45:
                within.append((np.nan, np.nan)); continue
            sz = sig["size_adv30"][t][m]; s = S[t][m]; r = R[t][m]
            cuts = np.quantile(sz, [1 / 3, 2 / 3]); g = np.digitize(sz, cuts)
            h = []
            for k in range(3):
                gs, gr = s[g == k], r[g == k]
                lo, hi = np.quantile(gs, [0.2, 0.8])
                h.append(gr[gs >= hi].mean() - gr[gs <= lo].mean())
            within.append((np.nanmean(h), h[2]))
        within = np.array(within)
        ds_m, ds_t = A.nw_t(within[:, 0]); big_m, big_t = A.nw_t(within[:, 1])
        al = A.alpha(sgn * hml_ew, F)
        g_stat, g_p = A.grs(P["EW"], F)
        to, cs = A.turnover_cost(P["q"], 4 if sgn > 0 else 0, 0 if sgn > 0 else 4, cost, R=np.nan_to_num(R))
        net = sgn * hml_ew - cs
        be = float((sgn * hml_ew).mean() / to.mean() / 2) if to.mean() > 0 else np.nan     # per one-way unit, both legs
        gb, nb, tob = A.band_long_short(sgn * S, R, cost, mask)
        band_net[name] = nb.to_numpy()
        P1 = A.sort_portfolios(S, R1, n=5, mask=mask); lag_m, lag_t = A.nw_t(sgn * A.long_short(P1["EW"]))
        subs = {}
        for k, sel in sub_idx.items():
            subs[k] = A.nw_t((sgn * hml_ew)[sel])[1]
        rows.append({"signal": name, "sign": sgn, "hml_ew_bp": m_ew * 1e4, "t_ew": t_ew, "hml_vw_bp": m_vw * 1e4, "t_vw": t_vw,
                     "sharpe_ew": A.sharpe(sgn * hml_ew, 365), "mr_p": p_mr, "fm_slope_bp": fm_m * 1e4, "fm_t": fm_t,
                     "dsort_t": ds_t, "dsort_big_t": big_t, "alpha_bp": al["alpha"] * 1e4, "alpha_t": al["t_alpha"],
                     "grs_p": g_p, "turnover": float(to.mean()), "net_bp": float(net.mean() * 1e4), "net_t": A.nw_t(net)[1],
                     "breakeven_bp": be * 1e4, "band_net_bp": float(np.nanmean(nb) * 1e4), "band_net_t": A.nw_t(nb)[1],
                     "lag1h_t": lag_t, **{f"t_{k}": v for k, v in subs.items()},
                     "avg_n_per_q": float(P["count"][P["count"][:, 0] > 0].mean())})
        print(name, f"t_ew {t_ew:+.2f} fm_t {fm_t:+.2f} alpha_t {al['t_alpha']:+.2f} net {net.mean() * 1e4:+.1f}bp", flush=True)
    Rt = pd.DataFrame(rows)
    p_fm = 2 * (1 - __import__("scipy").stats.norm.cdf(np.abs(Rt.fm_t.fillna(0))))
    Rt["fm_p"] = p_fm; Rt["bhy"] = A.bhy(p_fm); Rt["holm"] = A.holm(p_fm); Rt["hlz_t3"] = Rt.fm_t.abs() >= 3
    Rt["subperiod_consistent"] = (np.sign(Rt[[f"t_{k}" for k in SUB]]) > 0).all(1)
    Rt["survivor"] = Rt.hlz_t3 & Rt.bhy & Rt.subperiod_consistent & (Rt.alpha_t >= 2) & (Rt.band_net_bp > 0)
    M = len(Rt)
    BN = pd.DataFrame(band_net).fillna(0.0)
    srs = BN.mean() / BN.std()
    best = srs.idxmax()
    dsr, sr0 = A.deflated_sharpe(BN[best], M, float(srs.var()))
    pbo, lam = A.pbo_cscv(BN, S=16)
    spa = A.spa_stepm(np.zeros(len(BN)), -BN, reps=1000)
    fam = {"M": M, "best_band_strategy": best, "best_sharpe_ann": float(srs[best] * np.sqrt(365)), "dsr": dsr, "sr0_ann": sr0 * np.sqrt(365),
           "pbo": pbo, "pbo_median_logit": lam, **spa, "n_bhy": int(Rt.bhy.sum()), "n_hlz": int(Rt.hlz_t3.sum()), "n_survivors": int(Rt.survivor.sum()),
           "factor_premia": {k: {"bp_day": A.nw_t(F[k])[0] * 1e4, "t": A.nw_t(F[k])[1]} for k in F.columns}}
    Rt.to_csv(OUT / "battery.csv", index=False)
    json.dump({"family": fam, "signals": Rt.to_dict("records")}, open(OUT / "battery.json", "w"), indent=1, default=float)
    show = Rt.sort_values("fm_t", key=np.abs, ascending=False)
    cols = ["signal", "sign", "hml_ew_bp", "t_ew", "t_vw", "mr_p", "fm_t", "dsort_t", "alpha_t", "turnover", "net_bp", "breakeven_bp",
            "band_net_bp", "lag1h_t", "t_2024H2", "t_2025", "t_2026", "bhy", "survivor"]
    Lm = ["# B29: referee-grade battery on every cross-sectional candidate", "",
          f"Panel 2024-05-01..2026-09-23 daily, T={T} days, M={M} signals. Factors (bp/day, NW t): " +
          ", ".join(f"{k} {v['bp_day']:+.1f} ({v['t']:+.2f})" for k, v in fam["factor_premia"].items()), "",
          f"Family: BHY-significant {fam['n_bhy']}, |t|>=3 {fam['n_hlz']}, survivors {fam['n_survivors']}. "
          f"Best band strategy {best} (Sharpe {fam['best_sharpe_ann']:.2f}), Deflated Sharpe prob {dsr:.3f} (needs >= 0.95), "
          f"PBO {pbo:.2f} (needs < 0.5), SPA p {fam['spa_p_consistent']:.3f}, StepM superior: {fam['stepm_superior'] or 'none'}", "",
          "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in show[cols].itertuples(index=False):
        Lm.append("| " + " | ".join(f"{v:.2f}" if isinstance(v, float) else str(v) for v in r) + " |")
    (OUT / "battery.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm[:6]))


if __name__ == "__main__":
    main()
