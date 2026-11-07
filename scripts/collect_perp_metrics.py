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
logging.raiseExceptions = False  # a failing log handler must never print its own traceback storm (2026-09-30: 18GB err log filled the disk)


class Throttle:
    """Log the first error of a kind, then at most one summary per `every` seconds, and
    back off the caller while the same error keeps repeating (e.g. DB PoolTimeout)."""
    def __init__(self, name: str, every: float = 300.0) -> None:
        self.name, self.every, self.n, self.last, self.fails = name, every, 0, 0.0, 0

    def error(self, msg: str, *a) -> None:
        self.n += 1
        self.fails += 1
        now = time.time()
        if self.n == 1 or now - self.last >= self.every:
            log.error("[%s] " + msg + " (x%d since last report)", self.name, *a, self.n)
            self.n, self.last = 0, now
        time.sleep(min(60.0, 2 ** min(self.fails, 6)))  # 2s, 4s .. 60s while failures continue

    def ok(self) -> None:
        self.fails = 0
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
    th = Throttle("premium")
    while True:
        t0 = time.time()
        try:
            n = pm.insert_premium(storage, pm.fetch_premium(s, set(syms.get())))
            if int(t0) % 3600 < 60:
                log.info("premium: %d rows", n)
            th.ok()
        except Exception as e:  # noqa: BLE001
            th.error("premium failed: %r", e)
        if t0 - last_prune > 86400:
            last_prune = t0
            try:
                pm.prune(storage)
            except Exception:  # noqa: BLE001
                log.exception("prune failed")
        time.sleep(max(1.0, 60 - (time.time() % 60) + 2))


def hot_symbols(storage, n: int = 30) -> list[str]:
    """The coins most likely to move right now: the live TOP 10 plus the highest 1h volatility."""
    import json as _json
    out: list[str] = []
    try:
        row = storage.get_system_status("move_top10")
        out = [c["symbol"] for c in _json.loads(row["value"]).get("coins", [])] if row and row.get("value") else []
    except Exception:  # noqa: BLE001
        out = []
    try:
        with storage._connect() as c:  # noqa: SLF001
            rows = c.execute("SELECT symbol, (MAX(high) / NULLIF(MIN(low), 0)) AS rg FROM prices_1m WHERE ts > ? "
                             "GROUP BY symbol HAVING SUM(quote_volume) > 200000 ORDER BY rg DESC LIMIT ?",
                             (int(time.time()) - 3600, n)).fetchall()
        out += [r["symbol"] for r in rows]
    except Exception:  # noqa: BLE001
        pass
    seen: list[str] = []
    for s_ in out:
        if s_ not in seen:
            seen.append(s_)
    return seen[:n]


def hot_book_loop(storage) -> None:
    """Every minute: order book of the ~30 hottest coins -> book_1m (weight ~150/min)."""
    s = requests.Session()
    hb = Throttle("hot_book")
    while True:
        t0 = time.time()
        rows = []
        for sym in hot_symbols(storage):
            try:
                rows.append(pm.fetch_book(s, sym))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
        try:
            pm.insert_book(storage, rows, table="book_1m")
        except Exception as e:  # noqa: BLE001
            hb.error("hot book insert: %r", e)
        time.sleep(max(1.0, 60 - (time.time() - t0)))


def book_loop(storage, syms: Symbols) -> None:
    s = requests.Session()
    th = Throttle("book")

    def flush(rows):
        try:
            pm.insert_book(storage, rows)
            th.ok()
        except Exception as e:  # noqa: BLE001
            th.error("book insert failed: %r", e)
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
                flush(rows)
                rows = []
            time.sleep(pause)
        flush(rows)
        log.info("book lap: %d symbols in %.0fs", len(lst), time.time() - t0)


def futures_loop(storage, syms: Symbols) -> None:
    s = requests.Session()
    try:
        backfill(storage, BACKFILL_DAYS, only_missing=True)
    except Exception:  # noqa: BLE001
        log.exception("startup backfill failed")
    th = Throttle("futures")
    while True:
        t0 = time.time()
        lst = syms.get()
        for sym in lst:
            try:
                pm.insert_futures(storage, sym, pm.fetch_futures(s, sym, limit=6, pause=FUT_PAUSE))
                th.ok()
            except Exception as e:  # noqa: BLE001
                th.error("futures %s failed: %r", sym, e)
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
    threads.append(threading.Thread(target=hot_book_loop, args=(storage,), daemon=True, name="hot_book_loop"))
    for t in threads:
        t.start()
    while all(t.is_alive() for t in threads):
        time.sleep(30)
    log.error("a collector thread died; exiting so launchd restarts us")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
