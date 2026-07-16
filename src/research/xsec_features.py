"""Cross-sectional (universe-wide) features — the biggest gap in the data
inventory (2026-07-15). First feature: DISPERSION, the cross-sectional std-dev
of 24h returns across the alt universe at each hour.

Why it matters (EXP2, 2026-07-15): buy-weakness reversion only works when
dispersion is HIGH — an alt dumped for its OWN reasons (idiosyncratic) reverts
(+1.79%/72h); when the whole market falls TOGETHER (low dispersion, systematic)
it doesn't (−0.5%), and the beta-hedge can't save a correlated move.

POINT-IN-TIME: dispersion[t] uses only 24h returns ending at or before t, and
high_flag[t] compares dispersion[t] against the trailing window BEFORE t — both
are computed at backfill from data <= t, so a backtest that looks up high_flag
for an entry bar reads a value that was legitimately knowable at that bar. The
whole precomputed series can therefore be cached and indexed by entry time
without lookahead.
"""

from __future__ import annotations

import bisect
from typing import Any

import numpy as np

HOUR = 3600
_TRAIL_BARS = 2 * 365 * 24     # trailing window for the high/low percentile
_MIN_HISTORY = 720             # need 30d of dispersion history before flagging
_HIGH_PCTL = 67.0              # top tercile = "high dispersion"


def ensure_schema(storage: Any) -> None:
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xsec_dispersion (
                timestamp INTEGER PRIMARY KEY,
                dispersion REAL NOT NULL,
                n_symbols INTEGER NOT NULL,
                high_flag INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Market BREADTH: fraction of the universe with a positive return over
        # each window -- a market-regime feature the inventory found we never
        # computed, and regime is exactly where the six strategies died.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xsec_breadth (
                timestamp INTEGER PRIMARY KEY,
                breadth_24h REAL NOT NULL,
                breadth_72h REAL NOT NULL,
                breadth_7d REAL NOT NULL,
                n_symbols INTEGER NOT NULL
            )
        """)


def compute_and_store(storage: Any, symbols: list[str]) -> int:
    """Compute hourly cross-sectional dispersion across `symbols` and its
    trailing-percentile high_flag, and upsert. Returns rows written."""
    ensure_schema(storage)
    # per-hour list of 24h returns across the universe
    from collections import defaultdict
    buck: dict[int, list[float]] = defaultdict(list)
    for sym in symbols:
        rows = storage.get_prices(sym, limit=200000, timeframe="1h")
        if len(rows) < 25:
            continue
        cl = np.array([float(r["close"]) for r in rows])
        ts = np.array([int(r["timestamp"]) for r in rows])
        r24 = (cl[24:] - cl[:-24]) / cl[:-24]
        for t, v in zip(ts[24:], r24):
            if np.isfinite(v):
                buck[(int(t) // HOUR) * HOUR].append(float(v))

    hours = sorted(h for h, v in buck.items() if len(v) >= 5)
    disp = np.array([float(np.std(buck[h])) for h in hours])
    n = np.array([len(buck[h]) for h in hours])

    # trailing-window high_flag: dispersion[i] >= 67th pctl of the window BEFORE i
    high = np.zeros(len(hours), dtype=int)
    for i in range(len(hours)):
        j0 = max(0, i - _TRAIL_BARS)
        window = disp[j0:i]
        if len(window) >= _MIN_HISTORY and disp[i] >= np.percentile(window, _HIGH_PCTL):
            high[i] = 1

    rows_out = [
        {"timestamp": int(hours[i]), "dispersion": float(disp[i]),
         "n_symbols": int(n[i]), "high_flag": int(high[i])}
        for i in range(len(hours))
    ]
    with storage._connect() as conn:  # noqa: SLF001
        conn.executemany(
            "INSERT OR IGNORE INTO xsec_dispersion (timestamp,dispersion,n_symbols,high_flag) "
            "VALUES (:timestamp,:dispersion,:n_symbols,:high_flag)",
            rows_out,
        )
    return len(rows_out)


def compute_breadth_and_store(storage: Any, symbols: list[str]) -> int:
    """Per-hour market breadth: fraction of the universe with a positive return
    over 24h / 72h / 7d. Point-in-time (each hour's breadth uses only returns
    ending at or before that hour). Upsert; returns rows written."""
    ensure_schema(storage)
    from collections import defaultdict
    WINDOWS = {"24h": 24, "72h": 72, "7d": 168}
    # buckets[hour] = {"24h": [signs...], ...}
    up: dict[int, dict[str, list[int]]] = defaultdict(lambda: {w: [] for w in WINDOWS})
    for sym in symbols:
        rows = storage.get_prices(sym, limit=200000, timeframe="1h")
        if len(rows) < 200:
            continue
        cl = np.array([float(r["close"]) for r in rows])
        ts = np.array([int(r["timestamp"]) for r in rows])
        for wname, wbars in WINDOWS.items():
            r = np.full(len(cl), np.nan)
            r[wbars:] = (cl[wbars:] - cl[:-wbars]) / cl[:-wbars]
            for t, v in zip(ts[wbars:], r[wbars:]):
                if np.isfinite(v):
                    up[(int(t) // HOUR) * HOUR][wname].append(1 if v > 0 else 0)

    rows_out = []
    for hour, w in up.items():
        if len(w["24h"]) < 5:
            continue
        rows_out.append({
            "timestamp": int(hour),
            "breadth_24h": float(np.mean(w["24h"])) if w["24h"] else 0.5,
            "breadth_72h": float(np.mean(w["72h"])) if w["72h"] else 0.5,
            "breadth_7d": float(np.mean(w["7d"])) if w["7d"] else 0.5,
            "n_symbols": len(w["24h"]),
        })
    with storage._connect() as conn:  # noqa: SLF001
        conn.executemany(
            "INSERT OR IGNORE INTO xsec_breadth "
            "(timestamp,breadth_24h,breadth_72h,breadth_7d,n_symbols) VALUES "
            "(:timestamp,:breadth_24h,:breadth_72h,:breadth_7d,:n_symbols)",
            rows_out,
        )
    return len(rows_out)


_BREADTH_CACHE: tuple[list[int], dict[str, list[float]]] | None = None


def breadth_at(storage: Any, ts: int, window: str = "24h") -> float | None:
    """Fraction of the universe up over `window` (24h/72h/7d) as of the hour of
    `ts` (point-in-time: most recent bucket at or before ts). None if unknown."""
    global _BREADTH_CACHE
    if _BREADTH_CACHE is None:
        base = getattr(storage, "storage", None) or storage
        try:
            with base._connect() as conn:  # noqa: SLF001
                rr = conn.execute(
                    "SELECT timestamp,breadth_24h,breadth_72h,breadth_7d "
                    "FROM xsec_breadth ORDER BY timestamp"
                ).fetchall()
            tss = [int(dict(r)["timestamp"]) for r in rr]
            cols = {w: [float(dict(r)[f"breadth_{w}"]) for r in rr] for w in ("24h", "72h", "7d")}
            _BREADTH_CACHE = (tss, cols)
        except Exception:
            _BREADTH_CACHE = ([], {})
    tss, cols = _BREADTH_CACHE
    if not tss or window not in cols:
        return None
    bucket = (int(ts) // HOUR) * HOUR
    i = bisect.bisect_right(tss, bucket) - 1
    return cols[window][i] if i >= 0 else None


# --- read path (cached; used by the setup, called per-symbol-per-timestep) ---
_CACHE: tuple[list[int], list[int]] | None = None  # (sorted timestamps, high_flag)


def _load(storage: Any) -> tuple[list[int], list[int]]:
    global _CACHE
    if _CACHE is None:
        # Unwrap a SnapshotStorage (backtest) to the real Storage for the raw
        # table read -- the snapshot proxies get_prices but not _connect. This
        # is NOT a lookahead hole: is_high_dispersion() only ever indexes the
        # flag AT OR BEFORE the entry ts, and each flag was itself computed from
        # data <= its own hour, so nothing future can influence a past entry.
        base = getattr(storage, "storage", None) or storage
        try:
            with base._connect() as conn:  # noqa: SLF001
                rows = conn.execute(
                    "SELECT timestamp, high_flag FROM xsec_dispersion ORDER BY timestamp"
                ).fetchall()
            ts = [int(dict(r)["timestamp"]) for r in rows]
            hf = [int(dict(r)["high_flag"]) for r in rows]
            _CACHE = (ts, hf)
        except Exception:
            _CACHE = ([], [])
    return _CACHE


def reset_cache() -> None:
    global _CACHE, _BREADTH_CACHE
    _CACHE = None
    _BREADTH_CACHE = None


def is_high_dispersion(storage: Any, ts: int) -> bool:
    """True if the universe was in high cross-sectional dispersion at the hour
    of `ts`. Reads the most recent flag at or before ts (point-in-time). Absent
    data -> False (fail closed: no gate signal, no trade)."""
    tss, hf = _load(storage)
    if not tss:
        return False
    bucket = (int(ts) // HOUR) * HOUR
    i = bisect.bisect_right(tss, bucket) - 1
    return i >= 0 and hf[i] == 1
