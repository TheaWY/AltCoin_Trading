"""Data quality monitor — silent data gaps poison everything downstream.

A collection gap (Mac slept, API outage, network blip) corrupts backtests
and fresh evals QUIETLY: the replay just sees fewer bars and nobody notices.
This module makes gaps loud:

  - per-source freshness: seconds since last row (prices per symbol, funding,
    market_metrics) with a status vs the expected cadence
  - gap scan: intervals > 2x expected cadence recorded into data_gaps
  - NTP offset check (macOS sntp) — clock drift on an hourly system creates
    silent look-ahead
  - a single overall verdict the cycle can consult before opening entries,
    and the dashboard can render as a status board

    python -m src.research.data_quality            # report
    python -m src.research.data_quality --scan     # also record gaps
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from typing import Any

from src import config
from src.data.storage import get_storage

EXPECTED_CADENCE_S = {
    "prices_1h": 3600,
    "funding_rates": 3600,      # collected every cycle; settlement every 8h
    "market_metrics": 3600,
}
FRESH_FACTOR = 2.0              # stale when age > factor * cadence
GAP_FACTOR = 2.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    symbol TEXT,
    gap_start INTEGER NOT NULL,
    gap_end INTEGER NOT NULL,
    gap_seconds INTEGER NOT NULL,
    detected_at INTEGER NOT NULL,
    UNIQUE(source, symbol, gap_start)
);
"""


def _ensure_schema() -> None:
    with get_storage()._connect() as conn:  # noqa: SLF001
        for stmt in _SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(stmt)


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def _symbols() -> list[str]:
    with get_storage()._connect() as conn:  # noqa: SLF001
        rows = conn.execute("SELECT DISTINCT symbol FROM prices").fetchall()
        return [str(_row_value(r, "symbol")) for r in rows]


def _last_ts(table: str, symbol: str | None) -> int | None:
    query = f"SELECT MAX(timestamp) FROM {table}"
    params: tuple = ()
    if symbol:
        query += " WHERE symbol = ?"
        params = (symbol,)
    with get_storage()._connect() as conn:  # noqa: SLF001
        row = conn.execute(query, params).fetchone()
        timestamp = _row_value(row, "max", 0) if row else None
        return int(timestamp) if timestamp else None


def freshness() -> list[dict[str, Any]]:
    now = int(time.time())
    out = []
    for symbol in _symbols():
        last = _last_ts("prices", symbol)
        age = now - last if last else None
        out.append({
            "source": "prices_1h", "symbol": symbol, "last_ts": last, "age_s": age,
            "status": "missing" if last is None else
            ("stale" if age > EXPECTED_CADENCE_S["prices_1h"] * FRESH_FACTOR else "ok"),
        })
    for table in ("funding_rates", "market_metrics"):
        last = _last_ts(table, None)
        age = now - last if last else None
        out.append({
            "source": table, "symbol": None, "last_ts": last, "age_s": age,
            "status": "missing" if last is None else
            ("stale" if age > EXPECTED_CADENCE_S[table] * FRESH_FACTOR else "ok"),
        })
    return out


def scan_gaps(days: int = 30) -> list[dict[str, Any]]:
    _ensure_schema()
    since = int(time.time()) - days * 86400
    found = []
    cadence = EXPECTED_CADENCE_S["prices_1h"]
    for symbol in _symbols():
        with get_storage()._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT timestamp FROM prices WHERE symbol = ? AND timestamp >= ? "
                "ORDER BY timestamp",
                (symbol, since),
            ).fetchall()
        stamps = [int(_row_value(r, "timestamp")) for r in rows]
        for prev, cur in zip(stamps, stamps[1:]):
            if cur - prev > cadence * GAP_FACTOR:
                gap = {
                    "source": "prices_1h", "symbol": symbol,
                    "gap_start": prev, "gap_end": cur,
                    "gap_seconds": cur - prev,
                }
                found.append(gap)
                with get_storage()._connect() as conn:  # noqa: SLF001
                    conn.execute(
                        "INSERT OR IGNORE INTO data_gaps (source, symbol, gap_start, "
                        "gap_end, gap_seconds, detected_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (gap["source"], gap["symbol"], gap["gap_start"],
                         gap["gap_end"], gap["gap_seconds"], int(time.time())),
                    )
    return found


def ntp_offset() -> dict[str, Any]:
    """macOS: parse `sntp time.apple.com` offset. Non-fatal anywhere else."""
    try:
        proc = subprocess.run(
            ["sntp", "time.apple.com"], capture_output=True, text=True, timeout=10
        )
        for token in proc.stdout.split():
            try:
                offset = float(token)
            except ValueError:
                continue
            return {"offset_s": offset, "ok": abs(offset) < 5.0}
    except Exception:
        pass
    return {"offset_s": None, "ok": None, "note": "sntp unavailable"}


def verdict() -> dict[str, Any]:
    fresh = freshness()
    stale = [f for f in fresh if f["status"] != "ok"]
    clock = ntp_offset()
    healthy = not stale and clock.get("ok") is not False
    return {
        "healthy": healthy,
        "stale_sources": stale,
        "clock": clock,
        "checked_at": int(time.time()),
        "action": "ok to trade" if healthy else "HALT new entries (data health)",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    report: dict[str, Any] = {"verdict": verdict(), "freshness": freshness()}
    if args.scan:
        report["gaps_found"] = scan_gaps(args.days)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
