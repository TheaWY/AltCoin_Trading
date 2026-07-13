"""Unit tests for src/engine/risk_budget.py plus cross-engine capital-gate
parity (live vs backtest must refuse the same entries -- rule #2)."""

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
from src.engine import risk_budget  # noqa: E402
from src.engine.paper_trader import PaperTrader  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import backtest as backtest_module  # noqa: E402

BacktestPortfolio = backtest_module.BacktestPortfolio
HOUR = 3600


class PositionRiskTests(unittest.TestCase):
    def test_long_risk_is_distance_to_stop(self):
        self.assertAlmostEqual(risk_budget.position_risk("LONG", 100.0, 95.0, 2.0), 10.0)

    def test_short_risk_is_distance_to_stop(self):
        self.assertAlmostEqual(risk_budget.position_risk("SHORT", 100.0, 105.0, 2.0), 10.0)

    def test_ratcheted_stop_above_entry_consumes_zero(self):
        # LONG with trailing stop already above current price -> can't lose
        self.assertEqual(risk_budget.position_risk("LONG", 100.0, 101.0, 2.0), 0.0)


class RiskBudgetAllowsTests(unittest.TestCase):
    def test_within_budget(self):
        self.assertTrue(risk_budget.risk_budget_allows(30.0, 10.0, 1000.0, 0.05))

    def test_exceeds_budget(self):
        self.assertFalse(risk_budget.risk_budget_allows(45.0, 10.0, 1000.0, 0.05))

    def test_zero_equity_refuses(self):
        self.assertFalse(risk_budget.risk_budget_allows(0.0, 1.0, 0.0, 0.05))


class NetBetaExposureTests(unittest.TestCase):
    def test_longs_and_shorts_net(self):
        legs = [
            {"direction": "LONG", "notional": 200.0, "beta": 1.5},
            {"direction": "SHORT", "notional": 100.0, "beta": 1.0},
        ]
        self.assertAlmostEqual(risk_budget.net_beta_exposure(legs, 1000.0), 0.2)

    def test_missing_beta_counts_as_one(self):
        legs = [{"direction": "LONG", "notional": 100.0, "beta": None}]
        self.assertAlmostEqual(risk_budget.net_beta_exposure(legs, 1000.0), 0.1)

    def test_unlimited_cap_always_allows(self):
        legs = [{"direction": "LONG", "notional": 10_000.0, "beta": 3.0}]
        self.assertTrue(risk_budget.beta_exposure_allows(
            legs, {"direction": "LONG", "notional": 10_000.0, "beta": 3.0},
            1000.0, risk_budget.BETA_UNLIMITED))


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


def _seed(storage: Storage, symbols: list[str], now: int) -> None:
    rows = []
    for h in range(50, 0, -1):
        ts = (now // HOUR) * HOUR - h * HOUR
        for sym, px in [("BTC/USDT", 50_000.0), *[(s, 1.0) for s in symbols]]:
            rows.append({"symbol": sym, "timestamp": ts, "timeframe": "1h",
                         "open": px, "high": px * 1.01, "low": px * 0.99,
                         "close": px, "volume": 1.0})
    storage.insert_prices(rows, timeframe="1h")


class CapitalGateParityTests(unittest.TestCase):
    """Same open book + same candidate -> same refusal in both engines."""

    KNOBS = ("PAPER_STARTING_CAPITAL", "MAX_POSITION_PCT", "RISK_PER_TRADE_PCT",
             "TOTAL_RISK_BUDGET_PCT", "MAX_NET_BETA_EXPOSURE", "MAX_OPEN_POSITIONS",
             "COOLDOWN_HOURS_PER_SYMBOL")

    def setUp(self) -> None:
        self._saved = {k: getattr(config, k) for k in self.KNOBS}
        config.PAPER_STARTING_CAPITAL = 1000.0
        config.MAX_POSITION_PCT = 0.20
        config.RISK_PER_TRADE_PCT = 0.01
        config.MAX_OPEN_POSITIONS = 20
        config.COOLDOWN_HOURS_PER_SYMBOL = 0

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            setattr(config, k, v)

    def _open_n(self, n: int, risk_budget_pct: float, beta_cap: float) -> tuple[list[bool], list[bool]]:
        """Attempt n sequential entries on distinct symbols in both engines;
        returns (live_results, backtest_results) of opened-or-not."""
        config.TOTAL_RISK_BUDGET_PCT = risk_budget_pct
        config.MAX_NET_BETA_EXPOSURE = beta_cap
        now = int(time.time())
        symbols = [f"SYM{i}/USDT" for i in range(n)]
        storage = _storage()
        _seed(storage, symbols, now)
        storage.init_portfolio_state(1000.0)
        trader = PaperTrader(storage)
        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        prices = {s: 1.0 for s in symbols} | {"BTC/USDT": 50_000.0}

        live, bt = [], []
        for sym in symbols:
            r = trader.process_signal(
                {"symbol": sym, "strategy": "parity_test", "direction": "LONG",
                 "reason": "parity", "style": "scalp"}, 1.0)
            live.append(bool(r.get("opened")))
            t = portfolio.open_trade(sym, "LONG", 1.0, now, strategy="parity_test",
                                     prices=prices)
            bt.append(t is not None)
        return live, bt

    def test_risk_budget_binds_identically(self) -> None:
        # Each entry risks ~1% of equity (RISK_PER_TRADE_PCT); a 3% budget
        # admits ~3 positions and must refuse the rest -- in BOTH engines.
        live, bt = self._open_n(6, risk_budget_pct=0.03, beta_cap=999.0)
        self.assertEqual(live, bt, f"live={live} bt={bt}")
        self.assertGreaterEqual(sum(live), 2)
        self.assertLess(sum(live), 6, "risk budget never bound")

    def test_beta_cap_binds_identically(self) -> None:
        # All-LONG beta~1 entries at ~20% notional each: cap 0.5 admits ~2.
        live, bt = self._open_n(6, risk_budget_pct=1.0, beta_cap=0.5)
        self.assertEqual(live, bt, f"live={live} bt={bt}")
        self.assertLess(sum(live), 6, "beta cap never bound")

    def test_unlimited_caps_do_not_bind(self) -> None:
        live, bt = self._open_n(4, risk_budget_pct=1.0, beta_cap=999.0)
        self.assertEqual(live, bt)
        self.assertEqual(sum(live), 4)

    def test_live_refusal_carries_named_gate(self) -> None:
        config.TOTAL_RISK_BUDGET_PCT = 0.001  # everything refused
        config.MAX_NET_BETA_EXPOSURE = 999.0
        now = int(time.time())
        storage = _storage()
        _seed(storage, ["AAA/USDT"], now)
        storage.init_portfolio_state(1000.0)
        trader = PaperTrader(storage)
        r = trader.process_signal(
            {"symbol": "AAA/USDT", "strategy": "parity_test", "direction": "LONG",
             "reason": "parity", "style": "scalp"}, 1.0)
        self.assertFalse(r.get("opened"))
        self.assertEqual(r.get("gate"), "risk_budget")


if __name__ == "__main__":
    unittest.main()
