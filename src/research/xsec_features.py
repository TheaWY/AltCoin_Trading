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
    global _CACHE
    _CACHE = None


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
