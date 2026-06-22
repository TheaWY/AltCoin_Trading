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

    def portfolio_value(self, current_price: float) -> float:
        state = self.ensure_portfolio(current_price)
        cash = float(state["cash"])
        return cash + sum(
            self._position_value(trade, current_price)
            for trade in self.storage.get_open_trades()
        )

    def buy_and_hold_value(self, current_price: float) -> float | None:
        state = self.ensure_portfolio(current_price)
        benchmark_price = state.get("benchmark_btc_price")
        if not benchmark_price:
            return None
        shares = config.PAPER_STARTING_CAPITAL / float(benchmark_price)
        return shares * current_price

    def process_signal(self, signal_result: dict[str, Any], current_price: float) -> dict[str, Any]:
        direction = signal_result.get("direction")
        if direction not in (SignalDirection.LONG.value, SignalDirection.SHORT.value):
            return {"opened": False, "reason": "non-actionable signal"}

        open_trades = self.storage.get_open_trades()
        if open_trades:
            return {"opened": False, "reason": "position already open"}

        return self._open_trade(signal_result, current_price)

    def check_open_trades(self, current_price: float) -> list[dict[str, Any]]:
        closed = []
        for trade in self.storage.get_open_trades():
            exit_reason = self._check_exit(trade, current_price)
            if exit_reason:
                closed.append(self._close_trade(trade, current_price, exit_reason))
        return closed

    def _open_trade(self, signal_result: dict[str, Any], current_price: float) -> dict[str, Any]:
        state = self.ensure_portfolio(current_price)
        portfolio = self.portfolio_value(current_price)
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
                "symbol": signal_result.get("symbol", config.SYMBOL),
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
            "Paper trade opened id=%s %s qty=%.6f @ %.2f",
            trade_id,
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

    def _realized_pnl(self, trade: dict[str, Any], exit_price: float) -> float:
        qty = float(trade["quantity"])
        entry = float(trade["entry_price"])
        if trade["direction"] == SignalDirection.LONG.value:
            return (exit_price - entry) * qty
        return (entry - exit_price) * qty

    def summary(self, current_price: float) -> dict[str, Any]:
        state = self.ensure_portfolio(current_price)
        paper_value = self.portfolio_value(current_price)
        buy_hold = self.buy_and_hold_value(current_price)
        return {
            "cash": float(state["cash"]),
            "paper_value": paper_value,
            "starting_capital": config.PAPER_STARTING_CAPITAL,
            "buy_and_hold_value": buy_hold,
            "open_trades": len(self.storage.get_open_trades()),
            "pnl_vs_start": paper_value - config.PAPER_STARTING_CAPITAL,
            "pnl_vs_buy_hold": (paper_value - buy_hold) if buy_hold is not None else None,
        }
