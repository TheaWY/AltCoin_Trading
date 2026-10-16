"""B17 F16 Korea (registry F16_korea_001..012; 010 needs the orderbook recorder -> not_run).
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f16.py   -> data/reports/b17/f16.json
Data: B15 pumps + minute paths (index 60 = onset minute m0, 61 = entry m0+1); upbit1h_hist / bithumb1h_hist (ts = bar close, checked);
Upbit USDT/KRW hourly (from 2024-06-07) for the kimchi premium; b7 perp panel; Upbit '거래 유의 종목' designation / release notices
(notices/upbit_trade.parquet); kr1m_hist (2026-07-27..09-23) for 012. All Korean hourly values are taken at the LAST CLOSED hour before
the decision time (no look-ahead; up to 59 min stale). Deviation: the registry's 5-minute Upbit share (003) uses the hourly share, and
007 'precursor' is tested on pumps (Korean-listed & thin book at onset) rather than as a universe scan.
Each statement = flagged vs other pumps, trade in the stated direction, day-clustered bootstrap (b17_f20.contrast):
  001 premium > 3% at onset -> continues less: SHORT m0+1, exit 1h / 24h
  002 premium rising (first closed hour after onset vs last before) -> continue: LONG at that hour close, exit +1h / +24h
  003 Upbit share of (Upbit + perp) quote volume in the first closed hour after onset > 30% -> fade: SHORT at that close, +1h / +24h
  004 onset within 10 min after KST 09:00 -> continue: LONG m0+1, 1h / 24h
  005 Upbit warning designation active at onset -> SHORT after peak (dd3 trigger, 5% stop, +4h; close-based stop, contrast only)
  006 Bithumb-led (Bithumb return > Upbit return in the hour before onset) -> larger pump: LONG m0+1, 24h
  007 Korean-listed & thin book (depth1h / dv24 bottom quintile) -> LONG m0+1, 1h / 24h
  008 Korean-unlisted -> retrace faster: SHORT m0+60, exit m0+24h (unlisted vs listed)
  009 USDT/KRW move > 0.3% in the onset hour -> LONG m0+1, 1h / 24h
  011 Korean night onset (KST 01-06) -> dump deeper: SHORT after peak (dd3, 5% stop, +4h)
  012 Korean open gap (Upbit 09:00 open vs 08:59 close, |gap| > 2%) -> perp follows over the next hour (kr1m_hist, validation only)
Pass: flagged trade CI > 0 AND flagged-minus-other CI > 0 on validation (2026); BH q=0.10 over the runnable cells."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

os.environ["B2_ERA"] = "all"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b2_panel as bp  # noqa: E402
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f03 import bh, leg, load  # noqa: E402
from b17_f20 import contrast, depth_ratio  # noqa: E402

C, OUT = M.C, ROOT / "data/reports/b17"
VAL0, H = 1767225600, 3600


def series(d, col="c"):
    out = {}
    for fp in (C / d).glob("*.parquet"):
        x = pd.read_parquet(fp).drop_duplicates("ts").sort_values("ts")
        out[fp.stem] = (x["ts"].to_numpy(np.int64), x[col].to_numpy(float), x["value_krw"].to_numpy(float) if "value_krw" in x else None)
    return out


def at(s, t, off=0):
    """value of the last bar closed at or before t (off=+1 -> the next bar); returns (close_ts, value, value_krw) or None."""
    ts, c, v = s; k = np.searchsorted(ts, t, "right") - 1 + off
    if k < 0 or k >= len(ts):
        return None
    return ts[k], c[k], (v[k] if v is not None else np.nan)


def base_of(code):
    b = code[:-4]
    for p in ("1000000", "1000"):
        if b.startswith(p):
            return b[len(p):]
    return b


def warnings():
    N = pd.read_parquet(C / "notices/upbit_trade.parquet").sort_values("ts")
    ev = []
    for r in N.itertuples(index=False):
        if "거래 유의" not in r.title:
            continue
        sym = r.title.split("(")[-1].split(")")[0] if "(" in r.title else None
        if not sym:
            continue
        ev.append((sym, int(r.ts), 0 if "해제" in r.title else 1))
    W = {}
    for sym, t, on in ev:
        W.setdefault(sym, []).append((t, on))
    return W


def active_warning(W, base, t):
    st = 0
    for tt, on in W.get(base, []):
        if tt > t:
            break
        st = on
    return st == 1


def short_after_peak(A, i, cost):
    x = np.asarray(A[i, 60:, 0], np.float64); run = np.maximum.accumulate(x); dd = (1 + x) / (1 + run) - 1
    j = np.flatnonzero(dd <= -0.03); j = j[j >= 3]
    if len(j) and j[0] + 242 < 1440:
        g, _, _ = leg(A, i, int(j[0]) + 1, "t4h", 0.05, -1); return g - cost
    return np.nan


def compute():
    P, A = load(); n = len(P); t0 = P["ts"].to_numpy(np.int64); day = t0 // 86400; val = t0 >= VAL0
    cr = np.asarray(A[:, :, 0], np.float64); cost = P["cost"].to_numpy()
    ts_h, codes, X = L.data(); ci = {c: i for i, c in enumerate(codes)}; hpos = {int(t): k for k, t in enumerate(ts_h)}
    UP, BT = series("upbit1h_hist"), series("bithumb1h_hist"); USD = UP.get("USDT"); W = warnings()
    def idx_of(close_t, i):                                   # path index of the minute that closes at close_t
        m = 60 + (int(close_t) - 60 - int(t0[i])) // 60
        return m if 61 <= m <= 1500 else None
    def g(i, a, b):
        return (1 + cr[i, b]) / (1 + cr[i, a]) - 1
    F = {k: np.full(n, np.nan) for k in ("prem0", "dprem", "share", "bt_minus_up", "usd_chg")}
    E = {k: np.full(n, np.nan) for k in ("long1", "long24", "short1", "short24", "sap", "l2_1", "l2_24", "s3_1", "s3_24", "s60_24")}
    listed = np.zeros(n, bool); warn = np.zeros(n, bool)
    for i in range(n):
        code = P["code"].iloc[i]; b = base_of(code); dec = int(t0[i]) + 60
        E["long1"][i] = g(i, 61, 121) - cost[i]; E["long24"][i] = g(i, 61, 1500) - cost[i]
        E["short1"][i] = -g(i, 61, 121) - cost[i]; E["short24"][i] = -g(i, 61, 1500) - cost[i]
        E["s60_24"][i] = -g(i, 120, 1500) - cost[i]; E["sap"][i] = short_after_peak(A, i, cost[i])
        warn[i] = active_warning(W, b, dec)
        if b in UP:
            u0 = at(UP[b], dec); listed[i] = u0 is not None and dec - u0[0] < 6 * H
            if listed[i] and USD is not None and code in ci:
                j = ci[code]
                def prem(u):
                    k = hpos.get(int(u[0])); x = at(USD, u[0])
                    return u[1] / (np.exp(X["lc"][k, j]) * x[1]) - 1 if k is not None and x is not None and x[0] == u[0] else np.nan
                F["prem0"][i] = prem(u0)
                u1 = at(UP[b], dec, 1)
                if u1 is not None and idx_of(u1[0], i) is not None and u1[0] - dec <= H:
                    m1 = idx_of(u1[0], i); F["dprem"][i] = prem(u1) - F["prem0"][i]
                    k1 = hpos.get(int(u1[0])); x1 = at(USD, u1[0])
                    if k1 is not None and x1 is not None:
                        up_usd = u1[2] / x1[1]; F["share"][i] = up_usd / (up_usd + float(np.nan_to_num(X["qv"][k1, j])))
                    if m1 + 60 <= 1500:
                        E["l2_1"][i] = g(i, m1, m1 + 60) - cost[i]; E["s3_1"][i] = -g(i, m1, m1 + 60) - cost[i]
                    if m1 + 1440 <= 1500:
                        E["l2_24"][i] = g(i, m1, 1500) - cost[i]; E["s3_24"][i] = -g(i, m1, 1500) - cost[i]
            if b in BT:
                bt = at(BT[b], dec); btp = at(BT[b], dec, -1); up_p = at(UP[b], dec, -1)
                if bt and btp and up_p and u0 and bt[0] == u0[0]:
                    F["bt_minus_up"][i] = (bt[1] / btp[1] - 1) - (u0[1] / up_p[1] - 1)
        if USD is not None:
            x0 = at(USD, dec); xp = at(USD, dec, -1)
            if x0 and xp and dec - x0[0] < 2 * H:
                F["usd_chg"][i] = x0[1] / xp[1] - 1
    d2v = np.full(n, np.nan)
    for cde, gg in P.groupby("code"):
        d2v[gg.index] = depth_ratio(cde, gg["ts"].to_numpy(), gg["dv24"].to_numpy())
    thin = d2v < np.nanquantile(d2v[~val], 0.2); mod = t0 % 86400; hr = mod // 3600
    return P, n, day, val, F, E, listed, warn, thin, mod, hr


def open_gap():
    """012: Upbit 00:00 UTC (KST 09:00) open vs 23:59 close gap; perp follows over the next hour (kr1m_hist window only)."""
    rows = []
    for fp in sorted((C / "kr1m_hist/upbit").glob("*.parquet")):
        k = pd.read_parquet(fp, columns=["ts", "o", "c"]).drop_duplicates("ts").set_index("ts")
        code = None
        for c in (f"{fp.stem}USDT", f"1000{fp.stem}USDT"):
            d = bp.load_minutes(c)
            if d is not None:
                code = c; break
        if code is None:
            continue
        t = d.index.to_numpy().astype("int64"); t = t // 10 ** 9 if t.max() > 1e12 else t
        pc = pd.Series(d["c"].to_numpy(float), index=t); qv = pd.Series(d["qv"].to_numpy(float), index=t)
        for day0 in range(int(k.index.min()) // 86400 + 1, int(k.index.max()) // 86400):
            m0 = day0 * 86400
            if m0 not in k.index or (m0 - 60) not in k.index or m0 not in pc.index or (m0 + 3600) not in pc.index:
                continue
            gap = k.loc[m0, "o"] / k.loc[m0 - 60, "c"] - 1
            if abs(gap) < 0.02:
                continue
            dv = qv.loc[m0 - 86400:m0].sum(); cost = 2 * (L.FEE + float(M.slip(np.array([dv]))[0]))
            r = pc.loc[m0 + 3600] / pc.loc[m0] - 1                   # perp: close of 00:00 minute -> close of 01:00 minute
            rows.append((m0, code, gap, np.sign(gap) * r - cost))
    return pd.DataFrame(rows, columns=["ts", "code", "gap", "net"])


def main():
    P, n, day, val, F, E, listed, warn, thin, mod, hr = compute()
    kr = listed
    cells = {
        "001|1h": (E["short1"], F["prem0"] > 0.03, kr & np.isfinite(F["prem0"])), "001|24h": (E["short24"], F["prem0"] > 0.03, kr & np.isfinite(F["prem0"])),
        "002|1h": (E["l2_1"], F["dprem"] > 0, np.isfinite(F["dprem"])), "002|24h": (E["l2_24"], F["dprem"] > 0, np.isfinite(F["dprem"])),
        "003|1h": (E["s3_1"], F["share"] > 0.30, np.isfinite(F["share"])), "003|24h": (E["s3_24"], F["share"] > 0.30, np.isfinite(F["share"])),
        "004|1h": (E["long1"], mod < 600, np.ones(n, bool)), "004|24h": (E["long24"], mod < 600, np.ones(n, bool)),
        "005|sap": (E["sap"], warn, kr),
        "006|24h": (E["long24"], F["bt_minus_up"] > 0, np.isfinite(F["bt_minus_up"])),
        "007|1h": (E["long1"], kr & thin, np.ones(n, bool)), "007|24h": (E["long24"], kr & thin, np.ones(n, bool)),
        "008|24h": (E["s60_24"], ~kr, np.ones(n, bool)),
        "009|1h": (E["long1"], np.abs(F["usd_chg"]) > 0.003, np.isfinite(F["usd_chg"])), "009|24h": (E["long24"], np.abs(F["usd_chg"]) > 0.003, np.isfinite(F["usd_chg"])),
        "011|sap": (E["sap"], (hr >= 16) & (hr < 21), np.ones(n, bool)),
    }
    res = {"not_run": {"010": "Upbit orderbook imbalance needs the recorder history (weeks)"}, "tests": {},
           "n_korean_listed": int(kr.sum()), "n_with_premium": int(np.isfinite(F["prem0"]).sum())}
    pv = {}
    for k, (v, flag, scope) in cells.items():
        vv = np.where(scope, v, np.nan)
        o, p = contrast(vv, flag & scope, day, val); o["share_flagged_in_scope"] = float((flag & scope).sum() / max(1, scope.sum()))
        res["tests"][k] = o; pv[k] = p
        print(k, json.dumps({a: (round(b, 4) if isinstance(b, float) else b) for a, b in o.items()}, default=float), flush=True)
    G = open_gap(); G["day"] = G["ts"] // 86400
    if len(G) >= 20:
        x = G["net"].to_numpy(); b = pd.DataFrame({"x": x, "d": G["day"]}).groupby("d")["x"].agg(["sum", "count"])
        rng = np.random.default_rng(1716); su, cn = b["sum"].to_numpy(), b["count"].to_numpy()
        bo = np.array([su[i].sum() / cn[i].sum() for i in (rng.integers(0, len(su), len(su)) for _ in range(2000))])
        res["tests"]["012|1h"] = dict(n=int(len(G)), mean=float(x.mean()), ci=[float(a) for a in np.percentile(bo, [2.5, 97.5])]); pv["012|1h"] = float((bo <= 0).mean())
    else:
        res["tests"]["012|1h"] = dict(n=int(len(G)), note="too few gap events in the kr1m_hist window")
    print("012", res["tests"]["012|1h"], flush=True)
    res["bh_pass"] = bh(pv)
    res["pass"] = [k for k in res["bh_pass"] if (k == "012|1h" and res["tests"][k].get("ci", [0])[0] > 0)
                   or ("flagged_ci" in res["tests"][k] and res["tests"][k]["flagged_ci"][0] > 0 and res["tests"][k]["diff_ci"][0] > 0)]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f16.json", "w"), indent=1, default=float)
    print("F16 BH", res["bh_pass"], "PASS", res["pass"], flush=True)


if __name__ == "__main__":
    main()
