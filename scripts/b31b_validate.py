"""B31b (2026-10-01): adversarial checks on the B31 confirmations before anything goes to a forward test.
A2 funding post-settlement:
  placebo windows: same signal, same coins, 2-hour windows that do NOT contain a settlement (s+2..s+4, s+4..s+6, s-4..s-2)
  -> effect must be specific to the settlement window; by settlement hour (00/08/16 UTC); by year; by funding sign
  (positive vs negative funding coins); tradability: HML net of 2 x (fee + slippage) taker and maker (2bp/side)
D2 Upbit-listing fade: event list, first-day distribution (data-start artefacts), CAR path days 1..10, median vs mean,
  share of events negative, top-5 events removed, short-perp net of funding and 12bp round trip.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import aphx as A  # noqa: E402
import b7_lib as L  # noqa: E402
from b31_newideas import START, END, HOLD, D, rank_rows, xs_slope, hml_rows  # noqa: E402

OUT = ROOT / "data/reports/b31"


def main():
    ts, codes, X = L.data()
    from b29_battery import crypto_mask
    cm = crypto_mask(codes)
    lc = X["lc"].astype(np.float64); dv = np.nan_to_num(X["dv24"]); f8 = X["f8"]
    U = (dv >= 5e6) & (X["age"] >= 720) & cm[None, :] & np.isfinite(lc)
    hrs = np.flatnonzero((ts % (8 * 3600) == 0) & (ts >= START + 3 * D) & (ts < END))
    s = hrs[(hrs >= 30) & (hrs + 8 < len(ts))]
    fr = rank_rows(f8[s - 2]); M = U[s - 2] & np.isfinite(f8[s - 2])
    hold = ts[s] >= HOLD
    R = {}
    for nm, (a, b) in {"settle s-1..s+1 (A2)": (-1, 1), "placebo s+1..s+3": (1, 3), "placebo s+3..s+5": (3, 5),
                       "placebo s-5..s-3": (-5, -3), "pre s-2..s-1 (A1)": (-2, -1)}.items():
        Y = lc[s + b] - lc[s + a]
        sl = xs_slope(Y, fr, M); h = hml_rows(f8[s - 2], Y, M)
        R[nm] = {"fm_t_all": A.nw_t(sl)[1], "fm_t_in": A.nw_t(sl[~hold])[1], "fm_t_hold": A.nw_t(sl[hold])[1],
                 "hml_bp_hold": float(np.nanmean(h[hold]) * 1e4), "hml_t_hold": A.nw_t(h[hold])[1]}
    Y = lc[s + 1] - lc[s - 1]; h = hml_rows(f8[s - 2], Y, M)
    hour = (ts[s] % D) // 3600
    by = {f"{hh:02d}UTC": {"bp": float(np.nanmean(h[hold & (hour == hh)]) * 1e4), "t": A.nw_t(h[hold & (hour == hh)])[1]} for hh in (0, 8, 16)}
    yr = pd.to_datetime(ts[s], unit="s").year
    byyr = {int(y): {"bp": float(np.nanmean(h[yr == y]) * 1e4), "t": A.nw_t(h[yr == y])[1]} for y in np.unique(yr)}
    # sign split: among positive-funding coins only, and negative-funding only
    Mp, Mn = M & (f8[s - 2] > 0), M & (f8[s - 2] < 0)
    sign_split = {"pos_only_t": A.nw_t(xs_slope(Y, fr, Mp)[hold])[1], "neg_only_t": A.nw_t(xs_slope(Y, fr, Mn)[hold])[1]}
    cost = (L.FEE + L.slip(dv[s - 2]))
    S_ = np.where(M & np.isfinite(Y), f8[s - 2], np.nan)
    lo = np.nanquantile(S_, 0.2, axis=1, keepdims=True); hi = np.nanquantile(S_, 0.8, axis=1, keepdims=True)
    c_taker = 2 * (np.nanmean(np.where(S_ >= hi, cost, np.nan), 1) + np.nanmean(np.where(S_ <= lo, cost, np.nan), 1)) / 2
    net_t = h - c_taker; net_m = h - 2 * 0.0002
    trade = {"gross_bp_hold": float(np.nanmean(h[hold]) * 1e4), "net_taker_bp_hold": float(np.nanmean(net_t[hold]) * 1e4),
             "net_taker_t": A.nw_t(net_t[hold])[1], "net_maker_bp_hold": float(np.nanmean(net_m[hold]) * 1e4), "net_maker_t": A.nw_t(net_m[hold])[1],
             "per_day_maker_bp": float(np.nanmean(net_m[hold]) * 1e4 * 3)}
    # ---------------- D2 detail
    i0 = np.flatnonzero((ts % D == 0) & (ts >= START) & (ts < END)); i0 = i0[i0 + 24 < len(ts)]
    cf = pd.DataFrame(X["c"]).ffill().to_numpy()
    dret = np.where(np.isfinite(X["c"][i0]), cf[i0 + 24] / X["c"][i0] - 1, np.nan)
    Ux = U.copy(); Ux[:, codes.index("BTCUSDT")] = False
    mkt = np.nanmean(np.where(Ux[i0], dret, np.nan), 1, keepdims=True); ar = dret - mkt
    fund = np.stack([np.nan_to_num(f8)[i + 1:i + 25].sum(0) / 8.0 for i in i0])
    upq = np.asarray(np.load(ROOT / "data/cache/disc/prim_up_qv.npy", mmap_mode="r"))
    upd = pd.DataFrame(np.nan_to_num(upq)).rolling(24, min_periods=1).sum().to_numpy()[i0] > 0
    age_d = X["age"][i0] / 24.0
    ev = []
    for j in range(len(codes)):
        w = np.flatnonzero(upd[:, j])
        if len(w) and w[0] > 0 and age_d[w[0], j] >= 30:
            f0 = w[0]; seg = ar[f0 + 1:f0 + 11, j]; sf = fund[f0 + 1:f0 + 11, j]
            if len(seg) == 10 and np.isfinite(seg).sum() >= 8:
                ev.append({"code": codes[j], "date": str(pd.to_datetime(ts[i0[f0]], unit="s").date()), "day0_ar": float(ar[f0, j]),
                           "car_1_10": float(np.nansum(seg)), "car_1_3": float(np.nansum(seg[:3])), "car_4_10": float(np.nansum(seg[3:])),
                           "short_net": float(-np.nansum(seg) + np.nansum(sf) - 0.0012), "hold": bool(ts[i0[f0]] >= HOLD)})
    E = pd.DataFrame(ev)
    first_dates = E.date.value_counts().head(5).to_dict()
    def ev_stats(e):
        return {"n": int(len(e)), "mean_car": float(e.car_1_10.mean()), "median_car": float(e.car_1_10.median()),
                "share_neg": float((e.car_1_10 < 0).mean()), "t": float(e.car_1_10.mean() / (e.car_1_10.std() / np.sqrt(len(e)))),
                "car_1_3": float(e.car_1_3.mean()), "car_4_10": float(e.car_4_10.mean()),
                "short_net_mean": float(e.short_net.mean()), "mean_without_top5": float(e.car_1_10.sort_values().iloc[:-5].mean()),
                "mean_without_bottom5": float(e.car_1_10.sort_values().iloc[5:].mean()), "day0_mean": float(e.day0_ar.mean())}
    D2 = {"all": ev_stats(E), "insample": ev_stats(E[~E.hold]), "holdout": ev_stats(E[E.hold]), "most_common_dates": first_dates}
    E.to_csv(OUT / "d2_events.csv", index=False)
    Rj = {"A2_windows": R, "A2_by_hour_holdout": by, "A2_by_year": byyr, "A2_sign_split": sign_split, "A2_trade": trade, "D2": D2}
    json.dump(Rj, open(OUT / "validate.json", "w"), indent=1, default=float)
    print(json.dumps(Rj, indent=1, default=lambda v: round(float(v), 4)))


if __name__ == "__main__":
    main()
