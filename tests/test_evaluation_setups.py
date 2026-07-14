"""Safety tests for entry evaluation setups.

These tests encode real dashboard review cases before strategy changes are
trusted. Keep them lightweight: no network, no database, no live trading.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager

from src.engine.evaluation import (  # noqa: E402
    STYLE_FAILED_PUMP_LONG,
    STYLE_SCALP,
    _failed_pump_long_setup,
    _failed_pump_short_setup,
    _filter_setups_by_category,
    _funding_setup,
)
from src import config  # noqa: E402


@contextmanager
def _env(**kv):
    """Force env vars for a dynamically-read flag, robust to import order
    (unlike setdefault, which loses to a prior dotenv load)."""
    saved = {k: os.environ.get(k) for k in kv}
    os.environ.update({k: str(v) for k, v in kv.items()})
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def base_metrics(**overrides):
    metrics = {
        "candles": 720,
        "dollar_volume_24h": 100_000_000,
        "atr_pct": 3.0,
        "pct_24h": 0.0,
        "pct_7d": 0.0,
        "pct_30d": 0.0,
        "rsi_14": 50.0,
        "realized_vol_7d": 100.0,
        "realized_vol_30d": 100.0,
        "sharpe_7d": 0.0,
        "sharpe_30d": 0.0,
        "max_drawdown_30d": 0.0,
        "sma_20": 1.0,
        "sma_50": 0.95,
        "last_price": 1.0,
        "macd": {"hist": 0.0, "hist_pct": 0.0, "hist_rising": False},
        "bollinger": {"percent_b": 0.5, "bandwidth_pct": 8.0},
        "btc_correlation": 0.4,
        "btc_beta": 1.0,
        "volume_ratio": 1.0,
        "open_interest_usd": None,
        "long_short_ratio": None,
    }
    metrics.update(overrides)
    return metrics


class EvaluationSetupTests(unittest.TestCase):
    SKL_ROLLOVER = dict(
        pct_7d=29.54, pct_24h=-6.99, rsi_14=42.0, last_price=0.004787,
        sma_20=0.0052, sma_50=0.0042,
        macd={"hist": -0.0001, "hist_pct": -0.2, "hist_rising": False},
        btc_correlation=0.43,
    )

    def test_failed_pump_short_disabled_by_default(self):
        """failed_pump_short is permanently disabled (anti-predictive at
        n=1660). It must return None regardless of the SKL-like pattern."""
        with _env(SETUP_FAILED_PUMP_ENABLED="false"):
            self.assertIsNone(_failed_pump_short_setup(base_metrics(**self.SKL_ROLLOVER)))

    def test_failed_pump_long_detects_skl_like_rollover(self):
        """The SAME detection now fires as a LONG (oversold bounce) -- the
        validated inversion. Same condition, opposite direction."""
        with _env(SETUP_FAILED_PUMP_LONG_ENABLED="true"):
            setup = _failed_pump_long_setup(base_metrics(**self.SKL_ROLLOVER))
        self.assertIsNotNone(setup)
        self.assertEqual(setup["style"], STYLE_FAILED_PUMP_LONG)
        self.assertEqual(setup["strategy"], "failed_pump_long")
        self.assertEqual(setup["direction"], "LONG")
        self.assertGreaterEqual(setup["score"], 0.70)
        self.assertIn("과매도 반등 롱", setup["reason"])

    def test_failed_pump_long_disabled_by_default(self):
        """Default OFF -- earns its place through the walk-forward gate."""
        with _env(SETUP_FAILED_PUMP_LONG_ENABLED="false"):
            self.assertIsNone(_failed_pump_long_setup(base_metrics(**self.SKL_ROLLOVER)))

    def test_pump_with_negative_funding_is_not_failed_pump_short(self):
        """A live pump with extreme negative funding is an upside/squeeze case, not a short."""
        metrics = base_metrics(
            pct_7d=19.23,
            pct_24h=34.54,
            rsi_14=82.0,
            last_price=0.004596,
            sma_20=0.0038,
            sma_50=0.0034,
            macd={"hist": 0.0002, "hist_pct": 0.4, "hist_rising": True},
            btc_correlation=0.58,
            volume_ratio=1.8,
        )
        self.assertIsNone(_failed_pump_short_setup(metrics))
        funding_setup = _funding_setup(metrics, funding_rate=-0.02)
        self.assertIsNotNone(funding_setup)
        self.assertEqual(funding_setup["style"], STYLE_SCALP)
        self.assertEqual(funding_setup["direction"], "LONG")

    def test_directional_long_scalp_is_allowed_when_policy_allows_long(self):
        """No 장투 must not mean no directional 상승 trades."""
        old_allow_long = config.ALLOW_LONG
        old_long_term = config.LONG_TERM_HOLD_ENABLED
        try:
            config.ALLOW_LONG = True
            config.LONG_TERM_HOLD_ENABLED = False
            self.assertTrue(config.direction_allowed("LONG"))
            self.assertTrue(config.holding_style_allowed("scalp"))
            self.assertTrue(config.holding_style_allowed(STYLE_SCALP))
        finally:
            config.ALLOW_LONG = old_allow_long
            config.LONG_TERM_HOLD_ENABLED = old_long_term

    def test_long_term_hold_style_is_blocked_when_disabled(self):
        old_long_term = config.LONG_TERM_HOLD_ENABLED
        try:
            config.LONG_TERM_HOLD_ENABLED = False
            self.assertFalse(config.holding_style_allowed("long_term_hold"))
            self.assertFalse(config.holding_style_allowed("장투"))
            self.assertEqual(config.max_hold_hours_for_style("long_term_hold"), 0.0)
        finally:
            config.LONG_TERM_HOLD_ENABLED = old_long_term

    def test_category_strategy_mode_drops_unmatched_momentum_only_when_matched(self):
        setup = {
            "strategy": "momentum",
            "direction": "LONG",
            "style": STYLE_SCALP,
            "score": 0.76,
        }
        old_mode = config.CATEGORY_STRATEGY_MODE
        try:
            config.CATEGORY_STRATEGY_MODE = "off"
            kept, blocked = _filter_setups_by_category([setup], "range_meanrev")
            self.assertEqual(kept, [setup])
            self.assertEqual(blocked, [])

            config.CATEGORY_STRATEGY_MODE = "matched"
            kept, blocked = _filter_setups_by_category([setup], "range_meanrev")
            self.assertEqual(kept, [])
            self.assertEqual(len(blocked), 1)
            self.assertIn("not matched", blocked[0]["blocked_reason"])
        finally:
            config.CATEGORY_STRATEGY_MODE = old_mode


if __name__ == "__main__":
    unittest.main()
