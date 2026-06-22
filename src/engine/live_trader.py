"""Live Binance futures trader backed by ccxt orders."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import ccxt

from src import config
from src.data.collectors.binance import _build_exchange
from src.data.storage import Storage
from src.engine.paper_trader import PaperTrader
from src.strategies.base import SignalDirection
from src.symbols import ccxt_symbol

logger = logging.getLogger(__name__)


class LiveTrader(PaperTrader):
    """PaperTrader-compatible live execution engine for Binance futures."""

    def __init__(
        self,
        storage: Storage | None = None,
        exchange: ccxt.Exchange | None = None,
    ) -> None:
        super().__init__(storage)
        self.exchange = exchange or _build_exchange()
        self.exchange.load_markets()
        if config.BINANCE_TESTNET:
            logger.warning("LiveTrader is running against Binance testnet")
        else:
            logger.warning("LiveTrader is running against real Binance endpoints")

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
            return {"opened": False, "reason": f"position already tracked for {symbol}"}

        if self._has_open_exchange_position(symbol):
            return {"opened": False, "reason": f"exchange position already open for {symbol}"}

        if self.storage.count_open_trades() >= config.MAX_OPEN_POSITIONS:
            return {"opened": False, "reason": "max open positions reached"}

        futures_symbol = ccxt_symbol(symbol)
        side = "buy" if direction == SignalDirection.LONG.value else "sell"
        amount = self._order_amount(futures_symbol, current_price)
        order = self.exchange.create_order(
            futures_symbol,
            "market",
            side,
            amount,
            None,
            {"reduceOnly": False},
        )

        fill_price = self._order_fill_price(order) or current_price
        trade = self._record_live_trade(signal_result, fill_price, amount)
        logger.info(
            "Live order opened %s %s amount=%s price=%s order_id=%s",
            symbol,
            direction,
            amount,
            fill_price,
            order.get("id"),
        )
        return {
            "opened": True,
            "trade_id": trade["trade_id"],
            "order_id": order.get("id"),
            "direction": direction,
            "quantity": amount,
            "entry_price": fill_price,
        }

    def check_open_trades_for_symbol(
        self, symbol: str, current_price: float
    ) -> list[dict[str, Any]]:
        closed = []
        position = self._exchange_position(symbol)
        if not position:
            return closed

        for trade in self.storage.get_open_trades(symbol):
            exit_reason = self._check_exit(trade, current_price)
            if not exit_reason:
                continue

            futures_symbol = ccxt_symbol(symbol)
            side = "sell" if trade["direction"] == SignalDirection.LONG.value else "buy"
            amount = self._position_amount(position) or float(trade["quantity"])
            amount = self._amount_to_precision(futures_symbol, amount)
            order = self.exchange.create_order(
                futures_symbol,
                "market",
                side,
                amount,
                None,
                {"reduceOnly": True},
            )
            fill_price = self._order_fill_price(order) or current_price
            closed_trade = self._close_trade(trade, fill_price, exit_reason)
            closed_trade["order_id"] = order.get("id")
            closed.append(closed_trade)
            logger.info(
                "Live order closed %s amount=%s price=%s reason=%s order_id=%s",
                symbol,
                amount,
                fill_price,
                exit_reason,
                order.get("id"),
            )
        return closed

    def _record_live_trade(
        self, signal_result: dict[str, Any], current_price: float, quantity: float
    ) -> dict[str, Any]:
        symbol = signal_result.get("symbol", config.SYMBOL)
        state = self.ensure_portfolio(current_price)
        direction = signal_result["direction"]
        notional = quantity * current_price

        if direction == SignalDirection.LONG.value:
            stop_loss = current_price * (1 - config.STOP_LOSS_PCT)
            take_profit = current_price * (1 + config.TAKE_PROFIT_PCT)
        else:
            stop_loss = current_price * (1 + config.STOP_LOSS_PCT)
            take_profit = current_price * (1 - config.TAKE_PROFIT_PCT)

        now_ts = int(datetime.now(timezone.utc).timestamp())
        signal_id = signal_result.get("signal_id")
        if signal_id is not None:
            try:
                signal_id = int(signal_id)
            except (TypeError, ValueError):
                signal_id = None
        if signal_id is not None and self.storage.get_signal(signal_id) is None:
            signal_id = None
        trade_id = self.storage.insert_paper_trade(
            {
                "signal_id": signal_id,
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
        self.storage.update_portfolio_cash(float(state["cash"]) - notional)
        return {
            "trade_id": trade_id,
            "direction": direction,
            "quantity": quantity,
            "entry_price": current_price,
        }

    def _order_amount(self, futures_symbol: str, current_price: float) -> float:
        balance = self.exchange.fetch_balance()
        usdt = balance.get("USDT") or {}
        total = usdt.get("total") or usdt.get("free")
        if total is None:
            total = balance.get("total", {}).get("USDT") or balance.get("free", {}).get("USDT")
        if total is None:
            raise RuntimeError("USDT balance unavailable for live position sizing")

        notional = float(total) * config.MAX_POSITION_PCT
        raw_amount = notional / current_price
        market = self.exchange.market(futures_symbol)
        min_amount = (market.get("limits", {}).get("amount", {}) or {}).get("min")
        if min_amount is not None:
            raw_amount = max(raw_amount, float(min_amount))
        return self._amount_to_precision(futures_symbol, raw_amount)

    def _amount_to_precision(self, futures_symbol: str, amount: float) -> float:
        precise = float(self.exchange.amount_to_precision(futures_symbol, amount))
        market = self.exchange.market(futures_symbol)
        min_amount = (market.get("limits", {}).get("amount", {}) or {}).get("min")
        if min_amount is not None and precise < float(min_amount):
            raise RuntimeError(
                f"Order amount {precise} below minimum {min_amount} for {futures_symbol}"
            )
        if precise <= 0:
            raise RuntimeError(f"Order amount rounded to zero for {futures_symbol}")
        return precise

    def _has_open_exchange_position(self, symbol: str) -> bool:
        return self._exchange_position(symbol) is not None

    def _exchange_position(self, symbol: str) -> dict[str, Any] | None:
        futures_symbol = ccxt_symbol(symbol)
        market_id = self.exchange.market(futures_symbol).get("id")
        try:
            positions = self.exchange.fetch_positions([futures_symbol])
        except TypeError:
            positions = self.exchange.fetch_positions()
        for position in positions or []:
            pos_symbol = position.get("symbol") or position.get("info", {}).get("symbol")
            if pos_symbol not in (futures_symbol, market_id):
                continue
            if self._position_amount(position) > 0:
                return position
        return None

    def _position_amount(self, position: dict[str, Any]) -> float:
        for key in ("contracts",):
            value = position.get(key)
            if value not in (None, ""):
                try:
                    return abs(float(value))
                except (TypeError, ValueError):
                    pass
        info = position.get("info", {})
        for key in ("positionAmt", "positionAmount"):
            value = info.get(key)
            if value not in (None, ""):
                try:
                    return abs(float(value))
                except (TypeError, ValueError):
                    pass
        return 0.0

    def _order_fill_price(self, order: dict[str, Any]) -> float | None:
        for key in ("average", "price"):
            value = order.get(key)
            if value not in (None, ""):
                return float(value)
        info = order.get("info", {})
        for key in ("avgPrice", "price"):
            value = info.get(key)
            if value not in (None, "", "0"):
                return float(value)
        return None
