"""Signal book: exchange-minimum-aware sizing and exact paper accounting."""

from __future__ import annotations

import unittest
from unittest import mock

from src.engine import signal_book as sb
from tests.test_hypothesis_engine import _FakeStorage


class TargetBookTests(unittest.TestCase):
    def test_legs_clear_exchange_minimum(self):
        scores = {f"S{i}/USDT": float(i) for i in range(60)}
        book = sb.target_book(scores, 100.0, {})
        legs = [n for _, n in book.values()]
        self.assertTrue(all(n >= sb.MIN_LEG_USD for n in legs))
        self.assertEqual(sum(d == "LONG" for d, _ in book.values()), sum(d == "SHORT" for d, _ in book.values()))
        self.assertAlmostEqual(sum(legs), 100.0)
        self.assertIn("S59/USDT", book)
        self.assertEqual(book["S0/USDT"][0], "SHORT")

    def test_symbols_with_high_minimum_are_skipped(self):
        scores = {f"S{i}/USDT": float(i) for i in range(30)}
        scores["BTC/USDT"] = 99.0
        book = sb.target_book(scores, 100.0, {"BTC/USDT": 50.0})
        self.assertNotIn("BTC/USDT", book)

    def test_too_small_allocation_is_flat(self):
        self.assertEqual(sb.target_book({"A/USDT": 1.0, "B/USDT": 2.0}, 5.0, {}), {})


class _Store(_FakeStorage):
    def __init__(self, cash, prices):
        super().__init__(cash, 0.0)
        self.prices, self.status = prices, {}

    def get_latest_price(self, s):
        return {"close": self.prices[s]} if s in self.prices else None

    def get_system_status(self, k):
        return {"value": self.status[k]} if k in self.status else None

    def set_system_status(self, k, v):
        self.status[k] = v


class RebalanceTests(unittest.TestCase):
    def _run(self, st, scores, now):
        def summary(_self, _p):
            eq = st.cash + sum(sb._value(t, st.prices[t["symbol"]]) for t in st.get_open_trades())
            return {"equity": eq}

        with mock.patch.object(sb.config, "SIGNAL_BOOK_ENABLED", True, create=True), \
             mock.patch.object(sb.config, "SIGNAL_BOOK_PCT", 0.10, create=True), \
             mock.patch.object(sb, "min_notional_map", return_value={}), \
             mock.patch("src.engine.paper_trader.PaperTrader.summary", summary), \
             mock.patch("src.engine.paper_trader.PaperTrader.__init__", lambda self, s: None):
            return sb.run_signal_book_cycle(st, now=now, scores=scores)

    def test_open_mark_close_conserves_money(self):
        scores = {f"S{i}/USDT": float(i) for i in range(40)}
        st = _Store(1000.0, {s: 10.0 for s in scores})
        st.prices["BTC/USDT"] = 50000.0
        r = self._run(st, scores, 100_000)
        self.assertEqual(r["action"], "rebalance")
        open_fees = sum(t["fees"] for t in st.get_open_trades())
        eq = st.cash + sum(sb._value(t, 10.0) for t in st.get_open_trades())
        self.assertAlmostEqual(eq, 1000.0 - open_fees, places=6)
        # inside 24h: no trading
        self.assertEqual(self._run(st, scores, 100_000 + 3600)["action"], "hold")
        # same ranking a day later: legs survive, no new fees
        self._run(st, scores, 100_000 + 86_400)
        self.assertAlmostEqual(sum(t["fees"] for t in st.trades.values()), open_fees)


if __name__ == "__main__":
    unittest.main()
