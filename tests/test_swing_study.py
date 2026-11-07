"""Swing study: trade simulation exits and book cost accounting."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.research import swing_study as ss


class SwingStudyTests(unittest.TestCase):
    def test_long_stop_fills_at_stop(self):
        o = np.array([100, 100, 100, 100.0])
        h = np.array([101, 101, 101, 101.0])
        lo = np.array([99, 99, 90, 99.0])
        c = np.array([100, 100, 95, 100.0])
        net, j, held, _ = ss.simulate(o, h, lo, c, 0, 1, atr=0.02, trail=3.0, hold=10)
        self.assertEqual(j, 2)
        self.assertAlmostEqual(net, -0.04 - ss.ROUND_TRIP - ss.FUNDING_PER_DAY * 2, places=9)

    def test_timeout_exits_at_close(self):
        n = 15
        o = np.full(n, 100.0)
        h = np.full(n, 101.0)
        lo = np.full(n, 99.5)
        c = np.linspace(100, 110, n)
        net, j, held, _ = ss.simulate(o, h, lo, c, 0, 1, atr=0.05, trail=4.0, hold=5)
        self.assertEqual(held, 5)
        self.assertAlmostEqual(net, c[5] / 100 - 1 - ss.ROUND_TRIP - ss.FUNDING_PER_DAY * 5, places=9)

    def test_book_costs_on_switch(self):
        idx = pd.RangeIndex(0, 4)
        rets = pd.DataFrame({"A": [0.0, 0.10, 0.10, 0.10]}, index=idx)
        w = pd.DataFrame({"A": [1.0, 1.0, 0.0, 0.0]}, index=idx)
        x = ss._run_weights(w, rets, funding=False)
        self.assertAlmostEqual(x[1], 0.10 - ss.SWITCH_COST, places=9)   # entry paid on the first held day
        self.assertAlmostEqual(x[2], 0.10, places=9)                     # still held, no trade
        self.assertAlmostEqual(x[3], -ss.SWITCH_COST, places=9)          # flat, exit paid


if __name__ == "__main__":
    unittest.main()
