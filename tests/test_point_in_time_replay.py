from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts import backtest as bt
from src.data.storage import Storage
from src.market import bars
from src.strategies.base import BaseStrategy, Signal, SignalDirection


class AlwaysLongStrategy(BaseStrategy):
    name = "always_long"

    def get_required_data(self):
        return ["latest_price"]

    def generate_signal(self, data):
        row = data["latest_price"]
        return Signal(
            direction=SignalDirection.LONG,
            reason="fixture",
            symbol=data["symbol"],
            entry_price=float(row["close"]),
        )


class ThresholdLongStrategy(AlwaysLongStrategy):
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold

    def generate_signal(self, data):
        row = data["latest_price"]
        if float(row["close"]) < self.threshold:
            return Signal(SignalDirection.NONE, "below", data["symbol"])
        return super().generate_signal(data)


def ts(hours: int) -> int:
    return hours * 3600


def row(symbol: str, hour: int, open_: float, high: float, low: float, close: float):
    return {
        "symbol": symbol,
        "timestamp": ts(hour),
        "timeframe": "1h",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
    }


class PointInTimeReplayTests(unittest.TestCase):
    def make_storage(self, rows) -> Storage:
        path = Path(tempfile.mkdtemp()) / "test.db"
        storage = Storage(db_path=path, database_url="")
        storage.insert_prices(rows, timeframe="1h")
        return storage

    def run_engine(self, storage: Storage, strategy: BaseStrategy, symbols=None):
        engine = bt.BacktestEngine(
            start=datetime.fromtimestamp(ts(20), tz=timezone.utc),
            end=datetime.fromtimestamp(ts(30), tz=timezone.utc),
            symbols=symbols or ["AAA/USDT"],
            strategy_name="funding_rate",
            storage=storage,
        )
        engine.strategy = strategy
        fake_analyzer = mock.Mock()
        fake_analyzer.return_value.analyze.return_value = {
            "confidence": 1.0,
            "recommended_style": "swing",
        }
        with (
            mock.patch.object(bt, "AltAnalyzer", fake_analyzer),
            mock.patch.object(bt.config, "ALLOW_LONG", True),
            mock.patch.object(bt.config, "ALLOW_SHORT", True),
            mock.patch.object(bt.config, "COOLDOWN_HOURS_PER_SYMBOL", 0),
            mock.patch.object(bt.config, "MAX_OPEN_POSITIONS", 20),
            mock.patch.object(bt.config, "RISK_PER_TRADE_PCT", 0.01),
            mock.patch.object(bt.config, "MAX_POSITION_PCT", 0.2),
            mock.patch.object(bt.config, "TRAILING_STOP_ENABLED", False),
        ):
            return engine.run()

    def test_signal_from_bar_t_opens_at_next_bar_open(self):
        rows = [row("AAA/USDT", h, 100, 101, 99, 100) for h in range(20)]
        rows += [
            row("AAA/USDT", 20, 100, 101, 99, 110),
            row("AAA/USDT", 21, 120, 121, 119, 120),
            row("AAA/USDT", 22, 120, 121, 119, 120),
        ]
        result = self.run_engine(self.make_storage(rows), ThresholdLongStrategy(105))
        trade = result["closed_trade_pnls"][0]
        self.assertEqual(trade["signal_bar_close"], ts(21))
        self.assertEqual(trade["intended_execution_time"], ts(21))
        self.assertEqual(trade["actual_fill_time"], ts(21))
        self.assertGreater(trade["actual_fill"], 120.0)

    def test_execution_bar_close_cannot_change_its_own_entry(self):
        base = [row("AAA/USDT", h, 100, 101, 99, 100) for h in range(20)]
        rows_a = base + [
            row("AAA/USDT", 20, 100, 101, 99, 110),
            row("AAA/USDT", 21, 120, 121, 119, 120),
            row("AAA/USDT", 22, 120, 121, 119, 120),
        ]
        rows_b = base + [
            row("AAA/USDT", 20, 100, 101, 99, 110),
            row("AAA/USDT", 21, 120, 200, 119, 199),
            row("AAA/USDT", 22, 120, 121, 119, 120),
        ]
        a = self.run_engine(self.make_storage(rows_a), ThresholdLongStrategy(105))
        b = self.run_engine(self.make_storage(rows_b), ThresholdLongStrategy(105))
        self.assertEqual(a["closed_trade_pnls"][0]["actual_fill"], b["closed_trade_pnls"][0]["actual_fill"])
        self.assertEqual(a["closed_trade_pnls"][0]["actual_fill_time"], b["closed_trade_pnls"][0]["actual_fill_time"])

    def test_incomplete_current_candle_is_invisible(self):
        storage = self.make_storage(
            [
                row("AAA/USDT", 1, 100, 101, 99, 100),
                row("AAA/USDT", 2, 999, 1000, 998, 999),
            ]
        )
        snapshot = bt.SnapshotStorage(storage, ts(2), None)
        latest = snapshot.get_latest_price("AAA/USDT")
        self.assertEqual(int(latest["timestamp"]), ts(1))

    def test_gap_through_stop_fills_at_adverse_open(self):
        portfolio = bt.BacktestPortfolio()
        with mock.patch.object(bt.config, "ALLOW_LONG", True):
            trade = portfolio.open_trade("AAA/USDT", "LONG", 100, ts(1), atr_pct=None)
        self.assertIsNotNone(trade)
        assert trade is not None
        trade.stop_loss = 95
        portfolio.process_candle_exits({"AAA/USDT": row("AAA/USDT", 2, 90, 91, 89, 90)}, ts(3))
        closed = portfolio.closed_trades[0]
        self.assertEqual(closed.exit_reason, "stop_loss_gap")
        self.assertLess(closed.exit_price, 90)

    def test_ambiguous_stop_and_target_uses_adverse_first(self):
        portfolio = bt.BacktestPortfolio()
        with mock.patch.object(bt.config, "ALLOW_LONG", True):
            trade = portfolio.open_trade("AAA/USDT", "LONG", 100, ts(1), atr_pct=None)
        assert trade is not None
        trade.stop_loss = 95
        trade.take_profit = 110
        ambiguous = portfolio.process_candle_exits(
            {"AAA/USDT": row("AAA/USDT", 2, 100, 111, 94, 105)},
            ts(3),
        )
        self.assertEqual(ambiguous, 1)
        self.assertEqual(portfolio.closed_trades[0].exit_reason, "stop_loss")
        self.assertTrue(portfolio.closed_trades[0].ambiguous_exit)

    def test_stale_symbol_does_not_emit_repeated_signals(self):
        rows = [row("AAA/USDT", h, 100, 101, 99, 100) for h in range(20, 23)]
        rows += [row("BBB/USDT", h, 100, 101, 99, 100) for h in range(20, 27)]
        result = self.run_engine(
            self.make_storage(rows),
            AlwaysLongStrategy(),
            symbols=["AAA/USDT", "BBB/USDT"],
        )
        self.assertEqual(result["summary"]["signals"], 10)

    def test_final_liquidation_included_in_return_and_drawdown(self):
        rows = [row("AAA/USDT", h, 100, 101, 99, 100) for h in range(20)]
        rows += [
            row("AAA/USDT", 20, 100, 101, 99, 110),
            row("AAA/USDT", 21, 110, 111, 109, 110),
            row("AAA/USDT", 22, 130, 131, 129, 130),
        ]
        result = self.run_engine(self.make_storage(rows), ThresholdLongStrategy(105))
        self.assertGreaterEqual(result["summary"]["closed_trades"], 1)
        self.assertNotEqual(result["summary"]["final_value"], result["summary"]["starting_capital"])
        self.assertIn("max_drawdown_pct", result["summary"])

    def test_same_fixture_twice_is_deterministic(self):
        rows = [row("AAA/USDT", h, 100, 101, 99, 100) for h in range(20)]
        rows += [
            row("AAA/USDT", 20, 100, 101, 99, 110),
            row("AAA/USDT", 21, 120, 121, 119, 120),
            row("AAA/USDT", 22, 120, 121, 119, 120),
        ]
        result_a = self.run_engine(self.make_storage(rows), ThresholdLongStrategy(105))
        result_b = self.run_engine(self.make_storage(rows), ThresholdLongStrategy(105))
        self.assertEqual(result_a["summary"], result_b["summary"])
        self.assertEqual(result_a["closed_trade_pnls"], result_b["closed_trade_pnls"])

    def test_bar_helpers_validate_completed_visibility(self):
        visible = bars.visible_completed_bars(
            [row("AAA/USDT", 1, 1, 2, 1, 2), row("AAA/USDT", 2, 2, 3, 2, 3)],
            "1h",
            ts(2),
        )
        self.assertEqual([int(r["timestamp"]) for r in visible], [ts(1)])


if __name__ == "__main__":
    unittest.main()
