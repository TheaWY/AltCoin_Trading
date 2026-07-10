#!/usr/bin/env python3
"""Mac mini server status checker.

This script verifies that the project is no longer pointed at Railway and that
local Postgres/dashboard/data workers are healthy enough to use.

Run:
    source .venv/bin/activate
    python scripts/macmini_status.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

RAILWAY_MARKERS = ("railway", "rlwy", "up.railway.app", "proxy.rlwy.net")


def _bool_icon(value: bool) -> str:
    return "OK" if value else "WARN"


def _has_table(conn: Any, name: str) -> bool:
    if get_storage().is_postgres:
        row = conn.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = ?) AS exists",
            (name,),
        ).fetchone()
        return bool(row["exists"])
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _count(conn: Any, table: str) -> int | None:
    try:
        if not _has_table(conn, table):
            return None
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])
    except Exception:
        return None


def _latest(conn: Any, table: str, ts_col: str = "timestamp") -> int | None:
    try:
        if not _has_table(conn, table):
            return None
        row = conn.execute(f"SELECT MAX({ts_col}) AS ts FROM {table}").fetchone()
        return int(row["ts"]) if row and row["ts"] is not None else None
    except Exception:
        return None


def _api_check() -> dict[str, Any]:
    port = int(os.getenv("API_PORT", str(config.API_PORT)))
    url = f"http://127.0.0.1:{port}/healthz"
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            body = resp.read().decode("utf-8")
        return {"ok": True, "url": url, "ms": round((time.time() - started) * 1000), "body": body[:200]}
    except urllib.error.URLError as exc:
        return {"ok": False, "url": url, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "url": url, "error": repr(exc)}


def _age(ts: int | None) -> str:
    if not ts:
        return "none"
    seconds = int(time.time()) - ts
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def main() -> int:
    db_url = config.DATABASE_URL or ""
    railway_detected = any(marker in db_url.lower() for marker in RAILWAY_MARKERS)
    storage = get_storage()

    print("=== Mac mini AltCoin server status ===")
    print(f"Repo: {PROJECT_ROOT}")
    print(f"DATABASE_URL set: {bool(db_url)}")
    print(f"Railway URL detected: {'yes' if railway_detected else 'no'}")
    print(f"Storage: {'Postgres' if storage.is_postgres else 'SQLite'}")
    print(f"LIVE_TRADING: {config.LIVE_TRADING}")
    print(f"RUN_TRADING_SCHEDULER: {config.RUN_TRADING_SCHEDULER}")
    print(f"TRADING_SYMBOLS_LIMIT: {config.TRADING_SYMBOLS_LIMIT}")
    print("")

    try:
        with storage._connect() as conn:  # noqa: SLF001
            one = conn.execute("SELECT 1 AS ok").fetchone()["ok"]
            print(f"Database query: {one}")
            for table in (
                "prices",
                "funding_rates",
                "long_short_ratio",
                "open_interest",
                "market_categories",
                "tick_bars",
                "paper_trades",
                "experiments",
            ):
                n = _count(conn, table)
                ts = _latest(conn, table, "timestamp" if table not in {"experiments"} else "created_at")
                print(f"{table:22s} rows={n if n is not None else 'missing':>10} latest_age={_age(ts)}")
    except Exception as exc:
        print(f"Database query failed: {exc!r}")
        return 2

    print("")
    for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]:
        rows = storage.get_prices(sym, limit=3, timeframe="1h")
        latest = rows[-1] if rows else None
        print(f"{sym:9s} candles={len(rows):>2} latest={latest['close'] if latest else None} age={_age(int(latest['timestamp']) if latest else None)}")

    print("")
    api = _api_check()
    print(f"Dashboard API: {_bool_icon(api['ok'])} {api['url']} {json.dumps(api, ensure_ascii=False)}")

    if railway_detected:
        print("\nWARN: DATABASE_URL still points to Railway. Replace it with localhost Postgres before using Mac mini server mode.")
    if not storage.is_postgres:
        print("\nWARN: Storage is SQLite. That is okay for quick tests, but local Postgres is recommended for tick bars/research.")

    print("======================================")
    return 1 if railway_detected else 0


if __name__ == "__main__":
    raise SystemExit(main())
