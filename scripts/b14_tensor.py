"""B14 feature tensor (research/batch_B14.yaml). Long format at 4h cadence for the universe, plus a 48h x 12 sequence tensor.
  OMP_NUM_THREADS=4 .venv/bin/python -W ignore scripts/b14_tensor.py
Out: data/cache/b14/tab.parquet (row = coin-4h), data/cache/b14/seq.npy (float16, n x 48 x 12), data/cache/b14/market_daily.parquet
No torch, no lightgbm in this process."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
from b7_lib import M, MN, MX, S, SD, cs_rank, lag, safe_div  # noqa: E402
from b8_study import aligned  # noqa: E402
from b9_b13 import code_index  # noqa: E402

C = ROOT / "data/cache"
OUT = C / "b14"
H_, D_ = 3600, 86400
GROUPS = {}


def g(name, **feats):
    GROUPS.setdefault(name, []).extend(feats.keys())
    return feats


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    ts, codes, X = L.data()
    idx = {c: j for j, c in enumerate(codes)}
    find = code_index()
    T, N = X["c"].shape
    lc, r1, c, qv = X["lc"], X["r1"], X["c"], np.nan_to_num(X["qv"])
    tbq = np.nan_to_num(X["tbq"])
    flow = 2 * tbq - qv
    F = {}
    # ---------------- price / volume
    F.update(g("price_volume",
               ret_1h=r1, ret_6h=lc - lag(lc, 6), ret_24h=lc - lag(lc, 24), ret_7d=lc - lag(lc, 168), ret_28d=lc - lag(lc, 672),
               rv_24h=SD(r1, 24), rv_7d=SD(r1, 168), range_6h=np.log(safe_div(MX(X["h"], 6), MN(X["l"], 6))),
               vsurge_6h=np.log(safe_div(S(qv, 6) + 1, M(qv, 168) * 6 + 1)), taker_6h=safe_div(S(flow, 6), S(qv, 6)),
               taker_24h=safe_div(S(flow, 24), S(qv, 24)),
               qflow_6h=safe_div(S(2 * np.nan_to_num(X["tbq_q"]) - np.nan_to_num(X["qv_q"]), 6), S(np.nan_to_num(X["qv_q"]), 6)),
               ldv=np.log(X["dv24"] + 1), xs_ret24=cs_rank(np.where(X["U"], lc - lag(lc, 24), np.nan)),
               xs_rv=cs_rank(np.where(X["U"], SD(r1, 168), np.nan)), dlo30=c / MN(c, 720) - 1, dhi30=c / MX(c, 720) - 1,
               levy_7d=M(X["levy"], 168), corr_btc_24h=M(X["corr_btc"], 24)))
    # ---------------- positioning
    Mx = aligned(C / "metrics1h", codes, ts, ["oi", "oi_usd", "ls_top_pos", "ls_global", "taker_ratio"], lambda s: (idx.get(s), 1))
    oi = Mx["oi"]
    F.update(g("positioning",
               oi_chg_6h=np.log(safe_div(oi, lag(oi, 6))), oi_chg_24h=np.log(safe_div(oi, lag(oi, 24))),
               oi_to_vol=safe_div(Mx["oi_usd"], X["dv24"]), ls_top=Mx["ls_top_pos"], ls_top_chg=Mx["ls_top_pos"] - lag(Mx["ls_top_pos"], 24),
               ls_global=Mx["ls_global"], ls_global_chg=Mx["ls_global"] - lag(Mx["ls_global"], 24),
               taker_ratio_6h=M(Mx["taker_ratio"], 6), funding=X["f8"], funding_chg=X["f8"] - lag(X["f8"], 24)))
    # ---------------- korean
    U = aligned(C / "upbit1h_hist", codes, ts, ["c", "value_krw"], find)
    B = aligned(C / "bithumb1h_hist", codes, ts, ["c", "value_krw"], find)
    fx = pd.read_parquet(C / "upbit1h_hist/USDT.parquet").set_index("ts")["c"].reindex(ts).ffill(limit=6).to_numpy()[:, None].astype(np.float32)
    up = np.nan_to_num(U["value_krw"]) / fx
    kr = (np.nan_to_num(U["value_krw"]) + np.nan_to_num(B["value_krw"])) / fx
    has_up, has_kr = np.isfinite(U["c"]), np.isfinite(U["c"]) | np.isfinite(B["c"])
    prem = safe_div(U["c"], c * fx) - 1
    prem[np.abs(prem) > 0.5] = np.nan
    bprem = safe_div(B["c"], c * fx) - 1
    bprem[np.abs(bprem) > 0.5] = np.nan
    share = safe_div(S(up, 24), S(up, 24) + S(qv, 24))
    F.update(g("korean",
               upbit_surge_6h=np.where(has_up, np.log(safe_div(S(up, 6) + 1, M(up, 168) * 6 + 1)), np.nan),
               korea_surge_6h=np.where(has_kr, np.log(safe_div(S(kr, 6) + 1, M(kr, 168) * 6 + 1)), np.nan),
               upbit_share_chg=np.where(has_up, share - M(share, 168), np.nan), krw_premium=prem,
               krw_premium_chg_6h=prem - lag(prem, 6), bithumb_premium=bprem, korean_listed=has_kr.astype(np.float32)))
    # ---------------- spot
    Sp = aligned(C / "spot1h_hist", codes, ts, ["c", "qv", "tbq"],
                 lambda s: (idx.get(s) if s in idx else idx.get("1000" + s), 1.0 if s in idx else 1000.0))
    sq = np.nan_to_num(Sp["qv"])
    has_sp = np.isfinite(Sp["c"])
    basis = safe_div(c, Sp["c"]) - 1
    basis[np.abs(basis) > 0.2] = np.nan
    F.update(g("spot",
               spot_share_6h=np.where(has_sp, safe_div(S(sq, 6), S(sq + qv, 6)) - safe_div(S(sq, 168), S(sq + qv, 168)), np.nan),
               spot_taker_6h=np.where(has_sp, safe_div(S(2 * np.nan_to_num(Sp["tbq"]) - sq, 6), S(sq, 6)), np.nan),
               basis=basis, basis_chg_6h=basis - lag(basis, 6)))
    # ---------------- on-chain
    fl = pd.concat([pd.read_parquet(p) for p in sorted((C / "onchain").glob("flows_*.parquet"))])
    fl = fl[fl["code"].isin(idx)]
    Min, Mout, Nin = (np.zeros((T, N), np.float32) for _ in range(3))
    pos = np.searchsorted(ts, fl["ts"].to_numpy())
    ok = (pos < T) & (ts[np.minimum(pos, T - 1)] == fl["ts"].to_numpy())
    jj = fl["code"].map(idx).to_numpy()
    np.add.at(Min, (pos[ok], jj[ok]), fl["inflow"].to_numpy()[ok].astype(np.float32))
    np.add.at(Mout, (pos[ok], jj[ok]), fl["outflow"].to_numpy()[ok].astype(np.float32))
    np.add.at(Nin, (pos[ok], jj[ok]), fl["n_in"].to_numpy()[ok].astype(np.float32))
    has_oc = np.zeros(N, bool)
    has_oc[np.unique(jj)] = True
    ocm = np.where(has_oc[None, :], 1.0, np.nan).astype(np.float32)
    inu, outu = Min * c, Mout * c
    F.update(g("onchain",
               cex_in_6h=safe_div(S(np.nan_to_num(inu), 6), X["dv24"]) * ocm, cex_in_24h=safe_div(S(np.nan_to_num(inu), 24), X["dv24"]) * ocm,
               cex_net_24h=safe_div(S(np.nan_to_num(inu - outu), 24), X["dv24"]) * ocm, cex_n_in_24h=S(Nin, 24) * ocm))
    # ---------------- depth (may be partial while DL5 runs)
    Dp = aligned(C / "depth1h", codes, ts, ["bid_1", "ask_1", "imb_1"], lambda s: (idx.get(s), 1))
    for f in (C / "bookdepth").glob("*.parquet"):
        j = idx.get(f.stem)
        if j is None:
            continue
        d = pd.read_parquet(f)
        if not {"bid_1", "ask_1", "imb_1"} <= set(d.columns):
            continue
        d["h"] = (d["ts"] // 3600 + 1) * 3600
        h = d.groupby("h")[["bid_1", "ask_1", "imb_1"]].mean()
        p = np.searchsorted(ts, h.index.to_numpy())
        k = (p < T) & (ts[np.minimum(p, T - 1)] == h.index.to_numpy())
        for col in ("bid_1", "ask_1", "imb_1"):
            Dp[col][p[k], j] = h[col].to_numpy(np.float32)[k]
    F.update(g("depth",
               ask_chg_24h=np.log(safe_div(Dp["ask_1"], lag(Dp["ask_1"], 24))), bid_chg_24h=np.log(safe_div(Dp["bid_1"], lag(Dp["bid_1"], 24))),
               imb1_6h=M(Dp["imb_1"], 6), depth_to_vol=safe_div(Dp["bid_1"] + Dp["ask_1"], X["dv24"])))
    # ---------------- calendar: unlocks + listing notices
    from dotenv import load_dotenv
    import psycopg
    load_dotenv(ROOT / ".env")
    con = psycopg.connect(os.environ["DATABASE_URL"])
    cg2sym = {cid: s.replace("/USDT", "") for s, cid in con.execute("SELECT DISTINCT symbol, cg_id FROM cg_daily WHERE cg_id IS NOT NULL").fetchall()}
    unl = {}
    for f in (C / "unlocks").glob("*.json"):
        if f.name == "protocols.json":
            continue
        d = json.load(open(f))
        base = cg2sym.get(d.get("gecko_id"))
        j = find(base)[0] if base else None
        if j is None:
            continue
        circ = {}
        for sec in (d.get("documentedData") or {}).get("data", []):
            for p in sec.get("data", []):
                circ[p["timestamp"]] = circ.get(p["timestamp"], 0.0) + float(p.get("unlocked") or 0)
        if not circ:
            continue
        cts = np.array(sorted(circ))
        cval = np.array([circ[t] for t in cts])
        for e in (d.get("metadata") or {}).get("events", []):
            if e.get("unlockType") != "cliff":
                continue
            t = int(e["timestamp"])
            k = np.searchsorted(cts, t - D_) - 1
            if k < 0 or cval[k] <= 0:
                continue
            sz = float(sum(e.get("noOfTokens") or [0])) / cval[k]
            if sz >= 0.01:
                unl.setdefault(j, []).append((t, sz))
    h_next = np.full((T, N), 30 * 24, np.float32)
    sz_next = np.zeros((T, N), np.float32)
    h_since = np.full((T, N), 30 * 24, np.float32)
    for j, ev in unl.items():
        et = np.array(sorted(t for t, _ in ev))
        es = np.array([s for _, s in sorted(ev)])
        k = np.searchsorted(et, ts)                                  # next unlock index
        nxt = np.where(k < len(et), et[np.minimum(k, len(et) - 1)] - ts, 1e9) / H_
        h_next[:, j] = np.minimum(nxt, 30 * 24)
        sz_next[:, j] = np.where(nxt <= 30 * 24, es[np.minimum(k, len(es) - 1)], 0.0)
        prv = np.where(k > 0, ts - et[np.maximum(k - 1, 0)], 1e9) / H_
        h_since[:, j] = np.minimum(prv, 30 * 24)
    since_up = np.full((T, N), 365 * 24, np.float32)
    since_bn = np.full((T, N), 365 * 24, np.float32)
    for src, t, syms in con.execute("SELECT source, ts, symbols FROM exchange_notices WHERE kind='listing'").fetchall():
        t = int(t) // 1000 if int(t) > 1e11 else int(t)
        for s_ in (syms or "").replace(";", ",").split(","):
            s_ = s_.strip().upper().replace("/USDT", "").replace("USDT", "").replace("KRW-", "")
            j = find(s_)[0] if s_ else None
            if j is None:
                continue
            k0 = np.searchsorted(ts, t)
            arr = since_up if src == "upbit" else since_bn
            arr[k0:, j] = np.minimum(arr[k0:, j], (ts[k0:] - t) / H_)
    F.update(g("calendar", h_to_unlock=h_next, unlock_size=sz_next, h_since_unlock=h_since,
               h_since_upbit_listing=np.minimum(since_up, 365 * 24), h_since_binance_listing=np.minimum(since_bn, 365 * 24)))
    # ---------------- size (CoinGecko daily, lagged 1 day)
    mc = np.full((T, N), np.nan, np.float32)
    for f in (C / "cg_hist").glob("*.parquet"):
        base = cg2sym.get(f.stem)
        j = find(base)[0] if base else None
        if j is None:
            continue
        d = pd.read_parquet(f)
        if d.empty:
            continue
        day_ts = (d["ts"] // D_ + 1) * D_ + D_                          # available the day after
        p = np.searchsorted(ts, day_ts.to_numpy())
        for a, v in zip(p, d["mcap"].to_numpy()):
            if a < T and v and v > 0:
                mc[a:a + 24, j] = np.log(v)
    F.update(g("size", log_mcap=mc, turnover=np.exp(np.log(X["dv24"] + 1) - np.nan_to_num(mc, nan=np.nan))))
    # ---------------- market state
    bi = codes.index("BTCUSDT")
    Um = X["U"]
    liq = Um & (X["dv24"] >= 2e7)
    mkt = {"btc_ret_1h": r1[:, bi], "btc_ret_24h": (lc - lag(lc, 24))[:, bi], "btc_ret_7d": (lc - lag(lc, 168))[:, bi],
           "btc_rv_7d": SD(r1, 168)[:, bi], "breadth": np.nanmean(np.where(Um, (r1 >= 0.05), np.nan), 1),
           "mean_funding": np.nanmean(np.where(Um, X["f8"], np.nan), 1),
           "agg_oi_chg_24h": np.nanmean(np.where(liq, F["oi_chg_24h"], np.nan), 1),
           "mean_krw_premium": np.nanmean(np.where(Um, prem, np.nan), 1),
           "upbit_vol_share": safe_div(np.nansum(np.where(Um, S(up, 24), 0), 1), np.nansum(np.where(Um, S(up, 24) + S(qv, 24), 0), 1)),
           "alt_resid_mean": np.nanmean(np.where(Um, r1 - np.nan_to_num(X["beta"]) * r1[:, [bi]], np.nan), 1)}
    MK = pd.DataFrame({k: np.asarray(v, np.float32) for k, v in mkt.items()}, index=ts)
    MK.to_parquet(OUT / "market_hourly.parquet")
    MK.groupby(MK.index // D_).agg({**{k: "last" for k in MK.columns}, "alt_resid_mean": "sum", "breadth": "mean",
                                    "btc_ret_1h": "sum"}).to_parquet(OUT / "market_daily.parquet")
    for k, v in mkt.items():
        F[k] = np.broadcast_to(np.asarray(v, np.float32)[:, None], (T, N))
    GROUPS["market_state"] = list(mkt)
    # ---------------- rows at 4h cadence
    rows = np.flatnonzero((ts % (4 * H_) == 0) & (ts >= L.DISC[0] - 7 * D_) & (ts < L.HOLD[1]))
    ri, cj = np.nonzero(Um[rows])
    I = rows[ri]
    tab = pd.DataFrame({"i": I, "j": cj, "ts": ts[I], "code": np.array(codes)[cj]})
    for k, v in F.items():
        tab[k] = np.asarray(v)[I, cj].astype(np.float32)
    fwd24 = (lag(lc, -24) - lc)
    tab["y_res24"] = X["fwd24_res"][I, cj]
    tab["y_raw24"] = fwd24[I, cj]
    fwdmax6 = np.full((T, N), np.nan, np.float32)
    for k in range(1, 7):
        fwdmax6 = np.fmax(fwdmax6, lag(c, -k))
    tab["y_pump6"] = ((fwdmax6 / c - 1) >= 0.20)[I, cj].astype(np.int8)
    tab["cost"] = (2 * (L.FEE + L.slip(np.nan_to_num(X["dv24"][I, cj])))).astype(np.float32)
    fund = np.zeros(len(I), np.float32)
    for k in range(1, 25):
        fund += np.nan_to_num(X["f8"][np.minimum(I + k, T - 1), cj]) / 8
    tab["fund24"] = fund
    tab = tab.dropna(subset=["y_res24"]).reset_index(drop=True)
    tab["seq_idx"] = np.arange(len(tab))
    tab.to_parquet(OUT / "tab.parquet", index=False)
    json.dump(GROUPS, open(OUT / "groups.json", "w"), indent=1)
    # ---------------- sequence tensor 48h x 12
    ch = [r1, np.log(qv + 1) - M(np.log(qv + 1), 168), safe_div(flow, qv), np.log(safe_div(oi, lag(oi, 1))), Mx["ls_top_pos"], X["f8"],
          safe_div(kr, kr + qv), safe_div(sq, sq + qv), prem, safe_div(np.nan_to_num(inu), X["dv24"] / 24) * ocm,
          np.broadcast_to(r1[:, [bi]], (T, N)), np.broadcast_to(np.asarray(mkt["breadth"], np.float32)[:, None], (T, N))]
    seq = np.zeros((len(tab), 48, 12), np.float16)
    Ii, Jj = tab["i"].to_numpy(), tab["j"].to_numpy()
    for k, a in enumerate(ch):
        a = np.asarray(a, np.float32)
        sc = np.nanstd(a[::40]) + 1e-9
        for h in range(48):
            seq[:, h, k] = np.nan_to_num(a[Ii - 47 + h, Jj] / sc).astype(np.float16)
    np.save(OUT / "seq.npy", seq)
    print("tab", tab.shape, "seq", seq.shape, "groups", {k: len(v) for k, v in GROUPS.items()})
    print("coverage", {k: round(float(tab[k].notna().mean()), 3) for k in F if tab[k].isna().mean() > 0.02})


if __name__ == "__main__":
    build()
