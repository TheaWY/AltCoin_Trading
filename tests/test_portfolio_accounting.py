from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from src import config
from src.data.storage import Storage
from src.engine.paper_trader import PaperTrader


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


def _price(storage: Storage, symbol: str, close: float) -> None:
    storage.insert_prices(
        [
            {
                "symbol": symbol,
                "timestamp": int(time.time()),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1.0,
            }
        ]
    )


def _trade(storage: Storage, symbol: str, direction: str, entry: float, qty: float) -> None:
    storage.insert_paper_trade(
        {
            "signal_id": None,
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry,
            "exit_price": None,
            "quantity": qty,
            "stop_loss": entry * (0.95 if direction == "LONG" else 1.05),
            "take_profit": entry * (1.10 if direction == "LONG" else 0.90),
            "status": "open",
            "pnl": None,
            "opened_at": int(time.time()),
            "closed_at": None,
            "strategy": "test",
            "style": "scalp",
            "atr_pct": None,
            "trail_price": entry,
            "exit_reason": None,
            "fees": None,
        }
    )


class PortfolioAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_start = config.PAPER_STARTING_CAPITAL
        config.PAPER_STARTING_CAPITAL = 1000.0

    def tearDown(self) -> None:
        config.PAPER_STARTING_CAPITAL = self.old_start

    def _summary_for(self, direction: str, current: float) -> dict:
        storage = _storage()
        storage.init_portfolio_state(800.0)
        _trade(storage, "AAA/USDT", direction, 10.0, 20.0)
        _price(storage, "AAA/USDT", current)
        return PaperTrader(storage).summary()

    def test_open_short_profitable_when_price_below_entry(self):
        summary = self._summary_for("SHORT", 8.0)
        self.assertEqual(summary["total_unrealized_pnl"], 40.0)
        self.assertEqual(summary["equity"], 1040.0)

    def test_open_short_losing_when_price_above_entry(self):
        summary = self._summary_for("SHORT", 12.0)
        self.assertEqual(summary["total_unrealized_pnl"], -40.0)
        self.assertEqual(summary["equity"], 960.0)

    def test_open_long_profitable_when_price_above_entry(self):
        summary = self._summary_for("LONG", 12.0)
        self.assertEqual(summary["total_unrealized_pnl"], 40.0)
        self.assertEqual(summary["equity"], 1040.0)

    def test_open_long_losing_when_price_below_entry(self):
        summary = self._summary_for("LONG", 8.0)
        self.assertEqual(summary["total_unrealized_pnl"], -40.0)
        self.assertEqual(summary["equity"], 960.0)

    def test_short_reserved_margin_does_not_make_equity_negative(self):
        summary = self._summary_for("SHORT", 9.0)
        self.assertEqual(summary["available_cash"], 800.0)
        self.assertEqual(summary["reserved_margin"], 200.0)
        self.assertEqual(summary["open_position_value"], 220.0)
        self.assertGreater(summary["equity"], 0.0)

    def test_price_insert_refreshes_existing_candle(self):
        storage = _storage()
        ts = int(time.time())
        base = {
            "symbol": "AAA/USDT",
            "timestamp": ts,
            "timeframe": "1h",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1.0,
        }
        storage.insert_prices([base], timeframe="1h")
        storage.insert_prices([{**base, "close": 11.0, "high": 11.0}], timeframe="1h")

        latest = storage.get_latest_price("AAA/USDT", timeframe="1h")

        self.assertIsNotNone(latest)
        self.assertEqual(latest["close"], 11.0)


if __name__ == "__main__":
    unittest.main()
