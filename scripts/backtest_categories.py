#!/usr/bin/env python3
"""Backtest dynamic market categories and category transitions.

This script does not trade. It answers the research question:

    When a symbol is in category X, what happens over the next 1h/4h/24h?
    When a symbol moves from X -> Y, is that move useful or noisy?

Run on Mac mini against Railway Postgres:

    python scripts/backtest_categories.py --days 30 --horizons 1,4,24
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research.market_categories import ensure_schema  # noqa: E402


def _price_after(storage: Any, symbol: str, ts: int) -> float | None:
    rows = storage.get_prices(symbol, limit=1, since=ts, timeframe="1h")
    if not rows:
        return None
    return float(rows[0]["close"])


def _price_before(storage: Any, symbol: str, ts: int) -> float | None:
    rows = storage.get_prices(symbol, limit=1, before=ts + 1, timeframe="1h")
    if not rows:
        return None
    return float(rows[-1]["close"])


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _hit_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v > 0) / len(values) * 100.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--horizons", default="1,4,24", help="Comma-separated hour horizons")
    parser.add_argument("--limit", type=int, default=100000, help="Max category snapshot rows")
    args = parser.parse_args()

    storage = get_storage()
    ensure_schema(storage)
    horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]
    start_ts = int(time.time()) - args.days * 86400

    with storage._connect() as conn:  # noqa: SLF001
        category_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT symbol, timestamp, category, trade_allowed, features_json "
                "FROM market_categories WHERE timestamp >= ? "
                "ORDER BY timestamp ASC LIMIT ?",
                (start_ts, args.limit),
            ).fetchall()
        ]
        transition_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT symbol, from_category, to_category, from_ts, to_ts, reason "
                "FROM market_category_transitions WHERE to_ts >= ? "
                "ORDER BY to_ts ASC LIMIT ?",
                (start_ts, args.limit),
            ).fetchall()
        ]

    by_category: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_allowed: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in category_rows:
        symbol = row["symbol"]
        ts = int(row["timestamp"])
        entry = _price_before(storage, symbol, ts)
        if not entry:
            continue
        category = row["category"]
        allowed_key = "allowed" if int(row.get("trade_allowed") or 0) else "blocked"
        for h in horizons:
            future = _price_after(storage, symbol, ts + h * 3600)
            if future:
                ret = (future - entry) / entry * 100.0
                by_category[category][h].append(ret)
                by_allowed[allowed_key][h].append(ret)

    by_transition: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in transition_rows:
        symbol = row["symbol"]
        ts = int(row["to_ts"])
        entry = _price_before(storage, symbol, ts)
        if not entry:
            continue
        name = f"{row.get('from_category') or 'none'}->{row.get('to_category')}"
        for h in horizons:
            future = _price_after(storage, symbol, ts + h * 3600)
            if future:
                by_transition[name][h].append((future - entry) / entry * 100.0)

    def summarize(bucket: dict[str, dict[int, list[float]]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, per_h in bucket.items():
            out[name] = {}
            for h, values in per_h.items():
                out[name][f"{h}h"] = {
                    "n": len(values),
                    "avg_return_pct": round(_mean(values) or 0.0, 4),
                    "hit_rate_pct": round(_hit_rate(values) or 0.0, 2),
                    "median_return_pct": round(sorted(values)[len(values)//2], 4) if values else None,
                }
        return out

    result = {
        "params": {"days": args.days, "horizons": horizons},
        "category_rows": len(category_rows),
        "transition_rows": len(transition_rows),
        "by_category": summarize(by_category),
        "by_allowed_filter": summarize(by_allowed),
        "by_transition": summarize(by_transition),
    }
    out_path = PROJECT_ROOT / "data" / f"category_backtest_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
