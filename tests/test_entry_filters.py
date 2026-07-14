"""Unit + cross-engine parity tests for src/engine/entry_filters.py."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import Storage  # noqa: E402
from src.engine import entry_filters  # noqa: E402
from src.engine.paper_trader import PaperTrader  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import backtest as backtest_module  # noqa: E402

BacktestPortfolio = backtest_module.BacktestPortfolio
HOUR = 3600


class VolatilityBlockUnitTests(unittest.TestCase):
    def test_unlimited_never_blocks(self):
        self.assertIsNone(entry_filters.volatility_entry_block(
            50.0, 90.0, 3.0, max_entry_atr_pct=999, max_stop_gap_tolerance=999))

    def test_atr_cap_blocks_above(self):
        r = entry_filters.volatility_entry_block(
            6.0, None, 9.0, max_entry_atr_pct=5, max_stop_gap_tolerance=999)
        self.assertEqual(r[0], "entry_atr_too_high")

    def test_atr_cap_allows_at_or_below(self):
        self.assertIsNone(entry_filters.volatility_entry_block(
            5.0, None, 7.5, max_entry_atr_pct=5, max_stop_gap_tolerance=999))

    def test_stop_gap_blocks_when_range_exceeds_tolerance(self):
        # recent max range 20% vs stop distance 6% -> ratio 3.3 > 2.5
        r = entry_filters.volatility_entry_block(
            4.0, 20.0, 6.0, max_entry_atr_pct=999, max_stop_gap_tolerance=2.5)
        self.assertEqual(r[0], "stop_gap_risk")

    def test_stop_gap_allows_within_tolerance(self):
        self.assertIsNone(entry_filters.volatility_entry_block(
            4.0, 12.0, 6.0, max_entry_atr_pct=999, max_stop_gap_tolerance=2.5))

    def test_missing_inputs_do_not_block(self):
        self.assertIsNone(entry_filters.volatility_entry_block(
            None, None, 6.0, max_entry_atr_pct=3, max_stop_gap_tolerance=1.5))


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "t.db", database_url="")


def _seed(storage: Storage, symbol: str, now: int, vol_pct: float) -> None:
    """48 1h bars with per-bar range = vol_pct% of close, so ATR% ~= vol_pct."""
    rows = []
    for h in range(48, 0, -1):
        ts = (now // HOUR) * HOUR - h * HOUR
        rows.append({"symbol": symbol, "timestamp": ts, "timeframe": "1h",
                     "open": 1.0, "high": 1.0 + vol_pct / 200, "low": 1.0 - vol_pct / 200,
                     "close": 1.0, "volume": 1.0})
    for sym in (symbol, "BTC/USDT"):
        pass
    storage.insert_prices(rows, timeframe="1h")
    # BTC needed for beta/portfolio
    btc = [{"symbol": "BTC/USDT", "timestamp": (now // HOUR) * HOUR - h * HOUR, "timeframe": "1h",
            "open": 50000, "high": 50000, "low": 50000, "close": 50000, "volume": 1.0}
           for h in range(48, 0, -1)]
    storage.insert_prices(btc, timeframe="1h")


class EntryFilterParityTests(unittest.TestCase):
    """Same symbol volatility + same config -> both engines make the same
    open/refuse decision (rule #2)."""

    KNOBS = ("PAPER_STARTING_CAPITAL", "MAX_POSITION_PCT", "RISK_PER_TRADE_PCT",
             "MAX_ENTRY_ATR_PCT", "MAX_STOP_GAP_TOLERANCE", "COOLDOWN_HOURS_PER_SYMBOL",
             "MAX_OPEN_POSITIONS", "TOTAL_RISK_BUDGET_PCT", "MAX_NET_BETA_EXPOSURE")

    def setUp(self):
        self._saved = {k: getattr(config, k) for k in self.KNOBS}
        config.PAPER_STARTING_CAPITAL = 1000.0
        config.MAX_POSITION_PCT = 0.20
        config.RISK_PER_TRADE_PCT = 0.01
        config.COOLDOWN_HOURS_PER_SYMBOL = 0
        config.MAX_OPEN_POSITIONS = 20
        config.TOTAL_RISK_BUDGET_PCT = 1.0
        config.MAX_NET_BETA_EXPOSURE = 999.0

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)

    def _both_open(self, vol_pct: float):
        now = int(time.time())
        storage = _storage()
        _seed(storage, "AAA/USDT", now, vol_pct)
        storage.init_portfolio_state(1000.0)
        trader = PaperTrader(storage)
        live = trader.process_signal(
            {"symbol": "AAA/USDT", "strategy": "t", "direction": "LONG",
             "reason": "t", "style": "scalp"}, 1.0)
        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        bt = portfolio.open_trade("AAA/USDT", "LONG", 1.0, now, strategy="t",
                                  prices={"AAA/USDT": 1.0, "BTC/USDT": 50000.0})
        return bool(live.get("opened")), bt is not None, live.get("gate")

    def test_atr_cap_refuses_identically(self):
        config.MAX_ENTRY_ATR_PCT = 3.0
        config.MAX_STOP_GAP_TOLERANCE = 999.0
        # vol 6% -> ATR ~6% > 3% cap: both refuse
        live_ok, bt_ok, gate = self._both_open(6.0)
        self.assertEqual(live_ok, bt_ok)
        self.assertFalse(live_ok)
        self.assertEqual(gate, "entry_atr_too_high")

    def test_low_vol_admitted_identically(self):
        config.MAX_ENTRY_ATR_PCT = 3.0
        config.MAX_STOP_GAP_TOLERANCE = 999.0
        live_ok, bt_ok, _ = self._both_open(1.0)  # ATR ~1% < 3%
        self.assertEqual(live_ok, bt_ok)
        self.assertTrue(live_ok)

    def test_unlimited_default_admits_both(self):
        config.MAX_ENTRY_ATR_PCT = 999.0
        config.MAX_STOP_GAP_TOLERANCE = 999.0
        live_ok, bt_ok, _ = self._both_open(8.0)
        self.assertEqual(live_ok, bt_ok)
        self.assertTrue(live_ok)


if __name__ == "__main__":
    unittest.main()
