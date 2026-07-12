#!/usr/bin/env python3
"""Rebuild point-in-time market category snapshots from historical candles.

Historical tick bars are not available for the five-year candle archive, so this
uses the same category feature builder in reduced mode: OHLCV/funding features
are computed as of T, while 30m tick-burst features are left at zero.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import Storage, get_storage  # noqa: E402
from src.research.market_categories import (  # noqa: E402
    CATEGORY_VERSION,
    CategorySnapshot,
    categorize_symbols,
    ensure_schema,
)
from src.symbols import trading_symbols  # noqa: E402


def _parse_time(value: str | None, default: datetime) -> int:
    if not value:
        return int(default.timestamp())
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _insert_snapshots(storage: Storage, snapshots: list[CategorySnapshot], ts: int) -> int:
    rows = [snapshot.row(ts) for snapshot in snapshots]
    with storage._connect() as conn:  # noqa: SLF001
        conn.executemany(
            """
            INSERT OR IGNORE INTO market_categories
                (symbol, timestamp, version, cluster_id, category, trade_allowed,
                 confidence, reason, features_json)
            VALUES
                (:symbol, :timestamp, :version, :cluster_id, :category,
                 :trade_allowed, :confidence, :reason, :features_json)
            """,
            rows,
        )
    return len(rows)


def _symbol_list(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()]
    symbols = trading_symbols()
    if args.limit_symbols and args.limit_symbols > 0:
        symbols = symbols[: args.limit_symbols]
    return symbols


def _coverage(storage: Storage, symbols: list[str], start_ts: int, end_ts: int) -> dict[str, Any]:
    if not symbols:
        return {"bars": 0, "covered": 0, "coverage_pct": 0.0}
    placeholders = ",".join("?" for _ in symbols)
    sql = f"""
        SELECT
            COUNT(*) AS bars,
            SUM(
                CASE WHEN EXISTS (
                    SELECT 1 FROM market_categories mc
                    WHERE mc.symbol = p.symbol
                      AND mc.version = ?
                      AND mc.timestamp <= p.timestamp
                ) THEN 1 ELSE 0 END
            ) AS covered
        FROM prices p
        WHERE p.timeframe = '1h'
          AND p.symbol IN ({placeholders})
          AND p.timestamp BETWEEN ? AND ?
    """
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(sql, (CATEGORY_VERSION, *symbols, start_ts, end_ts)).fetchone()
    item = dict(row) if row else {}
    bars = int(item.get("bars") or 0)
    covered = int(item.get("covered") or 0)
    return {
        "bars": bars,
        "covered": covered,
        "coverage_pct": round((covered / bars * 100.0), 2) if bars else 0.0,
    }


def _era_distribution(storage: Storage) -> dict[str, dict[str, int]]:
    eras = {
        "2021_bull": (datetime(2021, 1, 1, tzinfo=timezone.utc), datetime(2021, 12, 31, tzinfo=timezone.utc)),
        "2022_bear": (datetime(2022, 1, 1, tzinfo=timezone.utc), datetime(2022, 12, 31, tzinfo=timezone.utc)),
        "2024_25": (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2025, 12, 31, tzinfo=timezone.utc)),
    }
    result: dict[str, dict[str, int]] = {}
    with storage._connect() as conn:  # noqa: SLF001
        for name, (start, end) in eras.items():
            rows = conn.execute(
                """
                SELECT category, COUNT(*) AS n
                FROM market_categories
                WHERE version = ? AND timestamp BETWEEN ? AND ?
                GROUP BY category
                ORDER BY n DESC, category ASC
                """,
                (CATEGORY_VERSION, int(start.timestamp()), int(end.timestamp())),
            ).fetchall()
            result[name] = {str(row["category"]): int(row["n"]) for row in rows}
    return result


def rebuild(args: argparse.Namespace) -> dict[str, Any]:
    storage = get_storage()
    ensure_schema(storage)
    symbols = _symbol_list(args)
    now = datetime.now(timezone.utc)
    start_ts = _parse_time(args.start, now - timedelta(days=365 * 5))
    end_ts = _parse_time(args.end, now)
    step_seconds = int(args.step_hours * 3600)
    if step_seconds <= 0:
        raise ValueError("--step-hours must be positive")

    inserted = 0
    steps = 0
    started = time.time()
    ts = start_ts
    while ts <= end_ts:
        snapshots = categorize_symbols(
            storage,
            symbols=symbols,
            now_ts=ts,
            point_in_time=True,
            use_tick_bursts=False,
        )
        # The active assertion lives inside build_feature_rows(); this extra check
        # keeps script-level output honest if the feature builder changes later.
        for snapshot in snapshots:
            max_feature_ts = int(snapshot.features.get("max_candle_ts") or ts)
            assert max_feature_ts <= ts, f"{snapshot.symbol} feature leak {max_feature_ts} > {ts}"
        if not args.dry_run:
            inserted += _insert_snapshots(storage, snapshots, ts)
        steps += 1
        if args.limit_steps and steps >= args.limit_steps:
            break
        ts += step_seconds

    return {
        "symbols": len(symbols),
        "steps": steps,
        "inserted_or_existing": inserted,
        "start_ts": start_ts,
        "end_ts": min(end_ts, ts),
        "step_hours": args.step_hours,
        "point_in_time_assertion": "active",
        "reduced_feature_set": "historical rebuild uses OHLCV/funding as-of T; 30m tick-burst features are zero because tick_bars do not exist for 5y history",
        "coverage": _coverage(storage, symbols, start_ts, min(end_ts, ts)),
        "era_distribution": _era_distribution(storage),
        "elapsed_s": round(time.time() - started, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild point-in-time market category history.")
    parser.add_argument("--start", default=None, help="UTC ISO timestamp/date; default is now minus 5 years")
    parser.add_argument("--end", default=None, help="UTC ISO timestamp/date; default is now")
    parser.add_argument("--step-hours", type=float, default=24.0)
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols; default uses trading_symbols()")
    parser.add_argument("--limit-symbols", type=int, default=0, help="Use the first N trading symbols; 0 means all")
    parser.add_argument("--limit-steps", type=int, default=0, help="Debug cap; 0 means full range")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(rebuild(args), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
