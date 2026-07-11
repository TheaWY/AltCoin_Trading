#!/usr/bin/env python3
"""Point-in-time historical replay for the production strategy path.

This intentionally does not reuse draft PR #9's same-candle loop. It delegates
to ``scripts.backtest.BacktestEngine``, whose event loop queues signals after a
completed bar and fills no earlier than the next bar open.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest import (  # noqa: E402
    BacktestEngine,
    _default_end,
    _default_start,
    _parse_datetime,
    _parse_symbols,
    resolve_replay_storage,
)
from src import config  # noqa: E402


def run_replay(
    *,
    start: datetime,
    end: datetime,
    symbols: list[str],
    strategy: str,
    database_path: str | None = None,
) -> dict:
    return BacktestEngine(
        start=start,
        end=end,
        symbols=symbols,
        strategy_name=strategy,
        storage=resolve_replay_storage(database_path),
    ).run()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--strategy", default=config.PRIMARY_STRATEGY)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--database-path",
        default=None,
        help="Separate SQLite database for replay data. Refuses live DATABASE_PATH/DATABASE_URL.",
    )
    args = parser.parse_args()

    start = _parse_datetime(args.start) if args.start else _default_start()
    end = _parse_datetime(args.end) if args.end else _default_end()
    symbols = _parse_symbols(args.symbols)
    report = run_replay(
        start=start,
        end=end,
        symbols=symbols,
        strategy=args.strategy,
        database_path=args.database_path,
    )

    output_path = (
        Path(args.out)
        if args.out
        else PROJECT_ROOT / "data" / f"backtest_eval_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
