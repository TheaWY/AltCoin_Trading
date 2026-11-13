#!/usr/bin/env python3
"""Reset the paper portfolio cash balance safely.

The trading engine uses USDT/USD notional internally because Binance futures are
USDT-margined. For a Korean-won target, convert KRW to USDT and reset cash.

Example for a ₩1,000,000 paper account at 1,400 KRW/USDT:

    python scripts/reset_paper_portfolio.py --krw 1000000 --krw-per-usdt 1400

Also set PAPER_STARTING_CAPITAL to the same USDT value in Railway/Mac env so
P&L percentages use the same starting capital after web redeploy.

Full reset (2026-09-28): a portfolio reset is not complete until the dashboard chart restarts too. By default this script now
  1. backs up paper_trades, portfolio_state, benchmark_equity, benchmark_meta to data/backups/paper_reset_<time>/
  2. with --close-open: closes every open paper trade at the latest 1-minute close (exit_reason manual_reset)
  3. resets cash + benchmark start (portfolio_state)
  4. clears the benchmark chart: DELETE benchmark_equity + benchmark_meta, moves data/benchmarks/random_*.db into the backup
     (btc_hold / alt_hold / random books re-initialise at the new start on the next worker cycle). --keep-benchmarks skips this.
Stop the worker first (launchctl bootout gui/$(id -u)/com.altcoin.worker) and bootstrap it again afterwards.
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

import shutil  # noqa: E402
import time  # noqa: E402


def _backup(storage: Any) -> Path:
    import pandas as pd
    bk = PROJECT_ROOT / "data/backups" / f"paper_reset_{time.strftime('%Y%m%d_%H%M%S')}"
    bk.mkdir(parents=True, exist_ok=True)
    with storage._connect() as conn:
        for tb in ("paper_trades", "portfolio_state", "benchmark_equity", "benchmark_meta"):
            try:
                rows = [dict(r) for r in conn.execute(f"SELECT * FROM {tb}").fetchall()]
            except Exception:  # noqa: BLE001 -- table may not exist yet
                continue
            pd.DataFrame(rows).to_parquet(bk / f"{tb}.parquet")
            print(f"backup {tb}: {len(rows)} rows")
    return bk


def _close_open(storage: Any) -> None:
    from src.engine.signal_book import _close
    now = int(datetime.now(timezone.utc).timestamp())
    for t in storage.get_open_trades():
        with storage._connect() as conn:
            r = conn.execute("SELECT close FROM prices_1m WHERE symbol = ? ORDER BY ts DESC LIMIT 1", (t["symbol"],)).fetchone()
        px = float(dict(r)["close"]) if r else float(t["entry_price"])
        _close(storage, t, px, now, "manual_reset")
        print(f"closed {t['symbol']} {t['direction']} at {px:g}")


def _reset_benchmarks(storage: Any, bk: Path) -> None:
    with storage._connect() as conn:
        for tb in ("benchmark_equity", "benchmark_meta"):
            try:
                conn.execute(f"DELETE FROM {tb}")
            except Exception:  # noqa: BLE001
                pass
    rnd = PROJECT_ROOT / "data/benchmarks"; dest = bk / "benchmarks_random"; dest.mkdir(exist_ok=True)
    moved = 0
    for f in rnd.glob("random_*.db"):
        shutil.move(str(f), dest / f.name); moved += 1
    print(f"benchmark chart cleared; {moved} random-seed books moved to {dest}")


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
    parser.add_argument("--close-open", action="store_true", help="Close all open paper trades at the latest 1m close first")
    parser.add_argument("--keep-benchmarks", action="store_true", help="Do NOT clear the dashboard benchmark chart")
    args = parser.parse_args()

    usdt = float(args.usdt if args.usdt is not None else args.krw / args.krw_per_usdt)
    storage = get_storage()
    bk = _backup(storage)
    if args.close_open:
        _close_open(storage)
    _execute_reset(storage, usdt, args.force)
    if not args.keep_benchmarks:
        _reset_benchmarks(storage, bk)
    print(f"Backup: {bk}")

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
