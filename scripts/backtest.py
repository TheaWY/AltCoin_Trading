#!/usr/bin/env python3
"""Historical backtest runner for configured trading strategies."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import config  # noqa: E402
from src.data.storage import Storage, get_storage  # noqa: E402
from src.engine.analyzer import AltAnalyzer  # noqa: E402
from src.engine import indicators  # noqa: E402
from src.engine.signal import gather_strategy_data  # noqa: E402
from src.market import bars  # noqa: E402
from src.strategies.base import SignalDirection  # noqa: E402
from src.strategies.funding_carry import settlement_rates  # noqa: E402
from src.strategies.registry import get_strategy, list_strategies  # noqa: E402


SECONDS_PER_DAY = 24 * 60 * 60


def _execution_cost_components(notional: float) -> tuple[float, float]:
    spread = abs(notional) * config.SPREAD_PCT_PER_SIDE
    slippage = abs(notional) * config.SLIPPAGE_PCT_PER_SIDE
    return spread, slippage


def _apply_entry_cost(direction: str, raw_open: float) -> float:
    cost = config.SPREAD_PCT_PER_SIDE + config.SLIPPAGE_PCT_PER_SIDE
    if direction == SignalDirection.LONG.value:
        return raw_open * (1 + cost)
    return raw_open * (1 - cost)


def _apply_exit_cost(direction: str, raw_price: float) -> float:
    cost = config.SPREAD_PCT_PER_SIDE + config.SLIPPAGE_PCT_PER_SIDE
    if direction == SignalDirection.LONG.value:
        return raw_price * (1 - cost)
    return raw_price * (1 + cost)


@dataclass
class BacktestTrade:
    symbol: str
    direction: str
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    opened_at: int
    strategy: str | None = None
    metadata: dict[str, Any] | None = None
    atr_pct: float | None = None
    exit_price: float | None = None
    closed_at: int | None = None
    pnl: float | None = None
    exit_reason: str | None = None
    fees: float | None = None
    signal_time: int | None = None
    signal_bar_close: int | None = None
    intended_execution_time: int | None = None
    actual_fill_time: int | None = None
    spread_cost: float = 0.0
    slippage: float = 0.0
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    ambiguous_exit: bool = False

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price


@dataclass
class PendingOrder:
    symbol: str
    direction: str
    signal_time: int
    signal_bar_close: int
    execution_bar_open: int
    strategy: str | None
    metadata: dict[str, Any] | None = None


@dataclass
class BacktestPortfolio:
    cash: float = config.PAPER_STARTING_CAPITAL
    storage: Storage | None = None
    open_trades: list[BacktestTrade] = field(default_factory=list)
    closed_trades: list[BacktestTrade] = field(default_factory=list)

    def open_trade(
        self,
        symbol: str,
        direction: str,
        price: float,
        timestamp: int,
        *,
        strategy: str | None = None,
        metadata: dict[str, Any] | None = None,
        signal_time: int | None = None,
        signal_bar_close: int | None = None,
        intended_execution_time: int | None = None,
        atr_pct: float | None = None,
        spread_cost: float = 0.0,
        slippage: float = 0.0,
    ) -> BacktestTrade | None:
        if direction not in (SignalDirection.LONG.value, SignalDirection.SHORT.value):
            return None
        if not config.direction_allowed(direction):
            return None
        if any(trade.symbol == symbol for trade in self.open_trades):
            return None
        if len(self.open_trades) >= config.MAX_OPEN_POSITIONS:
            return None

        if self._symbol_in_cooldown(symbol, timestamp):
            return None

        stop_loss, take_profit = self._exit_levels(direction, price, atr_pct)
        portfolio_value = self.value({}, timestamp)
        notional = self._position_notional(
            portfolio_value,
            self.cash,
            price,
            stop_loss,
        )
        if notional <= 0 or self.cash < notional:
            return None

        quantity = notional / price
        entry_fee = notional * config.fee_pct_per_side()

        trade = BacktestTrade(
            symbol=symbol,
            direction=direction,
            entry_price=price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=timestamp,
            strategy=strategy,
            metadata=metadata,
            signal_time=signal_time,
            signal_bar_close=signal_bar_close,
            intended_execution_time=intended_execution_time,
            actual_fill_time=timestamp,
            spread_cost=spread_cost,
            slippage=slippage,
            entry_fee=entry_fee,
            atr_pct=atr_pct,
        )
        self.cash -= notional + entry_fee
        self.open_trades.append(trade)
        return trade

    def check_exits(self, prices: dict[str, float], timestamp: int) -> None:
        still_open = []
        for trade in self.open_trades:
            price = prices.get(trade.symbol)
            reason = self._exit_reason(trade, price, timestamp)
            if reason:
                close_price = price if price is not None else trade.entry_price
                self.close_trade(trade, close_price, timestamp, reason)
            else:
                still_open.append(trade)
        self.open_trades = still_open

    def process_candle_exits(
        self,
        candles: dict[str, dict[str, Any]],
        timestamp: int,
    ) -> int:
        """Evaluate stops/targets during a completed candle.

        If stop and target are both inside the bar, use adverse-first ordering.
        If the bar opens beyond a stop, fill at that adverse open.
        """
        ambiguous = 0
        still_open: list[BacktestTrade] = []
        for trade in self.open_trades:
            candle = candles.get(trade.symbol)
            if not candle:
                still_open.append(trade)
                continue
            close_price = float(candle["close"])
            exit_price, reason, is_ambiguous = self._candle_exit_price(trade, candle)
            if exit_price is None:
                self._update_trailing_stop_from_candle(trade, candle)
                if self._time_stop_reached(trade, timestamp):
                    exit_price, reason, is_ambiguous = close_price, "time_stop", False
            if exit_price is not None and reason is not None:
                spread, slip = _execution_cost_components(trade.notional)
                self.close_trade(
                    trade,
                    _apply_exit_cost(trade.direction, exit_price),
                    timestamp,
                    reason,
                    spread_cost=spread,
                    slippage=slip,
                    ambiguous_exit=is_ambiguous,
                )
                ambiguous += 1 if is_ambiguous else 0
            else:
                still_open.append(trade)
        self.open_trades = still_open
        return ambiguous

    def _candle_exit_price(
        self, trade: BacktestTrade, candle: dict[str, Any]
    ) -> tuple[float | None, str | None, bool]:
        open_ = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        stop = float(trade.stop_loss)
        target = float(trade.take_profit)
        is_long = trade.direction == SignalDirection.LONG.value

        if is_long:
            if open_ <= stop:
                return open_, "stop_loss_gap", False
            if open_ >= target:
                return open_, "take_profit_gap", False
            hit_stop = low <= stop
            hit_target = high >= target
            if hit_stop and hit_target:
                return stop, "stop_loss", True
            if hit_stop:
                return stop, "stop_loss", False
            if hit_target:
                return target, "take_profit", False
        else:
            if open_ >= stop:
                return open_, "stop_loss_gap", False
            if open_ <= target:
                return open_, "take_profit_gap", False
            hit_stop = high >= stop
            hit_target = low <= target
            if hit_stop and hit_target:
                return stop, "stop_loss", True
            if hit_stop:
                return stop, "stop_loss", False
            if hit_target:
                return target, "take_profit", False
        return None, None, False

    def _update_trailing_stop_from_candle(
        self, trade: BacktestTrade, candle: dict[str, Any]
    ) -> None:
        if not config.TRAILING_STOP_ENABLED or not trade.atr_pct:
            return
        atr_frac = float(trade.atr_pct) / 100.0
        entry = float(trade.entry_price)
        best = float(trade.metadata.get("trail_price", entry) if trade.metadata else entry)
        stop = float(trade.stop_loss)
        if trade.direction == SignalDirection.LONG.value:
            best = max(best, float(candle["high"]))
            if best >= entry * (1 + atr_frac):
                trade.stop_loss = max(stop, best * (1 - config.TRAIL_ATR_MULT * atr_frac))
        else:
            best = min(best, float(candle["low"]))
            if best <= entry * (1 - atr_frac):
                trade.stop_loss = min(stop, best * (1 + config.TRAIL_ATR_MULT * atr_frac))
        trade.metadata = {**(trade.metadata or {}), "trail_price": best}

    def _time_stop_reached(self, trade: BacktestTrade, timestamp: int) -> bool:
        max_hours = (
            config.SCALP_MAX_HOLD_HOURS
            if (trade.metadata or {}).get("style") == "scalp"
            else config.SWING_MAX_HOLD_HOURS
        )
        return max_hours > 0 and (timestamp - int(trade.opened_at)) / 3600.0 >= max_hours

    def close_all(self, prices: dict[str, float], timestamp: int) -> None:
        for trade in list(self.open_trades):
            price = prices.get(trade.symbol, trade.entry_price)
            spread, slip = _execution_cost_components(trade.notional)
            self.close_trade(
                trade,
                _apply_exit_cost(trade.direction, price),
                timestamp,
                "end_of_backtest",
                spread_cost=spread,
                slippage=slip,
            )
        self.open_trades = []

    def close_trade(
        self,
        trade: BacktestTrade,
        price: float,
        timestamp: int,
        reason: str,
        *,
        spread_cost: float = 0.0,
        slippage: float = 0.0,
        ambiguous_exit: bool = False,
    ) -> None:
        gross_pnl = self._realized_pnl(trade, price, timestamp)
        if (
            trade.strategy == "funding_carry"
            and (trade.metadata or {}).get("execution_mode") == "delta_neutral"
        ):
            exit_fee = max(0.0, trade.notional * config.CARRY_FEE_ROUNDTRIP - trade.entry_fee)
        else:
            exit_fee = trade.notional * config.fee_pct_per_side()
        pnl = gross_pnl - trade.entry_fee - exit_fee
        trade.exit_fee = exit_fee
        trade.fees = trade.entry_fee + exit_fee
        trade.spread_cost += spread_cost
        trade.slippage += slippage
        trade.ambiguous_exit = ambiguous_exit
        trade.exit_price = price
        trade.closed_at = timestamp
        trade.pnl = pnl
        trade.exit_reason = reason
        self.cash += trade.notional + gross_pnl - exit_fee
        self.closed_trades.append(trade)

    def _exit_levels(
        self, direction: str, price: float, atr_pct: float | None
    ) -> tuple[float, float]:
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
        stop_frac = abs(price - stop_loss) / price if price else 0.0
        if stop_frac <= 0:
            return 0.0
        notional = (portfolio * config.RISK_PER_TRADE_PCT) / stop_frac
        return max(0.0, min(notional, portfolio * config.MAX_POSITION_PCT, cash))

    def value(self, prices: dict[str, float], timestamp: int | None) -> float:
        total = self.cash
        for trade in self.open_trades:
            price = prices.get(trade.symbol, trade.entry_price)
            total += self._position_value(trade, price, timestamp=timestamp)
        return total

    def pnl_by_symbol(self, prices: dict[str, float], timestamp: int | None) -> dict[str, float]:
        symbols = {trade.symbol for trade in self.closed_trades + self.open_trades}
        result: dict[str, float] = {}
        for symbol in symbols:
            realized = sum(float(t.pnl or 0.0) for t in self.closed_trades if t.symbol == symbol)
            unrealized = sum(
                self._realized_pnl(
                    t, prices.get(symbol, t.entry_price), timestamp=timestamp
                )
                for t in self.open_trades
                if t.symbol == symbol
            )
            result[symbol] = realized + unrealized
        return result

    def _symbol_in_cooldown(self, symbol: str, timestamp: int) -> bool:
        hours = config.COOLDOWN_HOURS_PER_SYMBOL
        if hours <= 0:
            return False
        cutoff = timestamp - int(hours * 3600)
        for trade in self.closed_trades + self.open_trades:
            if trade.symbol != symbol:
                continue
            if trade.opened_at >= cutoff:
                return True
        return False

    def _exit_reason(
        self, trade: BacktestTrade, price: float | None, timestamp: int
    ) -> str | None:
        if (
            trade.strategy == "funding_carry"
            and (trade.metadata or {}).get("execution_mode") == "delta_neutral"
        ):
            if self.storage is None:
                return None
            rates = settlement_rates(
                self.storage.get_funding_rates(
                    trade.symbol, limit=800, since=trade.opened_at, before=timestamp
                )
                or []
            )
            if not rates:
                return None
            if rates[-1] < 0:
                return "carry_negative_funding"
            need = int(config.CARRY_EXIT_CONSECUTIVE)
            if len(rates) >= need and all(rate < config.CARRY_EXIT_RATE for rate in rates[-need:]):
                return "carry_funding_cooled"
            return None

        if price is None:
            return None
        if trade.direction == SignalDirection.LONG.value:
            if price <= trade.stop_loss:
                return "stop_loss"
            if price >= trade.take_profit:
                return "take_profit"
        else:
            if price >= trade.stop_loss:
                return "stop_loss"
            if price <= trade.take_profit:
                return "take_profit"
        return None

    def _realized_pnl(
        self, trade: BacktestTrade, price: float, timestamp: int | None
    ) -> float:
        if (
            trade.strategy == "funding_carry"
            and (trade.metadata or {}).get("execution_mode") == "delta_neutral"
            and self.storage is not None
        ):
            rows = self.storage.get_funding_rates(
                trade.symbol, limit=800, since=trade.opened_at, before=timestamp
            ) or []
            # funding over complete settlements after entry
            opened_bucket = int(trade.opened_at) // (8 * 3600)
            buckets: dict[int, float] = {}
            for row in rows:
                try:
                    bucket = int(row["timestamp"]) // (8 * 3600)
                    buckets[bucket] = float(row["funding_rate"])
                except Exception:
                    continue
            settlement_list = [(bucket, buckets[bucket]) for bucket in sorted(buckets)]
            complete_after_entry = [rate for bucket, rate in settlement_list if bucket > opened_bucket]
            funding = sum(complete_after_entry) * trade.notional
            basis_drift = 0.0
            return funding - basis_drift
        if trade.direction == SignalDirection.LONG.value:
            return (price - trade.entry_price) * trade.quantity
        return (trade.entry_price - price) * trade.quantity

    def _position_value(
        self, trade: BacktestTrade, price: float, timestamp: int | None
    ) -> float:
        if (
            trade.strategy == "funding_carry"
            and (trade.metadata or {}).get("execution_mode") == "delta_neutral"
            and self.storage is not None
        ):
            before = timestamp if timestamp is not None else None
            rows = self.storage.get_funding_rates(
                trade.symbol, limit=800, since=trade.opened_at, before=before
            ) or []
            opened_bucket = int(trade.opened_at) // (8 * 3600)
            buckets: dict[int, float] = {}
            for row in rows:
                try:
                    bucket = int(row["timestamp"]) // (8 * 3600)
                    buckets[bucket] = float(row["funding_rate"])
                except Exception:
                    continue
            settlement_list = [(bucket, buckets[bucket]) for bucket in sorted(buckets)]
            complete_after_entry = [rate for bucket, rate in settlement_list if bucket > opened_bucket]
            funding = sum(complete_after_entry) * trade.notional
            basis_drift = 0.0
            return trade.notional + funding - basis_drift
        if trade.direction == SignalDirection.LONG.value:
            return trade.quantity * price
        return trade.notional + (trade.entry_price - price) * trade.quantity


class SnapshotStorage:
    """Read-only storage adapter that hides future rows from AltAnalyzer."""

    def __init__(
        self,
        storage: Storage,
        decision_time: int,
        latest_signal: dict[str, Any] | None,
    ) -> None:
        self.storage = storage
        self.decision_time = decision_time
        self.latest_signal = latest_signal

    def _visible_before(self, timeframe: str) -> int:
        return bars.latest_visible_open_timestamp(timeframe, self.decision_time)

    def get_latest_price(self, symbol: str, timeframe: str = "1h") -> dict[str, Any] | None:
        prices = self.storage.get_prices(
            symbol, limit=1, before=self._visible_before(timeframe), timeframe=timeframe
        )
        return prices[-1] if prices else None

    def get_latest_funding_rate(self, symbol: str) -> dict[str, Any] | None:
        rows = self.storage.get_funding_rates(symbol, limit=1, before=self.decision_time)
        return rows[-1] if rows else None

    def get_latest_signal(
        self, symbol: str | None = None, strategy: str | None = None
    ) -> dict[str, Any] | None:
        if self.latest_signal is None:
            return None
        if symbol and self.latest_signal.get("symbol") != symbol:
            return None
        if strategy and self.latest_signal.get("strategy") != strategy:
            return None
        return self.latest_signal

    def get_prices(
        self,
        symbol: str,
        limit: int = 100,
        since: int | None = None,
        before: int | None = None,
        timeframe: str = "1h",
    ) -> list[dict[str, Any]]:
        visible_before = self._visible_before(timeframe)
        effective_before = min(value for value in (before, visible_before) if value is not None)
        return self.storage.get_prices(
            symbol,
            limit=limit,
            since=since,
            before=effective_before,
            timeframe=timeframe,
        )

    def get_volume_stats(
        self, symbol: str, timeframe: str = "1h", lookback: int = 24
    ) -> dict[str, float | int | None]:
        rows = self.get_prices(symbol, limit=lookback, timeframe=timeframe)
        volumes = [float(row["volume"]) for row in rows]
        if not volumes:
            return {"avg": None, "stddev": None, "count": 0}
        avg = sum(volumes) / len(volumes)
        variance = sum((volume - avg) ** 2 for volume in volumes) / len(volumes)
        return {"avg": avg, "stddev": math.sqrt(variance), "count": len(volumes)}

    def get_open_trade_for_symbol(self, symbol: str) -> None:
        return None

    def get_funding_rates(
        self,
        symbol: str,
        limit: int = 100,
        since: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        effective_before = min(
            value for value in (before, self.decision_time) if value is not None
        )
        return self.storage.get_funding_rates(
            symbol, limit=limit, since=since, before=effective_before
        )

    def get_ls_ratio_history(
        self,
        symbol: str,
        limit: int = 2160,
        since: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        effective_before = min(
            value for value in (before, self.decision_time) if value is not None
        )
        return self.storage.get_ls_ratio_history(
            symbol, limit=limit, since=since, before=effective_before
        )

    def get_open_interest_history(
        self,
        symbol: str,
        limit: int = 720,
        since: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        effective_before = min(
            value for value in (before, self.decision_time) if value is not None
        )
        return self.storage.get_open_interest_history(
            symbol, limit=limit, since=since, before=effective_before
        )


class BacktestEngine:
    def __init__(
        self,
        start: datetime,
        end: datetime,
        symbols: list[str],
        strategy_name: str = "funding_rate",
        storage: Storage | None = None,
    ) -> None:
        self.storage = storage or get_storage()
        self.start = start
        self.end = end
        self.start_ts = int(start.timestamp())
        self.end_ts = int(end.timestamp())
        self.symbols = symbols
        self.strategy_name = strategy_name
        self.strategy = get_strategy(strategy_name)

    def run(self) -> dict[str, Any]:
        prices_by_symbol = self._load_prices()
        timeline = sorted(
            {
                row["timestamp"]
                for rows in prices_by_symbol.values()
                for row in rows
            }
        )

        portfolio = BacktestPortfolio(storage=self.storage)
        signal_count = 0
        confidence_pass_count = 0
        opened_count = 0
        orders_queued = 0
        orders_filled = 0
        ambiguous_bars = 0
        equity_curve: list[dict[str, float | int]] = []
        symbol_curves: dict[str, list[float]] = {symbol: [] for symbol in self.symbols}
        pending_orders: list[PendingOrder] = []
        last_decision_bar_by_symbol: dict[str, int] = {}

        last_prices: dict[str, float] = {}
        price_index = {symbol: 0 for symbol in self.symbols}

        for ts in timeline:
            if ts < self.start_ts or ts > self.end_ts:
                self._advance_prices(prices_by_symbol, price_index, ts)
                continue

            current_prices = self._advance_prices(prices_by_symbol, price_index, ts)
            last_prices.update(current_prices)
            current_candles = {
                symbol: self._bar_at(prices_by_symbol, symbol, ts)
                for symbol in self.symbols
            }
            current_candles = {k: v for k, v in current_candles.items() if v}

            still_pending: list[PendingOrder] = []
            for order in pending_orders:
                if order.execution_bar_open > ts:
                    still_pending.append(order)
                    continue
                candle = current_candles.get(order.symbol)
                if not candle:
                    still_pending.append(order)
                    continue
                raw_open = float(candle["open"])
                fill_price = _apply_entry_cost(order.direction, raw_open)
                notional_hint = portfolio.value(last_prices, ts) * config.MAX_POSITION_PCT
                spread, slip = _execution_cost_components(notional_hint)
                snapshot = SnapshotStorage(self.storage, order.signal_bar_close, None)
                atr_pct = self._atr_pct(snapshot, order.symbol)
                opened = portfolio.open_trade(
                    order.symbol,
                    order.direction,
                    fill_price,
                    ts,
                    strategy=order.strategy,
                    metadata=order.metadata,
                    signal_time=order.signal_time,
                    signal_bar_close=order.signal_bar_close,
                    intended_execution_time=order.execution_bar_open,
                    atr_pct=atr_pct,
                    spread_cost=spread,
                    slippage=slip,
                )
                if opened:
                    orders_filled += 1
                    opened_count += 1
            pending_orders = still_pending

            ambiguous_bars += portfolio.process_candle_exits(
                current_candles,
                ts + bars.timeframe_to_seconds("1h"),
            )

            for symbol in self.symbols:
                if symbol not in current_candles:
                    continue
                if last_decision_bar_by_symbol.get(symbol) == ts:
                    continue
                signal_bar_close = bars.bar_close_timestamp(current_candles[symbol], "1h")
                # Same data path as live trading: the snapshot hides rows
                # whose close is after `decision_time`, and gather_strategy_data
                # fetches exactly what the strategy declares in get_required_data().
                snapshot = SnapshotStorage(self.storage, signal_bar_close, None)
                data = gather_strategy_data(snapshot, self.strategy, symbol)
                if self.strategy.validate_data(data):
                    continue

                signal = self.strategy.generate_signal(data)
                if signal.direction == SignalDirection.NONE:
                    continue

                price_row = data.get("latest_price")
                if not price_row:
                    continue

                signal_count += 1
                funding_row = data.get("funding_rate") or {}
                signal_row = {
                    "strategy": self.strategy_name,
                    "symbol": symbol,
                    "timestamp": signal_bar_close,
                    "direction": signal.direction.value,
                    "reason": signal.reason,
                    "entry_price": signal.entry_price,
                    "funding_rate": funding_row.get("funding_rate"),
                    "metadata": {
                        **(signal.metadata or {}),
                        "signal_bar_close": signal_bar_close,
                        "execution_bar_open": signal_bar_close,
                    },
                }
                snapshot.latest_signal = signal_row
                analysis = AltAnalyzer(snapshot).analyze(
                    symbol, strategy_name=self.strategy_name
                )
                confidence_passed = analysis["confidence"] >= config.MIN_CONFIDENCE
                if confidence_passed:
                    confidence_pass_count += 1
                    execution_bar_open = signal_bar_close
                    pending_orders.append(
                        PendingOrder(
                            symbol=symbol,
                            direction=signal.direction.value,
                            signal_time=signal_bar_close,
                            signal_bar_close=signal_bar_close,
                            execution_bar_open=execution_bar_open,
                            strategy=self.strategy_name,
                            metadata={
                                **(signal.metadata or {}),
                                "style": analysis.get("recommended_style"),
                            },
                        )
                    )
                    orders_queued += 1
                last_decision_bar_by_symbol[symbol] = ts

            equity_curve.append({"timestamp": ts, "value": portfolio.value(last_prices, ts)})
            symbol_pnl = portfolio.pnl_by_symbol(last_prices, ts)
            for symbol in self.symbols:
                symbol_curves[symbol].append(
                    config.PAPER_STARTING_CAPITAL + symbol_pnl.get(symbol, 0.0)
                )

        if timeline:
            portfolio.close_all(last_prices, min(timeline[-1], self.end_ts))
            equity_curve.append(
                {
                    "timestamp": min(timeline[-1], self.end_ts),
                    "value": portfolio.value(last_prices, min(timeline[-1], self.end_ts)),
                }
            )

        final_value = portfolio.value(last_prices, timeline[-1] if timeline else None)
        return {
            "params": {
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "symbols": self.symbols,
                "strategy": self.strategy_name,
            },
            "summary": {
                "starting_capital": config.PAPER_STARTING_CAPITAL,
                "final_value": round(final_value, 2),
                "portfolio_return_pct": round(
                    _pct_return(config.PAPER_STARTING_CAPITAL, final_value), 2
                ),
                "max_drawdown_pct": round(_max_drawdown([p["value"] for p in equity_curve]), 2),
                "btc_buy_hold_return_pct": self._btc_buy_hold_return(prices_by_symbol),
                "signals": signal_count,
                "confidence_passed": confidence_pass_count,
                "orders_queued": orders_queued,
                "orders_filled": orders_filled,
                "ambiguous_bars": ambiguous_bars,
                "trades_opened": opened_count,
                "closed_trades": len(portfolio.closed_trades),
                "gross_pnl": round(sum((t.pnl or 0.0) + (t.fees or 0.0) for t in portfolio.closed_trades), 2),
                "fees": round(sum(float(t.fees or 0.0) for t in portfolio.closed_trades), 2),
                "spread_cost": round(sum(float(t.spread_cost or 0.0) for t in portfolio.closed_trades), 2),
                "slippage": round(sum(float(t.slippage or 0.0) for t in portfolio.closed_trades), 2),
                "funding": 0.0,
                "net_pnl": round(sum(float(t.pnl or 0.0) for t in portfolio.closed_trades), 2),
                "cash_benchmark_return_pct": 0.0,
            },
            "symbols": self._symbol_results(portfolio.closed_trades, symbol_curves),
            # Per-trade detail for the research stack (expectancy / PF / walk-forward
            # gates in src/research). Additive: nothing existing reads this key.
            "closed_trade_pnls": [
                {
                    "symbol": t.symbol,
                    "strategy": getattr(t, "strategy", None),
                    "pnl": round(float(t.pnl or 0.0), 6),
                    "fees": round(float(getattr(t, "fees", 0.0) or 0.0), 6),
                    "spread_cost": round(float(getattr(t, "spread_cost", 0.0) or 0.0), 6),
                    "slippage": round(float(getattr(t, "slippage", 0.0) or 0.0), 6),
                    "opened_at": getattr(t, "opened_at", None),
                    "closed_at": getattr(t, "closed_at", None),
                    "signal_time": getattr(t, "signal_time", None),
                    "signal_bar_close": getattr(t, "signal_bar_close", None),
                    "intended_execution_time": getattr(t, "intended_execution_time", None),
                    "actual_fill_time": getattr(t, "actual_fill_time", None),
                    "actual_fill": getattr(t, "entry_price", None),
                    "exit_reason": getattr(t, "exit_reason", None),
                }
                for t in portfolio.closed_trades
            ],
        }

    def _bar_at(
        self,
        prices_by_symbol: dict[str, list[dict[str, Any]]],
        symbol: str,
        timestamp: int,
    ) -> dict[str, Any] | None:
        for row in prices_by_symbol.get(symbol, []):
            if int(row["timestamp"]) == timestamp:
                return row
        return None

    def _atr_pct(self, snapshot: SnapshotStorage, symbol: str) -> float | None:
        rows = snapshot.get_prices(symbol, limit=48, timeframe="1h")
        if len(rows) < 15:
            return None
        return indicators.atr_pct(rows, period=14)

    def _load_prices(self) -> dict[str, list[dict[str, Any]]]:
        warmup_start = self.start_ts - 720 * bars.timeframe_to_seconds("1h")
        return {
            symbol: self.storage.get_prices(
                symbol,
                limit=1_000_000,
                since=warmup_start,
                before=self.end_ts,
                timeframe="1h",
            )
            for symbol in self.symbols
        }

    def _advance_prices(
        self,
        prices_by_symbol: dict[str, list[dict[str, Any]]],
        price_index: dict[str, int],
        timestamp: int,
    ) -> dict[str, float]:
        current: dict[str, float] = {}
        for symbol, rows in prices_by_symbol.items():
            while (
                price_index[symbol] < len(rows)
                and int(rows[price_index[symbol]]["timestamp"]) <= timestamp
            ):
                price_index[symbol] += 1
            if price_index[symbol] > 0:
                current[symbol] = float(rows[price_index[symbol] - 1]["close"])
        return current

    def _btc_buy_hold_return(
        self, prices_by_symbol: dict[str, list[dict[str, Any]]]
    ) -> float | None:
        rows = prices_by_symbol.get(config.SYMBOL) or self.storage.get_prices(
            config.SYMBOL,
            limit=1_000_000,
            since=self.start_ts,
            before=self.end_ts,
            timeframe="1h",
        )
        if len(rows) < 2:
            return None
        return round(_pct_return(float(rows[0]["close"]), float(rows[-1]["close"])), 2)

    def _symbol_results(
        self, trades: list[BacktestTrade], symbol_curves: dict[str, list[float]]
    ) -> dict[str, dict[str, float | int | None]]:
        result: dict[str, dict[str, float | int | None]] = {}
        for symbol in self.symbols:
            symbol_trades = [trade for trade in trades if trade.symbol == symbol]
            wins = [trade for trade in symbol_trades if float(trade.pnl or 0.0) > 0]
            returns = [
                (float(trade.pnl or 0.0) / trade.notional) * 100.0
                for trade in symbol_trades
                if trade.notional
            ]
            result[symbol] = {
                "trades": len(symbol_trades),
                "win_rate_pct": round((len(wins) / len(symbol_trades) * 100.0), 2)
                if symbol_trades
                else None,
                "avg_pnl": round(
                    sum(float(trade.pnl or 0.0) for trade in symbol_trades)
                    / len(symbol_trades),
                    2,
                )
                if symbol_trades
                else None,
                "avg_return_pct": round(sum(returns) / len(returns), 2)
                if returns
                else None,
                "max_drawdown_pct": round(_max_drawdown(symbol_curves[symbol]), 2),
            }
        return result


def _max_drawdown(values: list[float | int]) -> float:
    peak: float | None = None
    max_dd = 0.0
    for value in values:
        current = float(value)
        peak = current if peak is None else max(peak, current)
        if peak > 0:
            max_dd = max(max_dd, (peak - current) / peak * 100.0)
    return max_dd


def _pct_return(start: float, end: float) -> float:
    if not start:
        return 0.0
    return (end - start) / start * 100.0


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _default_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=90)


def _default_end() -> datetime:
    return datetime.now(timezone.utc)


def _parse_symbols(value: str | None) -> list[str]:
    if not value:
        return list(config.TRADING_SYMBOLS)
    return [symbol.strip() for symbol in value.split(",") if symbol.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run historical strategy backtests.")
    parser.add_argument("--start", default=None, help="Start date/time, e.g. 2024-01-01")
    parser.add_argument("--end", default=None, help="End date/time, defaults to now")
    parser.add_argument("--symbols", default=None, help="Comma-separated spot symbols")
    parser.add_argument(
        "--strategy",
        default=config.PRIMARY_STRATEGY,
        help=f"Strategy name (available: {', '.join(list_strategies())})",
    )
    args = parser.parse_args()

    start = _parse_datetime(args.start) if args.start else _default_start()
    end = _parse_datetime(args.end) if args.end else _default_end()
    symbols = _parse_symbols(args.symbols)

    result = BacktestEngine(
        start=start,
        end=end,
        symbols=symbols,
        strategy_name=args.strategy,
    ).run()

    output_dir = PROJECT_ROOT / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"backtest_{datetime.now(timezone.utc):%Y%m%d}.json"
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
