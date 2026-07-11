"""Safety tests for entry evaluation setups.

These tests encode real dashboard review cases before strategy changes are
trusted. Keep them lightweight: no network, no database, no live trading.
"""

from __future__ import annotations

import os
import unittest

# Make sure the dynamic setup is enabled for these tests even if the shell has a
# different research override.
os.environ.setdefault("SETUP_FAILED_PUMP_ENABLED", "true")

from src.engine.evaluation import (  # noqa: E402
    STYLE_SCALP,
    _failed_pump_short_setup,
    _funding_setup,
)


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
    def test_failed_pump_short_detects_skl_like_rollover(self):
        """A +7d pump that rolls over intraday should be seen as a short setup."""
        metrics = base_metrics(
            pct_7d=29.54,
            pct_24h=-6.99,
            rsi_14=42.0,
            last_price=0.004787,
            sma_20=0.0052,
            sma_50=0.0042,
            macd={"hist": -0.0001, "hist_pct": -0.2, "hist_rising": False},
            btc_correlation=0.43,
        )
        setup = _failed_pump_short_setup(metrics)
        self.assertIsNotNone(setup)
        self.assertEqual(setup["style"], STYLE_SCALP)
        self.assertEqual(setup["strategy"], "failed_pump_short")
        self.assertEqual(setup["direction"], "SHORT")
        self.assertGreaterEqual(setup["score"], 0.70)
        self.assertIn("펌프 실패 숏", setup["reason"])

    def test_pump_with_negative_funding_is_not_failed_pump_short(self):
        """A live pump with extreme negative funding is a banned LONG/squeeze case, not a short."""
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


if __name__ == "__main__":
    unittest.main()
