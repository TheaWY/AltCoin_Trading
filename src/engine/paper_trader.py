"""Paper trading engine — simulates positions on Binance Testnet signals."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
import json
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine import hedge as hedge_engine
from src.engine import indicators
from src.strategies.base import SignalDirection

logger = logging.getLogger(__name__)

ROUND_TRIP_COST_PCT = config.round_trip_cost_pct()


class FakeDeltaNeutralError(RuntimeError):
    """funding_carry's execution_mode="delta_neutral" has no real hedge leg
    in either path (see research_decisions, subject='funding_carry_disabled').
    Computing P&L for it would silently report returns for a spot-long leg
    that doesn't exist. Raise loudly instead -- a crash is recoverable and
    obvious; a wrong number that looks plausible is neither."""


def _execution_mode_from_trade(storage: Storage, trade: dict[str, Any]) -> str | None:
    signal_id = trade.get("signal_id")
    if signal_id is not None:
        try:
            signal_row = storage.get_signal(int(signal_id))
        except (TypeError, ValueError):
            signal_row = None
        if signal_row and signal_row.get("metadata"):
            try:
                metadata = json.loads(signal_row["metadata"])
                mode = (metadata or {}).get("execution_mode")
                return str(mode) if mode is not None else None
            except Exception:
                return None
    # Fallback: allow direct tagging via trade dict (used by some callers/tests)
    mode = trade.get("execution_mode")
    return str(mode) if mode is not None else None


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

    def _price_for(self, symbol: str, prices_by_symbol: dict[str, float]) -> float:
        price = prices_by_symbol.get(symbol)
        if price is not None:
            return price
        row = self.storage.get_latest_price(symbol)
        return float(row["close"]) if row else 0.0

    def _hedge_leg_value_and_pnl(
        self, trade: dict[str, Any], prices_by_symbol: dict[str, float]
    ) -> tuple[float, float]:
        """Current mark and unrealized P&L of the hedge leg, if this trade has
        one. Unrealized marks never include funding or fees -- consistent
        with the primary leg, which also only realizes those at close."""
        hedge_symbol = trade.get("hedge_symbol")
        if not hedge_symbol:
            return 0.0, 0.0
        price = prices_by_symbol.get(hedge_symbol)
        if price is None:
            row = self.storage.get_latest_price(hedge_symbol)
            price = float(row["close"]) if row else float(trade["hedge_entry_price"])
        direction = trade["hedge_direction"]
        entry = float(trade["hedge_entry_price"])
        qty = float(trade["hedge_quantity"])
        value = hedge_engine.leg_position_value(direction, entry, price, qty)
        pnl = hedge_engine.leg_pnl(direction, entry, price, qty)
        return value, pnl

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
            price = self._price_for(trade["symbol"], prices_by_symbol)
            total += self._position_value(trade, price)
            hedge_value, _ = self._hedge_leg_value_and_pnl(trade, prices_by_symbol)
            total += hedge_value
        return total

    def _open_position_value(self, trade: dict[str, Any], price: float) -> float:
        return self._position_value(trade, price)

    def open_position_values(self, prices: dict[str, float] | None = None) -> dict[str, float]:
        prices = prices or {}
        reserved = 0.0
        value = 0.0
        unrealized = 0.0
        for trade in self.storage.get_open_trades():
            sym = trade["symbol"]
            price = prices.get(sym)
            if price is None:
                row = self.storage.get_latest_price(sym)
                price = float(row["close"]) if row else float(trade["entry_price"])
            notional = float(trade["quantity"]) * float(trade["entry_price"])
            position_value = self._open_position_value(trade, price)
            reserved += notional
            value += position_value
            unrealized += self._realized_pnl(trade, price)
            hedge_value, hedge_pnl = self._hedge_leg_value_and_pnl(trade, prices)
            if trade.get("hedge_symbol"):
                reserved += float(trade["hedge_quantity"]) * float(trade["hedge_entry_price"])
            value += hedge_value
            unrealized += hedge_pnl
        return {
            "reserved_margin": reserved,
            "open_position_value": value,
            "total_unrealized_pnl": unrealized,
        }

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

        if not config.direction_allowed(direction):
            return {"opened": False, "reason": f"{direction} entries disabled by policy"}

        if not config.holding_style_allowed(signal_result.get("style")):
            return {"opened": False, "reason": "holding style disabled or unbounded"}

        if self.storage.get_open_trade_for_symbol(symbol):
            return {"opened": False, "reason": f"position already open for {symbol}"}

        if self.storage.count_open_trades() >= config.MAX_OPEN_POSITIONS:
            return {"opened": False, "reason": "max open positions reached"}

        if self._symbol_in_cooldown(symbol):
            return {
                "opened": False,
                "reason": f"cooldown active for {symbol} ({config.COOLDOWN_HOURS_PER_SYMBOL}h)",
            }

        return self._open_trade(signal_result, current_price)

    def check_open_trades_for_symbol(
        self, symbol: str, current_price: float
    ) -> list[dict[str, Any]]:
        closed = []
        for trade in self.storage.get_open_trades(symbol):
            self._update_trailing_stop(trade, current_price)
            exit_reason = self._check_exit(trade, current_price)
            if exit_reason:
                closed.append(self._close_trade(trade, current_price, exit_reason))
        return closed

    def _update_trailing_stop(self, trade: dict[str, Any], price: float) -> None:
        """Ratchet the stop behind the best price seen (swing trades).

        Once price has moved 1 ATR in favor, the stop trails TRAIL_ATR_MULT
        ATRs behind the high-water mark so a winner can't round-trip to a loss.
        """
        if not config.TRAILING_STOP_ENABLED:
            return
        atr = trade.get("atr_pct")
        if not atr:
            return
        atr_frac = float(atr) / 100.0
        entry = float(trade["entry_price"])
        best = float(trade.get("trail_price") or entry)
        stop = float(trade["stop_loss"])
        is_long = trade["direction"] == SignalDirection.LONG.value

        updates: dict[str, Any] = {}
        if is_long:
            if price > best:
                best = price
                updates["trail_price"] = best
            if best >= entry * (1 + atr_frac):
                new_stop = best * (1 - config.TRAIL_ATR_MULT * atr_frac)
                if new_stop > stop:
                    updates["stop_loss"] = new_stop
                    trade["stop_loss"] = new_stop
        else:
            if price < best:
                best = price
                updates["trail_price"] = best
            if best <= entry * (1 - atr_frac):
                new_stop = best * (1 + config.TRAIL_ATR_MULT * atr_frac)
                if new_stop < stop:
                    updates["stop_loss"] = new_stop
                    trade["stop_loss"] = new_stop

        if updates:
            self.storage.update_paper_trade(int(trade["id"]), updates)

    def check_open_trades(self, current_price: float) -> list[dict[str, Any]]:
        """Legacy — check all open trades using one price (BTC only)."""
        return self.check_open_trades_for_symbol(config.SYMBOL, current_price)

    def _atr_pct(self, symbol: str) -> float | None:
        """1h ATR% from stored candles (basis for stops and sizing)."""
        rows = self.storage.get_prices(symbol, limit=48, timeframe="1h")
        if len(rows) < 15:
            return None
        return indicators.atr_pct(rows, period=14)

    def _exit_levels(
        self, direction: str, price: float, atr_pct: float | None
    ) -> tuple[float, float]:
        """ATR-scaled stop/target; falls back to fixed percents without ATR."""
        if atr_pct:
            stop_frac = config.ATR_STOP_MULT * atr_pct / 100.0
            tp_frac = config.ATR_TP_MULT * atr_pct / 100.0
        else:
            stop_frac = config.STOP_LOSS_PCT
            tp_frac = config.TAKE_PROFIT_PCT

        if direction == SignalDirection.LONG.value:
            return price * (1 - stop_frac), price * (1 + tp_frac)
        return price * (1 + stop_frac), price * (1 - tp_frac)

    def _position_notional(
        self, portfolio: float, cash: float, price: float, stop_loss: float
    ) -> float:
        """Volatility-inverse sizing: risk a fixed fraction of the portfolio.

        notional = risk_budget / stop_distance, so a coin with a 4% stop gets
        half the size of a coin with a 2% stop. Capped by MAX_POSITION_PCT and
        available cash.
        """
        stop_frac = abs(price - stop_loss) / price if price else 0.0
        if stop_frac <= 0:
            return 0.0
        notional = (portfolio * config.RISK_PER_TRADE_PCT) / stop_frac
        return max(0.0, min(notional, portfolio * config.MAX_POSITION_PCT, cash))

    def _symbol_in_cooldown(self, symbol: str) -> bool:
        hours = config.COOLDOWN_HOURS_PER_SYMBOL
        if hours <= 0:
            return False
        trades = self.storage.get_all_trades_for_symbol(symbol)
        if not trades:
            return False
        opened_times = [int(t["opened_at"]) for t in trades if t.get("opened_at") is not None]
        if not opened_times:
            return False
        last_opened = max(opened_times)
        age_hours = (datetime.now(timezone.utc).timestamp() - last_opened) / 3600.0
        return age_hours < hours

    def _hedge_leg_for_open(
        self,
        signal_result: dict[str, Any],
        direction: str,
        portfolio: float,
        cash: float,
        primary_price: float,
    ) -> dict[str, Any] | None:
        """Compute the hedge leg's sizing for a market-neutral entry, or None
        if this isn't a market-neutral signal / the hedge can't be sized.

        No naked leg by construction: this is called from _open_trade before
        any insert happens, and both legs land in the SAME insert_paper_trade
        call -- there is no code path that opens the primary without also
        recording the hedge, or vice versa.
        """
        metadata = signal_result.get("metadata") or {}
        if metadata.get("execution_mode") != "market_neutral":
            return None
        hedge_symbol = metadata.get("hedge_symbol") or config.SYMBOL
        beta = metadata.get("beta")
        if not beta or beta <= 0:
            return None
        hedge_row = self.storage.get_latest_price(hedge_symbol)
        if not hedge_row:
            return None
        hedge_price = float(hedge_row["close"])

        primary_cap, hedge_cap = hedge_engine.position_caps(portfolio, config.MAX_POSITION_PCT, beta)
        available = min(primary_cap + hedge_cap, cash)
        primary_notional = available / (1.0 + beta)
        hedge_notional = primary_notional * beta
        if primary_notional <= 0 or hedge_notional <= 0:
            return None

        return {
            "hedge_symbol": hedge_symbol,
            "hedge_direction": hedge_engine.hedge_direction_for(direction),
            "hedge_entry_price": hedge_price,
            "hedge_quantity": hedge_notional / hedge_price,
            "hedge_beta": float(beta),
            "primary_notional": primary_notional,
            "hedge_notional": hedge_notional,
        }

    def _open_trade(self, signal_result: dict[str, Any], current_price: float) -> dict[str, Any]:
        symbol = signal_result.get("symbol", config.SYMBOL)
        state = self.ensure_portfolio(current_price)
        portfolio = self.portfolio_value()
        cash = float(state["cash"])
        direction = signal_result["direction"]
        style = config.normalize_holding_style(signal_result.get("style"))

        atr = self._atr_pct(symbol)
        stop_loss, take_profit = self._exit_levels(direction, current_price, atr)

        hedge_leg = self._hedge_leg_for_open(signal_result, direction, portfolio, cash, current_price)
        if hedge_leg is not None:
            notional = hedge_leg["primary_notional"]
        else:
            notional = self._position_notional(portfolio, cash, current_price, stop_loss)
        if notional <= 0:
            return {"opened": False, "reason": "no cash / zero position size"}
        quantity = notional / current_price

        now_ts = int(datetime.now(timezone.utc).timestamp())
        trade_row = {
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
            "strategy": signal_result.get("strategy"),
            "style": style,
            "atr_pct": atr,
            "trail_price": current_price,
            "exit_reason": None,
            "fees": None,
        }
        cash_reserved = notional
        if hedge_leg is not None:
            trade_row.update(
                {
                    "hedge_symbol": hedge_leg["hedge_symbol"],
                    "hedge_direction": hedge_leg["hedge_direction"],
                    "hedge_entry_price": hedge_leg["hedge_entry_price"],
                    "hedge_quantity": hedge_leg["hedge_quantity"],
                    "hedge_beta": hedge_leg["hedge_beta"],
                }
            )
            cash_reserved += hedge_leg["hedge_notional"]

        trade_id = self.storage.insert_paper_trade(trade_row)

        new_cash = cash - cash_reserved
        self.storage.update_portfolio_cash(new_cash)

        logger.info(
            "Paper trade opened id=%s %s %s qty=%.6f @ %.2f%s",
            trade_id,
            symbol,
            direction,
            quantity,
            current_price,
            f" hedge={hedge_leg['hedge_symbol']} {hedge_leg['hedge_direction']} "
            f"qty={hedge_leg['hedge_quantity']:.6f} beta={hedge_leg['hedge_beta']:.3f}"
            if hedge_leg is not None
            else "",
        )
        return {
            "opened": True,
            "trade_id": trade_id,
            "direction": direction,
            "quantity": quantity,
            "entry_price": current_price,
        }

    def _close_hedge_leg(
        self, trade: dict[str, Any], opened_at: int, closed_at: int
    ) -> tuple[float, dict[str, Any]]:
        """Close the hedge leg in lockstep with the primary leg. Returns the
        hedge leg's reserved notional (to release back to cash -- its P&L is
        already folded into the combined pnl the caller adds separately) and
        the fields to persist. No independent exit condition -- this only
        runs because the primary leg is closing, for any reason.
        """
        hedge_symbol = trade.get("hedge_symbol")
        if not hedge_symbol:
            return 0.0, {}

        direction = trade["hedge_direction"]
        entry = float(trade["hedge_entry_price"])
        qty = float(trade["hedge_quantity"])
        notional = entry * qty

        row = self.storage.get_latest_price(hedge_symbol)
        if row:
            exit_price = float(row["close"])
        else:
            # Stale/missing price: never defer the hedge close -- fall back
            # to entry price (zero hedge P&L) rather than leave a naked leg.
            exit_price = entry
            logger.warning(
                "No price available to close hedge leg %s for trade id=%s; "
                "closing at entry price %.6f",
                hedge_symbol,
                trade.get("id"),
                entry,
            )

        hedge_pnl = hedge_engine.leg_pnl(direction, entry, exit_price, qty)
        hedge_fees = hedge_engine.leg_fees(notional, ROUND_TRIP_COST_PCT)

        funding_rows = self.storage.get_funding_rates(hedge_symbol, since=opened_at, before=closed_at)
        funding_pnl = hedge_engine.funding_pnl_for_leg(direction, funding_rows, notional, opened_at, closed_at)

        primary_rows = self.storage.get_prices(trade["symbol"], since=opened_at, before=closed_at, timeframe="1h")
        hedge_rows = self.storage.get_prices(hedge_symbol, since=opened_at, before=closed_at, timeframe="1h")
        realized = hedge_engine.realized_beta_and_correlation(
            primary_rows, hedge_rows, opened_at, closed_at, config.HEDGE_BETA_MIN_POINTS
        )

        fields = {
            "hedge_exit_price": exit_price,
            "hedge_pnl": hedge_pnl,
            "hedge_fees": hedge_fees,
            "funding_pnl": funding_pnl,
            "realized_beta": realized["beta"],
            "realized_correlation": realized["correlation"],
        }
        return notional, fields

    def _close_trade(
        self, trade: dict[str, Any], current_price: float, reason: str
    ) -> dict[str, Any]:
        notional = float(trade["quantity"]) * float(trade["entry_price"])
        execution_mode = _execution_mode_from_trade(self.storage, trade)
        if trade.get("strategy") == "funding_carry" and execution_mode == "delta_neutral":
            raise FakeDeltaNeutralError(f"trade id={trade.get('id')} symbol={trade.get('symbol')}")
        fees = notional * ROUND_TRIP_COST_PCT
        primary_pnl = self._realized_pnl(trade, current_price)

        now_ts = int(datetime.now(timezone.utc).timestamp())
        opened_at = int(trade["opened_at"])
        hedge_notional, hedge_fields = self._close_hedge_leg(trade, opened_at, now_ts)
        pnl = hedge_engine.combined_trade_pnl(
            primary_pnl,
            fees,
            hedge_fields.get("hedge_pnl", 0.0),
            hedge_fields.get("hedge_fees", 0.0),
            hedge_fields.get("funding_pnl", 0.0),
        )

        state = self.storage.get_portfolio_state()
        cash = float(state["cash"]) if state else 0.0
        # Release both legs' reserved notional; pnl already nets out both
        # legs' P&L, fees, and funding, so it's added exactly once.
        self.storage.update_portfolio_cash(cash + notional + hedge_notional + pnl)

        update_fields = {
            "exit_price": current_price,
            "status": "closed",
            "pnl": pnl,
            "closed_at": now_ts,
            "exit_reason": reason,
            "fees": fees,
            **hedge_fields,
        }
        self.storage.update_paper_trade(int(trade["id"]), update_fields)
        logger.info(
            "Paper trade closed id=%s pnl=%.2f reason=%s%s",
            trade["id"],
            pnl,
            reason,
            f" hedge_pnl={hedge_fields['hedge_pnl']:.4f} funding_pnl={hedge_fields['funding_pnl']:.4f} "
            f"realized_beta={hedge_fields['realized_beta']}"
            if hedge_fields
            else "",
        )
        return {"trade_id": trade["id"], "pnl": pnl, "reason": reason}

    def _check_exit(self, trade: dict[str, Any], price: float) -> str | None:
        execution_mode = _execution_mode_from_trade(self.storage, trade)
        if trade.get("strategy") == "funding_carry" and execution_mode == "delta_neutral":
            raise FakeDeltaNeutralError(f"trade id={trade.get('id')} symbol={trade.get('symbol')}")

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

        max_hours = config.max_hold_hours_for_style(trade.get("style"))
        opened_at = trade.get("opened_at")
        if opened_at and max_hours > 0:
            age_hours = (
                datetime.now(timezone.utc).timestamp() - float(opened_at)
            ) / 3600.0
            if age_hours >= max_hours:
                return "time_stop"
        return None

    def _position_value(self, trade: dict[str, Any], price: float) -> float:
        qty = float(trade["quantity"])
        entry = float(trade["entry_price"])
        notional = qty * entry
        execution_mode = _execution_mode_from_trade(self.storage, trade)
        if trade.get("strategy") == "funding_carry" and execution_mode == "delta_neutral":
            raise FakeDeltaNeutralError(f"trade id={trade.get('id')} symbol={trade.get('symbol')}")
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

        position_values = self.open_position_values(prices)
        cash = float(state["cash"])
        paper_value = cash + position_values["open_position_value"]
        btc_price = prices.get(config.SYMBOL) or current_price
        buy_hold = self.buy_and_hold_value(btc_price) if btc_price else None
        return {
            "cash": cash,
            "available_cash": cash,
            "reserved_margin": position_values["reserved_margin"],
            "open_position_value": position_values["open_position_value"],
            "equity": paper_value,
            "paper_value": paper_value,
            "starting_capital": config.PAPER_STARTING_CAPITAL,
            "buy_and_hold_value": buy_hold,
            "open_trades": self.storage.count_open_trades(),
            "max_open_positions": config.MAX_OPEN_POSITIONS,
            "pnl_vs_start": paper_value - config.PAPER_STARTING_CAPITAL,
            "pnl_vs_buy_hold": (paper_value - buy_hold) if buy_hold is not None else None,
            "total_invested_open": position_values["reserved_margin"],
            "total_open_value": position_values["open_position_value"],
            "total_unrealized_pnl": position_values["total_unrealized_pnl"],
            "total_realized_pnl": self._total_realized_pnl(),
        }

    def _realized_pnl(self, trade: dict[str, Any], exit_price: float) -> float:
        qty = float(trade["quantity"])
        entry = float(trade["entry_price"])
        execution_mode = _execution_mode_from_trade(self.storage, trade)
        if trade.get("strategy") == "funding_carry" and execution_mode == "delta_neutral":
            raise FakeDeltaNeutralError(f"trade id={trade.get('id')} symbol={trade.get('symbol')}")
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

        hedge_info = None
        if open_trade.get("hedge_symbol"):
            _, hedge_unrealized = self._hedge_leg_value_and_pnl(open_trade, {})
            hedge_info = {
                "hedge_symbol": open_trade["hedge_symbol"],
                "hedge_direction": open_trade["hedge_direction"],
                "hedge_beta": float(open_trade["hedge_beta"]) if open_trade.get("hedge_beta") else None,
                "hedge_unrealized_pnl": round(hedge_unrealized, 2),
                "combined_unrealized_pnl": round(unrealized + hedge_unrealized, 2),
            }

        # Original ATR stop at entry — lets the UI show trailing adjustments
        # ("손절 $X → $Y 조정됨") without a separate audit table.
        entry = float(open_trade["entry_price"])
        atr = open_trade.get("atr_pct")
        initial_stop = None
        if atr:
            stop_frac = config.ATR_STOP_MULT * float(atr) / 100.0
            initial_stop = (
                entry * (1 - stop_frac)
                if open_trade["direction"] == SignalDirection.LONG.value
                else entry * (1 + stop_frac)
            )
        current_stop = float(open_trade["stop_loss"]) if open_trade.get("stop_loss") else None
        stop_moved = (
            initial_stop is not None
            and current_stop is not None
            and abs(current_stop - initial_stop) / entry > 0.0005
        )

        return {
            "strategy": open_trade.get("strategy"),
            "style": open_trade.get("style"),
            "atr_pct": float(atr) if atr else None,
            "initial_stop": initial_stop,
            "stop_moved": stop_moved,
            "trail_price": float(open_trade["trail_price"]) if open_trade.get("trail_price") else None,
            "opened_at": open_trade.get("opened_at"),
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
            "hedge": hedge_info,
        }

    def holdings_for_symbols(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        result = {}
        for symbol in symbols:
            row = self.storage.get_latest_price(symbol)
            price = float(row["close"]) if row else None
            result[symbol] = self.investment_for_symbol(symbol, price)
        return result
