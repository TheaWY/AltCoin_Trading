"""Variable discovery engine, pass 1 (2026-09-29).
Idea: new variables come from RELATIONSHIPS BETWEEN SOURCES nobody else joins, not from reprocessing one feed. This builds every sensible
cross-source variable on the hourly perp-panel grid (row t = bar CLOSE at ts[t]), removes what known factors already explain, and tests
the residual against forward perp returns with a discovery / validation split.
  OMP_NUM_THREADS=6 .venv/bin/python -u -W ignore scripts/disc_engine.py [build|scan|all]
    -> data/cache/disc/prim_*.npy, data/reports/disc/pass1.json, pass1_table.csv
Sources and timestamp conventions (all verified to be close-stamped, see research/b7_b13_results.md timestamp audit):
  b7 perp panel (lc, qv, tbq, n, f8 funding, dv24, rv, beta, U) | metrics1h (oi_usd, ls_top_pos, ls_global, ls_top_acct, taker_ratio)
  spot1h_hist (c, qv, tbq) | upbit1h_hist / bithumb1h_hist (c in KRW, value_krw) + Upbit USDT/KRW | onchain flows (hour close = +3600)
  depth1h (hourly mean of bookDepth snapshots, hour close; ends 2026-03-24)
Known factors removed each hour (cross-sectional Gaussian-rank OLS): r1, r24, r168, rv, log dv24, volume surprise, funding, OI change 24h, beta.
Test: Spearman rank IC of the residual vs forward simple return (h = 1, 4, 24 h; entry at the close of row t), universe U & dv24 >= $2M.
IC sampled every h hours (non-overlapping), aggregated per day; t-stat with a weekly block bootstrap. Discovery = 2024-03..2025-12,
validation = 2026. Selection: BH q=0.05 on discovery over all (variable x horizon); confirmation: validation same sign with CI excluding 0.
Survivors get a long-short quintile spread net of 2 x (fee + slippage) x turnover, per period."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b8_study import aligned  # noqa: E402

C = M.C
CD, OUT = C / "disc", ROOT / "data/reports/disc"
VAL0 = 1767225600
RNG = np.random.default_rng(2029)
HZ = (1, 4, 24)


def lagd(a, k):
    out = np.full_like(a, np.nan); out[k:] = a[:-k]; return out


def diffk(a, k):
    return a - lagd(a, k)


def rollmean(a, w):
    return pd.DataFrame(a).rolling(w, min_periods=max(2, w // 2)).mean().to_numpy(np.float32)


def rollz(a, w=168):
    df = pd.DataFrame(a); m = df.shift(1).rolling(w, min_periods=w // 2).mean(); s = df.shift(1).rolling(w, min_periods=w // 2).std()
    return ((df - m) / s.replace(0, np.nan)).to_numpy(np.float32)


def base_key(idx):
    def f(stem):
        for c in (f"{stem}USDT", f"1000{stem}USDT", f"1000000{stem}USDT"):
            if c in idx:
                return idx[c], 1
        return None, 1
    return f


def build():
    CD.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data(); idx = {c: j for j, c in enumerate(codes)}; T, N = len(ts), len(codes)
    P = {k: X[k].astype(np.float32) for k in ("lc", "qv", "tbq", "n", "f8", "dv24", "rv", "beta")}
    P["U"] = (X["U"] & (np.nan_to_num(X["dv24"]) >= 2e6)).astype(np.float32)
    Mx = aligned(C / "metrics1h", codes, ts, ["oi_usd", "ls_top_pos", "ls_global", "ls_top_acct", "taker_ratio"], lambda s: (idx.get(s), 1))
    P.update({f"m_{k}": v for k, v in Mx.items()})
    Sp = aligned(C / "spot1h_hist", codes, ts, ["c", "qv", "tbq"], lambda s: (idx.get(s), 1))
    P.update({"sp_lc": np.log(np.where(Sp["c"] > 0, Sp["c"], np.nan)).astype(np.float32), "sp_qv": Sp["qv"], "sp_tbq": Sp["tbq"]})
    usd = pd.read_parquet(C / "upbit1h_hist/USDT.parquet").drop_duplicates("ts").set_index("ts")["c"].reindex(ts).ffill(limit=3).to_numpy(np.float32)
    for ven, d in (("up", "upbit1h_hist"), ("bt", "bithumb1h_hist")):
        K = aligned(C / d, codes, ts, ["c", "value_krw"], base_key(idx))
        P[f"{ven}_lc"] = (np.log(np.where(K["c"] > 0, K["c"], np.nan)) - np.log(usd)[:, None]).astype(np.float32)   # log USD price (constant offsets cancel in diffs)
        P[f"{ven}_qv"] = (K["value_krw"] / usd[:, None]).astype(np.float32)
    import glob
    O = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(C / "onchain/flows_*.parquet")))])
    for col in ("inflow", "outflow"):
        a = np.full((T, N), np.nan, np.float32)
        for code, g in O.groupby("code"):
            j = idx.get(code)
            if j is None:
                continue
            pos = np.searchsorted(ts, g["ts"].to_numpy()); ok = (pos < T) & (ts[np.minimum(pos, T - 1)] == g["ts"].to_numpy())
            a[pos[ok], j] = np.log1p(g[col].to_numpy(np.float64)[ok])
        cov = np.isfinite(a).any(0); a[:, cov] = np.nan_to_num(a[:, cov], nan=0.0)                # covered coins: missing hour = no transfers
        P[f"oc_{col}"] = a
    D = aligned(C / "depth1h", codes, ts, ["bid_1", "ask_1", "imb_1", "imb_5"], lambda s: (idx.get(s), 1))
    P.update({f"dp_{k}": v for k, v in D.items()})
    for k, v in P.items():
        np.save(CD / f"prim_{k}.npy", v.astype(np.float32))
    np.save(CD / "ts.npy", ts); json.dump(list(codes), open(CD / "codes.json", "w"))
    cover = {k: float(np.isfinite(v[P["U"] > 0]).mean()) for k, v in P.items()}
    print("primitives", len(P), json.dumps({k: round(v, 3) for k, v in cover.items()}), flush=True)


def prim(k):
    return np.load(CD / f"prim_{k}.npy", mmap_mode="r")


def share(a, b):
    return (a / (a + b)).astype(np.float32)


def variables():
    """yields (name, family, T x N array). Every variable uses data known at the close of row t only."""
    lc = np.asarray(prim("lc")); qv = np.asarray(prim("qv")); tbq = np.asarray(prim("tbq"))
    pt = share(tbq, qv - tbq)                                                                          # perp taker-buy share
    for ven, fam in (("sp", "spot"), ("up", "upbit"), ("bt", "bithumb")):
        v = np.asarray(prim(f"{ven}_lc")); vq = np.asarray(prim(f"{ven}_qv"))
        for w in (1, 4, 24, 168):
            yield f"div_{ven}_perp_{w}h", fam, (diffk(v, w) - diffk(lc, w)).astype(np.float32)        # venue move not (yet) in the perp
        s = share(vq, qv)
        yield f"vshare_{ven}", fam, s
        yield f"vshare_{ven}_chg", fam, (rollmean(s, 24) - rollmean(s, 168)).astype(np.float32)
        yield f"vshare_{ven}_z", fam, rollz(np.log(vq / qv))
        if ven == "sp":
            yield "basis_spot_perp", fam, (v - lc).astype(np.float32)
            yield "basis_spot_perp_z", fam, rollz(v - lc)
            st = share(np.asarray(prim("sp_tbq")), vq - np.asarray(prim("sp_tbq")))
            for w in (1, 4, 24):
                yield f"taker_gap_spot_perp_{w}h", fam, (rollmean(st, w) - rollmean(pt, w)).astype(np.float32)
    up = np.asarray(prim("up_lc")); bt = np.asarray(prim("bt_lc"))
    for w in (1, 4, 24):
        yield f"div_bithumb_upbit_{w}h", "korea", (diffk(bt, w) - diffk(up, w)).astype(np.float32)
    kq = np.nan_to_num(np.asarray(prim("up_qv"))) + np.nan_to_num(np.asarray(prim("bt_qv")))
    kq[kq == 0] = np.nan
    yield "vshare_korea", "korea", share(kq, qv)
    yield "vshare_korea_chg", "korea", (rollmean(share(kq, qv), 24) - rollmean(share(kq, qv), 168)).astype(np.float32)
    # positioning: smart (top traders) vs crowd, and positioning vs price
    tp, gl, ta, tr = (np.asarray(prim(k)) for k in ("m_ls_top_pos", "m_ls_global", "m_ls_top_acct", "m_taker_ratio"))
    yield "ls_smart_minus_crowd", "positioning", (np.log(tp) - np.log(gl)).astype(np.float32)
    for w in (4, 24):
        yield f"ls_smart_minus_crowd_chg_{w}h", "positioning", diffk((np.log(tp) - np.log(gl)).astype(np.float32), w)
        yield f"ls_crowd_against_price_{w}h", "positioning", (diffk(np.log(gl), w) * -np.sign(diffk(lc, w))).astype(np.float32)   # crowd adds longs into a fall / shorts into a rise
    yield "ls_pos_minus_acct", "positioning", (np.log(tp) - np.log(ta)).astype(np.float32)                    # top traders: size-weighted vs headcount
    yield "taker_ratio_minus_kline_taker", "positioning", (np.log(tr) - np.log(pt / (1 - pt))).astype(np.float32)
    oi = np.log(np.asarray(prim("m_oi_usd")))
    for w in (4, 24, 168):
        yield f"oi_vs_price_{w}h", "positioning", (diffk(oi, w) - diffk(lc, w)).astype(np.float32)
    dv = np.asarray(prim("dv24"))
    yield "oi_to_volume", "positioning", (oi - np.log(dv)).astype(np.float32)
    yield "oi_to_volume_z", "positioning", rollz(oi - np.log(dv))
    f8 = np.asarray(prim("f8"))
    yield "funding_vs_premium_z", "carry", (rollz(f8) - rollz(lc - np.asarray(prim("sp_lc")))).astype(np.float32)   # funding rich/cheap vs the live perp premium
    yield "funding_vs_oi_chg", "carry", (rollz(f8) - rollz(diffk(oi, 24))).astype(np.float32)
    yield "funding_vs_momentum", "carry", (rollz(f8) - rollz(diffk(lc, 24))).astype(np.float32)

    # on-chain exchange flows (hour close; +3600 already applied in the source query)
    oin, oout = np.asarray(prim("oc_inflow")), np.asarray(prim("oc_outflow"))
    net = (oin - oout).astype(np.float32)
    for w in (24, 168):
        yield f"oc_netflow_{w}h", "onchain", rollmean(net, w)
    yield "oc_inflow_z", "onchain", rollz(oin)
    yield "oc_netflow_z", "onchain", rollz(net)
    yield "oc_inflow_vs_volume", "onchain", (rollmean(oin, 24) - np.log1p(np.nan_to_num(dv))).astype(np.float32)
    # order-book depth (hourly mean, ends 2026-03-24 -> validation coverage is partial)
    for k in ("imb_1", "imb_5"):
        d = np.asarray(prim(f"dp_{k}"))
        yield f"depth_{k}", "depth", d
        yield f"depth_{k}_chg24", "depth", diffk(d, 24)
    b1, a1 = np.asarray(prim("dp_bid_1")), np.asarray(prim("dp_ask_1"))
    yield "depth_bid_ask_log", "depth", (np.log(b1) - np.log(a1)).astype(np.float32)


# ------------------------------------------------------------------ scan
def extra_known():
    """Known factors on top of b7_lib.known_factors(): volume surprise, OI change 24h, BTC beta."""
    _, _, X = L.data()
    qv = np.nan_to_num(X["qv"])
    vs = np.log1p(L.S(qv, 24)) - np.log1p(L.S(qv, 168) / 7.0)
    oi = np.log(np.asarray(prim("m_oi_usd")))
    return {"vol_surprise": vs.astype(np.float32), "oi_chg_24h": diffk(oi, 24), "beta": X["beta"]}


def p_two_sided(ic):
    from math import erf, sqrt
    ic = np.asarray(ic); ic = ic[np.isfinite(ic)]
    if len(ic) < 30 or ic.std() == 0:
        return 1.0
    # weekly blocks: t-stat on weekly means (days are autocorrelated)
    wk = [ic[i:i + 7].mean() for i in range(0, len(ic) - 6, 7)]
    t = np.mean(wk) / (np.std(wk, ddof=1) / np.sqrt(len(wk)))
    return float(2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2)))))


def bh(p, q=0.05):
    p = np.asarray(p); n = len(p); o = np.argsort(p); thr = q * (np.arange(1, n + 1) / n)
    passed = p[o] <= thr; k = np.flatnonzero(passed).max() + 1 if passed.any() else 0
    out = np.zeros(n, bool); out[o[:k]] = True
    return out


def scan():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    now = int(ts[-8])
    DISC, VALI = (1711929600, VAL0), (VAL0, now)          # 2024-04-01 .. 2025-12-31 | 2026-01-01 .. end of panel
    K = dict(L.known_factors()); K.update(extra_known())
    rows_d, rows_v = L.eval_rows(DISC), L.eval_rows(VALI)
    res = []
    for name, fam, F in variables():
        F = np.where(np.isfinite(F), F, np.nan).astype(np.float32)
        cov = float(np.isfinite(F[rows_v][X["U"][rows_v]]).mean()) if len(rows_v) else 0.0
        r = {"var": name, "family": fam, "val_coverage": round(cov, 3)}
        for tag, rows in (("disc", rows_d), ("val", rows_v)):
            try:
                R = L.residualise(F, rows, K)
                nic = L.daily(L.ic_series(R, rows))
                raw = L.daily(L.ic_series(F, rows))
                r.update({f"{tag}_ic": float(raw.mean()), f"{tag}_novel_ic": float(nic.mean()), f"{tag}_novel_ci": L.boot_ci(nic.to_numpy()),
                          f"{tag}_days": int(len(nic)), f"{tag}_p": p_two_sided(nic.to_numpy())})
            except Exception as e:  # noqa: BLE001
                r[f"{tag}_err"] = repr(e)[:120]
        res.append(r)
        print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k in ("var", "disc_novel_ic", "disc_p", "val_novel_ic", "val_coverage")}), flush=True)
        json.dump(res, open(OUT / "pass1_partial.json", "w"))
    ok = [i for i, r in enumerate(res) if "disc_p" in r]
    sel = bh([res[i]["disc_p"] for i in ok], 0.05)
    for j, i in enumerate(ok):
        r = res[i]; r["disc_bh"] = bool(sel[j])
        ci = r.get("val_novel_ci", [0, 0])
        r["val_confirm"] = bool(sel[j] and r.get("val_days", 0) >= 60 and np.sign(r["val_novel_ic"]) == np.sign(r["disc_novel_ic"])
                                and (ci[0] > 0 or ci[1] < 0))
    surv = [r for r in res if r.get("val_confirm")]
    for r in surv:                                        # tradability: quintile L/S net of fee + slippage, validation period
        F = dict((n, f) for n, _, f in variables() if n == r["var"])[r["var"]]
        ev = L.evaluate(np.sign(r["disc_novel_ic"]) * F, VALI, K=None, with_ls=True)
        r.update({k: ev[k] for k in ("ls_net_day", "ls_net_ci", "ls_sharpe", "ls_hedged_day", "ls_hedged_ci", "turnover_day")})
    summary = {"n_vars": len(res), "n_disc_bh": int(sum(r.get("disc_bh", False) for r in res)), "n_confirmed": len(surv),
               "confirmed": [r["var"] for r in surv], "periods": {"disc": DISC, "val": VALI}, "known_factors": list(K)}
    json.dump({"summary": summary, "rows": res}, open(OUT / "pass1.json", "w"), indent=1, default=float)
    pd.DataFrame(res).to_csv(OUT / "pass1_table.csv", index=False)
    print("SUMMARY", json.dumps(summary, default=float), flush=True)


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("build", "all") and not (CD / "prim_lc.npy").exists():
        build()
    if step in ("scan", "all"):
        scan()
