"""Perp/spot/funding panel for the edge search, with a persistent on-disk cache.

Spot klines + funding come from the DB (already backfilled). Perp klines come
from data.binance.vision (futures/um monthly 1h) — reachable from Korea (it's
the static data CDN, not the geo-blocked trading API) — cached to disk so the
autonomous loop never re-downloads. Refreshed incrementally when stale.
"""
from __future__ import annotations

import concurrent.futures as cf
import csv
import datetime as dt
import io
import os
import pickle
import time
import urllib.request as U
import zipfile
from typing import Any, Callable

import numpy as np

from src.data.storage import get_storage

HOUR = 3600
VBASE = "https://data.binance.vision/data/futures/um/monthly/klines"
CACHE_DIR = os.getenv("PERP_CACHE_DIR", os.path.expanduser("~/.altcoin_perp_cache"))
CACHE_FILE = os.path.join(CACHE_DIR, "panel.pkl")
START = dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc)
RECENT_TS = 1780000000
STALE_S = 6 * 3600           # refresh perp for the newest month if cache older than this


def now_ts() -> float:
    return time.time()


def _months(a: dt.datetime, b: dt.datetime):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield y, m
        m = m % 12 + 1
        if m == 1:
            y += 1


def _dl_perp(sym: str, start: dt.datetime, end: dt.datetime) -> tuple[str, dict[int, float]]:
    code = sym.replace("/", "")
    out: dict[int, float] = {}
    for y, m in _months(start, end):
        url = f"{VBASE}/{code}/1h/{code}-1h-{y}-{m:02d}.zip"
        try:
            with U.urlopen(url, timeout=45) as r:
                payload = r.read()
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                for nm in zf.namelist():
                    if nm.endswith(".csv"):
                        for line in csv.reader(io.TextIOWrapper(zf.open(nm), "utf-8")):
                            if line and line[0].isdigit():
                                t = int(line[0])
                                while t > 20_000_000_000:
                                    t //= 1000
                                out[(t // HOUR) * HOUR] = float(line[4])
        except Exception:
            continue
    return sym, out


def fund_sum(fund: tuple, t0: float, t1: float) -> float:
    fts, fr = fund
    lo = np.searchsorted(fts, t0, "left"); hi = np.searchsorted(fts, t1, "left")
    return float(fr[lo:hi].sum()) if hi > lo else 0.0


def fund_signal(fund: tuple, t: float, look_d: int) -> float | None:
    fts, fr = fund
    lo = np.searchsorted(fts, t - look_d * 86400, "left"); hi = np.searchsorted(fts, t, "left")
    return float(fr[lo:hi].mean()) if hi > lo else None


def funding_stat(fund: tuple, t: float, mode: str, look_d: int = 3,
                 base_d: int = 30) -> float | None:
    """Funding signal in one of several modes. 'level' = raw trailing mean (decays
    as absolute funding falls over time). 'zscore'/'pctl' = current funding vs the
    coin's OWN trailing distribution (stationary -> should decay less). 'accel' =
    recent funding minus older funding (is crowding building?)."""
    fts, fr = fund
    hi = np.searchsorted(fts, t, "left")
    lo = np.searchsorted(fts, t - look_d * 86400, "left")
    if hi <= lo:
        return None
    recent = float(fr[lo:hi].mean())
    if mode == "level":
        return recent
    blo = np.searchsorted(fts, t - base_d * 86400, "left")
    base = fr[blo:hi]
    if len(base) < 8:
        return None
    if mode == "zscore":
        sd = base.std()
        return (recent - base.mean()) / sd if sd > 0 else None
    if mode == "pctl":
        return float((base < recent).mean())
    if mode == "accel":
        older = fr[blo:lo]
        return recent - float(older.mean()) if len(older) else None
    return recent


def _liquid_funding_symbols(storage, max_names: int) -> list[str]:
    ph = "%s" if storage.is_postgres else "?"
    with storage._connect() as c:
        syms = [dict(r)["symbol"] for r in c.execute(
            f"""SELECT p.symbol FROM
                 (SELECT symbol FROM prices WHERE timeframe='1h' GROUP BY symbol
                  HAVING COUNT(*)>=9000 AND MAX(timestamp)>={ph}) p
                 JOIN (SELECT symbol FROM funding_rates GROUP BY symbol HAVING COUNT(*)>=2000) f
                 ON p.symbol=f.symbol""", (RECENT_TS,)).fetchall()]
    syms = [s for s in syms if s != "BTC/USDT"]
    liq = []
    for s in syms:
        rows = storage.get_prices(s, limit=1000, timeframe="1h")
        if len(rows) < 500:
            continue
        dv = np.median([float(r["close"]) * float(r.get("volume") or 0) for r in rows[-720:]])
        liq.append((dv, s))
    return [s for _, s in sorted(liq, reverse=True)[:max_names]]


def load_panel(max_names: int = 100, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Return {perp, spot, fund, btc, by_liquidity}. Uses/refreshes the disk cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    storage = get_storage()
    cache = None
    if os.path.exists(CACHE_FILE):
        try:
            cache = pickle.load(open(CACHE_FILE, "rb"))
        except Exception:
            cache = None

    fresh = cache and (now_ts() - cache.get("_saved", 0) < STALE_S) and \
        len(cache.get("perp", {})) >= max_names
    if fresh:
        return cache

    names = _liquid_funding_symbols(storage, max_names)
    log(f"perp_cache: refreshing panel for {len(names)} liquid funding names")
    end = dt.datetime.now(dt.timezone.utc)
    perp = (cache or {}).get("perp", {})
    # download only missing symbols fully; for existing, refresh the last 2 months
    to_full = [s for s in names if s not in perp]
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for sym, o in ex.map(lambda s: _dl_perp(s, START, end), to_full):
            if len(o) > 3000:
                perp[sym] = o
    # incremental refresh of recent months for already-cached symbols
    recent_start = (end.replace(day=1) - dt.timedelta(days=40))
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for sym, o in ex.map(lambda s: _dl_perp(s, recent_start, end),
                             [s for s in names if s in perp and s not in to_full]):
            perp.get(sym, {}).update(o)

    spot, fund = {}, {}
    ph = "%s" if storage.is_postgres else "?"
    for s in names:
        rows = storage.get_prices(s, limit=200000, timeframe="1h")
        spot[s] = {(int(r["timestamp"]) // HOUR) * HOUR: float(r["close"]) for r in rows}
        with storage._connect() as c:
            fr = [dict(r) for r in c.execute(
                f"SELECT timestamp, funding_rate FROM funding_rates WHERE symbol={ph} ORDER BY timestamp",
                (s,)).fetchall()]
        fund[s] = (np.array([int(x["timestamp"]) for x in fr]),
                   np.array([float(x["funding_rate"]) for x in fr]))
    btc_rows = storage.get_prices("BTC/USDT", limit=200000, timeframe="1h")
    btc = {(int(r["timestamp"]) // HOUR) * HOUR: float(r["close"]) for r in btc_rows}

    panel = {"perp": {s: perp[s] for s in names if s in perp}, "spot": spot,
             "fund": fund, "btc": btc, "by_liquidity": names, "_saved": now_ts()}
    try:
        pickle.dump(panel, open(CACHE_FILE, "wb"))
    except Exception as e:
        log(f"perp_cache: could not persist cache: {e}")
    return panel
