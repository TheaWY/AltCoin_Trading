from __future__ import annotations

import unittest

from unittest.mock import patch

from src.engine.cycle import _cycle_symbols, _portfolio_accounting_invariant


class _StorageWithOpenTrades:
    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    def get_open_trades(self) -> list[dict[str, str]]:
        return [{"symbol": symbol} for symbol in self._symbols]


class CycleAccountingInvariantTests(unittest.TestCase):
    def test_cycle_symbols_keep_open_position_outside_top_n(self) -> None:
        universe = ["BTC/USDT", "ETH/USDT", "SKL/USDT", "VELVET/USDT"]
        storage = _StorageWithOpenTrades(["VELVET/USDT"])

        with patch("src.config.ACTIVE_TRADING_SYMBOLS_LIMIT", 2):
            selected = _cycle_symbols(universe, storage)  # type: ignore[arg-type]

        self.assertEqual(selected, ["BTC/USDT", "ETH/USDT", "VELVET/USDT"])

    def test_rejects_negative_cash(self) -> None:
        ok, values = _portfolio_accounting_invariant(
            {
                "cash": -1.0,
                "equity": 99.0,
                "open_position_value": 100.0,
            }
        )

        self.assertFalse(ok)
        self.assertEqual(values["cash"], -1.0)
        self.assertEqual(values["equity"], 99.0)
        self.assertEqual(values["positions_value"], 100.0)

    def test_rejects_equity_mismatch(self) -> None:
        ok, values = _portfolio_accounting_invariant(
            {
                "cash": 700.0,
                "equity": 900.0,
                "open_position_value": 100.0,
            }
        )

        self.assertFalse(ok)
        self.assertEqual(values["cash"], 700.0)
        self.assertEqual(values["equity"], 900.0)
        self.assertEqual(values["positions_value"], 100.0)

    def test_accepts_cash_plus_position_value_equity(self) -> None:
        ok, values = _portfolio_accounting_invariant(
            {
                "cash": 700.0,
                "equity": 800.0,
                "open_position_value": 100.0,
            }
        )

        self.assertTrue(ok)
        self.assertEqual(values["cash"], 700.0)
        self.assertEqual(values["equity"], 800.0)
        self.assertEqual(values["positions_value"], 100.0)


if __name__ == "__main__":
    unittest.main()
