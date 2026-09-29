"""B17 F07 venue lead-lag (registry F07_venue_leadlag_001..048). Universe scan (every eligible coin-period, no null weighting).
  OMP_NUM_THREADS=6 .venv/bin/python -W ignore scripts/b17_f07.py   -> data/reports/b17/f07.json
Trigger: venue log-return over the lag >= log(1.05) while the Binance perp's return over the same window < half of it (the move is 'not yet in
the perp'). follow = long perp at the perp close of the trigger bar, fade = short; exit after one more lag. Costs 2 x (0.05% + dv24 slippage).
One trade per coin per lag window (no overlap). Rules are fixed (no fitting) -> both periods are out-of-sample; pass = validation (2026) net
CI > 0, discovery mean > 0, BH q=0.10 over the runnable cells.
Timestamp conventions (checked 2026-09-29 by return correlation against the perp panel, whose row = bar CLOSE):
  upbit1h_hist / bithumb1h_hist / spot1h_hist: ts = bar close (corr 0.91 / 0.90 / 1.00 at shift 0)
  coinalyze_1h: ts = bar OPEN (corr 0.99 at +1h, 0.02 at 0) -> shifted +3600; one USDT perp contract per (exchange, base), max volume
  kr1m_hist (Upbit/Bithumb 1m) and b2 perp 1m klines: both indexed by minute OPEN (corr peak at shift 0); close known at ts+60.
Runnable cells: 1h cells for upbit, bithumb, spot (2024-03..2026-09), hyperliquid, bybit, coinalyze(=okx) (2026-06-24..09-23, validation only);
1m/5m/15m cells for upbit and bithumb from kr1m_hist (2026-07-27..09-23, validation only).
Not run: spot / bybit 1m-15m (need a 1m backfill; queued), hyperliquid 1m-15m and coinalyze 1m-15m (no sub-hour history)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

os.environ["B2_ERA"] = "all"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import b2_panel as bp  # noqa: E402
import b7_lib as L  # noqa: E402
import b15_models as M  # noqa: E402
from b17_f03 import bh  # noqa: E402

C, OUT = M.C, ROOT / "data/reports/b17"
RNG = np.random.default_rng(1707)
VAL0, H, TH = 1767225600, 3600, np.log(1.05)
LAGS_M = {"1m": 1, "5m": 5, "15m": 15}
HID = {"upbit": 0, "bithumb": 8, "spot": 16, "hyperliquid": 24, "bybit": 32, "coinalyze": 40}
LAG_IDX = {"1m": 0, "5m": 2, "15m": 4, "1h": 6}


def scan(v, p, dv, tclose, k):
    """v, p: log prices aligned on bars (NaN allowed); tclose: bar close times; k: lag in bars. Returns rows (ts, gross_long, cost)."""
    out = []; n = len(p); t = k; cost = 2 * (L.FEE + M.slip(np.nan_to_num(dv, nan=0.0)))
    rv = np.full(n, np.nan); rp = np.full(n, np.nan); rv[k:] = v[k:] - v[:-k]; rp[k:] = p[k:] - p[:-k]
    trig = (rv >= TH) & (rp < 0.5 * rv) & np.isfinite(rp)
    idx = np.flatnonzero(trig); nxt = -1
    for i in idx:
        if i < nxt or i + k >= n or not np.isfinite(p[i + k]):
            continue
        out.append((int(tclose[i]), float(np.exp(p[i + k] - p[i]) - 1), float(cost[i]))); nxt = i + k
    return out


def hourly_cells():
    ts_h, codes, X = L.data(); ci = {c: i for i, c in enumerate(codes)}
    def perp_code(base):
        for c in (f"{base}USDT", f"1000{base}USDT", f"1000000{base}USDT"):
            if c in ci:
                return c
        return None
    rows = []
    for venue, d in (("upbit", "upbit1h_hist"), ("bithumb", "bithumb1h_hist"), ("spot", "spot1h_hist")):
        for fp in sorted((C / d).glob("*.parquet")):
            base = fp.stem[:-4] if venue == "spot" and fp.stem.endswith("USDT") else fp.stem
            code = perp_code(base) if venue != "spot" else (fp.stem if fp.stem in ci else perp_code(base))
            if code is None:
                continue
            x = pd.read_parquet(fp, columns=["ts", "c"]).drop_duplicates("ts").set_index("ts")["c"].astype(float)
            v = np.log(x.where(x > 0).reindex(ts_h).to_numpy()); j = ci[code]
            p = X["lc"][:, j].astype(float); p[~X["U"][:, j]] = np.nan
            rows += [(venue, "1h", code) + r for r in scan(v, p, X["dv24"][:, j], ts_h, 1)]
    # coinalyze per-exchange closes (bar OPEN stamps -> +3600)
    sys.path.insert(0, str(ROOT / "scripts"))
    from src.data.storage import get_storage
    from oi_drop_short_paper import q
    st = get_storage()
    for venue, ex in (("hyperliquid", "hyperliquid"), ("bybit", "bybit"), ("coinalyze", "okx")):
        D = q(st, f"SELECT symbol, base, ts, close, volume FROM coinalyze_1h WHERE exchange = '{ex}' AND close IS NOT NULL")
        if D is None or not len(D):
            continue
        D = D[D["symbol"].str.contains("USDT", na=False) | (ex == "hyperliquid")]
        best = D.groupby(["base", "symbol"])["volume"].sum().reset_index().sort_values("volume").groupby("base").tail(1)
        D = D.merge(best[["base", "symbol"]], on=["base", "symbol"])
        for base, g in D.groupby("base"):
            code = perp_code(base)
            if code is None:
                continue
            x = g.drop_duplicates("ts").set_index(g.drop_duplicates("ts")["ts"].astype("int64") + H)["close"].astype(float)
            v = np.log(x.where(x > 0).reindex(ts_h).to_numpy()); j = ci[code]
            p = X["lc"][:, j].astype(float); p[~X["U"][:, j]] = np.nan
            rows += [(venue, "1h", code) + r for r in scan(v, p, X["dv24"][:, j], ts_h, 1)]
        print("coinalyze", venue, "done", flush=True)
    return rows


def korea_minute_cells():
    rows = []
    for venue in ("upbit", "bithumb"):
        files = sorted((C / f"kr1m_hist/{venue}").glob("*.parquet"))
        for n_, fp in enumerate(files):
            base = fp.stem; code = None
            for c in (f"{base}USDT", f"1000{base}USDT"):
                d = bp.load_minutes(c)
                if d is not None:
                    code = c; break
            if code is None:
                continue
            k = pd.read_parquet(fp, columns=["ts", "c"]).drop_duplicates("ts").set_index("ts")["c"].astype(float)
            t = d.index.to_numpy().astype("int64"); t = t // 10 ** 9 if t.max() > 1e12 else t
            m = (t >= k.index.min()) & (t <= k.index.max())
            if m.sum() < 1440:
                continue
            t = t[m]; p = np.log(d["c"].to_numpy(float)[m]); qv = d["qv"].to_numpy(float)[m]
            dv = pd.Series(qv).rolling(1440, min_periods=720).sum().to_numpy()
            v = np.log(k.where(k > 0).reindex(t).ffill(limit=2).to_numpy())
            for lag, kk in LAGS_M.items():
                rows += [(venue, lag, code) + r for r in scan(v, p, dv, t + 60, kk)]      # minute-open index -> close time = t + 60
        print("korea minute", venue, len(files), "coins", flush=True)
    return rows


def stats(x, day):
    x = np.asarray(x, float)
    if len(x) < 30:
        return dict(n=int(len(x)), mean=float(x.mean()) if len(x) else None, ci=[np.nan, np.nan], p=1.0)
    b = pd.DataFrame({"x": x, "d": day}).groupby("d")["x"].agg(["sum", "count"]); su, cn = b["sum"].to_numpy(), b["count"].to_numpy()
    bo = np.array([su[i].sum() / cn[i].sum() for i in (RNG.integers(0, len(su), len(su)) for _ in range(2000))])
    return dict(n=int(len(x)), mean=float(x.mean()), ci=[float(v) for v in np.percentile(bo, [2.5, 97.5])], p=float((bo <= 0).mean()))


def main():
    T = pd.DataFrame(hourly_cells() + korea_minute_cells(), columns=["venue", "lag", "code", "ts", "gross", "cost"])
    T["day"] = T["ts"] // 86400; T["val"] = T["ts"] >= VAL0
    T.to_parquet(ROOT / "data/cache/b17/f07_trades.parquet", index=False)
    res = {"tests": {}, "not_run": {"spot|1m,5m,15m": "needs Binance spot 1m backfill (queued)", "bybit|1m,5m,15m": "needs Bybit 1m backfill (queued)",
                                     "hyperliquid|1m,5m,15m": "no sub-hour history", "coinalyze|1m,5m,15m": "coinalyze is hourly only"}}; pv = {}
    for (venue, lag), g in T.groupby(["venue", "lag"]):
        for side, sgn in (("follow", 1), ("fade", -1)):
            net = sgn * g["gross"] - g["cost"]
            hid = f"F07_{HID[venue] + LAG_IDX[lag] + (1 if side == 'follow' else 2):03d}|{venue}|{lag}|{side}"
            v = stats(net[g["val"]], g.loc[g["val"], "day"]); dsc = stats(net[~g["val"]], g.loc[~g["val"], "day"]) if (~g["val"]).any() else None
            res["tests"][hid] = dict(val=v, disc=dsc, coins=int(g["code"].nunique())); pv[hid] = v["p"]
            print(hid, "val", {k: (round(x, 4) if isinstance(x, float) else x) for k, x in v.items()}, "disc", None if dsc is None else round(dsc["mean"] or 0, 4), flush=True)
    res["bh_pass"] = bh(pv)
    res["pass"] = [h for h in res["bh_pass"] if res["tests"][h]["val"]["ci"][0] > 0 and (res["tests"][h]["disc"] is None or (res["tests"][h]["disc"]["mean"] or 0) > 0)]
    OUT.mkdir(parents=True, exist_ok=True); json.dump(res, open(OUT / "f07.json", "w"), indent=1, default=float)
    print("F07 BH", res["bh_pass"], "PASS", res["pass"], flush=True)


if __name__ == "__main__":
    main()
