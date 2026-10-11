"""B20_COSTAWARE (registered 2026-10-01, before running). Can a cost-aware construction turn the robust predictive signals
(B21: IC +0.11..+0.14, no sign flips, survives delay) into a book that clears costs?

Signals (fixed in advance, no picking among B19 ids):
  CATX  mean cross-sectional rank of the four CatBoost xs72 models H023/H028/H033/H038 (sign from B19 discovery)
  H013  CatBoost xs24 xven
  VSH   -vshare_up (Upbit volume share)        OIV   +oi_to_volume
Constructions (all: equal weight, +0.5/-0.5 gross, marked every 8h, P&L = w*ret - w*funding - |dw|*(fee + slip(dv24)))
  K0  band: enter top/bottom 20%, keep while inside 30%           (b18_disc2 rule, rebalanced every 8h)
  K1  wide band: enter 10%, keep until the signal crosses the median
  K2  K0 on the liquid universe only (top 30% of dv24 in that row; B21 found IC is higher there and slippage is 2-5bp)
  K3  K1 on the liquid universe
  K4  K3 with the signal averaged over the last 9 rows (3 days)
  K5  K4 + no longs in the top decile of predicted dump risk (H073) + BTC beta hedge
Protocol: DISCOVERY 2024-10-01..2025-12-31 picks ONE construction per signal (highest mean daily net). Only those 4 are
validated on 2026. Pass = discovery daily-net bootstrap CI > 0 for the pick AND 2026 daily-net CI > 0.
2026 has been read before (B17..B21), so a pass here only earns a forward paper test, nothing more.
Output data/reports/b20/costaware.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402

OUT = ROOT / "data/reports/b20"
D0, VAL0, END = (int(pd.Timestamp(d).timestamp()) for d in ("2024-10-01", "2026-01-01", "2026-09-24"))
T0 = int(pd.Timestamp("2024-03-15").timestamp())


def rk(A):
    return pd.DataFrame(A).rank(axis=1, pct=True).to_numpy()


def run(F, rows, X, ent, ext, liquid=False, smooth=1, dump=None, hedge=False, bi=None):
    c, N = X["c"], X["c"].shape[1]
    if smooth > 1:
        S = np.full_like(F, np.nan)
        S[rows] = pd.DataFrame(F[rows]).rolling(smooth, min_periods=smooth // 2 + 1).mean().to_numpy()
        F = S
    w_prev, h_prev, out = np.zeros(N), 0.0, []
    for i in rows:
        if i + 8 >= len(c):
            break
        m = X["U"][i] & np.isfinite(F[i])
        dv = np.nan_to_num(X["dv24"][i])
        if liquid and m.sum():
            m &= dv >= np.quantile(dv[m], 0.7)
        w = np.zeros(N)
        if m.sum() >= 20:
            p = np.full(N, np.nan); p[m] = pd.Series(F[i][m]).rank(pct=True).to_numpy()
            keep_l = (w_prev > 0) & m & (p >= 1 - ext)
            keep_s = (w_prev < 0) & m & (p <= ext)
            Lg, Sg = (m & (p >= 1 - ent)) | keep_l, (m & (p <= ent)) | keep_s
            if dump is not None:
                dd = dump[i]
                ok = np.isfinite(dd) & m
                if ok.sum() > 20:
                    Lg &= ~(ok & (dd >= np.nanquantile(dd[ok], 0.9)))
            if Lg.sum() and Sg.sum():
                w[Lg], w[Sg] = 0.5 / Lg.sum(), -0.5 / Sg.sum()
        g = np.nan_to_num(c[i + 8] / c[i] - 1)
        fund = np.nan_to_num(np.nanmean(X["f8"][i + 1:i + 9], 0))
        dw = np.abs(w - w_prev)
        net = (w * g).sum() - (w * fund).sum() - (dw * (L.FEE + L.slip(dv))).sum()
        if hedge:
            hb = (w * np.nan_to_num(X["beta"][i])).sum()
            net += -hb * g[bi] + hb * fund[bi] - abs(hb - h_prev) * (L.FEE + 0.0002)
            h_prev = hb
        out.append((X["ts"][i] if "ts" in X else i, net, dw.sum()))
        w_prev = w
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    X = dict(X); X["ts"] = ts
    bi = codes.index("BTCUSDT")
    R8 = np.flatnonzero((ts % (8 * 3600) == 0) & (ts >= T0))

    def b19(hid):
        P = np.load(ROOT / f"data/cache/b19/{hid}.npy").astype(np.float32)
        s = json.load(open(ROOT / f"data/reports/b19/{hid}.json"))["sign"]
        full = np.full(X["c"].shape, np.nan, np.float32); full[R8] = s * P
        return full
    sig = {}
    cat = [b19(h) for h in ("H023", "H028", "H033", "H038")]
    A = np.full(X["c"].shape, np.nan); A[R8] = np.nanmean(np.stack([rk(x[R8]) for x in cat]), 0)
    sig["CATX"] = A
    sig["H013"] = b19("H013")
    import disc_engine as DE
    want = {"vshare_up": -1, "oi_to_volume": +1}
    got = {}
    for n, _, f in DE.variables():
        if n in want:
            got[n] = want[n] * np.asarray(f)
        if len(got) == 2:
            break
    sig["VSH"], sig["OIV"] = got["vshare_up"], got["oi_to_volume"]
    dump = b19("H073")

    rows = np.flatnonzero((ts % (8 * 3600) == 0) & (ts >= D0) & (ts < END))
    K = {"K0": dict(ent=0.2, ext=0.3), "K1": dict(ent=0.1, ext=0.5), "K2": dict(ent=0.2, ext=0.3, liquid=True),
         "K3": dict(ent=0.1, ext=0.5, liquid=True), "K4": dict(ent=0.1, ext=0.5, liquid=True, smooth=9),
         "K5": dict(ent=0.1, ext=0.5, liquid=True, smooth=9, dump="H073", hedge=True)}
    res = {}
    for sn, F in sig.items():
        for kn, kw in K.items():
            kw = dict(kw)
            if kw.pop("dump", None):
                kw["dump"] = dump
            o = pd.DataFrame(run(F, rows, X, bi=bi, **kw), columns=["ts", "net", "turn"])
            day = o.groupby(o.ts // 86400).agg(net=("net", "sum"), ts=("ts", "first"))
            dd, dv = day[day.ts < VAL0].net, day[day.ts >= VAL0].net
            r = {"disc_bp": dd.mean() * 1e4, "disc_ci": [x * 1e4 for x in L.boot_ci(dd.to_numpy())],
                 "val_bp": dv.mean() * 1e4, "val_ci": [x * 1e4 for x in L.boot_ci(dv.to_numpy())],
                 "turn_per_8h": float(o.turn.mean()), "val_sharpe": float(dv.mean() / dv.std() * np.sqrt(365))}
            res[f"{sn}|{kn}"] = r
            print(sn, kn, json.dumps({k: (round(v, 2) if isinstance(v, float) else [round(x, 1) for x in v]) for k, v in r.items()}), flush=True)
    picks = {}
    for sn in sig:
        best = max(K, key=lambda k: res[f"{sn}|{k}"]["disc_bp"])
        r = res[f"{sn}|{best}"]
        picks[sn] = {"construction": best, **r, "pass": bool(r["disc_ci"][0] > 0 and r["val_ci"][0] > 0)}
    json.dump({"all": res, "picks": picks}, open(OUT / "costaware.json", "w"), indent=1, default=float)
    f = lambda v: f"{v:+.1f}"  # noqa: E731
    Ls = ["# B20_COSTAWARE (bp per day, daily-net bootstrap 95% CI)", "", "## Pre-registered picks (chosen on discovery only)", "",
          "| signal | pick | disc bp/day [CI] | 2026 bp/day [CI] | 2026 Sharpe | turnover/8h | pass |", "|---|---|---|---|---|---|---|"]
    for sn, p in picks.items():
        Ls.append(f"| {sn} | {p['construction']} | {f(p['disc_bp'])} [{f(p['disc_ci'][0])}, {f(p['disc_ci'][1])}] | {f(p['val_bp'])} [{f(p['val_ci'][0])}, {f(p['val_ci'][1])}] | {p['val_sharpe']:.2f} | {p['turn_per_8h']:.3f} | {'PASS' if p['pass'] else '-'} |")
    Ls += ["", "## All 24 cells (for transparency; only the picks count)", "", "| cell | disc bp/day | 2026 bp/day | turnover/8h |", "|---|---|---|---|"]
    for k, r in res.items():
        Ls.append(f"| {k} | {f(r['disc_bp'])} | {f(r['val_bp'])} | {r['turn_per_8h']:.3f} |")
    (OUT / "costaware.md").write_text("\n".join(Ls) + "\n")
    print("\n".join(Ls))


if __name__ == "__main__":
    main()
