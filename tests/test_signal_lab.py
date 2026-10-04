"""Signal lab book: market-neutral target, exact fee accounting, flat without weights."""

from __future__ import annotations

import unittest

from src.engine import signal_lab as sl


class SignalLabTests(unittest.TestCase):
    def test_target_book_is_balanced(self):
        scores = {f"S{i}": float(i) for i in range(50)}
        book = sl.target_book(scores, 1000.0)
        longs = [s for s, (d, _) in book.items() if d == "LONG"]
        shorts = [s for s, (d, _) in book.items() if d == "SHORT"]
        self.assertEqual(len(longs), 10)
        self.assertEqual(len(shorts), 10)
        self.assertIn("S49", longs)
        self.assertIn("S0", shorts)
        self.assertAlmostEqual(sum(n for d, n in book.values() if d == "LONG"), 500.0)
        self.assertAlmostEqual(sum(n for d, n in book.values() if d == "SHORT"), 500.0)

    def test_too_few_names_stays_flat(self):
        self.assertEqual(sl.target_book({"A": 1.0, "B": 2.0}, 1000.0), {})

    def test_surviving_legs_are_not_retraded(self):
        state = sl.fresh_state(0)
        scores = {f"S{i}": float(i) for i in range(50)}
        prices = {s: 10.0 for s in scores}
        sl.rebalance(state, scores, prices.get, 0)
        fees_first = state["fees_paid"]
        eq_before = sl.equity(state, prices.get)
        sl.rebalance(state, scores, prices.get, 86400)   # same ranking
        self.assertAlmostEqual(state["fees_paid"], fees_first)          # nothing traded
        self.assertAlmostEqual(sl.equity(state, prices.get), eq_before)
        self.assertEqual(len(state["positions"]), 20)

    def test_rebalance_accounting(self):
        state = sl.fresh_state(0)
        state["cash"] = 1000.0
        state["starting"] = 1000.0
        scores = {f"S{i}": float(i) for i in range(50)}
        prices = {s: 10.0 for s in scores}
        sl.rebalance(state, scores, prices.get, 0)
        fees = state["fees_paid"]
        self.assertAlmostEqual(sl.equity(state, prices.get), 1000.0 - fees, places=6)
        # longs +10%, shorts -10%: both sides earn 10% of 500
        moved = {s: (11.0 if i >= 40 else 9.0 if i < 10 else 10.0) for i, s in enumerate(scores)}
        self.assertAlmostEqual(sl.equity(state, moved.get), 1000.0 - fees + 100.0, places=6)
        # flat rebalance closes everything and pays exit fees
        sl.rebalance(state, {}, moved.get, 86400)
        self.assertEqual(state["positions"], [])
        self.assertAlmostEqual(state["cash"], 1000.0 + 100.0 - state["fees_paid"], places=6)


if __name__ == "__main__":
    unittest.main()
