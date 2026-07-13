"""Integration test: orderbook collection wired into the real trading cycle.

Unlike the other tests/ files, this hits real public Binance endpoints (no
credentials needed) -- proving the actual collector wiring and API contract,
not just the internal logic. Runs against an ISOLATED database (own temp
sqlite file, static 1-symbol universe) so it never touches production state
and is safe to run repeatedly / concurrently with the live worker.

    python tests/test_orderbook_cycle_integration.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_PATH"] = str(_tmp / "cycle_integration.db")
os.environ["DATABASE_URL"] = ""
os.environ["DATA_DIR"] = str(_tmp)
os.environ["SYMBOL_UNIVERSE"] = "static"
os.environ["TRADING_SYMBOLS"] = "BTC/USDT"
os.environ["ACTIVE_TRADING_SYMBOLS_LIMIT"] = "1"
os.environ["LIVE_TRADING"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.storage import get_storage  # noqa: E402
from src.engine.cycle import run_trading_cycle  # noqa: E402


class OrderbookCycleIntegrationTests(unittest.TestCase):
    def test_cycle_collects_orderbook_for_active_union_open_symbols(self):
        storage = get_storage()
        with storage._connect() as conn:  # noqa: SLF001
            before = conn.execute("SELECT COUNT(*) AS n FROM orderbook_snapshots").fetchone()
        before_n = dict(before)["n"]

        t0 = time.time()
        result = run_trading_cycle(storage)
        elapsed = time.time() - t0

        self.assertLess(elapsed, 300, "cycle must complete inside 5 minutes")

        with storage._connect() as conn:  # noqa: SLF001
            after = conn.execute("SELECT COUNT(*) AS n FROM orderbook_snapshots").fetchone()
            rows = conn.execute(
                "SELECT symbol, timestamp FROM orderbook_snapshots ORDER BY id DESC LIMIT 5"
            ).fetchall()
        after_n = dict(after)["n"]

        self.assertGreater(after_n, before_n, "orderbook_snapshots must gain rows this cycle")
        symbols_seen = {dict(r)["symbol"] for r in rows}
        self.assertIn("BTC/USDT", symbols_seen)

        # timestamps must be seconds, not ms -- a 13-digit ms value would be
        # ~1.7e12, a correct seconds value for 2026 is ~1.78e9.
        for r in rows:
            ts = dict(r)["timestamp"]
            self.assertLess(ts, 10_000_000_000, f"timestamp {ts} looks like milliseconds, not seconds")

        self.assertIsInstance(result, dict)

    def test_forced_collector_exception_does_not_halt_cycle(self):
        storage = get_storage()
        with patch(
            "src.data.collectors.orderbook.run_orderbook_collection",
            side_effect=RuntimeError("forced failure for T2"),
        ):
            try:
                result = run_trading_cycle(storage)
            except Exception as exc:  # pragma: no cover - failure path under test
                self.fail(f"run_trading_cycle raised despite orderbook collector failing: {exc}")
        self.assertIsInstance(result, dict)

    def test_idempotent_rerun_does_not_duplicate_rows(self):
        """Re-running collection for the same instant must not create
        duplicate rows (UNIQUE(symbol, timestamp) + INSERT OR IGNORE)."""
        from src.data.collectors.orderbook import OrderbookCollector
        from src.data.collectors.binance import _build_exchange
        from src import config

        storage = get_storage()
        exchange = _build_exchange(use_testnet=config.BINANCE_MARKET_DATA_TESTNET, authenticated=False)
        collector = OrderbookCollector("BTC/USDT", exchange, storage)

        # freeze "now" so both calls target the same timestamp
        with patch("src.data.collectors.orderbook.time.time", return_value=1_800_000_000.0):
            first = collector.collect()
            second = collector.collect()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0, "re-collecting the same second must be a no-op, not a duplicate")


if __name__ == "__main__":
    unittest.main()
