#!/usr/bin/env python3
"""Storage smoke test — exercises every table on SQLite and (optionally) Postgres.

Usage:
    python scripts/storage_smoke.py                  # SQLite in a temp file
    DATABASE_URL=postgres://... python scripts/storage_smoke.py   # also Postgres
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import Storage  # noqa: E402


def exercise(storage: Storage, label: str) -> None:
    now = int(datetime.now(timezone.utc).timestamp())

    inserted = storage.insert_prices(
        [
            {
                "symbol": "BTC/USDT",
                "timestamp": now - 3600 * i,
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 100.0 + i,
                "volume": 1000.0 + i,
            }
            for i in range(30)
        ]
    )
    assert inserted == 30, f"prices inserted={inserted}"
    # duplicate insert must be ignored, not fail
    dup = storage.insert_prices(
        [
            {
                "symbol": "BTC/USDT",
                "timestamp": now,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
        ]
    )
    assert dup == 0, f"duplicate prices inserted={dup}"

    latest = storage.get_latest_price("BTC/USDT")
    assert latest and float(latest["close"]) == 100.0, latest
    assert len(storage.get_prices("BTC/USDT", limit=10)) == 10
    stats = storage.get_volume_stats("BTC/USDT")
    assert stats["count"] == 24 and stats["avg"], stats

    storage.insert_funding_rates(
        [{"symbol": "BTC/USDT", "timestamp": now, "funding_rate": 0.0012}]
    )
    fr = storage.get_latest_funding_rate("BTC/USDT")
    assert fr and abs(float(fr["funding_rate"]) - 0.0012) < 1e-9, fr

    signal_id = storage.insert_signal(
        {
            "strategy": "funding_rate",
            "symbol": "BTC/USDT",
            "timestamp": now,
            "direction": "SHORT",
            "reason": "smoke",
            "entry_price": 100.0,
            "funding_rate": 0.0012,
            "metadata": "{}",
        }
    )
    assert signal_id > 0
    assert storage.get_signal(signal_id)["direction"] == "SHORT"
    assert storage.get_latest_signal(symbol="BTC/USDT", strategy="funding_rate")

    trade_id = storage.insert_paper_trade(
        {
            "signal_id": signal_id,
            "symbol": "BTC/USDT",
            "direction": "SHORT",
            "entry_price": 100.0,
            "exit_price": None,
            "quantity": 1.0,
            "stop_loss": 103.0,
            "take_profit": 94.0,
            "status": "open",
            "pnl": None,
            "opened_at": now,
            "closed_at": None,
        }
    )
    assert storage.count_open_trades() == 1
    storage.update_paper_trade(
        trade_id, {"status": "closed", "exit_price": 94.0, "pnl": 6.0, "closed_at": now}
    )
    assert storage.count_open_trades() == 0
    assert storage.get_recent_closed_trades()[0]["pnl"] == 6.0

    outcome_id = storage.insert_market_outcome(
        {
            "signal_id": signal_id,
            "symbol": "BTC/USDT",
            "direction": "SHORT",
            "entry_price": 100.0,
            "price_1h": 99.0,
            "price_4h": None,
            "price_24h": None,
            "correct_1h": 1,
            "correct_4h": None,
            "correct_24h": None,
        }
    )
    storage.update_market_outcome(outcome_id, {"price_24h": 95.0, "correct_24h": 1})
    assert storage.get_incomplete_market_outcomes()[0]["id"] == outcome_id
    accuracy = storage.get_signal_accuracy(days=7)
    assert accuracy["total"] == 1 and accuracy["correct"] == 1, accuracy
    assert storage.get_actionable_signals_without_outcomes() == []

    state = storage.init_portfolio_state(10000.0, benchmark_btc_price=100.0)
    assert float(state["cash"]) == 10000.0
    storage.update_portfolio_cash(9000.0)
    assert float(storage.get_portfolio_state()["cash"]) == 9000.0
    storage.set_benchmark_price(101.0)

    deleted = storage.cleanup_old_prices()
    assert isinstance(deleted, dict)

    print(f"[OK] {label}: all storage operations passed")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        sqlite_storage = Storage(db_path=Path(tmp) / "smoke.db", database_url="")
        exercise(sqlite_storage, "sqlite")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        pg = Storage(database_url=database_url)
        # start from a clean slate for repeatable assertions
        with pg._connect() as conn:
            for table in (
                "market_outcomes",
                "paper_trades",
                "signals",
                "funding_rates",
                "prices",
                "portfolio_state",
            ):
                conn.raw.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
        exercise(pg, "postgres")
    else:
        print("[SKIP] postgres: DATABASE_URL not set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
