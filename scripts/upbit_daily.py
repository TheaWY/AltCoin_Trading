"""Upbit KRW daily candles 2017-09..now for every current KRW market -> data/upbit_db/d1/<MKT>.parquet (prereg v2).
Uses the rate-limited client in upbit_db.py. ~291 markets x up to 17 requests. Paper research only."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upbit_db import DB, candles, get  # noqa: E402

OUT = DB / "d1"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mk = [m["market"] for m in get("/v1/market/all", {"isDetails": "false"}) if m["market"].startswith("KRW-")]
    t0 = int(pd.Timestamp("2017-09-01", tz="UTC").timestamp()); t1 = int(time.time())
    for i, m in enumerate(mk):
        f = OUT / f"{m}.parquet"
        if f.exists():
            continue
        d = candles(m, "days", t0, t1)
        d.to_parquet(f, index=False)
        if i % 25 == 0:
            print(time.strftime("%T"), i, len(mk), m, len(d), flush=True)
    print("done", len(mk))


if __name__ == "__main__":
    main()
