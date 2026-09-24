#!/usr/bin/env python3
"""Live collector for perp_1m / perp_5m / book_5m (launchd: com.altcoin.perpmetrics).

Three threads:
  premium   every minute, one call for all symbols (mark, index, basis, funding)
  book      walks all symbols with a pause so each is snapshotted about every 5 min
  futures   walks all symbols continuously at ~3 requests/s (futures/data allows
            ~1000 per 5 min); asks for the last 6 points (30 min) so a slow lap
            leaves no holes

    .venv/bin/python scripts/collect_perp_metrics.py
    .venv/bin/python scripts/collect_perp_metrics.py --backfill-days 14
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.collectors import klines_1m as k1  # noqa: E402
from src.data.collectors import perp_metrics as pm  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("perp_metrics")
FUT_PAUSE = 0.33
BOOK_LAP_S = 300
BACKFILL_DAYS = 14


class Symbols:
    def __init__(self) -> None:
        self.list: list[str] = []
        self.at = 0.0

    def get(self) -> list[str]:
        if not self.list or time.time() - self.at > 6 * 3600:
            try:
                self.list, self.at = k1.usdt_perps(), time.time()
            except Exception:  # noqa: BLE001
                log.exception("symbol refresh failed")
        return self.list


def premium_loop(storage, syms: Symbols) -> None:
    s = requests.Session()
    last_prune = 0.0
    while True:
        t0 = time.time()
        try:
            n = pm.insert_premium(storage, pm.fetch_premium(s, set(syms.get())))
            if int(t0) % 3600 < 60:
                log.info("premium: %d rows", n)
        except Exception:  # noqa: BLE001
            log.exception("premium failed")
        if t0 - last_prune > 86400:
            last_prune = t0
            try:
                pm.prune(storage)
            except Exception:  # noqa: BLE001
                log.exception("prune failed")
        time.sleep(max(1.0, 60 - (time.time() % 60) + 2))


def book_loop(storage, syms: Symbols) -> None:
    s = requests.Session()
    while True:
        lst = syms.get()
        pause = BOOK_LAP_S / max(1, len(lst))
        t0, rows = time.time(), []
        for sym in lst:
            try:
                rows.append(pm.fetch_book(s, sym))
            except Exception:  # noqa: BLE001
                pass
            if len(rows) >= 50:
                pm.insert_book(storage, rows)
                rows = []
            time.sleep(pause)
        pm.insert_book(storage, rows)
        log.info("book lap: %d symbols in %.0fs", len(lst), time.time() - t0)


def futures_loop(storage, syms: Symbols) -> None:
    s = requests.Session()
    try:
        backfill(storage, BACKFILL_DAYS, only_missing=True)
    except Exception:  # noqa: BLE001
        log.exception("startup backfill failed")
    while True:
        t0 = time.time()
        lst = syms.get()
        for sym in lst:
            try:
                pm.insert_futures(storage, sym, pm.fetch_futures(s, sym, limit=6, pause=FUT_PAUSE))
            except Exception:  # noqa: BLE001
                log.exception("futures %s failed", sym)
        log.info("futures lap: %d symbols in %.0fs", len(lst), time.time() - t0)


def _last_ts(storage) -> dict[str, int]:
    with storage._connect() as c:  # noqa: SLF001
        rows = c.execute("SELECT symbol, MAX(ts) AS mx FROM perp_5m WHERE oi IS NOT NULL GROUP BY symbol").fetchall()
    return {dict(r)["symbol"]: int(dict(r)["mx"]) for r in rows}


def backfill(storage, days: int, only_missing: bool = False) -> None:
    """30-day max on Binance's side. 500 points (~41h) per request. With
    only_missing, each symbol resumes from its last stored point, so a
    restart fills its own hole and a fresh install loads `days` of history."""
    s = requests.Session()
    lst = k1.usdt_perps()
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400 * 1000
    step = 500 * 300 * 1000
    have = _last_ts(storage) if only_missing else {}
    for i, sym in enumerate(lst):
        t = max(start_ms, (have.get(sym, 0) + 300) * 1000)
        if now_ms - t < 30 * 60 * 1000:
            continue  # the live lap covers the last 30 minutes
        n = 0
        while t < now_ms:
            data = pm.fetch_futures(s, sym, limit=500, start_ms=t, end_ms=min(now_ms, t + step), pause=FUT_PAUSE)
            n += pm.insert_futures(storage, sym, data)
            t += step
        if i % 20 == 0:
            log.info("backfill [%d/%d] %s: %d rows", i + 1, len(lst), sym, n)
    log.info("backfill done")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=int, default=0)
    args = ap.parse_args()
    storage = get_storage()
    pm.ensure_schema(storage)
    if args.backfill_days:
        backfill(storage, args.backfill_days)
        return 0
    syms = Symbols()
    threads = [threading.Thread(target=f, args=(storage, syms), daemon=True, name=f.__name__)
               for f in (premium_loop, book_loop, futures_loop)]
    for t in threads:
        t.start()
    while all(t.is_alive() for t in threads):
        time.sleep(30)
    log.error("a collector thread died; exiting so launchd restarts us")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
