#!/usr/bin/env python3
"""Historical backtest runner for configured trading strategies."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
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
from src.engine.evaluation import STRATEGY_CATEGORY_MAP, evaluate_symbol  # noqa: E402
from src.engine import hedge as hedge_engine  # noqa: E402
from src.engine.paper_trader import FakeDeltaNeutralError  # noqa: E402
from src.engine.regime import btc_regime  # noqa: E402
from src.engine.signal import gather_strategy_data  # noqa: E402
from src.research.market_categories import category_at  # noqa: E402
from src.research.rel_strength import LOOKBACK_BARS as REL_STRENGTH_LOOKBACK_BARS  # noqa: E402
from src.strategies.base import Signal, SignalDirection  # noqa: E402
from src.strategies.registry import get_strategy, list_strategies  # noqa: E402


SECONDS_PER_DAY = 24 * 60 * 60


def _strategy_category_allowed(
    storage: Storage,
    symbol: str,
    timestamp: int,
    strategy_name: str,
) -> tuple[bool, str | None, bool, bool]:
    if config.CATEGORY_STRATEGY_MODE != "matched":
        return True, None, False, False
    category = category_at(storage, symbol, timestamp)
    category_name = str((category or {}).get("category") or "")
    if not category_name:
        return True, None, True, False
    return (
        category_name in STRATEGY_CATEGORY_MAP.get(strategy_name, set()),
        category_name,
        True,
        True,
    )


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
    exit_price: float | None = None
    closed_at: int | None = None
    pnl: float | None = None
    exit_reason: str | None = None
    fees: float | None = None
    hedge_symbol: str | None = None
    hedge_direction: str | None = None
    hedge_entry_price: float | None = None
    hedge_quantity: float | None = None
    hedge_beta: float | None = None
    hedge_exit_price: float | None = None
    hedge_pnl: float | None = None
    hedge_fees: float | None = None
    funding_pnl: float | None = None
    realized_beta: float | None = None
    realized_correlation: float | None = None

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price


@dataclass
class BacktestPortfolio:
    cash: float = config.PAPER_STARTING_CAPITAL
    storage: Storage | None = None
    open_trades: list[BacktestTrade] = field(default_factory=list)
    closed_trades: list[BacktestTrade] = field(default_factory=list)

    def _hedge_leg_for_open(
        self,
        direction: str,
        metadata: dict[str, Any] | None,
        prices: dict[str, float],
        timestamp: int,
    ) -> dict[str, Any] | None:
        """Compute the hedge leg's sizing for a market-neutral entry, or None
        if this isn't a market-neutral signal / the hedge can't be sized.
        Mirrors src.engine.paper_trader.PaperTrader._hedge_leg_for_open so
        live and backtest can never size a hedge differently."""
        metadata = metadata or {}
        if metadata.get("execution_mode") != "market_neutral":
            return None
        hedge_symbol = metadata.get("hedge_symbol") or config.SYMBOL
        beta = metadata.get("beta")
        if not beta or beta <= 0:
            return None
        hedge_price = prices.get(hedge_symbol)
        if hedge_price is None:
            return None

        portfolio_value = self.value(prices, timestamp)
        primary_cap, hedge_cap = hedge_engine.position_caps(portfolio_value, config.MAX_POSITION_PCT, beta)
        available = min(primary_cap + hedge_cap, self.cash)
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

    def open_trade(
        self,
        symbol: str,
        direction: str,
        price: float,
        timestamp: int,
        *,
        strategy: str | None = None,
        metadata: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
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

        prices = prices or {}
        hedge_leg = self._hedge_leg_for_open(direction, metadata, prices, timestamp)
        if hedge_leg is not None:
            notional = hedge_leg["primary_notional"]
        else:
            portfolio_value = self.value({}, timestamp)
            notional = portfolio_value * config.MAX_POSITION_PCT
        combined_notional = notional + (hedge_leg["hedge_notional"] if hedge_leg else 0.0)
        if notional <= 0 or self.cash < combined_notional:
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
            strategy=strategy,
            metadata=metadata,
            hedge_symbol=hedge_leg["hedge_symbol"] if hedge_leg else None,
            hedge_direction=hedge_leg["hedge_direction"] if hedge_leg else None,
            hedge_entry_price=hedge_leg["hedge_entry_price"] if hedge_leg else None,
            hedge_quantity=hedge_leg["hedge_quantity"] if hedge_leg else None,
            hedge_beta=hedge_leg["hedge_beta"] if hedge_leg else None,
        )
        self.cash -= combined_notional
        self.open_trades.append(trade)
        return trade

    def check_exits(self, prices: dict[str, float], timestamp: int) -> None:
        still_open = []
        for trade in self.open_trades:
            price = prices.get(trade.symbol)
            reason = self._exit_reason(trade, price, timestamp)
            if reason:
                close_price = price if price is not None else trade.entry_price
                self.close_trade(trade, close_price, timestamp, reason, prices=prices)
            else:
                still_open.append(trade)
        self.open_trades = still_open

    def close_all(self, prices: dict[str, float], timestamp: int) -> None:
        for trade in list(self.open_trades):
            price = prices.get(trade.symbol, trade.entry_price)
            self.close_trade(trade, price, timestamp, "end_of_backtest", prices=prices)
        self.open_trades = []

    def _close_hedge_leg(
        self, trade: BacktestTrade, closed_at: int, prices: dict[str, float]
    ) -> tuple[float, dict[str, Any]]:
        """Close the hedge leg in lockstep with the primary leg. Returns the
        hedge leg's reserved notional (to release back to cash -- its P&L is
        folded into the combined pnl the caller adds separately) and the
        fields to persist. Mirrors PaperTrader._close_hedge_leg exactly."""
        if not trade.hedge_symbol:
            return 0.0, {}

        direction = trade.hedge_direction
        entry = float(trade.hedge_entry_price)
        qty = float(trade.hedge_quantity)
        notional = entry * qty
        exit_price = prices.get(trade.hedge_symbol, entry)

        hedge_pnl = hedge_engine.leg_pnl(direction, entry, exit_price, qty)
        hedge_fees = hedge_engine.leg_fees(notional, config.round_trip_cost_pct())

        opened_at = trade.opened_at
        realized_beta = realized_correlation = None
        funding_pnl = 0.0
        if self.storage is not None:
            funding_rows = self.storage.get_funding_rates(
                trade.hedge_symbol, since=opened_at, before=closed_at
            )
            funding_pnl = hedge_engine.funding_pnl_for_leg(
                direction, funding_rows, notional, opened_at, closed_at
            )
            primary_rows = self.storage.get_prices(
                trade.symbol, since=opened_at, before=closed_at, timeframe="1h"
            )
            hedge_rows = self.storage.get_prices(
                trade.hedge_symbol, since=opened_at, before=closed_at, timeframe="1h"
            )
            realized = hedge_engine.realized_beta_and_correlation(
                primary_rows, hedge_rows, opened_at, closed_at, config.HEDGE_BETA_MIN_POINTS
            )
            realized_beta, realized_correlation = realized["beta"], realized["correlation"]

        fields = {
            "hedge_exit_price": exit_price,
            "hedge_pnl": hedge_pnl,
            "hedge_fees": hedge_fees,
            "funding_pnl": funding_pnl,
            "realized_beta": realized_beta,
            "realized_correlation": realized_correlation,
        }
        return notional, fields

    def close_trade(
        self,
        trade: BacktestTrade,
        price: float,
        timestamp: int,
        reason: str,
        prices: dict[str, float] | None = None,
    ) -> None:
        if (
            trade.strategy == "funding_carry"
            and (trade.metadata or {}).get("execution_mode") == "delta_neutral"
        ):
            raise FakeDeltaNeutralError(f"trade symbol={trade.symbol} opened_at={trade.opened_at}")
        primary_pnl = self._realized_pnl(trade, price, timestamp)
        fees = trade.notional * config.round_trip_cost_pct()

        hedge_notional, hedge_fields = self._close_hedge_leg(trade, timestamp, prices or {})
        pnl = hedge_engine.combined_trade_pnl(
            primary_pnl,
            fees,
            hedge_fields.get("hedge_pnl", 0.0),
            hedge_fields.get("hedge_fees", 0.0),
            hedge_fields.get("funding_pnl", 0.0),
        )

        trade.fees = fees
        trade.exit_price = price
        trade.closed_at = timestamp
        trade.pnl = pnl
        trade.exit_reason = reason
        for key, value in hedge_fields.items():
            setattr(trade, key, value)
        # Release both legs' reserved notional; pnl already nets out both
        # legs' P&L, fees, and funding, so it's added exactly once.
        self.cash += trade.notional + hedge_notional + pnl
        self.closed_trades.append(trade)

    def _hedge_unrealized(
        self, trade: BacktestTrade, prices: dict[str, float]
    ) -> tuple[float, float]:
        if not trade.hedge_symbol:
            return 0.0, 0.0
        price = prices.get(trade.hedge_symbol, trade.hedge_entry_price)
        direction = trade.hedge_direction
        entry = float(trade.hedge_entry_price)
        qty = float(trade.hedge_quantity)
        value = hedge_engine.leg_position_value(direction, entry, price, qty)
        pnl = hedge_engine.leg_pnl(direction, entry, price, qty)
        return value, pnl

    def value(self, prices: dict[str, float], timestamp: int | None) -> float:
        total = self.cash
        for trade in self.open_trades:
            price = prices.get(trade.symbol, trade.entry_price)
            total += self._position_value(trade, price, timestamp=timestamp)
            hedge_value, _ = self._hedge_unrealized(trade, prices)
            total += hedge_value
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
            # Hedge legs aren't a separate tradable position -- their P&L
            # belongs to the primary symbol's trade that opened them.
            hedge_unrealized = sum(
                self._hedge_unrealized(t, prices)[1]
                for t in self.open_trades
                if t.symbol == symbol
            )
            result[symbol] = realized + unrealized + hedge_unrealized
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
            raise FakeDeltaNeutralError(f"trade symbol={trade.symbol} opened_at={trade.opened_at}")

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

        # Mirrors src/engine/paper_trader.py's time_stop so a swing-style
        # signal (e.g. rel_strength_rotation) is actually exit-simulated in
        # backtest the same way live would apply it -- without this, no
        # strategy's max-hold cap was ever exercised by walk-forward research.
        max_hours = config.max_hold_hours_for_style((trade.metadata or {}).get("style"))
        if max_hours > 0:
            age_hours = (timestamp - trade.opened_at) / 3600.0
            if age_hours >= max_hours:
                return "time_stop"
        return None

    def _realized_pnl(
        self, trade: BacktestTrade, price: float, timestamp: int | None
    ) -> float:
        if (
            trade.strategy == "funding_carry"
            and (trade.metadata or {}).get("execution_mode") == "delta_neutral"
        ):
            raise FakeDeltaNeutralError(f"trade symbol={trade.symbol} opened_at={trade.opened_at}")
        if trade.direction == SignalDirection.LONG.value:
            return (price - trade.entry_price) * trade.quantity
        return (trade.entry_price - price) * trade.quantity

    def _position_value(
        self, trade: BacktestTrade, price: float, timestamp: int | None
    ) -> float:
        if (
            trade.strategy == "funding_carry"
            and (trade.metadata or {}).get("execution_mode") == "delta_neutral"
        ):
            raise FakeDeltaNeutralError(f"trade symbol={trade.symbol} opened_at={trade.opened_at}")
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
        price_cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.storage = storage
        self.timestamp = timestamp
        self.latest_signal = latest_signal
        # {(symbol, timeframe): rows sorted ascending by timestamp}, pre-loaded
        # once per BacktestEngine.run() to cover everything the run could ever
        # need. Without this, get_prices() re-queries the DB from scratch on
        # every hourly timestep -- fine for the ~100-720 row lookbacks most
        # strategies use, ruinous for rel_strength_rotation's 17,520-row (2y)
        # lookback: 1440 timestamps x 6 symbols x 2 such queries per 60-day
        # window measured at ~7.5 minutes/window before this cache existed.
        self._price_cache = price_cache or {}

    def _cached_prices(
        self, symbol: str, timeframe: str, since: int | None, effective_before: int
    ) -> list[dict[str, Any]] | None:
        rows = self._price_cache.get((symbol, timeframe))
        if rows is None:
            return None
        lo = bisect.bisect_left(rows, since, key=lambda r: int(r["timestamp"])) if since is not None else 0
        hi = bisect.bisect_right(rows, effective_before, key=lambda r: int(r["timestamp"]))
        return rows[lo:hi]

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
        cached = self._cached_prices(symbol, timeframe, since, effective_before)
        if cached is not None:
            return cached[-limit:] if limit else cached
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
            value for value in (before, self.timestamp) if value is not None
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
            value for value in (before, self.timestamp) if value is not None
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
            value for value in (before, self.timestamp) if value is not None
        )
        return self.storage.get_open_interest_history(
            symbol, limit=limit, since=since, before=effective_before
        )


# Strategies validated through the ACTUAL live decision function
# (evaluate_symbol) instead of BaseStrategy.generate_signal(). Narrow,
# deliberate exception -- see BacktestEngine._evaluation_engine_signal.
# The other five strategies still test via generate_signal(); this does not
# change how they're validated or re-open the backtest<->evaluate_symbol
# path-divergence question in general (see research_decisions:
# backtest_evaluate_symbol_realignment_parked).
EVALUATION_ENGINE_STRATEGIES = frozenset({"rel_strength_rotation"})

# Setup toggles forced off so evaluate_symbol's only possible verdict is the
# one EVALUATION_ENGINE_STRATEGIES member being tested -- otherwise a
# walk-forward run would be measuring the whole confluence ensemble, not the
# one setup under test. Two groups: most setups gate on a real config.py
# module attribute; SETUP_FAILED_PUMP_ENABLED has none -- _failed_pump_short_
# setup reads os.getenv directly every call (see evaluation.py's
# _env_bool_dynamic), so it only needs the env var forced, not a config attr.
_OTHER_LIVE_SETUP_FLAGS = (
    "SETUP_MEANREV_ENABLED", "SETUP_BREAKOUT_ENABLED", "SETUP_TSMOM_ENABLED",
    "SETUP_VOLUME_ENABLED", "SETUP_FUNDING_ENABLED", "SETUP_SWING_ENABLED",
)
_OTHER_LIVE_SETUP_ENV_ONLY_FLAGS = ("SETUP_FAILED_PUMP_ENABLED",)


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
        # For EVALUATION_ENGINE_STRATEGIES this is only used so get_strategy()
        # validates the name; generate_signal() is bypassed in run() below in
        # favor of _evaluation_engine_signal.
        self.strategy = get_strategy(strategy_name)

    def _evaluation_engine_signal(self, ts: int, symbol: str) -> Signal | None:
        """Route through evaluate_symbol() (what live actually runs under
        ENTRY_DECISION_ENGINE=evaluation) instead of a BaseStrategy
        reimplementation, so backtest validates the real decision -- entry
        gate, confluence, direction/style -- not just an equivalent entry
        criterion. All setups except the one under test are forced off for
        the duration of this call so its verdict is the only possible one.

        Applies the SAME tradable gate live's _entry_candidates_from_evaluation
        uses (result["tradable"], which already folds in ENTRY_MIN_CONFIDENCE)
        -- not the separate AltAnalyzer/MIN_CONFIDENCE gate the generate_signal()
        path below uses, which live's evaluation engine never consults.

        Deliberate simplification: calibration is skipped (empty map), not
        computed point-in-time. build_calibration_map()'s underlying query
        (Storage.get_strategy_stats) has no `before=` cutoff, so calling it
        for real inside a walk-forward loop would leak future trade outcomes
        into past entry decisions. Calibration only adjusts the confidence
        NUMBER, not direction or entry, so skipping it is documented and
        bounded rather than a silent lookahead gap.
        """
        snapshot = SnapshotStorage(self.storage, ts, None, price_cache=self._price_cache)
        category_row = category_at(self.storage, symbol, ts)
        category = (category_row or {}).get("category")
        regime = btc_regime(snapshot)
        btc_rows = snapshot.get_prices(config.SYMBOL, limit=720, timeframe="1h")

        all_flags = _OTHER_LIVE_SETUP_FLAGS + _OTHER_LIVE_SETUP_ENV_ONLY_FLAGS
        saved_attr = {name: getattr(config, name) for name in _OTHER_LIVE_SETUP_FLAGS}
        saved_env = {name: os.environ.get(name) for name in all_flags}
        try:
            for name in _OTHER_LIVE_SETUP_FLAGS:
                setattr(config, name, False)
            for name in all_flags:
                os.environ[name] = "false"
            result = evaluate_symbol(
                snapshot, symbol, btc_rows, regime=regime, calibration={}, category=category
            )
        finally:
            for name in _OTHER_LIVE_SETUP_FLAGS:
                setattr(config, name, saved_attr[name])
            for name in all_flags:
                if saved_env[name] is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = saved_env[name]

        if not result.get("tradable"):
            return None
        verdict = result.get("verdict")
        if not verdict or verdict.get("direction") not in ("LONG", "SHORT"):
            return None
        last_price = (result.get("metrics") or {}).get("last_price")
        if last_price is None:
            return None
        return Signal(
            direction=SignalDirection(verdict["direction"]),
            reason=verdict.get("reason", ""),
            symbol=symbol,
            entry_price=float(last_price),
            # verdict["style"] is the raw Korean label ("스윙") --
            # config.normalize_holding_style() accepts it directly, same as
            # the live path, so no translation needed here. execution_mode/
            # beta/hedge_symbol are only set by market-neutral setups (e.g.
            # _rel_strength_setup); absent for everything else, so this is a
            # no-op for strategies that don't hedge.
            metadata={
                "style": verdict.get("style"),
                "execution_mode": verdict.get("execution_mode"),
                "beta": verdict.get("beta"),
                "hedge_symbol": verdict.get("hedge_symbol"),
            },
        )

    def run(self) -> dict[str, Any]:
        prices_by_symbol = self._load_prices()
        self._price_cache = self._load_price_cache()
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
        category_checks = 0
        category_present = 0
        equity_curve: list[dict[str, float | int]] = []
        symbol_curves: dict[str, list[float]] = {symbol: [] for symbol in self.symbols}

        last_prices: dict[str, float] = {}
        price_index = {symbol: 0 for symbol in self.symbols}

        for ts in timeline:
            current_prices = self._advance_prices(prices_by_symbol, price_index, ts)
            last_prices.update(current_prices)
            portfolio.check_exits(current_prices, ts)

            for symbol in self.symbols:
                if self.strategy_name in EVALUATION_ENGINE_STRATEGIES:
                    # Self-contained: evaluate_symbol() already applied the
                    # real entry gate (ENTRY_MIN_CONFIDENCE via `tradable`),
                    # confluence, and style -- the shared AltAnalyzer
                    # confidence path below is a DIFFERENT scoring system
                    # live's evaluation engine never consults, so it must not
                    # also gate here.
                    signal = self._evaluation_engine_signal(ts, symbol)
                    if signal is None:
                        continue
                    category_allowed, category_name, category_checked, category_found = (
                        _strategy_category_allowed(self.storage, symbol, ts, self.strategy_name)
                    )
                    category_checks += 1 if category_checked else 0
                    category_present += 1 if category_found else 0
                    if not category_allowed:
                        continue
                    signal_count += 1
                    confidence_pass_count += 1
                    opened = portfolio.open_trade(
                        symbol,
                        signal.direction.value,
                        float(signal.entry_price),
                        ts,
                        strategy=self.strategy_name,
                        metadata=signal.metadata,
                        prices=current_prices,
                    )
                    if opened:
                        opened_count += 1
                    continue

                # Same data path as live trading: the snapshot hides rows
                # after `ts`, and gather_strategy_data fetches exactly what
                # the strategy declares in get_required_data().
                snapshot = SnapshotStorage(self.storage, ts, None, price_cache=self._price_cache)
                data = gather_strategy_data(snapshot, self.strategy, symbol)
                if self.strategy.validate_data(data):
                    continue

                signal = self.strategy.generate_signal(data)
                if signal.direction == SignalDirection.NONE:
                    continue

                price_row = data.get("latest_price")
                if not price_row:
                    continue
                category_allowed, category_name, category_checked, category_found = _strategy_category_allowed(
                    self.storage, symbol, ts, self.strategy_name
                )
                category_checks += 1 if category_checked else 0
                category_present += 1 if category_found else 0
                if not category_allowed:
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
                    "metadata": {
                        **(signal.metadata or {}),
                        "market_category": category_name,
                        "category_strategy_mode": config.CATEGORY_STRATEGY_MODE,
                    },
                }
                snapshot.latest_signal = signal_row
                analysis = AltAnalyzer(snapshot).analyze(
                    symbol, strategy_name=self.strategy_name
                )
                confidence_passed = analysis["confidence"] >= config.MIN_CONFIDENCE
                if confidence_passed:
                    confidence_pass_count += 1
                    opened = portfolio.open_trade(
                        symbol,
                        signal.direction.value,
                        float(price_row["close"]),
                        ts,
                        strategy=self.strategy_name,
                        metadata=signal.metadata,
                        prices=current_prices,
                    )
                    if opened:
                        opened_count += 1

            equity_curve.append({"timestamp": ts, "value": portfolio.value(last_prices, ts)})
            symbol_pnl = portfolio.pnl_by_symbol(last_prices, ts)
            for symbol in self.symbols:
                symbol_curves[symbol].append(
                    config.PAPER_STARTING_CAPITAL + symbol_pnl.get(symbol, 0.0)
                )

        if timeline:
            portfolio.close_all(last_prices, timeline[-1])

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
                "trades_opened": opened_count,
                "closed_trades": len(portfolio.closed_trades),
                "category_checks": category_checks,
                "category_present": category_present,
                "category_coverage_pct": round(
                    category_present / category_checks * 100.0, 2
                ) if category_checks else None,
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
                    "opened_at": getattr(t, "opened_at", None),
                    "closed_at": getattr(t, "closed_at", None),
                    # Hedge fields (None for non-hedge trades) -- carried through
                    # so the walk-forward runner can report net-of-funding
                    # expectancy and the basis-risk split without re-deriving
                    # them from raw paper_trades rows.
                    "hedge_symbol": getattr(t, "hedge_symbol", None),
                    "hedge_beta": getattr(t, "hedge_beta", None),
                    "hedge_pnl": getattr(t, "hedge_pnl", None),
                    "hedge_fees": getattr(t, "hedge_fees", None),
                    "funding_pnl": getattr(t, "funding_pnl", None),
                    "realized_beta": getattr(t, "realized_beta", None),
                    "realized_correlation": getattr(t, "realized_correlation", None),
                }
                for t in portfolio.closed_trades
            ],
            # Market-neutral trades only (research_decisions,
            # subject='rel_strength_market_neutral', additions 2 & 3): net
            # expectancy with/without funding, and expectancy split by
            # whether the realized beta stayed close to the ex-ante beta
            # used for sizing. None when no hedge trades closed.
            "hedge_analysis": _hedge_analysis(portfolio.closed_trades),
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

    def _load_price_cache(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Pre-load once per run() call so SnapshotStorage.get_prices() never
        re-queries the DB per timestep. EVALUATION_ENGINE_STRATEGIES members
        (e.g. rel_strength_rotation) need REL_STRENGTH_LOOKBACK_BARS (~2y) of
        prior history for their own beta/spread computation; everything else
        only ever asks for a few hundred bars, well inside the backtest
        window itself, so the extra lookback is skipped for them to avoid
        loading years of unnecessary history on every run.
        """
        extra_lookback_s = (
            REL_STRENGTH_LOOKBACK_BARS * 3600
            if self.strategy_name in EVALUATION_ENGINE_STRATEGIES
            else 0
        )
        since = self.start_ts - extra_lookback_s
        symbols = set(self.symbols) | {config.SYMBOL}
        return {
            (symbol, "1h"): self.storage.get_prices(
                symbol, limit=1_000_000, since=since, before=self.end_ts, timeframe="1h"
            )
            for symbol in symbols
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


def _hedge_analysis(trades: list[BacktestTrade]) -> dict[str, Any] | None:
    """Net-of-funding expectancy and basis-risk split for market-neutral
    trades (research_decisions, subject='rel_strength_market_neutral',
    additions 2 & 3). None when no hedge trades closed -- zero trades is
    information, not something to paper over with a zeroed-out report."""
    hedge_trades = [t for t in trades if t.hedge_symbol]
    if not hedge_trades:
        return None

    def pct(pnl: float, notional: float) -> float | None:
        return (pnl / notional * 100.0) if notional else None

    def bucket_summary(bucket: list[BacktestTrade]) -> dict[str, Any]:
        if not bucket:
            return {"n": 0, "avg_pnl": None, "avg_pnl_pct": None}
        pnls = [float(t.pnl or 0.0) for t in bucket]
        pcts = [p for t, p in zip(bucket, (pct(pn, t.notional) for pn, t in zip(pnls, bucket))) if p is not None]
        return {
            "n": len(bucket),
            "avg_pnl": round(sum(pnls) / len(pnls), 6),
            "avg_pnl_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        }

    with_funding = [float(t.pnl or 0.0) for t in hedge_trades]
    without_funding = [float(t.pnl or 0.0) - float(t.funding_pnl or 0.0) for t in hedge_trades]
    with_funding_pcts = [p for t, p in zip(hedge_trades, (pct(v, t.notional) for v, t in zip(with_funding, hedge_trades))) if p is not None]
    without_funding_pcts = [p for t, p in zip(hedge_trades, (pct(v, t.notional) for v, t in zip(without_funding, hedge_trades))) if p is not None]

    buckets: dict[str, list[BacktestTrade]] = {
        "correlation_held": [], "correlation_spiked": [], "unknown": [],
    }
    for t in hedge_trades:
        status = hedge_engine.basis_risk_status(t.hedge_beta, t.realized_beta, config.BASIS_RISK_BETA_TOLERANCE)
        buckets[status].append(t)

    return {
        "trades": len(hedge_trades),
        "avg_pnl_with_funding": round(sum(with_funding) / len(with_funding), 6),
        "avg_pnl_pct_with_funding": round(sum(with_funding_pcts) / len(with_funding_pcts), 4)
        if with_funding_pcts else None,
        "avg_pnl_without_funding": round(sum(without_funding) / len(without_funding), 6),
        "avg_pnl_pct_without_funding": round(sum(without_funding_pcts) / len(without_funding_pcts), 4)
        if without_funding_pcts else None,
        "avg_funding_pnl": round(sum(float(t.funding_pnl or 0.0) for t in hedge_trades) / len(hedge_trades), 6),
        "basis_risk": {
            "correlation_held": bucket_summary(buckets["correlation_held"]),
            "correlation_spiked": bucket_summary(buckets["correlation_spiked"]),
            "unknown": bucket_summary(buckets["unknown"]),
        },
    }


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
