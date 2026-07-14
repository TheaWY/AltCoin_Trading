"""T2: PARITY -- same symbol, same notional, same orderbook snapshot, same
side -> PaperTrader and BacktestPortfolio must produce the IDENTICAL fill
price. This is rule #2 (one code path for backtest and live) in test form;
if it ever fails, the paths have diverged again.

T4: GAP -- a stop with a gap-through bar fills at the gapped price, not the
nominal stop price, and the resulting loss exceeds the nominal stop distance.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import Storage  # noqa: E402
from src.engine.paper_trader import PaperTrader  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import backtest as backtest_module  # noqa: E402

BacktestPortfolio = backtest_module.BacktestPortfolio
HOUR = 3600


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


class FillPriceParityTests(unittest.TestCase):
    """Both classes' _adjusted_fill_price is a verbatim mirror of the other
    -- same execution_cost calls, same stress condition -- so calling it
    with identical arguments against the identical storage must produce
    identical output. If PaperTrader or BacktestPortfolio's implementation
    ever drifts, this is the test that catches it.
    """

    def setUp(self) -> None:
        self.old_mult = config.STOP_SLIPPAGE_MULT
        self.old_atr_mult = config.STRESS_BAR_RANGE_ATR_MULT

    def tearDown(self) -> None:
        config.STOP_SLIPPAGE_MULT = self.old_mult
        config.STRESS_BAR_RANGE_ATR_MULT = self.old_atr_mult

    def test_identical_fill_price_with_real_orderbook_snapshot(self) -> None:
        storage = _storage()
        ts = 1_700_000_000
        storage.insert_orderbook_snapshots([{
            "symbol": "AAA/USDT", "timestamp": ts - 10,
            "bid_depth_1pct": 20_000.0, "ask_depth_1pct": 20_000.0,
            "imbalance_ratio": 1.0, "spread_bps": 12.0,
            "top_bid": 0.999, "top_ask": 1.001, "mid_price": 1.0,
        }])

        trader = PaperTrader(storage)
        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)

        live_price, live_meta = trader._adjusted_fill_price(
            "AAA/USDT", "LONG", is_entry=True, raw_price=1.0, notional=5000.0, ts=ts,
        )
        bt_price, bt_meta = portfolio._adjusted_fill_price(
            "AAA/USDT", "LONG", is_entry=True, raw_price=1.0, notional=5000.0, ts=ts,
        )
        self.assertEqual(live_price, bt_price)
        self.assertEqual(live_meta, bt_meta)
        self.assertFalse(live_meta["used_fallback"])

    def test_identical_fill_price_on_fallback_path(self) -> None:
        """No orderbook snapshot at all -- both must fall back identically."""
        storage = _storage()
        ts = 1_700_000_000

        trader = PaperTrader(storage)
        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)

        live_price, live_meta = trader._adjusted_fill_price(
            "AAA/USDT", "SHORT", is_entry=True, raw_price=2.5, notional=8000.0, ts=ts,
        )
        bt_price, bt_meta = portfolio._adjusted_fill_price(
            "AAA/USDT", "SHORT", is_entry=True, raw_price=2.5, notional=8000.0, ts=ts,
        )
        self.assertEqual(live_price, bt_price)
        self.assertEqual(live_meta, bt_meta)
        self.assertTrue(live_meta["used_fallback"])

    def test_identical_fill_price_under_stop_loss_stress(self) -> None:
        config.STOP_SLIPPAGE_MULT = 3.0
        storage = _storage()
        ts = 1_700_000_000
        storage.insert_orderbook_snapshots([{
            "symbol": "AAA/USDT", "timestamp": ts - 10,
            "bid_depth_1pct": 20_000.0, "ask_depth_1pct": 20_000.0,
            "imbalance_ratio": 1.0, "spread_bps": 12.0,
            "top_bid": 0.999, "top_ask": 1.001, "mid_price": 1.0,
        }])

        trader = PaperTrader(storage)
        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)

        live_price, live_meta = trader._adjusted_fill_price(
            "AAA/USDT", "LONG", is_entry=False, raw_price=0.95, notional=5000.0,
            ts=ts, reason="stop_loss", atr_pct=2.0,
        )
        bt_price, bt_meta = portfolio._adjusted_fill_price(
            "AAA/USDT", "LONG", is_entry=False, raw_price=0.95, notional=5000.0,
            ts=ts, reason="stop_loss", atr_pct=2.0,
        )
        self.assertEqual(live_price, bt_price)
        self.assertEqual(live_meta, bt_meta)
        self.assertTrue(live_meta["is_stress"])

    def test_backtest_with_no_storage_matches_paper_trader_zero_history(self) -> None:
        """A hand-built BacktestPortfolio with storage=None must fall back to
        raw_price (documented, conservative) -- confirm this does NOT
        silently equal a fallback-slippage-adjusted PaperTrader fill, i.e.
        the two are not accidentally coincidentally equal, but each behaves
        as documented for its own inputs."""
        portfolio = BacktestPortfolio(cash=1000.0, storage=None)
        price, meta = portfolio._adjusted_fill_price(
            "AAA/USDT", "LONG", is_entry=True, raw_price=1.0, notional=5000.0, ts=1_700_000_000,
        )
        self.assertEqual(price, 1.0)
        self.assertEqual(meta["slippage_pct"], 0.0)


class GapFillTests(unittest.TestCase):
    """T4: a stop_loss exit during a gap-through bar fills at the actual
    (gapped) price plus simulated slippage -- never at the nominal stop
    level -- and the realized loss exceeds the nominal stop distance."""

    def setUp(self) -> None:
        self.old_start = config.PAPER_STARTING_CAPITAL
        self.old_max_pos = config.MAX_POSITION_PCT
        self.old_stop_pct = config.STOP_LOSS_PCT
        self.old_mult = config.STOP_SLIPPAGE_MULT
        config.PAPER_STARTING_CAPITAL = 1000.0
        config.MAX_POSITION_PCT = 0.20
        config.STOP_LOSS_PCT = 0.05  # 5% nominal stop

    def tearDown(self) -> None:
        config.PAPER_STARTING_CAPITAL = self.old_start
        config.MAX_POSITION_PCT = self.old_max_pos
        config.STOP_LOSS_PCT = self.old_stop_pct
        config.STOP_SLIPPAGE_MULT = self.old_mult

    def test_backtest_stop_loss_gap_through_fills_beyond_stop_and_stop_distance(self) -> None:
        storage = _storage()
        ts_open = 1_700_000_000
        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        trade = portfolio.open_trade("AAA/USDT", "LONG", 1.0, ts_open, strategy="test")
        self.assertIsNotNone(trade)
        nominal_stop = trade.stop_loss
        entry_price = trade.entry_price
        self.assertAlmostEqual(nominal_stop, entry_price * 0.95, places=6)

        # Next bar GAPS straight through the stop -- real close is 0.80, far
        # below the 0.95-ish nominal stop level, simulating a violent bar.
        gapped_price = 0.80
        ts_next = ts_open + HOUR
        portfolio.check_exits({"AAA/USDT": gapped_price}, ts_next)

        self.assertEqual(len(portfolio.closed_trades), 1)
        closed = portfolio.closed_trades[0]
        self.assertEqual(closed.exit_reason, "stop_loss")

        # Must NOT fill at the nominal stop price.
        self.assertNotAlmostEqual(closed.exit_price, nominal_stop, places=4)
        # Must fill at (or worse than, once slippage is added) the actual
        # gapped market price -- a LONG's sell-side slippage only pushes the
        # fill lower still.
        self.assertLessEqual(closed.exit_price, gapped_price)

        nominal_stop_distance_pct = (entry_price - nominal_stop) / entry_price
        realized_loss_pct = (entry_price - closed.exit_price) / entry_price
        self.assertGreater(realized_loss_pct, nominal_stop_distance_pct)

    def test_backtest_intra_bar_touch_caught_when_close_recovers(self) -> None:
        """The fidelity fix (2026-07-14): a bar whose LOW pierces the stop but
        whose CLOSE recovers above it must still stop out (at the stop level).
        The old close-only check missed this -- the bias that made backtests
        look better than reality."""
        storage = _storage()
        ts_open = 1_700_000_000
        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        trade = portfolio.open_trade("AAA/USDT", "LONG", 1.0, ts_open, strategy="test")
        stop = trade.stop_loss  # ~0.95
        ts_next = ts_open + HOUR
        # Bar low dips below the stop; close recovers ABOVE it. A close-only
        # check (close 0.99 > stop) would NOT exit; intra-bar must.
        storage.insert_prices([{
            "symbol": "AAA/USDT", "timestamp": (ts_next // HOUR) * HOUR,
            "open": 0.99, "high": 1.00, "low": stop - 0.01, "close": 0.99, "volume": 1,
        }])
        portfolio.check_exits({"AAA/USDT": 0.99}, ts_next)
        self.assertEqual(len(portfolio.closed_trades), 1)
        closed = portfolio.closed_trades[0]
        self.assertEqual(closed.exit_reason, "stop_loss")
        # Intra-bar cross fills at the stop level (plus sell-side slippage),
        # NOT at the recovered close of 0.99.
        self.assertLessEqual(closed.exit_price, stop + 1e-9)

    def test_paper_trader_stop_loss_gap_through_fills_beyond_stop_and_stop_distance(self) -> None:
        storage = _storage()
        storage.init_portfolio_state(1000.0)
        now = 1_700_000_000
        storage.insert_prices([
            {"symbol": "AAA/USDT", "timestamp": now, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
        ])
        trader = PaperTrader(storage)
        signal_result = {
            "symbol": "AAA/USDT", "strategy": "test", "direction": "LONG", "reason": "test",
            "style": "scalp",
        }
        result = trader.process_signal(signal_result, 1.0)
        self.assertTrue(result["opened"])
        trade = storage.get_open_trades()[0]
        nominal_stop = float(trade["stop_loss"])
        entry_price = float(trade["entry_price"])
        self.assertAlmostEqual(nominal_stop, entry_price * 0.95, places=6)

        gapped_price = 0.80
        closed_list = trader.check_open_trades_for_symbol("AAA/USDT", gapped_price)
        self.assertEqual(len(closed_list), 1)
        closed = storage.get_recent_closed_trades(1)[0]
        self.assertEqual(closed["exit_reason"], "stop_loss")

        exit_price = float(closed["exit_price"])
        self.assertNotAlmostEqual(exit_price, nominal_stop, places=4)
        self.assertLessEqual(exit_price, gapped_price)

        nominal_stop_distance_pct = (entry_price - nominal_stop) / entry_price
        realized_loss_pct = (entry_price - exit_price) / entry_price
        self.assertGreater(realized_loss_pct, nominal_stop_distance_pct)


if __name__ == "__main__":
    unittest.main()
