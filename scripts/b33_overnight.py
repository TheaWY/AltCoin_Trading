"""B33_OVERNIGHT (registered 2026-10-02 ~00:45 KST, BEFORE running). Four studies on data sources not yet tested with the
academic battery. A-priori signs; splits fixed here; BHY within each study; aphx library. Each section is independent.

S1 UNLOCKS (event study). 373 DefiLlama emission schedules (data/cache/unlocks), mapped to Binance perps via
   data/cache/coingecko_ids.json. Event = a day where the scheduled unlocked amount jumps by >= 1% of the amount already
   unlocked (cliff), token listed on Binance perps >= 30 days. Market-adjusted daily abnormal returns (minus liquid-alt index).
   Windows: [-10,-1] (anticipatory selling, a-priori NEGATIVE), [0,+5] (NEGATIVE), [+6,+20] (POSITIVE, rebound).
   Tests: calendar-time portfolio (Mitchell-Stafford) NW t; per-event CARs with BMP standardisation and Kolari-Pynnonen
   cross-correlation adjustment; split in-sample < 2025-07-01 <= holdout.
S2 ORDER BOOK (cross-section). 5-minute Binance book-depth snapshots since 2026-03-25 (data/cache/bookdepth).
   OBI24 = mean 24h top-5 imbalance (a-priori POSITIVE: bid-heavy books predict higher returns, Cont-Kukanov-Stoikov 2014),
   OBI1 = last-hour imbalance (POSITIVE), DEPTHVOL = log(2% depth / 24h volume) (a-priori NEGATIVE: thin relative
   depth -> fragile, Amihud-style premium reversed for perps? we pre-register NEGATIVE as in B13 pump precursors).
   B30 battery; in-sample 2026-03-26..2026-06-30, holdout 2026-07-01..2026-09-23.
S3 LIQUIDATION CASCADES (hourly event study, liquidation_agg_1h since 2026-07-16). Long-liquidation spike = coin-hour with
   long_liq_notional >= 0.5% of 24h volume and >= $200k: next 1..4h and 1..24h market-adjusted return a-priori POSITIVE
   (forced-selling overshoot reverts; Brunnermeier-Pedersen 2009). Short-liquidation spike: a-priori NEGATIVE.
   Calendar-time hourly portfolios, NW t; split first half / second half of the sample.
S4 EDGE CASES of the only consistent survivor (Upbit volume share; B29 / B30 H2 / B31 E1-E2): 72 implementation
   variants (lookback 24/72/168h x rebalance daily/weekly x quantile 20/30% x volume floor $2M/$5M/$20M x EW/VW), in the
   spirit of Fieberg et al. (2024) 53,920 variants. Report: share of variants with holdout HML t > 2 and > 0, median t,
   PBO across variants (CSCV, S=16), Deflated Sharpe of the best variant, worst variant.
Outputs data/reports/b33/s{1..4}.md and overnight.json
"""
from __future__ import annotations

import glob
import json
import os
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import aphx as A  # noqa: E402
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b33"
D = 86400
HOLD = int(pd.Timestamp("2025-07-01").timestamp())
RES = {}


def panel():
    ts, codes, X = L.data()
    from b29_battery import crypto_mask
    cm = crypto_mask(codes); bi = codes.index("BTCUSDT")
    dv = np.nan_to_num(X["dv24"])
    U = (dv >= 5e6) & (X["age"] >= 720) & cm[None, :] & np.isfinite(X["c"]); U[:, bi] = False
    return ts, codes, X, cm, U


def s1_unlocks():
    ts, codes, X, cm, U = panel()
    ids = json.load(open(ROOT / "data/cache/coingecko_ids.json"))
    inv = {}
    for sym, cg in ids.items():
        code = sym.replace("/", "")
        if code in codes:
            inv.setdefault(cg, code)
    i0 = np.flatnonzero(ts % D == 0); i0 = i0[i0 + 24 < len(ts)]
    day = ts[i0] // D
    cf = pd.DataFrame(X["c"]).ffill().to_numpy()
    dret = np.where(np.isfinite(X["c"][i0]), cf[i0 + 24] / X["c"][i0] - 1, np.nan)
    mkt = np.nanmean(np.where(U[i0], dret, np.nan), 1, keepdims=True); AR = dret - mkt
    age_d = X["age"][i0] / 24
    events = []
    for f in glob.glob(str(ROOT / "data/cache/unlocks/*.json")):
        slug = os.path.basename(f)[:-5]
        code = inv.get(slug)
        if code is None:
            continue
        try:
            dj = json.load(open(f))
            series = dj.get("documentedData", {}).get("data", []) or dj.get("data", [])
            tot = {}
            for sec in series:
                for p in sec.get("data", []):
                    tot[int(p["timestamp"]) // D] = tot.get(int(p["timestamp"]) // D, 0.0) + float(p.get("unlocked", 0) or 0)
            s = pd.Series(tot).sort_index()
            jump = s.diff() / s.shift(1)
            for dd, v in jump[(jump >= 0.01) & (s.shift(1) > 0)].items():
                events.append((code, int(dd), float(v)))
        except Exception:  # noqa: BLE001
            continue
    j_of = {c: k for k, c in enumerate(codes)}
    pos = {d_: k for k, d_ in enumerate(day)}
    rows = []
    wins = {"pre_-10_-1": (-10, -1, -1), "post_0_5": (0, 5, -1), "post_6_20": (6, 20, +1)}
    for code, dd, v in events:
        k = pos.get(dd); j = j_of[code]
        if k is None or k < 70 or k + 21 >= len(day) or age_d[k - 11, j] < 30:
            continue
        est = AR[k - 70:k - 11, j]; sd = np.nanstd(est)
        if not np.isfinite(sd) or sd == 0:
            continue
        r = {"code": code, "day": dd, "jump": v, "sd": sd, "hold": dd * D >= HOLD}
        for w, (a, b, _) in wins.items():
            r[w] = float(np.nansum(AR[k + a:k + b + 1, j]))
            r[w + "_scar"] = r[w] / (sd * np.sqrt(b - a + 1))
        rows.append(r)
    E = pd.DataFrame(rows).drop_duplicates(["code", "day"])
    out = {"n_events": int(len(E))}
    # Kolari-Pynnonen: average pairwise correlation of estimation-period residuals ~ use cross-event average correlation of
    # the market-adjusted returns of event coins over the full sample (conservative proxy)
    jj = sorted({j_of[c] for c in E.code})
    C = pd.DataFrame(AR[:, jj]).corr(min_periods=60).to_numpy(); rbar = float(np.nanmean(C[np.triu_indices_from(C, 1)])) if len(jj) > 2 else 0.0
    out["kp_rbar"] = rbar
    for tag, e in (("all", E), ("insample", E[~E.hold]), ("holdout", E[E.hold])):
        o = {"n": int(len(e))}
        for w, (a, b, sg) in wins.items():
            if len(e) < 5:
                continue
            sc = e[w + "_scar"].to_numpy(); n = len(sc)
            t_bmp = sc.mean() / (sc.std(ddof=1) / np.sqrt(n))
            t_kp = t_bmp * np.sqrt((1 - rbar) / (1 + (n - 1) * rbar))
            o[w] = {"mean_car": float(e[w].mean()), "median_car": float(e[w].median()), "share_neg": float((e[w] < 0).mean()),
                    "t_bmp": float(t_bmp), "t_kp": float(t_kp), "t_signed_kp": float(sg * t_kp)}
        out[tag] = o
    # calendar-time portfolio for post_0_5 (short) and pre window
    for w, (a, b, sg) in wins.items():
        Mx = np.zeros(AR.shape, bool)
        for r in E.itertuples():
            k = pos[r.day]; Mx[max(0, k + a):k + b + 1, j_of[r.code]] = True
        port = pd.Series(np.nanmean(np.where(Mx, AR, np.nan), 1), index=day * D)
        for tag, sel in (("insample", port.index < HOLD), ("holdout", port.index >= HOLD)):
            m_, t_ = A.nw_t(port[sel]); out.setdefault("calendar_time", {})[f"{w}_{tag}"] = {"bp_day": m_ * 1e4, "t_signed": sg * t_}
    E.to_csv(OUT / "s1_events.csv", index=False)
    RES["S1_unlocks"] = out
    return out


def s2_book():
    import b30_rigorous as B30
    d = B30.build_xs()
    ts, codes, X = L.data()
    i0 = np.flatnonzero((ts % D == 0) & (ts >= B30.START) & (ts < B30.END)); i0 = i0[i0 + 25 < len(ts)]
    t0 = ts[i0]
    T, N = len(i0), len(codes)
    obi24 = np.full((T, N), np.nan); obi1 = np.full((T, N), np.nan); dep = np.full((T, N), np.nan)
    dv = np.nan_to_num(X["dv24"])[i0]
    for j, c in enumerate(codes):
        f = ROOT / f"data/cache/bookdepth/{c}.parquet"
        if not f.exists():
            continue
        b = pd.read_parquet(f, columns=["ts", "bid_2", "ask_2", "imb_5"]).sort_values("ts").set_index("ts")
        if not len(b):
            continue
        s24 = b.imb_5.rolling("24h").mean() if False else None
        bt = b.index.to_numpy()
        cs = np.concatenate([[0], np.cumsum(np.nan_to_num(b.imb_5.to_numpy()))]); cn = np.concatenate([[0], np.cumsum(np.isfinite(b.imb_5.to_numpy()))])
        hi = np.searchsorted(bt, t0, side="right")
        for k_, (w, arr) in enumerate(((86400, obi24), (3600, obi1))):
            lo = np.searchsorted(bt, t0 - w, side="left"); n = cn[hi] - cn[lo]
            arr[:, j] = np.where(n >= (12 if w == 3600 else 100), (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
        last = np.clip(hi - 1, 0, len(bt) - 1)
        depth = (b.bid_2.to_numpy() + b.ask_2.to_numpy())[last]
        ok = (hi > 0) & (t0 - bt[last] < 3600)
        dep[:, j] = np.where(ok, -np.log((depth + 1) / (dv[:, j] + 1)), np.nan)   # a-priori NEGATIVE on depth/volume -> signal = -log(depth/vol)
    F = A.ltw_factors(d["R"], d["size"], d["mom21"], d["mask"], mkt_w=d["adv30"])
    ins = (t0 >= pd.Timestamp("2026-03-26").timestamp()) & (t0 < pd.Timestamp("2026-07-01").timestamp())
    hold = t0 >= pd.Timestamp("2026-07-01").timestamp()
    rows = []
    for name, S in (("OBI24", obi24), ("OBI1", obi1), ("THIN_DEPTH", dep)):
        for tag, sel in (("insample", ins), ("holdout", hold)):
            r, _ = B30.battery(name, S, d, F, sel); r["set"] = tag; rows.append(r)
    Rt = pd.DataFrame(rows)
    H = Rt[Rt.set == "holdout"].copy(); p1 = 1 - stats.norm.cdf(H.fm_t.fillna(-9).to_numpy()); H["bhy"] = A.bhy(p1)
    H["insample_fm_t"] = H.signal.map(Rt[Rt.set == "insample"].set_index("signal").fm_t)
    RES["S2_book"] = H[["signal", "insample_fm_t", "fm_t", "t_ew", "t_vw", "alpha_t", "lag1h_t", "dsort_t", "net_bp", "band_net_bp", "bhy"]].to_dict("records")
    return RES["S2_book"]


def s3_liq():
    ts, codes, X, cm, U = panel()
    from src.data.storage import get_storage
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor(); cur.execute("SELECT symbol, timestamp, long_liq_notional, short_liq_notional FROM liquidation_agg_1h")
        raw = cur.fetchall()
        Lq = pd.DataFrame(raw) if raw and isinstance(raw[0], dict) else pd.DataFrame(raw, columns=["symbol", "timestamp", "long_liq_notional", "short_liq_notional"])
        Lq = Lq.rename(columns={"timestamp": "ts", "long_liq_notional": "ll", "short_liq_notional": "sl"})
    Lq["code"] = Lq.symbol.str.replace("/", "")
    j_of = {c: k for k, c in enumerate(codes)}; t_of = {t: k for k, t in enumerate(ts)}
    lc = X["lc"]; r1 = np.vstack([np.full((1, lc.shape[1]), np.nan), np.diff(lc, axis=0)])
    mkt = np.nanmean(np.where(U, r1, np.nan), 1, keepdims=True); ar = r1 - mkt
    dv = np.nan_to_num(X["dv24"])
    out = {}
    for side, col, sg in (("long_liq", "ll", +1), ("short_liq", "sl", -1)):
        ev = []
        for r in Lq[(Lq[col] >= 2e5)].itertuples():
            j = j_of.get(r.code); k = t_of.get(int(r.ts))
            if j is None or k is None or k + 25 >= len(ts) or not cm[j]:
                continue
            if getattr(r, col) < 0.005 * max(dv[k, j], 1):
                continue
            ev.append((k, j))
        if not ev:
            out[side] = {"n": 0}; continue
        res = {"n": len(ev)}
        for h, (a, b) in {"1_4h": (1, 4), "1_24h": (1, 24)}.items():
            Mx = np.zeros(ar.shape, bool)
            for k, j in ev:
                Mx[k + a:k + b + 1, j] = True                     # the liquidation hour k is the bar ending at ts+1h; start at next bar
            port = pd.Series(np.nanmean(np.where(Mx, ar, np.nan), 1), index=ts).dropna()
            mid = port.index[len(port) // 2] if len(port) else 0
            for tag, sel in (("first_half", port.index < mid), ("second_half", port.index >= mid)):
                m_, t_ = A.nw_t(port[sel], lag=24)
                res[f"{h}_{tag}"] = {"bp_per_hour": m_ * 1e4, "t_signed": sg * t_}
            ev_car = [np.nansum(ar[k + a:k + b + 1, j]) for k, j in ev]
            res[f"{h}_event_mean_car_bp"] = float(np.nanmean(ev_car) * 1e4); res[f"{h}_share_signed_pos"] = float(np.mean(np.sign(ev_car) == sg))
        out[side] = res
    RES["S3_liquidations"] = out
    return out


def s4_grid():
    import b30_rigorous as B30
    ts, codes, X = L.data()
    from b29_battery import crypto_mask
    cm = crypto_mask(codes)
    i0 = np.flatnonzero((ts % D == 0) & (ts >= B30.START) & (ts < B30.END)); i0 = i0[i0 + 25 < len(ts)]
    day = ts[i0] // D
    c = X["c"]; cf = pd.DataFrame(c).ffill().to_numpy(); listed = np.isfinite(c[i0])
    f8 = np.nan_to_num(X["f8"]); fund = np.stack([f8[i + 1:i + 25].sum(0) / 8.0 for i in i0])
    R = np.where(listed, cf[i0 + 24] / c[i0] - 1 - fund, np.nan)
    dv = np.nan_to_num(X["dv24"]); adv30 = pd.DataFrame(dv).rolling(720, min_periods=240).mean().to_numpy()[i0]
    upq = np.nan_to_num(np.asarray(B30.P("up_qv"))); qv = np.nan_to_num(X["qv"])
    hold = day >= B30.HOLD                                   # B30.HOLD is in days
    series, rows = {}, []
    for Lb in (24, 72, 168):
        sh = -(pd.DataFrame(upq).rolling(Lb, min_periods=Lb // 2).sum().to_numpy() / np.maximum(pd.DataFrame(qv).rolling(Lb, min_periods=Lb // 2).sum().to_numpy(), 1))[i0]
        sh = np.where(upq[i0 - 1:i0 + 0].sum(0) > 0 if False else pd.DataFrame(upq).rolling(Lb, min_periods=1).sum().to_numpy()[i0] > 0, sh, np.nan)
        for floor in (2e6, 5e6, 2e7):
            mask = (dv[i0] >= floor) & (X["age"][i0] >= 720) & listed & cm[None, :]
            S = A.winsor_rows(np.where(mask, sh, np.nan))
            for q in (0.2, 0.3):
                for reb in (1, 7):
                    rows_t = np.arange(len(day)) % reb == 0
                    if reb == 1:
                        Rr = R
                    else:
                        Rr = np.full(R.shape, np.nan)
                        for t in np.flatnonzero(rows_t):
                            seg = R[t:t + 7]
                            if len(seg) == 7:
                                Rr[t] = np.prod(1 + np.nan_to_num(seg), 0) - 1
                    for wt in ("EW", "VW"):
                        P = A.sort_portfolios(S[rows_t], Rr[rows_t], W=adv30[rows_t], n=int(round(1 / q)), mask=mask[rows_t])
                        h = A.long_short(P[wt]) / reb                          # per-day units
                        name = f"L{Lb}_f{int(floor / 1e6)}M_q{int(q * 100)}_r{reb}_{wt}"
                        hh = hold[rows_t]
                        m_i, t_i = A.nw_t(h[~hh]); m_h, t_h = A.nw_t(h[hh])
                        rows.append({"variant": name, "in_bp": m_i * 1e4, "in_t": t_i, "hold_bp": m_h * 1e4, "hold_t": t_h})
                        if reb == 1:
                            series[name] = h.to_numpy()
    G = pd.DataFrame(rows)
    M = pd.DataFrame(series).fillna(0.0)
    pbo, lam = A.pbo_cscv(M, S=16)
    srs = M.mean() / M.std(); best = srs.idxmax()
    dsr, _ = A.deflated_sharpe(M[best], len(G), float(srs.var()))
    out = {"n_variants": int(len(G)), "share_hold_t_gt2": float((G.hold_t > 2).mean()), "share_hold_positive": float((G.hold_bp > 0).mean()),
           "share_in_t_gt2": float((G.in_t > 2).mean()), "median_hold_t": float(G.hold_t.median()), "worst_variant": G.loc[G.hold_t.idxmin()].to_dict(),
           "best_variant": G.loc[G.hold_t.idxmax()].to_dict(), "pbo_daily_variants": pbo, "dsr_best_daily": dsr, "best_daily": best}
    G.to_csv(OUT / "s4_grid.csv", index=False)
    RES["S4_korea_grid"] = out
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "overnight.json").exists():
        RES.update(json.load(open(OUT / "overnight.json")))
    only = sys.argv[1:]
    for name, fn in (("S4", s4_grid), ("S2", s2_book), ("S1", s1_unlocks), ("S3", s3_liq)):
        if only and name not in only:
            continue
        try:
            r = fn()
            print(name, "done", json.dumps(r, default=float)[:1500], flush=True)
        except Exception:  # noqa: BLE001
            RES[name + "_error"] = traceback.format_exc()
            print(name, "FAILED", traceback.format_exc(), flush=True)
        json.dump(RES, open(OUT / "overnight.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
