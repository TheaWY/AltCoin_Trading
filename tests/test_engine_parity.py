"""CROSS-ENGINE PARITY SUITE -- the standing guard for rule #2 (backtest and
live share one code path).

For every behavior-affecting knob, PaperTrader (live) and BacktestPortfolio
(backtest) must produce IDENTICAL outcomes given identical inputs. This
divergence has bitten three times before this suite existed (ACTIVE_STRATEGY
promotable-but-unvalidated, funding_carry's fake hedge, ATR-vs-flat exit
mechanism) -- each invisible until someone exercised the path end-to-end,
none caught by code review. A hard test gate catches all of them by
construction: if the engines diverge on any knob, this suite FAILS, and the
failure message names the knob.

Fixture principle: both engines run against the SAME seeded storage (prices,
orderbook snapshots, funding) with the SAME config values, and every assert
compares live's outcome directly against backtest's -- never against a
hand-computed expectation, so the suite tests agreement, not either engine's
correctness in isolation.
"""

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
from src.engine.paper_trader import PaperTrader  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import backtest as backtest_module  # noqa: E402

BacktestPortfolio = backtest_module.BacktestPortfolio
BacktestTrade = backtest_module.BacktestTrade
HOUR = 3600


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


def _seed_candles(storage: Storage, symbol: str, end_ts: int, n: int,
                  close: float = 1.0, vol_pct: float = 2.0) -> None:
    """n hourly candles ending just before end_ts's hour, flat close with
    high/low spread of vol_pct -- gives a deterministic, equal ATR to both
    engines' _atr_pct."""
    end_bucket = (end_ts // HOUR) * HOUR
    rows = []
    for i in range(n, 0, -1):
        ts = end_bucket - i * HOUR
        rows.append({
            "symbol": symbol, "timestamp": ts, "timeframe": "1h",
            "open": close, "high": close * (1 + vol_pct / 200), "low": close * (1 - vol_pct / 200),
            "close": close, "volume": 1.0,
        })
    storage.insert_prices(rows, timeframe="1h")


def _seed_orderbook(storage: Storage, symbol: str, ts: int, mid: float = 1.0) -> None:
    storage.insert_orderbook_snapshots([{
        "symbol": symbol, "timestamp": ts,
        "bid_depth_1pct": 20_000.0 / mid, "ask_depth_1pct": 20_000.0 / mid,
        "imbalance_ratio": 1.0, "spread_bps": 10.0,
        "top_bid": mid * 0.9995, "top_ask": mid * 1.0005, "mid_price": mid,
    }])


def _live_trade_row(symbol: str, direction: str, entry: float, qty: float,
                    stop: float, tp: float, opened_at: int, atr_pct: float | None = 2.0,
                    style: str = "scalp") -> dict:
    return {
        "signal_id": None, "symbol": symbol, "direction": direction,
        "entry_price": entry, "exit_price": None, "quantity": qty,
        "stop_loss": stop, "take_profit": tp, "status": "open", "pnl": None,
        "opened_at": opened_at, "closed_at": None, "strategy": "parity_test",
        "style": style, "atr_pct": atr_pct, "trail_price": entry,
        "exit_reason": None, "fees": None,
    }


def _bt_trade(symbol: str, direction: str, entry: float, qty: float,
              stop: float, tp: float, opened_at: int, atr_pct: float | None = 2.0,
              style: str = "scalp") -> BacktestTrade:
    return BacktestTrade(
        symbol=symbol, direction=direction, entry_price=entry, quantity=qty,
        stop_loss=stop, take_profit=tp, opened_at=opened_at,
        strategy="parity_test", metadata={"style": style}, atr_pct=atr_pct,
    )


class _ParityBase(unittest.TestCase):
    """Saves/restores every config knob the tests touch."""

    KNOBS = (
        "PAPER_STARTING_CAPITAL", "MAX_POSITION_PCT", "RISK_PER_TRADE_PCT",
        "STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "ATR_STOP_MULT", "ATR_TP_MULT",
        "FEE_MODE", "STOP_SLIPPAGE_MULT", "TRAILING_STOP_ENABLED",
        "TRAIL_ATR_MULT", "COOLDOWN_HOURS_PER_SYMBOL", "MAX_OPEN_POSITIONS",
        "SCALP_MAX_HOLD_HOURS", "ALLOW_LONG", "ALLOW_SHORT",
    )

    def setUp(self) -> None:
        self._saved = {k: getattr(config, k) for k in self.KNOBS}
        config.PAPER_STARTING_CAPITAL = 1000.0
        config.MAX_POSITION_PCT = 0.20
        config.RISK_PER_TRADE_PCT = 0.01
        config.COOLDOWN_HOURS_PER_SYMBOL = 0
        config.MAX_OPEN_POSITIONS = 10
        config.ALLOW_LONG = True
        config.ALLOW_SHORT = True

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            setattr(config, k, v)


class EntryParityTests(_ParityBase):
    """Same candidate + same config -> same fill price, size, stop, target.

    KNOWN DIVERGENCES this test documents until the exits.py wiring lands
    (staged behind the in-flight execution-cost sweep):
      - stop/target: live is ATR-scaled (ATR_STOP_MULT/ATR_TP_MULT), backtest
        is flat STOP_LOSS_PCT/TAKE_PROFIT_PCT;
      - size: live is risk-based (RISK_PER_TRADE_PCT / stop distance, capped
        at MAX_POSITION_PCT), backtest is flat MAX_POSITION_PCT of equity.
    When those fixes land these subtests flip to green and become the guard.
    """

    def _open_both(self, vol_pct: float, direction: str):
        storage = _storage()
        now = int(time.time())
        _seed_candles(storage, "AAA/USDT", now, 48, close=1.0, vol_pct=vol_pct)
        _seed_orderbook(storage, "AAA/USDT", now - 10)
        storage.init_portfolio_state(1000.0)

        trader = PaperTrader(storage)
        live = trader.process_signal(
            {"symbol": "AAA/USDT", "strategy": "parity_test", "direction": direction,
             "reason": "parity", "style": "scalp"},
            1.0,
        )
        self.assertTrue(live.get("opened"), live)
        live_row = storage.get_open_trades("AAA/USDT")[0]

        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        bt_trade = portfolio.open_trade(
            "AAA/USDT", direction, 1.0, now,
            strategy="parity_test", metadata={"style": "scalp"},
        )
        self.assertIsNotNone(bt_trade)
        return live_row, bt_trade

    def test_entry_parity_across_volatility_and_direction(self) -> None:
        for vol_pct in (0.5, 2.0, 5.0):
            for direction in ("LONG", "SHORT"):
                live_row, bt = self._open_both(vol_pct, direction)
                ctx = f"vol={vol_pct}% {direction}"
                live_notional = float(live_row["quantity"]) * float(live_row["entry_price"])
                with self.subTest(knob="size (notional)", ctx=ctx):
                    self.assertAlmostEqual(live_notional, bt.notional, places=6)
                with self.subTest(knob="entry fill price", ctx=ctx):
                    self.assertAlmostEqual(float(live_row["entry_price"]), bt.entry_price, places=9)
                with self.subTest(knob="stop_loss", ctx=ctx):
                    self.assertAlmostEqual(float(live_row["stop_loss"]), bt.stop_loss, places=9)
                with self.subTest(knob="take_profit", ctx=ctx):
                    self.assertAlmostEqual(float(live_row["take_profit"]), bt.take_profit, places=9)


class ExitTriggerParityTests(_ParityBase):
    """Identically-constructed open trades + same price -> same trigger,
    same exit fill, same reason, same pnl. Trailing disabled here to isolate
    the trigger/fill path (it has its own parity test below)."""

    def setUp(self) -> None:
        super().setUp()
        config.TRAILING_STOP_ENABLED = False
        config.SCALP_MAX_HOLD_HOURS = 6.0

    def _close_both(self, direction: str, stop: float, tp: float,
                    exit_price: float, opened_ago_s: int = 60,
                    stop_slippage_mult: float = 1.0):
        config.STOP_SLIPPAGE_MULT = stop_slippage_mult
        storage = _storage()
        now = int(time.time())
        opened_at = now - opened_ago_s
        _seed_orderbook(storage, "AAA/USDT", now - 10)
        storage.init_portfolio_state(1000.0)

        storage.insert_paper_trade(
            _live_trade_row("AAA/USDT", direction, 1.0, 100.0, stop, tp, opened_at)
        )
        trader = PaperTrader(storage)
        closed = trader.check_open_trades_for_symbol("AAA/USDT", exit_price)
        live_closed = storage.get_recent_closed_trades(1)[0] if closed else None

        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        bt = _bt_trade("AAA/USDT", direction, 1.0, 100.0, stop, tp, opened_at)
        portfolio.open_trades.append(bt)
        portfolio.cash -= bt.notional
        portfolio.check_exits({"AAA/USDT": exit_price}, now)
        bt_closed = portfolio.closed_trades[0] if portfolio.closed_trades else None
        return live_closed, bt_closed

    def _assert_closed_identically(self, live, bt, ctx: str) -> None:
        with self.subTest(knob="exit triggered", ctx=ctx):
            self.assertEqual(live is not None, bt is not None)
        if live is None or bt is None:
            return
        with self.subTest(knob="exit_reason", ctx=ctx):
            self.assertEqual(live["exit_reason"], bt.exit_reason)
        with self.subTest(knob="exit fill price", ctx=ctx):
            self.assertAlmostEqual(float(live["exit_price"]), bt.exit_price, places=9)
        with self.subTest(knob="fees", ctx=ctx):
            self.assertAlmostEqual(float(live["fees"]), bt.fees, places=9)
        with self.subTest(knob="pnl", ctx=ctx):
            self.assertAlmostEqual(float(live["pnl"]), bt.pnl, places=9)

    def test_stop_loss_parity_across_stress_mults(self) -> None:
        for mult in (1.0, 3.0):
            live, bt = self._close_both("LONG", stop=0.95, tp=1.10,
                                        exit_price=0.90, stop_slippage_mult=mult)
            self._assert_closed_identically(live, bt, f"LONG stop mult={mult}")
            live, bt = self._close_both("SHORT", stop=1.05, tp=0.90,
                                        exit_price=1.10, stop_slippage_mult=mult)
            self._assert_closed_identically(live, bt, f"SHORT stop mult={mult}")

    def test_take_profit_parity(self) -> None:
        live, bt = self._close_both("LONG", stop=0.95, tp=1.05, exit_price=1.06)
        self._assert_closed_identically(live, bt, "LONG tp")
        live, bt = self._close_both("SHORT", stop=1.05, tp=0.95, exit_price=0.94)
        self._assert_closed_identically(live, bt, "SHORT tp")

    def test_time_stop_parity(self) -> None:
        live, bt = self._close_both("LONG", stop=0.90, tp=1.10, exit_price=1.001,
                                    opened_ago_s=7 * HOUR)
        self._assert_closed_identically(live, bt, "LONG time_stop 7h > 6h cap")

    def test_no_exit_parity(self) -> None:
        live, bt = self._close_both("LONG", stop=0.90, tp=1.10, exit_price=1.001)
        self.assertIsNone(live)
        self.assertIsNone(bt)


class TrailingStopParityTests(_ParityBase):
    """Same high-water path -> same ratcheted stop.

    KNOWN DIVERGENCE until the exits.py wiring lands: live ratchets the stop
    behind the high-water mark; backtest has no trailing logic at all."""

    def test_trailing_ratchet_parity(self) -> None:
        config.TRAILING_STOP_ENABLED = True
        config.TRAIL_ATR_MULT = 2.0
        storage = _storage()
        now = int(time.time())
        _seed_orderbook(storage, "AAA/USDT", now - 10)
        storage.init_portfolio_state(1000.0)

        # entry 1.0, ATR 2%, stop 0.97, tp 1.10; price runs to 1.03 (=1.5 ATR
        # in favor, trail armed) without touching tp.
        storage.insert_paper_trade(
            _live_trade_row("AAA/USDT", "LONG", 1.0, 100.0, 0.97, 1.10, now - 60)
        )
        trader = PaperTrader(storage)
        trader.check_open_trades_for_symbol("AAA/USDT", 1.03)
        live_stop = float(storage.get_open_trades("AAA/USDT")[0]["stop_loss"])

        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        bt = _bt_trade("AAA/USDT", "LONG", 1.0, 100.0, 0.97, 1.10, now - 60)
        portfolio.open_trades.append(bt)
        portfolio.cash -= bt.notional
        portfolio.check_exits({"AAA/USDT": 1.03}, now)

        self.assertAlmostEqual(
            live_stop, bt.stop_loss, places=9,
            msg="trailing-stop ratchet diverges between live and backtest",
        )


class CostParityTests(_ParityBase):
    """Same fill -> same cost, across the FEE_MODE research axis."""

    def test_fees_parity_across_fee_modes(self) -> None:
        for fee_mode in ("taker", "maker"):
            config.FEE_MODE = fee_mode
            config.TRAILING_STOP_ENABLED = False
            storage = _storage()
            now = int(time.time())
            _seed_orderbook(storage, "AAA/USDT", now - 10)
            storage.init_portfolio_state(1000.0)

            storage.insert_paper_trade(
                _live_trade_row("AAA/USDT", "LONG", 1.0, 100.0, 0.95, 1.05, now - 60)
            )
            trader = PaperTrader(storage)
            trader.check_open_trades_for_symbol("AAA/USDT", 1.06)
            live = storage.get_recent_closed_trades(1)[0]

            portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
            bt = _bt_trade("AAA/USDT", "LONG", 1.0, 100.0, 0.95, 1.05, now - 60)
            portfolio.open_trades.append(bt)
            portfolio.cash -= bt.notional
            portfolio.check_exits({"AAA/USDT": 1.06}, now)
            bt_closed = portfolio.closed_trades[0]

            with self.subTest(knob="fees", fee_mode=fee_mode):
                self.assertAlmostEqual(float(live["fees"]), bt_closed.fees, places=9)
            with self.subTest(knob="pnl", fee_mode=fee_mode):
                self.assertAlmostEqual(float(live["pnl"]), bt_closed.pnl, places=9)


class HedgeParityTests(_ParityBase):
    """Same beta -> same hedge notional; same close -> same combined P&L."""

    def _seed_hedge_world(self, storage: Storage, now: int) -> None:
        _seed_candles(storage, "AAA/USDT", now, 30, close=1.0, vol_pct=2.0)
        _seed_candles(storage, "BTC/USDT", now, 30, close=50_000.0, vol_pct=1.0)
        # get_latest_price needs a row AT now for live's hedge pricing to
        # match the 50_000 the backtest gets via its prices dict.
        storage.insert_prices([{
            "symbol": "BTC/USDT", "timestamp": now, "open": 50_000, "high": 50_000,
            "low": 50_000, "close": 50_000, "volume": 1.0,
        }])
        storage.insert_prices([{
            "symbol": "AAA/USDT", "timestamp": now, "open": 1.0, "high": 1.0,
            "low": 1.0, "close": 1.0, "volume": 1.0,
        }])
        _seed_orderbook(storage, "AAA/USDT", now - 10)

    def test_hedge_leg_sizing_parity_across_betas(self) -> None:
        for beta in (0.8, 1.2):
            storage = _storage()
            now = int(time.time())
            self._seed_hedge_world(storage, now)
            storage.init_portfolio_state(1000.0)

            trader = PaperTrader(storage)
            live_leg = trader._hedge_leg_for_open(
                {"metadata": {"execution_mode": "market_neutral",
                              "hedge_symbol": "BTC/USDT", "beta": beta}},
                "LONG", 1000.0, 1000.0, 1.0,
            )
            portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
            bt_leg = portfolio._hedge_leg_for_open(
                "LONG",
                {"execution_mode": "market_neutral", "hedge_symbol": "BTC/USDT", "beta": beta},
                {"AAA/USDT": 1.0, "BTC/USDT": 50_000.0}, now,
            )
            self.assertIsNotNone(live_leg)
            self.assertIsNotNone(bt_leg)
            for key in ("hedge_symbol", "hedge_direction", "hedge_entry_price",
                        "hedge_quantity", "hedge_beta", "primary_notional", "hedge_notional"):
                with self.subTest(knob=f"hedge {key}", beta=beta):
                    if isinstance(live_leg[key], float):
                        self.assertAlmostEqual(live_leg[key], bt_leg[key], places=9)
                    else:
                        self.assertEqual(live_leg[key], bt_leg[key])

    def test_hedge_combined_pnl_parity_end_to_end(self) -> None:
        config.TRAILING_STOP_ENABLED = False
        storage = _storage()
        now = int(time.time())
        opened_at = now - 25 * HOUR
        self._seed_hedge_world(storage, now)
        storage.insert_funding_rates([
            {"symbol": "BTC/USDT", "timestamp": opened_at + 8 * HOUR, "funding_rate": 0.0004},
            {"symbol": "BTC/USDT", "timestamp": opened_at + 16 * HOUR, "funding_rate": 0.0003},
        ])
        storage.init_portfolio_state(1000.0)

        hedge_fields = {
            "hedge_symbol": "BTC/USDT", "hedge_direction": "SHORT",
            "hedge_entry_price": 50_000.0, "hedge_quantity": 0.002, "hedge_beta": 1.0,
        }
        live_row = _live_trade_row("AAA/USDT", "LONG", 1.0, 100.0, 0.95, 1.05, opened_at)
        live_row.update(hedge_fields)
        storage.insert_paper_trade(live_row)
        trader = PaperTrader(storage)
        trader.check_open_trades_for_symbol("AAA/USDT", 1.06)
        live = storage.get_recent_closed_trades(1)[0]

        portfolio = BacktestPortfolio(cash=1000.0, storage=storage)
        bt = _bt_trade("AAA/USDT", "LONG", 1.0, 100.0, 0.95, 1.05, opened_at)
        for k, v in hedge_fields.items():
            setattr(bt, k, v)
        portfolio.open_trades.append(bt)
        portfolio.cash -= bt.notional + 50_000.0 * 0.002
        portfolio.check_exits({"AAA/USDT": 1.06, "BTC/USDT": 50_000.0}, now)
        bt_closed = portfolio.closed_trades[0]

        for knob, live_v, bt_v in (
            ("hedge_exit_price", float(live["hedge_exit_price"]), bt_closed.hedge_exit_price),
            ("hedge_pnl", float(live["hedge_pnl"]), bt_closed.hedge_pnl),
            ("hedge_fees", float(live["hedge_fees"]), bt_closed.hedge_fees),
            ("funding_pnl", float(live["funding_pnl"]), bt_closed.funding_pnl),
            ("combined pnl", float(live["pnl"]), bt_closed.pnl),
        ):
            with self.subTest(knob=knob):
                self.assertAlmostEqual(live_v, bt_v, places=9)


class EntryGateParityTests(_ParityBase):
    """The refusal gates: both engines must refuse the same entries."""

    def _engines(self, now: int):
        storage = _storage()
        _seed_candles(storage, "AAA/USDT", now, 48, close=1.0, vol_pct=2.0)
        _seed_orderbook(storage, "AAA/USDT", now - 10)
        storage.init_portfolio_state(1000.0)
        return storage, PaperTrader(storage), BacktestPortfolio(cash=1000.0, storage=storage)

    def _live_open(self, trader, direction="LONG", symbol="AAA/USDT") -> bool:
        r = trader.process_signal(
            {"symbol": symbol, "strategy": "parity_test", "direction": direction,
             "reason": "parity", "style": "scalp"}, 1.0)
        return bool(r.get("opened"))

    def test_direction_policy_refused_identically(self) -> None:
        config.ALLOW_LONG = False
        now = int(time.time())
        storage, trader, portfolio = self._engines(now)
        live_opened = self._live_open(trader, "LONG")
        bt_trade = portfolio.open_trade("AAA/USDT", "LONG", 1.0, now, strategy="parity_test")
        self.assertEqual(live_opened, bt_trade is not None)
        self.assertFalse(live_opened)

    def test_duplicate_position_refused_identically(self) -> None:
        now = int(time.time())
        storage, trader, portfolio = self._engines(now)
        self.assertTrue(self._live_open(trader))
        self.assertIsNotNone(portfolio.open_trade("AAA/USDT", "LONG", 1.0, now, strategy="parity_test"))
        live_second = self._live_open(trader)
        bt_second = portfolio.open_trade("AAA/USDT", "LONG", 1.0, now + 60, strategy="parity_test")
        self.assertEqual(live_second, bt_second is not None)
        self.assertFalse(live_second)

    def test_cooldown_refused_identically(self) -> None:
        config.COOLDOWN_HOURS_PER_SYMBOL = 24
        config.TRAILING_STOP_ENABLED = False
        now = int(time.time())
        storage, trader, portfolio = self._engines(now)
        self.assertTrue(self._live_open(trader))
        self.assertIsNotNone(portfolio.open_trade("AAA/USDT", "LONG", 1.0, now, strategy="parity_test"))
        # close both so the refusal below can only come from the cooldown
        trader.check_open_trades_for_symbol("AAA/USDT", 0.5)
        portfolio.check_exits({"AAA/USDT": 0.5}, now + 60)
        live_retry = self._live_open(trader)
        bt_retry = portfolio.open_trade("AAA/USDT", "LONG", 1.0, now + 2 * HOUR, strategy="parity_test")
        self.assertEqual(live_retry, bt_retry is not None)
        self.assertFalse(live_retry)

    def test_max_open_positions_refused_identically(self) -> None:
        config.MAX_OPEN_POSITIONS = 1
        now = int(time.time())
        storage, trader, portfolio = self._engines(now)
        _seed_candles(storage, "BBB/USDT", now, 48, close=1.0, vol_pct=2.0)
        _seed_orderbook(storage, "BBB/USDT", now - 10)
        self.assertTrue(self._live_open(trader, symbol="AAA/USDT"))
        self.assertIsNotNone(portfolio.open_trade("AAA/USDT", "LONG", 1.0, now, strategy="parity_test"))
        live_second = self._live_open(trader, symbol="BBB/USDT")
        bt_second = portfolio.open_trade("BBB/USDT", "LONG", 1.0, now + 60, strategy="parity_test")
        self.assertEqual(live_second, bt_second is not None)
        self.assertFalse(live_second)


if __name__ == "__main__":
    unittest.main()
