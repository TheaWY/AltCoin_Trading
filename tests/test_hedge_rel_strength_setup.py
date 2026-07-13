"""_rel_strength_setup's market-neutral behavior (research_decisions,
subject='rel_strength_market_neutral'): fires only with a real, positive
ex-ante beta; carries execution_mode/beta/hedge_symbol through to the
verdict; and stays off unless SETUP_REL_STRENGTH_ENABLED is set.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import config
from src.data.storage import Storage
from src.engine.cycle import STYLE_MAP
from src.engine.evaluation import STYLE_REL_STRENGTH_NEUTRAL, _rel_strength_setup
from src.research.rel_strength import MIN_HISTORY, SEVEN_DAYS_S

HOUR = 3600


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


def _seed_correlated_series_with_recent_outperformance(storage: Storage) -> int:
    """Enough hourly history for the 7d spread's percentile gate to fire on
    the latest bar, with the symbol correlated to (but recently
    outperforming) BTC so a positive beta is estimable."""
    hours = MIN_HISTORY + SEVEN_DAYS_S // HOUR + 24 * 30 + 10
    now = 2_000_000_000
    start = now - hours * HOUR
    btc_rows, sym_rows = [], []
    btc_price, sym_price = 50000.0, 1.0
    for i in range(hours + 1):
        ts = start + i * HOUR
        # Small deterministic oscillation so returns aren't constant
        # (needed for a well-defined covariance/beta), correlated 1:1.
        wiggle = 0.001 if i % 2 == 0 else -0.0008
        btc_price *= (1 + wiggle)
        sym_ret = wiggle  # base correlation with BTC
        if i > hours - 24 * 10:
            sym_ret += 0.003  # recent idiosyncratic outperformance -> triggers 95th pctl
        sym_price *= (1 + sym_ret)
        btc_rows.append({"symbol": "BTC/USDT", "timestamp": ts, "open": btc_price, "high": btc_price, "low": btc_price, "close": btc_price, "volume": 1})
        sym_rows.append({"symbol": "AAA/USDT", "timestamp": ts, "open": sym_price, "high": sym_price, "low": sym_price, "close": sym_price, "volume": 1})
    storage.insert_prices(btc_rows)
    storage.insert_prices(sym_rows)
    return now


class RelStrengthMarketNeutralSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_enabled = config.SETUP_REL_STRENGTH_ENABLED

    def tearDown(self) -> None:
        config.SETUP_REL_STRENGTH_ENABLED = self.old_enabled

    def test_off_by_default(self) -> None:
        config.SETUP_REL_STRENGTH_ENABLED = False
        storage = _storage()
        _seed_correlated_series_with_recent_outperformance(storage)
        self.assertIsNone(_rel_strength_setup(storage, "AAA/USDT", {}))

    def test_fires_market_neutral_with_hedge_metadata_when_enabled(self) -> None:
        config.SETUP_REL_STRENGTH_ENABLED = True
        storage = _storage()
        _seed_correlated_series_with_recent_outperformance(storage)
        setup = _rel_strength_setup(storage, "AAA/USDT", {})
        self.assertIsNotNone(setup)
        self.assertEqual(setup["direction"], "LONG")
        self.assertEqual(setup["style"], STYLE_REL_STRENGTH_NEUTRAL)
        self.assertEqual(setup["execution_mode"], "market_neutral")
        self.assertEqual(setup["hedge_symbol"], "BTC/USDT")
        self.assertIsInstance(setup["beta"], float)
        self.assertGreater(setup["beta"], 0)

    def test_style_maps_to_72h_hold_cap_not_swing(self) -> None:
        """setup['style'] is the raw Korean label; cycle.py's STYLE_MAP
        translates it to the internal key before config consumes it --
        mirrors exactly how STYLE_SWING ("스윙") already works for every
        other setup, so this must route the same way, not to swing's 720h."""
        config.SETUP_REL_STRENGTH_ENABLED = True
        storage = _storage()
        _seed_correlated_series_with_recent_outperformance(storage)
        setup = _rel_strength_setup(storage, "AAA/USDT", {})
        self.assertIsNotNone(setup)
        normalized = config.normalize_holding_style(STYLE_MAP.get(setup["style"]))
        self.assertEqual(normalized, "rel_strength_neutral")
        self.assertEqual(config.max_hold_hours_for_style(normalized), 72.0)
        self.assertTrue(config.holding_style_allowed(normalized))

    def test_no_history_returns_none(self) -> None:
        config.SETUP_REL_STRENGTH_ENABLED = True
        storage = _storage()
        self.assertIsNone(_rel_strength_setup(storage, "AAA/USDT", {}))

    def test_btc_itself_never_fires(self) -> None:
        config.SETUP_REL_STRENGTH_ENABLED = True
        storage = _storage()
        _seed_correlated_series_with_recent_outperformance(storage)
        self.assertIsNone(_rel_strength_setup(storage, config.SYMBOL, {}))


if __name__ == "__main__":
    unittest.main()
