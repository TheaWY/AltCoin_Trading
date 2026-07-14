"""Standalone-entry exemption for market-neutral setups.

mean_reversion_long is a self-hedged, market-neutral setup whose OWN
pre-registered criteria (bottom-5th-pctl 24h weakness, drawdown exclusion,
computable beta) are its entry gate. It fires on weakness and so structurally
never earns confluence from momentum setups that fire on strength -- subjecting
it to the confluence-based ENTRY_MIN_CONFIDENCE gate blocked it entirely
(score 0.58 -> confidence 0.52 < 0.60, zero trades in the walk-forward gate).

This test guards the fix: evaluate_symbol() skips the confidence threshold for a
setup that declares standalone_entry, so a fired verdict is tradable -- WITHOUT
lowering the threshold for anything else.
"""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config
from src.data.storage import Storage
from src.engine.evaluation import evaluate_symbol


def _storage_with_weakness_series() -> Storage:
    """BTC + an alt sharing hourly returns (finite beta), where the alt takes a
    sharp -14% dive over its final 24h -> current 24h return is far below the
    5th percentile of its own history -> mean_reversion_long fires."""
    st = Storage(Path(tempfile.mkdtemp()) / "t.db", database_url="")
    n = 800
    t0 = 1_600_000_000
    btc_close = 100.0
    alt_close = 50.0
    btc_rows, alt_rows = [], []
    for i in range(n):
        ts = t0 + i * 3600
        btc_ret = 0.001 * math.sin(i / 10.0) + 0.0002
        if i > 0:
            btc_close *= (1 + btc_ret)
            alt_ret = 1.2 * btc_ret if i < n - 24 else -0.006  # final 24h: steady dive
            alt_close *= (1 + alt_ret)

        def row(sym, c):
            return {"symbol": sym, "timestamp": ts, "timeframe": "1h",
                    "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 50_000.0}
        btc_rows.append(row(config.SYMBOL, btc_close))
        alt_rows.append(row("ALT/USDT", alt_close))
    st.insert_prices(btc_rows, timeframe="1h")
    st.insert_prices(alt_rows, timeframe="1h")
    return st


# force every other isolatable setup off so mean_reversion_long is the only
# candidate -- same isolation the walk-forward gate applies.
_OFF_FLAGS = [
    "SETUP_MEANREV_ENABLED", "SETUP_BREAKOUT_ENABLED", "SETUP_TSMOM_ENABLED",
    "SETUP_VOLUME_ENABLED", "SETUP_FUNDING_ENABLED", "SETUP_SWING_ENABLED",
    "SETUP_REL_STRENGTH_ENABLED", "SETUP_CAPITULATION_BAR_ENABLED",
    "SETUP_VOLUME_ZSCORE_ENABLED", "SETUP_PUMP24_EXTREME_ENABLED",
    "SETUP_FAILED_PUMP_LONG_ENABLED",
]


class StandaloneEntryExemptionTests(unittest.TestCase):
    def _evaluate(self):
        st = _storage_with_weakness_series()
        btc_rows = st.get_prices(config.SYMBOL, limit=720, timeframe="1h")
        env = {f: "false" for f in _OFF_FLAGS}
        env["SETUP_MEAN_REVERSION_LONG_ENABLED"] = "true"
        with mock.patch.dict(os.environ, env), \
             mock.patch.multiple(config, **{f: False for f in _OFF_FLAGS}):
            return evaluate_symbol(st, "ALT/USDT", btc_rows, regime={"regime": "neutral"},
                                   calibration={}, category=None)

    def test_fires_and_is_tradable_despite_subthreshold_confidence(self):
        res = self._evaluate()
        verdict = res.get("verdict") or {}
        self.assertEqual(verdict.get("strategy"), "mean_reversion_long")
        self.assertEqual(verdict.get("direction"), "LONG")
        self.assertTrue(verdict.get("standalone_entry"))
        # confidence is BELOW the 0.60 gate, yet the setup is tradable via the
        # standalone exemption -- this is the whole point.
        self.assertLess(res.get("confidence"), config.ENTRY_MIN_CONFIDENCE)
        self.assertTrue(res.get("tradable"),
                        f"standalone setup must be tradable; why_not={res.get('why_not')}")

    def test_exemption_does_not_lower_gate_for_non_standalone(self):
        # A verdict without standalone_entry at the same sub-0.60 confidence must
        # still be blocked -- the exemption is scoped, not a global threshold cut.
        from src.engine import evaluation
        real = evaluation._mean_reversion_long_setup

        def _strip_standalone(*a, **k):
            out = real(*a, **k)
            if out:
                out.pop("standalone_entry", None)  # same setup, minus the opt-in
            return out

        with mock.patch.object(evaluation, "_mean_reversion_long_setup", _strip_standalone):
            res = self._evaluate()
        self.assertLess(res.get("confidence"), config.ENTRY_MIN_CONFIDENCE)
        self.assertFalse(res.get("tradable"),
                         "without standalone_entry, sub-0.60 confidence must NOT be tradable")


if __name__ == "__main__":
    unittest.main()
