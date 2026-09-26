"""Sentiment lab + gate: planted signals are found, noise is not, weights move
slowly, and the gate respects its mode."""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np
import pandas as pd

from src.research import sentiment_lab as lab

HOUR = 3600


def _panel(n_sym=40, n_hours=2400, signal=0.0, seed=0):
    """Random-walk prices; if signal != 0, the 24h change in open interest
    predicts the next 24h market-relative return."""
    rng = np.random.default_rng(seed)
    idx = pd.RangeIndex(0, n_hours * HOUR, HOUR)
    cols = [f"S{i}/USDT" for i in range(n_sym)]
    oi_ret = rng.normal(0, 0.01, (n_hours, n_sym))
    log_oi = np.cumsum(oi_ret, axis=0) + 10
    oi = pd.DataFrame(np.exp(log_oi), index=idx, columns=cols)
    oi_chg24 = np.log(oi / oi.shift(24)).fillna(0.0).to_numpy()
    noise = rng.normal(0, 0.004, (n_hours, n_sym))
    ret = noise.copy()
    if signal:
        # the 24h OI change known at t drives returns spread over t+1..t+24
        ret[1:] += signal * oi_chg24[:-1] / 24
    close = pd.DataFrame(100 * np.exp(np.cumsum(ret, axis=0)), index=idx, columns=cols)
    ls = pd.DataFrame(np.exp(rng.normal(0, 0.1, (n_hours, n_sym))), index=idx, columns=cols)
    funding = pd.DataFrame(rng.normal(1e-4, 5e-5, (n_hours, n_sym)), index=idx, columns=cols)
    vol = pd.DataFrame(rng.uniform(1e5, 1e6, (n_hours, n_sym)), index=idx, columns=cols)
    return {"close": close, "funding": funding, "ls": ls, "oi": oi, "dollar_volume": vol * close}


class SentimentLabTests(unittest.TestCase):
    def test_planted_signal_is_found_and_weighted(self):
        res = lab.run_research(_panel(signal=0.5))
        q = {x["qid"]: x for x in res["questions"]}
        self.assertEqual(q["oi_chg24|24h|residual"]["verdict"], "supported")
        self.assertGreater(q["oi_chg24|24h|residual"]["mean_ic"], 0)
        self.assertIn("oi_chg24", res["target_weights"])
        self.assertGreater(res["weights"]["oi_chg24"], 0)
        # follow-up questions are generated for supported findings
        self.assertTrue(any(f["parent"] == "oi_chg24|24h|residual" for f in res["follow_ups"]))
        self.assertTrue(res["oos"]["passed"], res["oos"])

    def test_noise_supports_little_and_never_enforces(self):
        res = lab.run_research(_panel(signal=0.0, seed=3))
        supported = [x for x in res["questions"] if x["verdict"] == "supported"]
        self.assertLessEqual(len(supported), 2)  # FDR 10% on ~42 nulls
        self.assertFalse(res["enforce_ok"] and not supported)

    def test_weight_shift_is_capped(self):
        target = {"oi_chg24": {"weight": 1.0}}
        w = lab.shift_weights({}, target)
        self.assertAlmostEqual(w["oi_chg24"], lab.MAX_WEIGHT_SHIFT)
        w = lab.shift_weights({"oi_chg24": 0.5, "ls_z": 0.05}, {})
        self.assertAlmostEqual(w["oi_chg24"], 0.5 - lab.MAX_WEIGHT_SHIFT)
        self.assertNotIn("ls_z", w)  # decayed to zero and dropped

    def test_features_are_point_in_time(self):
        p = _panel(n_hours=400)
        f1 = lab.build_features(p)
        cut = {k: v.iloc[:300] for k, v in p.items()}
        f2 = lab.build_features(cut)
        for name in lab.FEATURES:
            a = f1[name].iloc[:300]
            pd.testing.assert_frame_equal(a, f2[name], check_freq=False)

    def test_bh_qvalues(self):
        q = lab.bh_qvalues([0.01, 0.04, 0.03, 0.5])
        self.assertAlmostEqual(q[0], 0.04)
        self.assertTrue(all(0 <= x <= 1 for x in q))

    def test_insufficient_data_is_reported(self):
        res = lab.run_research(_panel(n_hours=250))
        self.assertTrue(all(x["verdict"] in ("insufficient", "not_supported", "supported") for x in res["questions"]))
        self.assertTrue(any(x["verdict"] == "insufficient" for x in res["questions"]))


class SentimentGateTests(unittest.TestCase):
    def setUp(self):
        from src.engine import sentiment_gate
        self.g = sentiment_gate

    def test_decide(self):
        self.assertFalse(self.g.decide(-1.5, "LONG", 1.0)[0])
        self.assertTrue(self.g.decide(-0.5, "LONG", 1.0)[0])
        self.assertFalse(self.g.decide(1.2, "SHORT", 1.0)[0])
        self.assertTrue(self.g.decide(None, "SHORT", 1.0)[0])

    def test_auto_mode_enforces_only_when_validated(self):
        with mock.patch.object(self.g.config, "SENTIMENT_GATE_MODE", "auto", create=True):
            self.assertEqual(self.g.effective_mode({"weights": {"x": 0.1}, "enforce_ok": False}), "shadow")
            self.assertEqual(self.g.effective_mode({"weights": {"x": 0.1}, "enforce_ok": True}), "enforce")
            self.assertEqual(self.g.effective_mode({"weights": {}, "enforce_ok": True}), "shadow")

    def test_shadow_never_blocks(self):
        storage = mock.MagicMock()
        storage.get_system_status.return_value = None
        with mock.patch.object(self.g, "load_state", return_value={"weights": {"x": 1}, "enforce_ok": False}), \
             mock.patch.object(self.g, "scores", return_value={"A/USDT": -3.0}), \
             mock.patch.object(self.g.config, "SENTIMENT_GATE_MODE", "auto", create=True):
            ok, reason = self.g.allow_entry(storage, "A/USDT", "LONG")
        self.assertTrue(ok)
        self.assertIn("shadow", reason)

    def test_enforce_blocks_pair_against_sentiment(self):
        storage = mock.MagicMock()
        storage.get_system_status.return_value = None
        with mock.patch.object(self.g, "load_state", return_value={"weights": {"x": 1}, "enforce_ok": True}), \
             mock.patch.object(self.g, "scores", return_value={"L/USDT": -1.0, "S/USDT": 0.8}), \
             mock.patch.object(self.g.config, "SENTIMENT_GATE_MODE", "auto", create=True):
            ok, _ = self.g.allow_pair(storage, "L/USDT", "S/USDT")
        self.assertFalse(ok)

    def test_compute_scores_uses_latest_row(self):
        p = _panel(n_hours=900)
        sc = self.g.compute_scores(p, {"oi:chg24": 1.0})
        self.assertEqual(len(sc), 40)
        self.assertAlmostEqual(float(np.mean(list(sc.values()))), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
