#!/usr/bin/env python3
"""Reset the paper portfolio cash balance safely.

The trading engine uses USDT/USD notional internally because Binance futures are
USDT-margined. For a Korean-won target, convert KRW to USDT and reset cash.

Example for a ₩1,000,000 paper account at 1,400 KRW/USDT:

    python scripts/reset_paper_portfolio.py --krw 1000000 --krw-per-usdt 1400

Also set PAPER_STARTING_CAPITAL to the same USDT value in Railway/Mac env so
P&L percentages use the same starting capital after web redeploy.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import get_storage  # noqa: E402


def _execute_reset(storage: Any, usdt: float, force: bool) -> None:
    open_trades = storage.get_open_trades()
    if open_trades and not force:
        print(f"Refusing to reset: {len(open_trades)} open paper trades exist.")
        print("Close them first or rerun with --force if you intentionally want cash reset only.")
        raise SystemExit(2)

    btc = storage.get_latest_price(config.SYMBOL)
    btc_price = float(btc["close"]) if btc else None
    now_ts = int(datetime.now(timezone.utc).timestamp())

    with storage._connect() as conn:
        conn.execute("DELETE FROM portfolio_state WHERE id = 1")
        conn.execute(
            """
            INSERT INTO portfolio_state
                (id, cash, benchmark_btc_price, benchmark_started_at, updated_at)
            VALUES (1, ?, ?, ?, datetime('now'))
            """,
            (usdt, btc_price, now_ts if btc_price else None),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--krw", type=float, help="KRW target paper capital, e.g. 1000000")
    group.add_argument("--usdt", type=float, help="USDT target paper capital")
    parser.add_argument("--krw-per-usdt", type=float, default=1400.0, help="Conversion rate for KRW display/target")
    parser.add_argument("--force", action="store_true", help="Allow reset even if open trades exist")
    args = parser.parse_args()

    usdt = float(args.usdt if args.usdt is not None else args.krw / args.krw_per_usdt)
    storage = get_storage()
    _execute_reset(storage, usdt, args.force)

    print("Paper portfolio reset complete.")
    print(f"Target USDT: {usdt:.6f}")
    if args.krw is not None:
        print(f"Target KRW: ₩{int(args.krw):,} at {args.krw_per_usdt:,.0f} KRW/USDT")
    print("\nSet this in Railway and Mac .env for consistent P&L percentages:")
    print(f"PAPER_STARTING_CAPITAL={usdt:.6f}")
    print("Then redeploy/restart the web service and Mac worker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
