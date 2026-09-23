#!/usr/bin/env python3
"""Backfill 1-minute klines for every USDT perp (resumable, idempotent).

    .venv/bin/python scripts/backfill_1m.py --days 14

Uses public /fapi/v1/klines (1500 bars per call, weight 10). Paced to stay
well under Binance's 2400/min weight limit.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.collectors import klines_1m as k1  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

PAUSE_S = 0.3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--symbols", default="")
    args = ap.parse_args()
    storage = get_storage()
    k1.ensure_schema(storage)
    symbols = [s for s in args.symbols.split(",") if s] or k1.usdt_perps()
    now_min = int(time.time()) // 60 * 60
    start_floor = now_min - args.days * 86400
    have = k1.last_ts(storage)
    session = requests.Session()
    total = 0
    for i, sym in enumerate(symbols):
        cursor = max(start_floor, have.get(sym, 0) + 60)
        rows = []
        while cursor < now_min:
            r = session.get(f"{k1.FAPI}/fapi/v1/klines", params={
                "symbol": k1.code(sym), "interval": "1m", "startTime": cursor * 1000,
                "endTime": (now_min - 1) * 1000, "limit": 1500}, timeout=20)
            if r.status_code in (418, 429):
                time.sleep(int(r.headers.get("Retry-After", "30")))
                continue
            if r.status_code != 200:
                print(f"skip {sym}: {r.status_code} {r.text[:80]}")
                break
            batch = r.json()
            if not batch:
                break
            rows += [k1.row_from_rest(sym, k) for k in batch if int(k[0]) // 1000 + 60 <= now_min]
            cursor = int(batch[-1][0]) // 1000 + 60
            time.sleep(PAUSE_S)
        total += k1.insert_rows(storage, rows)
        if i % 25 == 0:
            print(f"[{i + 1}/{len(symbols)}] {sym}: +{len(rows)} (total {total})", flush=True)
    print(f"backfill done: {len(symbols)} symbols, {total} bars", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
