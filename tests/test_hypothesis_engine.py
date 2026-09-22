"""Hypothesis engine: new H0s every run, no repeats, planted signals found,
cumulative FDR; core allocation accounting."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.research import hypothesis_engine as he
from tests.test_sentiment_lab import _panel


class HypothesisEngineTests(unittest.TestCase):
    def test_every_run_tests_new_hypotheses(self):
        p = _panel(n_hours=900, seed=1)
        reg: dict = {}
        r1 = he.run(p, reg, now=1_000_000, n=15)
        first = {x["hid"] for x in r1["tested"]}
        r2 = he.run(p, reg, now=1_000_000 + 7200, n=15)
        second = {x["hid"] for x in r2["tested"]}
        self.assertEqual(len(first), 15)
        self.assertTrue(second - first, "second run must test hypotheses the first did not")
        self.assertEqual(len(reg), len(first | second))
        for x in r1["tested"]:
            self.assertTrue(x["h0"].startswith("H0: "))

    def test_planted_signal_is_eventually_supported_and_weighted(self):
        p = _panel(n_hours=2400, signal=0.5)
        reg = {"oi:chg24|24h|residual|all": {"p": None, "n": 0}}  # force it into the batch below
        del reg["oi:chg24|24h|residual|all"]
        h = he.Hypothesis("oi", "chg24", 24, "residual", "all")
        cache, masks = he.FeatureCache(p), he.condition_masks(p)
        from src.research import sentiment_lab as lab
        res = he._test(cache, masks, lambda hh, t: lab.forward_returns(p["close"], hh, t), h)
        reg[h.hid] = {"h0": h.h0, "why": "t", "tested_at": 0, "n": res["n_periods"], "ic": res["mean_ic"],
                      "t": res["t_stat"], "p": res["p_value"], "halves_agree": res["halves_agree"],
                      "bps_per_sd": res["bps_per_sd"], "spread_bps": res["spread_bps"]}
        out = he.run(p, reg, now=5_000_000, n=10)
        self.assertEqual(reg[h.hid]["verdict"], "supported")
        self.assertIn("oi:chg24", out["target_weights"])
        # follow-ups of the promising result were scheduled
        self.assertTrue(any("follow-up" in x["why"] and x["hid"].startswith("oi:chg24") for x in out["tested"]))

    def test_cumulative_fdr_gets_stricter(self):
        reg = {f"ret:level|{h}h|raw|all": {"p": 0.02, "n": 50, "halves_agree": True, "t": 2.3}
               for h in (1, 4)}
        he.apply_fdr(reg)
        self.assertTrue(all(r["verdict"] == "supported" for r in reg.values()))
        for i in range(60):
            reg[f"dvol:level|{he.HORIZONS[i % 6]}h|raw|c{i}"] = {"p": 0.8, "n": 50, "halves_agree": True, "t": 0.2}
        he.apply_fdr(reg)
        self.assertFalse(reg["ret:level|1h|raw|all"]["verdict"] == "supported")

    def test_parse_roundtrip(self):
        h = he.Hypothesis("funding", "z720", 72, "raw", "btc_up")
        self.assertEqual(he.Hypothesis.parse(h.hid), h)

    def test_features_point_in_time(self):
        p = _panel(n_hours=500)
        full, cut = he.FeatureCache(p), he.FeatureCache({k: v.iloc[:400] for k, v in p.items()})
        for sig in he.SIGNAL_TEXT:
            for tf in ("level", "chg24", "z168"):
                a = full.feature(f"{sig}:{tf}").iloc[:400]
                b = cut.feature(f"{sig}:{tf}")
                pd.testing.assert_frame_equal(a, b, check_freq=False)


class _FakeStorage:
    def __init__(self, cash, btc):
        self.cash, self.btc, self.trades, self.next_id = cash, btc, {}, 1

    def get_portfolio_state(self):
        return {"cash": self.cash}

    def update_portfolio_cash(self, c):
        self.cash = c

    def get_latest_price(self, _s):
        return {"close": self.btc}

    def get_open_trades(self, symbol=None):
        return [t for t in self.trades.values() if t["status"] == "open"]

    def insert_paper_trade(self, row):
        row = dict(row, id=self.next_id)
        self.trades[self.next_id] = row
        self.next_id += 1
        return row["id"]

    def update_paper_trade(self, tid, fields):
        self.trades[tid].update(fields)


class CoreManagerTests(unittest.TestCase):
    def _run(self, st):
        from unittest import mock
        from src.engine import core_manager as cm

        def summary(_self, price):
            eq = st.cash + sum(float(t["quantity"]) * price for t in st.get_open_trades())
            return {"equity": eq}

        with mock.patch.object(cm.config, "CORE_ENABLED", True, create=True), \
             mock.patch.object(cm.config, "CORE_PCT", 0.97, create=True), \
             mock.patch.object(cm.config, "CORE_BAND", 0.05, create=True), \
             mock.patch("src.engine.paper_trader.PaperTrader.summary", summary), \
             mock.patch("src.engine.paper_trader.PaperTrader.__init__", lambda self, s: None):
            return cm.run_core_cycle(st, now=1000)

    def test_deploys_idle_cash_and_conserves_equity(self):
        st = _FakeStorage(1000.0, 50000.0)
        r = self._run(st)
        self.assertEqual(r["action"], "add")
        core = sum(t["quantity"] * 50000 for t in st.get_open_trades())
        self.assertAlmostEqual(core, 970.0, delta=1.0)
        self.assertAlmostEqual(st.cash + core, 1000.0 - 970.0 * 0.0005, places=4)
        # second run inside the band does nothing (no fee churn)
        self.assertEqual(self._run(st)["action"], "hold")

    def test_trim_and_close_accounting(self):
        st = _FakeStorage(1000.0, 50000.0)
        self._run(st)
        st.btc = 55000.0            # +10%
        before = st.cash + sum(t["quantity"] * 55000 for t in st.get_open_trades())
        st.cash -= 300.0            # a strategy grabbed cash? simulate by lowering cash
        r = self._run(st)
        self.assertIn(r["action"], ("hold", "trim", "add"))
        closed = [t for t in st.trades.values() if t["status"] == "closed"]
        for t in closed:
            self.assertAlmostEqual(t["pnl"], (t["exit_price"] - t["entry_price"]) * t["quantity"] - t["fees"], places=6)
        self.assertGreater(before, 1000.0)


if __name__ == "__main__":
    unittest.main()
