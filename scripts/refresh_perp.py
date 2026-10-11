#!/usr/bin/env python3
"""Keep the '1h_perp' price series current (launchd: com.altcoin.perp, hourly).

load_history.py --perp only reads Binance Vision's monthly archives, so the
series stopped at the last full month it was run for (2026-06-30). This job
pulls closed 1h USDT-M perp klines from the public /fapi/v1/klines endpoint
(no API key) for every symbol already in the series, starting from each
symbol's last stored candle. INSERT is idempotent, so reruns are safe.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402

FAPI = "https://fapi.binance.com/fapi/v1/klines"
HOUR = 3600
LIMIT = 1500              # max candles per request
PAUSE_S = 0.25            # ~10 weight per call -> well under 2400/min
MAX_BACKFILL_DAYS = 120


def _last_ts(storage: Any) -> dict[str, int]:
    with storage._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT symbol, MAX(timestamp) AS ts FROM prices WHERE timeframe = '1h_perp' GROUP BY symbol"
        ).fetchall()
    return {dict(r)["symbol"]: int(dict(r)["ts"]) for r in rows}


def fetch(symbol: str, start_ts: int, end_ts: int, session: requests.Session) -> list[dict[str, Any]]:
    """Closed candles with open time in [start_ts, end_ts)."""
    code = symbol.replace("/", "")
    out: list[dict[str, Any]] = []
    cursor = start_ts
    while cursor < end_ts:
        resp = session.get(FAPI, params={
            "symbol": code, "interval": "1h", "startTime": cursor * 1000,
            "endTime": (end_ts - 1) * 1000, "limit": LIMIT,
        }, timeout=20)
        if resp.status_code == 400:
            raise ValueError(resp.json().get("msg", "bad request"))
        if resp.status_code in (418, 429):
            time.sleep(int(resp.headers.get("Retry-After", "30")))
            continue
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for k in batch:
            ts = int(k[0]) // 1000
            if ts + HOUR > end_ts:          # still-forming candle
                continue
            out.append({"symbol": symbol, "timestamp": ts, "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
        cursor = int(batch[-1][0]) // 1000 + HOUR
        time.sleep(PAUSE_S)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list; default = every symbol already in 1h_perp")
    args = ap.parse_args()

    storage = get_storage()
    last = _last_ts(storage)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or sorted(last)
    now_hour = (int(time.time()) // HOUR) * HOUR
    floor = now_hour - MAX_BACKFILL_DAYS * 86400
    session = requests.Session()
    inserted = skipped = failed = 0
    for sym in symbols:
        start = max(last.get(sym, floor) + HOUR, floor)
        if start >= now_hour:
            continue
        try:
            rows = fetch(sym, start, now_hour, session)
        except ValueError as exc:            # delisted / renamed on futures
            skipped += 1
            print(f"skip {sym}: {exc}")
            continue
        except requests.RequestException as exc:
            failed += 1
            print(f"fail {sym}: {exc}")
            continue
        if rows:
            inserted += storage.insert_prices(rows, timeframe="1h_perp")
    print(f"refresh_perp: {len(symbols)} symbols, {inserted} candles inserted, "
          f"{skipped} skipped (not on futures), {failed} failed")
    return 1 if failed and not inserted else 0


if __name__ == "__main__":
    raise SystemExit(main())
