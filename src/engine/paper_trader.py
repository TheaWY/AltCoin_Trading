"""Paper trading engine — simulates positions on Binance Testnet signals."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.strategies.base import SignalDirection

logger = logging.getLogger(__name__)


class PaperTrader:
    """Opens/closes simulated trades with position sizing and SL/TP rules."""

    def __init__(self, storage: Storage | None = None) -> None:
        self.storage = storage or get_storage()

    def ensure_portfolio(self, current_price: float | None = None) -> dict[str, Any]:
        state = self.storage.get_portfolio_state()
        if state is None:
            state = self.storage.init_portfolio_state(
                config.PAPER_STARTING_CAPITAL,
                benchmark_btc_price=current_price,
            )
        elif current_price and not state.get("benchmark_btc_price"):
            self.storage.set_benchmark_price(current_price)
            state = self.storage.get_portfolio_state()
        assert state is not None
        return state

    def portfolio_value(self, prices_by_symbol: dict[str, float] | None = None) -> float:
        """Total portfolio value using latest price per open position."""
        prices_by_symbol = prices_by_symbol or {}
        state = self.storage.get_portfolio_state()
        if state is None:
            self.ensure_portfolio()
            state = self.storage.get_portfolio_state()
        cash = float(state["cash"]) if state else config.PAPER_STARTING_CAPITAL

        total = cash
        for trade in self.storage.get_open_trades():
            sym = trade["symbol"]
            price = prices_by_symbol.get(sym)
            if price is None:
                row = self.storage.get_latest_price(sym)
                price = float(row["close"]) if row else float(trade["entry_price"])
            total += self._position_value(trade, price)
        return total

    def portfolio_value_legacy(self, current_price: float) -> float:
        """Legacy single-symbol estimate (BTC price proxy)."""
        return self.portfolio_value({config.SYMBOL: current_price})

    def buy_and_hold_value(self, current_price: float) -> float | None:
        state = self.ensure_portfolio(current_price)
        benchmark_price = state.get("benchmark_btc_price")
        if not benchmark_price:
            return None
        shares = config.PAPER_STARTING_CAPITAL / float(benchmark_price)
        return shares * current_price

    def process_signal(
        self,
        signal_result: dict[str, Any],
        current_price: float,
        require_worth: bool = False,
    ) -> dict[str, Any]:
        direction = signal_result.get("direction")
        symbol = signal_result.get("symbol", config.SYMBOL)
        if direction not in (SignalDirection.LONG.value, SignalDirection.SHORT.value):
            return {"opened": False, "reason": "non-actionable signal"}

        if self.storage.get_open_trade_for_symbol(symbol):
            return {"opened": False, "reason": f"position already open for {symbol}"}

        if self.storage.count_open_trades() >= config.MAX_OPEN_POSITIONS:
            return {"opened": False, "reason": "max open positions reached"}

        return self._open_trade(signal_result, current_price)

    def check_open_trades_for_symbol(
        self, symbol: str, current_price: float
    ) -> list[dict[str, Any]]:
        closed = []
        for trade in self.storage.get_open_trades(symbol):
            exit_reason = self._check_exit(trade, current_price)
            if exit_reason:
                closed.append(self._close_trade(trade, current_price, exit_reason))
        return closed

    def check_open_trades(self, current_price: float) -> list[dict[str, Any]]:
        """Legacy — check all open trades using one price (BTC only)."""
        return self.check_open_trades_for_symbol(config.SYMBOL, current_price)

    def _open_trade(self, signal_result: dict[str, Any], current_price: float) -> dict[str, Any]:
        symbol = signal_result.get("symbol", config.SYMBOL)
        state = self.ensure_portfolio(current_price)
        portfolio = self.portfolio_value()
        notional = portfolio * config.MAX_POSITION_PCT
        quantity = notional / current_price
        direction = signal_result["direction"]

        if direction == SignalDirection.LONG.value:
            stop_loss = current_price * (1 - config.STOP_LOSS_PCT)
            take_profit = current_price * (1 + config.TAKE_PROFIT_PCT)
        else:
            stop_loss = current_price * (1 + config.STOP_LOSS_PCT)
            take_profit = current_price * (1 - config.TAKE_PROFIT_PCT)

        now_ts = int(datetime.now(timezone.utc).timestamp())
        trade_id = self.storage.insert_paper_trade(
            {
                "signal_id": signal_result.get("signal_id"),
                "symbol": symbol,
                "direction": direction,
                "entry_price": current_price,
                "exit_price": None,
                "quantity": quantity,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "status": "open",
                "pnl": None,
                "opened_at": now_ts,
                "closed_at": None,
            }
        )

        new_cash = float(state["cash"]) - notional
        self.storage.update_portfolio_cash(new_cash)

        logger.info(
            "Paper trade opened id=%s %s %s qty=%.6f @ %.2f",
            trade_id,
            symbol,
            direction,
            quantity,
            current_price,
        )
        return {
            "opened": True,
            "trade_id": trade_id,
            "direction": direction,
            "quantity": quantity,
            "entry_price": current_price,
        }

    def _close_trade(
        self, trade: dict[str, Any], current_price: float, reason: str
    ) -> dict[str, Any]:
        pnl = self._realized_pnl(trade, current_price)
        notional = float(trade["quantity"]) * float(trade["entry_price"])
        state = self.storage.get_portfolio_state()
        cash = float(state["cash"]) if state else 0.0
        self.storage.update_portfolio_cash(cash + notional + pnl)

        now_ts = int(datetime.now(timezone.utc).timestamp())
        self.storage.update_paper_trade(
            int(trade["id"]),
            {
                "exit_price": current_price,
                "status": "closed",
                "pnl": pnl,
                "closed_at": now_ts,
            },
        )
        logger.info(
            "Paper trade closed id=%s pnl=%.2f reason=%s",
            trade["id"],
            pnl,
            reason,
        )
        return {"trade_id": trade["id"], "pnl": pnl, "reason": reason}

    def _check_exit(self, trade: dict[str, Any], price: float) -> str | None:
        direction = trade["direction"]
        stop = float(trade["stop_loss"])
        target = float(trade["take_profit"])

        if direction == SignalDirection.LONG.value:
            if price <= stop:
                return "stop_loss"
            if price >= target:
                return "take_profit"
        else:
            if price >= stop:
                return "stop_loss"
            if price <= target:
                return "take_profit"
        return None

    def _position_value(self, trade: dict[str, Any], price: float) -> float:
        qty = float(trade["quantity"])
        entry = float(trade["entry_price"])
        notional = qty * entry
        if trade["direction"] == SignalDirection.LONG.value:
            return qty * price
        return notional + (entry - price) * qty

    def summary(self, current_price: float | None = None) -> dict[str, Any]:
        state = self.ensure_portfolio(current_price)
        prices: dict[str, float] = {}
        if current_price:
            prices[config.SYMBOL] = current_price
        for trade in self.storage.get_open_trades():
            sym = trade["symbol"]
            if sym not in prices:
                row = self.storage.get_latest_price(sym)
                if row:
                    prices[sym] = float(row["close"])

        paper_value = self.portfolio_value(prices)
        btc_price = prices.get(config.SYMBOL) or current_price
        buy_hold = self.buy_and_hold_value(btc_price) if btc_price else None
        return {
            "cash": float(state["cash"]),
            "paper_value": paper_value,
            "starting_capital": config.PAPER_STARTING_CAPITAL,
            "buy_and_hold_value": buy_hold,
            "open_trades": self.storage.count_open_trades(),
            "max_open_positions": config.MAX_OPEN_POSITIONS,
            "pnl_vs_start": paper_value - config.PAPER_STARTING_CAPITAL,
            "pnl_vs_buy_hold": (paper_value - buy_hold) if buy_hold is not None else None,
            "total_invested_open": self._total_invested_open(),
            "total_unrealized_pnl": self._total_unrealized_pnl(prices),
            "total_realized_pnl": self._total_realized_pnl(),
        }

    def _realized_pnl(self, trade: dict[str, Any], exit_price: float) -> float:
        qty = float(trade["quantity"])
        entry = float(trade["entry_price"])
        if trade["direction"] == SignalDirection.LONG.value:
            return (exit_price - entry) * qty
        return (entry - exit_price) * qty

    def _total_invested_open(self) -> float:
        total = 0.0
        for trade in self.storage.get_open_trades():
            total += float(trade["quantity"]) * float(trade["entry_price"])
        return total

    def _total_unrealized_pnl(self, prices: dict[str, float]) -> float:
        total = 0.0
        for trade in self.storage.get_open_trades():
            sym = trade["symbol"]
            price = prices.get(sym)
            if price is None:
                row = self.storage.get_latest_price(sym)
                price = float(row["close"]) if row else float(trade["entry_price"])
            total += self._realized_pnl(trade, price)
        return total

    def _total_realized_pnl(self) -> float:
        total = 0.0
        for sym in {t["symbol"] for t in self.storage.get_recent_trades(500)}:
            for trade in self.storage.get_closed_trades_for_symbol(sym):
                if trade.get("pnl") is not None:
                    total += float(trade["pnl"])
        return total

    def investment_for_symbol(
        self, symbol: str, current_price: float | None = None
    ) -> dict[str, Any]:
        """Per-coin invested amount, value, and P&L (open + realized)."""
        open_trade = self.storage.get_open_trade_for_symbol(symbol)
        closed = self.storage.get_closed_trades_for_symbol(symbol)
        realized_pnl = sum(float(t["pnl"] or 0) for t in closed)
        closed_invested = sum(
            float(t["quantity"]) * float(t["entry_price"]) for t in closed
        )

        if not open_trade:
            return {
                "status": "flat",
                "direction": None,
                "invested": 0.0,
                "current_value": 0.0,
                "unrealized_pnl": 0.0,
                "unrealized_pnl_pct": None,
                "realized_pnl": round(realized_pnl, 2),
                "total_pnl": round(realized_pnl, 2),
                "total_pnl_pct": None,
                "entry_price": None,
                "current_price": current_price,
                "quantity": 0.0,
                "closed_trades": len(closed),
                "lifetime_invested": round(closed_invested, 2),
            }

        price = current_price
        if price is None:
            row = self.storage.get_latest_price(symbol)
            price = float(row["close"]) if row else float(open_trade["entry_price"])

        invested = float(open_trade["quantity"]) * float(open_trade["entry_price"])
        unrealized = self._realized_pnl(open_trade, price)
        current_value = invested + unrealized
        unrealized_pct = (unrealized / invested * 100.0) if invested else None
        total_pnl = realized_pnl + unrealized
        total_pnl_pct = (total_pnl / invested * 100.0) if invested else None

        return {
            "status": "open",
            "direction": open_trade["direction"],
            "invested": round(invested, 2),
            "current_value": round(current_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "unrealized_pnl_pct": round(unrealized_pct, 2) if unrealized_pct is not None else None,
            "realized_pnl": round(realized_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2) if total_pnl_pct is not None else None,
            "entry_price": float(open_trade["entry_price"]),
            "current_price": price,
            "quantity": float(open_trade["quantity"]),
            "stop_loss": float(open_trade["stop_loss"]),
            "take_profit": float(open_trade["take_profit"]),
            "trade_id": open_trade["id"],
            "closed_trades": len(closed),
            "lifetime_invested": round(closed_invested + invested, 2),
        }

    def holdings_for_symbols(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        result = {}
        for symbol in symbols:
            row = self.storage.get_latest_price(symbol)
            price = float(row["close"]) if row else None
            result[symbol] = self.investment_for_symbol(symbol, price)
        return result
