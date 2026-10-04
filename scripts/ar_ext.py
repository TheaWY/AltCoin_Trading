"""Extra data sources for the autonomous research engine that live only in the DB (no research cache):
coinalyze_1h -> cross-exchange liquidations and OI per base coin, aggregated over exchanges, gridded to the panel's
hourly ts x codes. History starts 2026-06-26, so signals built from it run on the engine's SHORT-HISTORY track
(ar_engine.run_one splits the available days 60/40 instead of using the 2025-07 holdout) and are flagged as such."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/cache/coinalyze_agg.parquet"


def coinalyze_agg(st, t0, refresh_h=20, fresh=False):
    """(base, ts) -> liq_long, liq_short, oi_usd summed over exchanges; cached parquet refreshed every refresh_h hours.
    fresh=True (live trading) always queries the DB and leaves the research cache alone."""
    from oi_drop_short_paper import q
    if fresh:
        d = q(st, "SELECT base, ts, sum(liq_long) AS liq_long, sum(liq_short) AS liq_short, sum(oi_usd) AS oi_usd "
                  "FROM coinalyze_1h WHERE ts >= %s GROUP BY base, ts", (int(t0),))
        d = d.dropna(subset=["base"]); d["ts"] = d["ts"].astype("int64"); return d
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < refresh_h * 3600:
        d = pd.read_parquet(CACHE)
        if len(d) and d["ts"].min() <= t0 + 86400:
            return d
    d = q(st, "SELECT base, ts, sum(liq_long) AS liq_long, sum(liq_short) AS liq_short, sum(oi_usd) AS oi_usd "
              "FROM coinalyze_1h WHERE ts >= %s GROUP BY base, ts", (int(t0),))
    d = d.dropna(subset=["base"]); d["ts"] = d["ts"].astype("int64")
    d.to_parquet(CACHE, index=False)
    return d


def coinalyze_grid(st, ts, codes, t0=None, fresh=False):
    """hourly T x N arrays aligned to (ts, codes): liq_long, liq_short (USD per hour, NaN = no data), oi_cz."""
    ts = np.asarray(ts, dtype="int64"); t0 = int(ts[0]) if t0 is None else int(t0)
    d = coinalyze_agg(st, t0, fresh=fresh)
    col = {c: j for j, c in enumerate(codes)}
    d = d.assign(code=d["base"].astype(str) + "USDT")
    d = d[d["code"].isin(col)]
    row = {int(t): i for i, t in enumerate(ts)}
    ii = d["ts"].map(row); ok = ii.notna()
    ii = ii[ok].astype(int).to_numpy(); jj = d.loc[ok, "code"].map(col).astype(int).to_numpy()
    out = {}
    for k, name in (("liq_long", "liq_long"), ("liq_short", "liq_short"), ("oi_usd", "oi_cz")):
        A = np.full((len(ts), len(codes)), np.nan)
        A[ii, jj] = d.loc[ok, k].astype(float).to_numpy()
        out[name] = A
    return out
