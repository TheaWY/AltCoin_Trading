"""Pump study: planted continuation pumps are found and validated, random
noise is not, exits account for costs correctly."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.research import pump_study as ps


def _panel(n_sym=30, n_min=6000, pumps=True, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.RangeIndex(0, n_min * 60, 60)
    ret = rng.normal(0, 0.001, (n_min, n_sym))
    qv = rng.uniform(5e3, 1.5e4, (n_min, n_sym))
    tb = qv * rng.uniform(0.4, 0.6, (n_min, n_sym))
    if pumps:
        for j in range(n_sym):
            for start in rng.choice(np.arange(400, n_min - 300), 12, replace=False):
                ret[start:start + 3, j] += 0.012         # sharp 3-minute burst ...
                ret[start + 3:start + 40, j] += 0.002    # ... that keeps running ~40 min
                ret[start + 40:start + 60, j] -= 0.002   # then fades
                qv[start:start + 3, j] *= 8
                tb[start:start + 3, j] = qv[start:start + 3, j] * 0.8
    close = 1.0 * np.exp(np.cumsum(ret, axis=0))
    spread = np.abs(rng.normal(0, 0.0005, (n_min, n_sym)))
    cols = [f"S{i}/USDT" for i in range(n_sym)]
    mk = lambda a: pd.DataFrame(a, index=idx, columns=cols)  # noqa: E731
    return {"open": mk(close), "close": mk(close), "high": mk(close * (1 + spread)),
            "low": mk(close * (1 - spread)), "quote_volume": mk(qv * 30), "taker_buy_quote": mk(tb * 30)}


class PumpStudyTests(unittest.TestCase):
    def test_exit_accounting(self):
        hi = np.array([1.02, 1.05, 1.08, 1.06, 1.03])
        lo = np.array([1.00, 1.03, 1.06, 1.04, 1.00])
        net, held, why = ps.simulate_exit(1.0, hi, lo, trail=0.03, tp=None)
        self.assertEqual(why, "trail")
        expected = (1.08 * 0.97 * (1 - ps.SLIP) * (1 - ps.FEE)) / (1.0 * (1 + ps.SLIP) * (1 + ps.FEE)) - 1
        self.assertAlmostEqual(net, expected)
        net_tp, _, why_tp = ps.simulate_exit(1.0, hi, lo, trail=0.03, tp=0.05)
        self.assertEqual(why_tp, "target")

    def test_planted_pumps_validate(self):
        res = ps.run_study(_panel())
        self.assertTrue(res["validated"], [ (r["rule"], r["train_mean_net"], r["test_mean_net"]) for r in res["results"][:5]])
        best = res["validated"][0]
        self.assertGreater(best["mfe_median"], 0.03)

    def test_noise_does_not_validate(self):
        res = ps.run_study(_panel(pumps=False, seed=4))
        self.assertEqual(res["validated"], [])

    def test_features_point_in_time(self):
        p = _panel(n_min=1200)
        a = ps.features(p, 5)["z"].iloc[:900]
        b = ps.features({k: v.iloc[:900] for k, v in p.items()}, 5)["z"]
        pd.testing.assert_frame_equal(a, b, check_freq=False)


if __name__ == "__main__":
    unittest.main()
