#!/usr/bin/env python3
"""Live collector (launchd com.altcoin.korea): Upbit hourly candles for every
KRW market (last 3 hours re-fetched every 5 minutes, so the current hour
stays fresh) and exchange announcements every minute.

    .venv/bin/python scripts/collect_korea_notices.py                  # live
    .venv/bin/python scripts/collect_korea_notices.py --backfill-days 185
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.collectors import korea_and_notices as kn  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("korea")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=int, default=0)
    args = ap.parse_args()
    storage = get_storage()
    kn.ensure_schema(storage)
    if args.backfill_days:
        kn.backfill_hourly(storage, args.backfill_days, log.info)
        log.info("upbit backfill done")
        return 0
    s = requests.Session()
    markets, last_candles, last_markets = [], 0.0, 0.0
    while True:
        t0 = time.time()
        try:
            n = kn.insert_notices(storage, kn.fetch_notices(s))
            if n and int(t0) % 3600 < 60:
                log.info("notices upserted: %d", n)
        except Exception:  # noqa: BLE001
            log.exception("notices failed")
        if t0 - last_markets > 6 * 3600 or not markets:
            try:
                markets, last_markets = kn.krw_markets(s), t0
            except Exception:  # noqa: BLE001
                log.exception("market list failed")
        if t0 - last_candles >= 300:
            last_candles = t0
            rows = []
            for m in markets:
                rows += kn.fetch_hourly(s, m, 3)
                time.sleep(0.12)
            kn.insert_hourly(storage, rows)
        time.sleep(max(1.0, 60 - (time.time() - t0)))


if __name__ == "__main__":
    raise SystemExit(main())
