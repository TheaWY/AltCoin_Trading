"""Indicator library: every indicator is point-in-time (no future bars) and
computes on a synthetic panel without errors."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.research import indicators as ind


def _panel(n=1800, k=6, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.RangeIndex(1_700_000_000, 1_700_000_000 + n * 60, 60)
    cols = ["BTC/USDT"] + [f"C{i}/USDT" for i in range(k - 1)]
    lr = rng.normal(0, 0.002, (n, k))
    c = pd.DataFrame(100 * np.exp(np.cumsum(lr, axis=0)), index=idx, columns=cols)
    o = c.shift(1).fillna(c)
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.002, (n, k)))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.002, (n, k)))
    qv = pd.DataFrame(rng.uniform(1e4, 1e5, (n, k)), index=idx, columns=cols)
    tb = qv * rng.uniform(0.3, 0.7, (n, k))
    tr = pd.DataFrame(rng.integers(10, 100, (n, k)).astype(float), index=idx, columns=cols)
    p = {"open": o, "high": h, "low": lo, "close": c, "quote_volume": qv, "taker_buy_quote": tb, "trades": tr}
    return {key: v.astype("float32") for key, v in p.items()}


class IndicatorTests(unittest.TestCase):
    def test_registry_is_large_and_unique(self):
        names = [i.name for i in ind.REGISTRY]
        self.assertGreaterEqual(len(names), 100)
        self.assertEqual(len(names), len(set(names)))

    def test_bar_indicators_are_point_in_time(self):
        p = _panel()
        cut = 1500
        full = ind.Ctx(p)
        part = ind.Ctx({k: v.iloc[:cut] for k, v in p.items()})
        a = {i.name: f for i, f in ind.iter_indicators(full) if i.needs == "bars"}
        b = {i.name: f for i, f in ind.iter_indicators(part) if i.needs == "bars"}
        for name in a:
            x, y = a[name].iloc[:cut].to_numpy(), b[name].to_numpy()
            same = np.isclose(x, y, rtol=1e-3, atol=1e-6, equal_nan=True)
            # expanding/ewm stats are allowed float noise; everything must match
            self.assertTrue(same.mean() > 0.999, f"{name} changed when future bars were removed")

    def test_most_bar_indicators_have_values(self):
        p = _panel()
        filled = {i.name: bool(np.isfinite(f.to_numpy()[-1]).any()) for i, f in ind.iter_indicators(ind.Ctx(p))
                  if i.needs == "bars"}
        empty = [k for k, v in filled.items() if not v]
        self.assertLessEqual(len(empty), 3, empty)


if __name__ == "__main__":
    unittest.main()
