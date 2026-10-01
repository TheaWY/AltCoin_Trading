"""Read kr_ticks parquet files with consistent timestamps.
Old files (before 2026-10-01 21:00 KST): no ts_us column; Bithumb ts_ms actually holds microseconds.
New files: ts_us (int microseconds) and ts_ms (int milliseconds) for both exchanges."""
from __future__ import annotations

import pandas as pd


def read(path) -> pd.DataFrame:
    d = pd.read_parquet(path)
    if "ts_us" not in d.columns:
        t = d["ts_ms"].astype("int64")
        d["ts_us"] = t.where(t > 10 ** 14, t * 1000)
        d["ts_ms"] = d["ts_us"] // 1000
    return d
