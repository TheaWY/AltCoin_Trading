#!/usr/bin/env python3
"""Extra free sources (src/data/collectors/extra_sources.py).

    .venv/bin/python scripts/collect_extra.py --backfill      # 200d mcap, fng, 185d coinbase
    .venv/bin/python scripts/collect_extra.py                 # live loop (launchd com.altcoin.extra)

Live: CoinGecko trending + Bithumb every hour; Fear & Greed, Coinbase and a
CoinGecko market-cap refresh (last 3 days for every mapped coin) once a day.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.collectors import extra_sources as ex  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("extra")
IDS = PROJECT_ROOT / "data" / "cache" / "coingecko_ids.json"


def perp_symbols(storage) -> list[str]:
    with storage._connect() as c:  # noqa: SLF001
        return [dict(r)["symbol"] for r in c.execute(
            "SELECT DISTINCT symbol FROM prices WHERE timeframe='1h_perp' AND timestamp > ?",
            (int(time.time()) - 200 * 86400,)).fetchall()]


def ids(storage, cg, refresh: bool = False) -> dict[str, str]:
    if IDS.exists() and not refresh:
        return json.loads(IDS.read_text())
    m = ex.map_ids(cg, perp_symbols(storage))
    IDS.parent.mkdir(parents=True, exist_ok=True)
    IDS.write_text(json.dumps(m))
    log.info("coingecko ids mapped: %d", len(m))
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()
    storage = get_storage()
    ex.ensure_schema(storage)
    cg = ex.CoinGecko()
    if args.backfill:
        log.info("fear&greed: %d", ex.fear_greed(storage, 0))
        log.info("coinbase: %d", ex.coinbase(storage, 185))
        log.info("bithumb: %d", ex.bithumb(storage))
        ex.backfill_mcap(storage, cg, ids(storage, cg, refresh=True), 200, log.info)
        log.info("extra backfill done")
        return 0
    last_day, last_hour = 0, 0
    while True:
        now = int(time.time())
        if now // 3600 != last_hour:
            last_hour = now // 3600
            for name, fn in (("trending", lambda: ex.trending(storage, cg)), ("bithumb", lambda: ex.bithumb(storage)),
                             ("coinbase", lambda: ex.coinbase(storage, 1))):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("%s failed", name)
        if now // 86400 != last_day and now % 86400 > 1800:
            last_day = now // 86400
            try:
                ex.fear_greed(storage, 3)
                ex.backfill_mcap(storage, cg, ids(storage, cg, refresh=(last_day % 7 == 0)), 3, log.info)
            except Exception:  # noqa: BLE001
                log.exception("daily refresh failed")
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
