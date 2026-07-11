#!/usr/bin/env python3
"""Scan and optionally repair candle quality issues.

Defaults to dry-run. Use ``--apply`` before any database writes. ``--download``
attempts authoritative replacement downloads for affected symbols after
invalid/partial rows are removed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.collectors.binance import BinanceCollector, _build_exchange  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from src.market.bars import is_bar_closed, timeframe_to_seconds, validate_bars  # noqa: E402
from src.symbols import ccxt_symbol, trading_symbols  # noqa: E402


def _scan_symbol(storage, symbol: str, timeframe: str, now_ts: int) -> dict[str, Any]:
    rows = storage.get_prices(symbol, limit=1_000_000, since=0, timeframe=timeframe)
    issues = validate_bars(rows, timeframe)
    counts: dict[str, int] = defaultdict(int)
    invalid_ts: set[int] = set()
    for issue in issues:
        counts[issue.reason] += 1
        if issue.timestamp is not None and issue.reason != "missing_interval":
            invalid_ts.add(int(issue.timestamp))

    partial_ts = [
        int(row["timestamp"])
        for row in rows
        if not is_bar_closed(row, timeframe, now_ts)
    ]
    if partial_ts:
        counts["potentially_partial"] += len(partial_ts)
        invalid_ts.update(partial_ts)

    duplicates = 0
    seen: set[int] = set()
    for row in rows:
        ts = int(row["timestamp"])
        if ts in seen:
            duplicates += 1
            invalid_ts.add(ts)
        seen.add(ts)
    if duplicates:
        counts["duplicate"] += duplicates

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "rows": len(rows),
        "issues": dict(counts),
        "repair_timestamps": sorted(invalid_ts),
        "min_timestamp": int(rows[0]["timestamp"]) if rows else None,
        "max_timestamp": int(rows[-1]["timestamp"]) if rows else None,
    }


def _delete_rows(storage, symbol: str, timeframe: str, timestamps: list[int]) -> int:
    if not timestamps:
        return 0
    deleted = 0
    with storage._connect() as conn:  # noqa: SLF001
        for ts in timestamps:
            cursor = conn.execute(
                "DELETE FROM prices WHERE symbol = ? AND timeframe = ? AND timestamp = ?",
                (symbol, timeframe, ts),
            )
            deleted += max(cursor.rowcount, 0)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    apply_changes = bool(args.apply)
    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else trading_symbols()
    )
    storage = get_storage()
    now_ts = int(datetime.now(timezone.utc).timestamp())
    before = [_scan_symbol(storage, symbol, args.timeframe, now_ts) for symbol in symbols]

    repairs = []
    if apply_changes:
        exchange = _build_exchange(authenticated=False) if args.download else None
        for item in before:
            deleted = _delete_rows(
                storage,
                item["symbol"],
                item["timeframe"],
                item["repair_timestamps"],
            )
            downloaded = 0
            if args.download and exchange is not None and item["repair_timestamps"]:
                collector = BinanceCollector(
                    storage=storage,
                    exchange=exchange,
                    symbol=item["symbol"],
                    futures_symbol=ccxt_symbol(item["symbol"]),
                )
                report = collector.collect_ohlcv_report(item["timeframe"])
                downloaded = int(report.get("rows_inserted") or 0)
            repairs.append(
                {
                    "symbol": item["symbol"],
                    "timeframe": item["timeframe"],
                    "deleted": deleted,
                    "downloaded": downloaded,
                }
            )

    after = [_scan_symbol(storage, symbol, args.timeframe, now_ts) for symbol in symbols]
    report = {
        "dry_run": not apply_changes,
        "apply": apply_changes,
        "download": bool(args.download),
        "before": before,
        "repairs": repairs,
        "after": after,
    }
    output = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

