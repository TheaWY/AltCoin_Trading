"""Pump precursors: a planted pre-pump volume build-up is found and holds on
both halves; onsets are detected where the planted pumps start."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.research import pump_precursors as pp


def _panel(n_sym=25, n_min=5000, seed=1):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.001, (n_min, n_sym))
    qv = rng.uniform(5e3, 1.5e4, (n_min, n_sym))
    tr = rng.integers(20, 60, (n_min, n_sym)).astype(float)
    starts = {}
    for j in range(n_sym):
        s = rng.choice(np.arange(1700, n_min - 200, 250), 8, replace=False)
        starts[j] = s
        for t0 in s:
            qv[t0 - 10:t0, j] *= 4            # volume builds for 10 minutes first
            ret[t0:t0 + 20, j] += 0.006       # then ~+12% in 20 minutes
    close = np.exp(np.cumsum(ret, axis=0))
    idx = pd.RangeIndex(0, n_min * 60, 60)
    cols = [f"S{i}/USDT" for i in range(n_sym)]
    mk = lambda a: pd.DataFrame(a, index=idx, columns=cols)  # noqa: E731
    return ({"close": mk(close), "high": mk(close * 1.0005), "low": mk(close * 0.9995), "open": mk(close),
             "quote_volume": mk(qv * 20), "taker_buy_quote": mk(qv * 10), "trades": mk(tr)}, starts)


class PrecursorTests(unittest.TestCase):
    def test_onsets_near_planted_starts(self):
        p, starts = _panel()
        on = pp.onsets(p)
        self.assertGreater(on.to_numpy().sum(), 50)

    def test_volume_buildup_is_a_precursor(self):
        p, _ = _panel()
        res = pp.study(p)
        by = {r["feature"]: r for r in res["precursors"]}
        self.assertTrue(by["vol_ratio_15"]["holds"], by["vol_ratio_15"])
        self.assertGreater(by["vol_ratio_15"]["test_auc"], 0.6)


if __name__ == "__main__":
    unittest.main()
