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
from src.engine.signal import gather_strategy_data  # noqa: E402
from src.strategies.base import SignalDirection  # noqa: E402
from src.strategies.registry import get_strategy, list_strategies  # noqa: E402


SECONDS_PER_DAY = 24 * 60 * 60


@dataclass
class BacktestTrade:
    symbol: str
    direction: str
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    opened_at: int
    exit_price: float | None = None
    closed_at: int | None = None
    pnl: float | None = None
    exit_reason: str | None = None

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price


@dataclass
class BacktestPortfolio:
    cash: float = config.PAPER_STARTING_CAPITAL
    open_trades: list[BacktestTrade] = field(default_factory=list)
    closed_trades: list[BacktestTrade] = field(default_factory=list)

    def open_trade(
        self, symbol: str, direction: str, price: float, timestamp: int
    ) -> BacktestTrade | None:
        if direction not in (SignalDirection.LONG.value, SignalDirection.SHORT.value):
            return None
        if not config.direction_allowed(direction):
            return None
        if any(trade.symbol == symbol for trade in self.open_trades):
            return None
        if len(self.open_trades) >= config.MAX_OPEN_POSITIONS:
            return None

        portfolio_value = self.value({})
        notional = portfolio_value * config.MAX_POSITION_PCT
        if notional <= 0 or self.cash < notional:
            return None

        quantity = notional / price
        if direction == SignalDirection.LONG.value:
            stop_loss = price * (1 - config.STOP_LOSS_PCT)
            take_profit = price * (1 + config.TAKE_PROFIT_PCT)
        else:
            stop_loss = price * (1 + config.STOP_LOSS_PCT)
            take_profit = price * (1 - config.TAKE_PROFIT_PCT)

        trade = BacktestTrade(
            symbol=symbol,
            direction=direction,
            entry_price=price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=timestamp,
        )
        self.cash -= notional
        self.open_trades.append(trade)
        return trade

    def check_exits(self, prices: dict[str, float], timestamp: int) -> None:
        still_open = []
        for trade in self.open_trades:
            price = prices.get(trade.symbol)
            if price is None:
                still_open.append(trade)
                continue
            reason = self._exit_reason(trade, price)
            if reason:
                self.close_trade(trade, price, timestamp, reason)
            else:
                still_open.append(trade)
        self.open_trades = still_open

    def close_all(self, prices: dict[str, float], timestamp: int) -> None:
        for trade in list(self.open_trades):
            price = prices.get(trade.symbol, trade.entry_price)
            self.close_trade(trade, price, timestamp, "end_of_backtest")
        self.open_trades = []

    def close_trade(
        self, trade: BacktestTrade, price: float, timestamp: int, reason: str
    ) -> None:
        pnl = self._realized_pnl(trade, price)
        trade.exit_price = price
        trade.closed_at = timestamp
        trade.pnl = pnl
        trade.exit_reason = reason
        self.cash += trade.notional + pnl
        self.closed_trades.append(trade)

    def value(self, prices: dict[str, float]) -> float:
        total = self.cash
        for trade in self.open_trades:
            price = prices.get(trade.symbol, trade.entry_price)
            total += self._position_value(trade, price)
        return total

    def pnl_by_symbol(self, prices: dict[str, float]) -> dict[str, float]:
        symbols = {trade.symbol for trade in self.closed_trades + self.open_trades}
        result: dict[str, float] = {}
        for symbol in symbols:
            realized = sum(float(t.pnl or 0.0) for t in self.closed_trades if t.symbol == symbol)
            unrealized = sum(
                self._realized_pnl(t, prices.get(symbol, t.entry_price))
                for t in self.open_trades
                if t.symbol == symbol
            )
            result[symbol] = realized + unrealized
        return result

    def _exit_reason(self, trade: BacktestTrade, price: float) -> str | None:
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

    def _realized_pnl(self, trade: BacktestTrade, price: float) -> float:
        if trade.direction == SignalDirection.LONG.value:
            return (price - trade.entry_price) * trade.quantity
        return (trade.entry_price - price) * trade.quantity

    def _position_value(self, trade: BacktestTrade, price: float) -> float:
        if trade.direction == SignalDirection.LONG.value:
            return trade.quantity * price
        return trade.notional + (trade.entry_price - price) * trade.quantity


class SnapshotStorage:
    """Read-only storage adapter that hides future rows from AltAnalyzer."""

    def __init__(
        self,
        storage: Storage,
        timestamp: int,
        latest_signal: dict[str, Any] | None,
    ) -> None:
        self.storage = storage
        self.timestamp = timestamp
        self.latest_signal = latest_signal

    def get_latest_price(self, symbol: str, timeframe: str = "1h") -> dict[str, Any] | None:
        prices = self.storage.get_prices(
            symbol, limit=1, before=self.timestamp, timeframe=timeframe
        )
        return prices[-1] if prices else None

    def get_latest_funding_rate(self, symbol: str) -> dict[str, Any] | None:
        rows = self.storage.get_funding_rates(symbol, limit=1, before=self.timestamp)
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
        effective_before = min(
            value for value in (before, self.timestamp) if value is not None
        )
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

        portfolio = BacktestPortfolio()
        signal_count = 0
        confidence_pass_count = 0
        opened_count = 0
        equity_curve: list[dict[str, float | int]] = []
        symbol_curves: dict[str, list[float]] = {symbol: [] for symbol in self.symbols}

        last_prices: dict[str, float] = {}
        price_index = {symbol: 0 for symbol in self.symbols}

        for ts in timeline:
            current_prices = self._advance_prices(prices_by_symbol, price_index, ts)
            last_prices.update(current_prices)
            portfolio.check_exits(current_prices, ts)

            for symbol in self.symbols:
                # Same data path as live trading: the snapshot hides rows
                # after `ts`, and gather_strategy_data fetches exactly what
                # the strategy declares in get_required_data().
                snapshot = SnapshotStorage(self.storage, ts, None)
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
                    "timestamp": ts,
                    "direction": signal.direction.value,
                    "reason": signal.reason,
                    "entry_price": signal.entry_price,
                    "funding_rate": funding_row.get("funding_rate"),
                    "metadata": signal.metadata,
                }
                snapshot.latest_signal = signal_row
                analysis = AltAnalyzer(snapshot).analyze(
                    symbol, strategy_name=self.strategy_name
                )
                confidence_passed = analysis["confidence"] >= config.PAPER_MIN_CONFIDENCE
                if confidence_passed:
                    confidence_pass_count += 1
                    opened = portfolio.open_trade(
                        symbol,
                        signal.direction.value,
                        float(price_row["close"]),
                        ts,
                    )
                    if opened:
                        opened_count += 1

            equity_curve.append({"timestamp": ts, "value": portfolio.value(last_prices)})
            symbol_pnl = portfolio.pnl_by_symbol(last_prices)
            for symbol in self.symbols:
                symbol_curves[symbol].append(
                    config.PAPER_STARTING_CAPITAL + symbol_pnl.get(symbol, 0.0)
                )

        if timeline:
            portfolio.close_all(last_prices, timeline[-1])

        final_value = portfolio.value(last_prices)
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
                "trades_opened": opened_count,
                "closed_trades": len(portfolio.closed_trades),
            },
            "symbols": self._symbol_results(portfolio.closed_trades, symbol_curves),
        }

    def _load_prices(self) -> dict[str, list[dict[str, Any]]]:
        return {
            symbol: self.storage.get_prices(
                symbol,
                limit=1_000_000,
                since=self.start_ts,
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
