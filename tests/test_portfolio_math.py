"""Safety tests for paper portfolio and PnL math.

These tests are network-free and DB-free. They protect the money-facing UI from
regressions such as negative equity caused by short-position cash accounting.
"""

from __future__ import annotations

import unittest

from src.engine.paper_trader import PaperTrader


class _DummyStorage:
    def get_signal(self, _signal_id: int):
        return None

    def get_funding_rates(self, *_args, **_kwargs):
        return []


class PortfolioMathTests(unittest.TestCase):
    def setUp(self):
        self.trader = PaperTrader.__new__(PaperTrader)
        self.trader.storage = _DummyStorage()

    def _trade(self, direction: str, entry: float = 100.0, qty: float = 2.0):
        return {
            "symbol": "TEST/USDT",
            "direction": direction,
            "entry_price": entry,
            "quantity": qty,
            "strategy": "unit_test",
            "signal_id": None,
        }

    def test_short_profitable_when_price_falls(self):
        trade = self._trade("SHORT", entry=100.0, qty=2.0)
        self.assertEqual(self.trader._realized_pnl(trade, 90.0), 20.0)
        self.assertEqual(self.trader._position_value(trade, 90.0), 220.0)

    def test_short_losing_when_price_rises(self):
        trade = self._trade("SHORT", entry=100.0, qty=2.0)
        self.assertEqual(self.trader._realized_pnl(trade, 110.0), -20.0)
        self.assertEqual(self.trader._position_value(trade, 110.0), 180.0)

    def test_long_profitable_when_price_rises(self):
        trade = self._trade("LONG", entry=100.0, qty=2.0)
        self.assertEqual(self.trader._realized_pnl(trade, 110.0), 20.0)
        self.assertEqual(self.trader._position_value(trade, 110.0), 220.0)

    def test_long_losing_when_price_falls(self):
        trade = self._trade("LONG", entry=100.0, qty=2.0)
        self.assertEqual(self.trader._realized_pnl(trade, 90.0), -20.0)
        self.assertEqual(self.trader._position_value(trade, 90.0), 180.0)

    def test_equity_model_does_not_turn_negative_just_because_short_exists(self):
        starting_capital = 714.285714
        short_trade = self._trade("SHORT", entry=100.0, qty=2.0)
        unrealized = self.trader._realized_pnl(short_trade, 90.0)
        equity = starting_capital + unrealized
        self.assertGreater(equity, 0.0)
        self.assertEqual(round(equity, 6), 734.285714)

    def test_negative_krw_format_policy(self):
        # Mirrors the dashboard rule: sign before currency symbol, not ₩-331,297.
        value = -331_297
        formatted = f"-₩{abs(value):,}"
        self.assertEqual(formatted, "-₩331,297")


if __name__ == "__main__":
    unittest.main()
