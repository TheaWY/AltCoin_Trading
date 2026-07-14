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
from src.engine import entry_filters  # noqa: E402
from src.engine import execution_cost  # noqa: E402
from src.engine import exits  # noqa: E402
from src.engine import risk_budget  # noqa: E402
from src.engine import hedge as hedge_engine  # noqa: E402
from src.engine import indicators  # noqa: E402
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
    # 1h ATR% at open, for the exit-time stress-bar-range check
    # (execution_cost.is_stress_bar) -- backtest previously computed
    # stop/take-profit from a FIXED pct, never ATR, so this field didn't
    # exist; it is populated in open_trade() specifically for this check,
    # not for stop sizing (that stays unchanged, out of scope here).
    atr_pct: float | None = None
    # Dollar cost of simulated execution slippage (entry + exit), separate
    # from fees -- lets the research report answer "how much of gross edge
    # did realistic costs consume" without a second no-slippage run.
    slippage_cost: float = 0.0
    # High-water mark for the trailing-stop ratchet (mirrors live's
    # paper_trades.trail_price; backtest had NO trailing logic until
    # 2026-07-13 -- another exit-mechanism divergence from live).
    trail_price: float | None = None
    # PARTIAL_TP_AT_R: half already banked (live infers this from the
    # closed partial_tp sibling row; the dataclass just carries a flag).
    partial_done: bool = False
    # Standing exit-geometry metrics, set at close: how far the trade went
    # in our favor at its best (from the trail high-water mark) and what
    # fraction of that favorable move the exit captured.
    mfe_pct: float | None = None
    mfe_capture: float | None = None

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price


@dataclass
class BacktestPortfolio:
    cash: float = config.PAPER_STARTING_CAPITAL
    storage: Storage | None = None
    open_trades: list[BacktestTrade] = field(default_factory=list)
    closed_trades: list[BacktestTrade] = field(default_factory=list)
    # (symbol, day-bucket) -> ex-ante beta memo for the capital gate;
    # mirrors PaperTrader._beta_cache.
    _beta_cache: dict = field(default_factory=dict)

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

    def _symbol_beta(self, symbol: str, timestamp: int) -> float | None:
        """Mirrors PaperTrader._symbol_beta -- ex-ante beta vs BTC over the
        trailing HEDGE_BETA_LOOKBACK_H, point-in-time (before=timestamp)."""
        if symbol == config.SYMBOL:
            return 1.0
        if self.storage is None:
            return None
        key = (symbol, timestamp // 86400)
        if key not in self._beta_cache:
            limit = config.HEDGE_BETA_LOOKBACK_H + 24
            sym_rows = self.storage.get_prices(symbol, limit=limit, before=timestamp, timeframe="1h")
            btc_rows = self.storage.get_prices(config.SYMBOL, limit=limit, before=timestamp, timeframe="1h")
            self._beta_cache[key] = hedge_engine.ex_ante_beta(
                sym_rows, btc_rows, config.HEDGE_BETA_LOOKBACK_H, config.HEDGE_BETA_MIN_POINTS
            )
        return self._beta_cache[key]

    def _capital_gate(
        self,
        symbol: str,
        direction: str,
        price: float,
        stop_loss: float,
        notional: float,
        equity: float,
        hedge_leg: dict[str, Any] | None,
        prices: dict[str, float],
        timestamp: int,
    ) -> tuple[str, str] | None:
        """Mirrors PaperTrader._capital_gate exactly -- risk-budget + beta
        gates through src/engine/risk_budget.py, same order, same semantics,
        so live and backtest refuse the same entries (rule #2)."""
        if not risk_budget.position_large_enough(notional, equity):
            return ("position_too_small",
                    f"notional {notional:.2f} < {risk_budget.MIN_POSITION_NOTIONAL_PCT:.0%} "
                    f"of equity {equity:.2f} (dust; cash nearly exhausted)")
        quantity = notional / price if price else 0.0
        cand_risk = risk_budget.position_risk(direction, price, stop_loss, quantity)
        open_risk = 0.0
        legs: list[dict[str, Any]] = []
        for t in self.open_trades:
            cur = prices.get(t.symbol, t.entry_price)
            open_risk += risk_budget.position_risk(t.direction, cur, t.stop_loss, t.quantity)
            legs.append({"direction": t.direction, "notional": t.quantity * cur,
                         "beta": self._symbol_beta(t.symbol, timestamp)})
            if t.hedge_symbol:
                legs.append({"direction": t.hedge_direction,
                             "notional": float(t.hedge_quantity) * float(t.hedge_entry_price),
                             "beta": self._symbol_beta(t.hedge_symbol, timestamp)})

        if not risk_budget.risk_budget_allows(open_risk, cand_risk, equity, config.TOTAL_RISK_BUDGET_PCT):
            return ("risk_budget",
                    f"risk budget: open {open_risk:.2f} + candidate {cand_risk:.2f} "
                    f"> {config.TOTAL_RISK_BUDGET_PCT:.0%} of equity {equity:.2f}")

        cand_legs = [{"direction": direction, "notional": notional,
                      "beta": self._symbol_beta(symbol, timestamp)}]
        if hedge_leg is not None:
            cand_legs.append({"direction": hedge_leg["hedge_direction"],
                              "notional": hedge_leg["hedge_notional"],
                              "beta": self._symbol_beta(hedge_leg["hedge_symbol"], timestamp)})
        if config.MAX_NET_BETA_EXPOSURE < risk_budget.BETA_UNLIMITED:
            net = risk_budget.net_beta_exposure([*legs, *cand_legs], equity)
            if abs(net) > config.MAX_NET_BETA_EXPOSURE:
                return ("beta_exposure",
                        f"net beta exposure {net:+.2f} exceeds cap {config.MAX_NET_BETA_EXPOSURE:g}")
        return None

    def _atr_pct(self, symbol: str, timestamp: int) -> float | None:
        """Mirrors PaperTrader._atr_pct exactly, point-in-time (before=timestamp
        so no lookahead). Backtest stop/take-profit stay a FIXED pct below
        (unchanged, out of scope for this change) -- this is only for the
        exit-time stress-bar-range check."""
        if self.storage is None:
            return None
        rows = self.storage.get_prices(symbol, limit=48, before=timestamp, timeframe="1h")
        if len(rows) < 15:
            return None
        return indicators.atr_pct(rows, period=14)

    def _recent_max_range_pct(self, symbol: str, timestamp: int, lookback: int = 24) -> float | None:
        """Mirrors PaperTrader._recent_max_range_pct, point-in-time
        (before=timestamp). Max (high-low)/close over the last `lookback` 1h
        bars -- the MAX_STOP_GAP_TOLERANCE entry-filter input."""
        if self.storage is None:
            return None
        rows = self.storage.get_prices(symbol, limit=lookback, before=timestamp, timeframe="1h")
        ranges = [
            (float(r["high"]) - float(r["low"])) / float(r["close"]) * 100.0
            for r in rows if float(r["close"]) > 0
        ]
        return max(ranges) if ranges else None

    def _bar_range_pct(self, symbol: str, ts: int) -> float | None:
        """Mirrors PaperTrader._bar_range_pct exactly."""
        if self.storage is None:
            return None
        bucket = (ts // 3600) * 3600
        rows = self.storage.get_prices(symbol, limit=1, since=bucket, before=bucket + 1, timeframe="1h")
        if not rows:
            return None
        row = rows[-1]
        close = float(row["close"])
        if close <= 0:
            return None
        return (float(row["high"]) - float(row["low"])) / close * 100.0

    def _adjusted_fill_price(
        self,
        symbol: str,
        direction: str,
        is_entry: bool,
        raw_price: float,
        notional: float,
        ts: int,
        reason: str | None = None,
        atr_pct: float | None = None,
    ) -> tuple[float, dict[str, Any]]:
        """Mirrors src.engine.paper_trader.PaperTrader._adjusted_fill_price
        exactly -- same execution_cost calls, same stress condition -- so
        live and backtest can never silently diverge on cost. No storage
        (e.g. a hand-built BacktestPortfolio in a test) means no slippage:
        conservative, matches the pre-change behavior for callers that
        don't wire a storage in."""
        no_cost_meta = {"used_fallback": False, "slippage_pct": 0.0, "is_stress": False,
                         "spread_bps": 0.0, "depth_usd": 0.0}
        if self.storage is None:
            return raw_price, no_cost_meta

        side = execution_cost.side_for(direction, is_entry)
        ob_rows = self.storage.get_orderbook_snapshots(symbol, limit=1, before=ts)
        ob_snapshot = ob_rows[-1] if ob_rows else None
        fallback = execution_cost.symbol_fallback_stats(self.storage, symbol)
        spread_bps, depth_usd, used_fallback = execution_cost.resolve_spread_and_depth(
            side, ob_snapshot, fallback["spread_bps"], fallback["depth_usd"]
        )

        is_stress = False
        if not is_entry:
            bar_range_pct = self._bar_range_pct(symbol, ts)
            is_stress = reason == "stop_loss" or execution_cost.is_stress_bar(
                bar_range_pct, atr_pct, config.STRESS_BAR_RANGE_ATR_MULT
            )

        slip_pct = execution_cost.slippage_pct(
            notional, spread_bps, depth_usd, is_stress, config.STOP_SLIPPAGE_MULT
        )
        fill_price = execution_cost.adjusted_fill_price(raw_price, side, slip_pct)
        meta = {
            "used_fallback": used_fallback,
            "slippage_pct": slip_pct,
            "is_stress": is_stress,
            "spread_bps": spread_bps,
            "depth_usd": depth_usd,
        }
        return fill_price, meta

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
        # Same order as PaperTrader._open_trade: ATR -> exit levels -> sizing,
        # because risk-based sizing derives from the stop distance. All three
        # go through src/engine/exits.py, the shared path.
        atr_pct = self._atr_pct(symbol, timestamp)
        stop_loss, take_profit = exits.exit_levels(
            direction, price, atr_pct,
            atr_stop_mult=config.ATR_STOP_MULT, atr_tp_mult=config.ATR_TP_MULT,
            fallback_stop_pct=config.STOP_LOSS_PCT, fallback_tp_pct=config.TAKE_PROFIT_PCT,
        )

        # Volatility entry filters -- mirrors PaperTrader._open_trade exactly
        # (shared entry_filters.volatility_entry_block), same inputs, so live
        # and backtest refuse the same violent-symbol entries (rule #2).
        stop_dist_pct = abs(price - stop_loss) / price * 100.0 if price else 0.0
        if entry_filters.volatility_entry_block(
            atr_pct, self._recent_max_range_pct(symbol, timestamp), stop_dist_pct,
            max_entry_atr_pct=config.MAX_ENTRY_ATR_PCT,
            max_stop_gap_tolerance=config.MAX_STOP_GAP_TOLERANCE,
        ) is not None:
            return None

        hedge_leg = self._hedge_leg_for_open(direction, metadata, prices, timestamp)
        if hedge_leg is not None:
            notional = hedge_leg["primary_notional"]
        else:
            portfolio_value = self.value({}, timestamp)
            # Risk-based sizing (divergence #4): flat MAX_POSITION_PCT until
            # 2026-07-13, which oversized backtest ~50% exactly on wide-stop
            # (high-ATR) names relative to what live would have done.
            notional = exits.position_notional(
                portfolio_value, self.cash, price, stop_loss,
                risk_per_trade_pct=config.RISK_PER_TRADE_PCT,
                max_position_pct=config.MAX_POSITION_PCT,
            )
        combined_notional = notional + (hedge_leg["hedge_notional"] if hedge_leg else 0.0)
        if notional <= 0 or self.cash < combined_notional:
            return None

        equity = self.value(prices, timestamp)
        if self._capital_gate(symbol, direction, price, stop_loss, notional,
                              equity, hedge_leg, prices, timestamp) is not None:
            return None

        # Slippage moves the FILL price; stop/take-profit keep the same
        # percentage distance, re-anchored to the actual fill (mirrors
        # PaperTrader._open_trade exactly).
        fill_price, entry_cost_meta = self._adjusted_fill_price(
            symbol, direction, is_entry=True, raw_price=price, notional=notional, ts=timestamp,
        )
        price_shift = fill_price / price if price else 1.0
        stop_loss *= price_shift
        take_profit *= price_shift
        quantity = notional / fill_price

        trade = BacktestTrade(
            symbol=symbol,
            direction=direction,
            entry_price=fill_price,
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
            atr_pct=atr_pct,
            slippage_cost=notional * entry_cost_meta["slippage_pct"],
            trail_price=fill_price,
        )
        self.cash -= combined_notional
        self.open_trades.append(trade)
        return trade

    def _r_unit(self, trade: BacktestTrade) -> float:
        """Mirrors PaperTrader._r_unit exactly."""
        if trade.atr_pct:
            return trade.entry_price * config.ATR_STOP_MULT * float(trade.atr_pct) / 100.0
        return trade.entry_price * config.STOP_LOSS_PCT

    def _maybe_partial_tp(self, trade: BacktestTrade, price: float, timestamp: int) -> None:
        """Mirrors PaperTrader._maybe_partial_tp exactly -- half off at N R
        through the same cost machinery, remainder keeps running."""
        if config.PARTIAL_TP_AT_R <= 0 or trade.hedge_symbol or trade.partial_done:
            return
        level = exits.partial_tp_level(
            trade.direction, trade.entry_price, self._r_unit(trade), config.PARTIAL_TP_AT_R
        )
        if level is None:
            return
        is_long = trade.direction == SignalDirection.LONG.value
        if (is_long and price < level) or (not is_long and price > level):
            return

        half_qty = trade.quantity / 2.0
        half_notional = half_qty * trade.entry_price
        fill_price, _meta = self._adjusted_fill_price(
            trade.symbol, trade.direction, is_entry=False, raw_price=price,
            notional=half_notional, ts=timestamp, reason="partial_tp",
            atr_pct=trade.atr_pct,
        )
        pnl_per_unit = (fill_price - trade.entry_price) if is_long else (trade.entry_price - fill_price)
        fees = half_notional * config.round_trip_cost_pct()
        partial_pnl = pnl_per_unit * half_qty - fees

        partial = BacktestTrade(
            symbol=trade.symbol, direction=trade.direction,
            entry_price=trade.entry_price, quantity=half_qty,
            stop_loss=trade.stop_loss, take_profit=trade.take_profit,
            opened_at=trade.opened_at, strategy=trade.strategy,
            metadata=trade.metadata, exit_price=fill_price, closed_at=timestamp,
            pnl=partial_pnl, exit_reason="partial_tp", fees=fees,
            atr_pct=trade.atr_pct, trail_price=trade.trail_price,
        )
        self.closed_trades.append(partial)
        trade.quantity = half_qty
        trade.partial_done = True
        self.cash += half_notional + partial_pnl

    def check_exits(self, prices: dict[str, float], timestamp: int) -> None:
        still_open = []
        for trade in self.open_trades:
            price = prices.get(trade.symbol)
            # Trailing ratchet BEFORE the exit check, same order as live's
            # check_open_trades_for_symbol (_update_trailing_stop then
            # _check_exit), through the same shared function.
            if price is not None and config.TRAILING_STOP_ENABLED:
                updates = exits.trailing_stop_update(
                    trade.direction, trade.entry_price, price,
                    trade.trail_price or trade.entry_price, trade.stop_loss,
                    trade.atr_pct, trail_atr_mult=config.TRAIL_ATR_MULT,
                    trail_arm_atr=config.TRAIL_ARM_ATR,
                )
                if "trail_price" in updates:
                    trade.trail_price = updates["trail_price"]
                if "stop_loss" in updates:
                    trade.stop_loss = updates["stop_loss"]
            if price is not None:
                self._maybe_partial_tp(trade, price, timestamp)

            # funding_carry delta-neutral guard (raise), preserved from
            # _exit_reason.
            if (trade.strategy == "funding_carry"
                    and (trade.metadata or {}).get("execution_mode") == "delta_neutral"):
                raise FakeDeltaNeutralError(f"trade symbol={trade.symbol} opened_at={trade.opened_at}")

            # INTRA-BAR stop/TP via the shared exits.evaluate_stop_exit: uses
            # this bar's high/low, not just the close, so a touch the close
            # recovered from is no longer missed (the bias that made backtests
            # look better than reality). Lookahead-safe: run() calls
            # check_exits BEFORE entries each timestep, so a trade opened at
            # ts is first checked at ts+1 against a bar entirely after entry.
            # Falls back to a degenerate one-tick bar (= the old point check,
            # = live's tick) when no stored bar exists, which keeps the
            # cross-engine parity tests exact.
            exit_result = None
            if price is not None:
                bar = self._current_bar(trade.symbol, timestamp)
                o, h, l, c = bar if bar else (price, price, price, price)
                exit_result = exits.evaluate_stop_exit(
                    trade.direction, trade.stop_loss, trade.take_profit, o, h, l, c
                )
            if exit_result is not None:
                reason, fill_price = exit_result
                self.close_trade(trade, fill_price, timestamp, reason, prices=prices)
                continue

            time_reason = self._time_stop_reason(trade, timestamp)
            if time_reason:
                close_price = price if price is not None else trade.entry_price
                self.close_trade(trade, close_price, timestamp, time_reason, prices=prices)
            else:
                still_open.append(trade)
        self.open_trades = still_open

    def _current_bar(self, symbol: str, timestamp: int) -> tuple[float, float, float, float] | None:
        """OHLC of the 1h bar containing `timestamp` (for intra-bar stop
        evaluation). None when no storage or no bar. DATA CONSTRAINT: this is
        1h-bar granularity -- the honest ceiling, since no 1-min history
        exists; it catches within-the-hour touches via high/low but cannot
        resolve sub-hour order (stop-vs-target within one bar defaults to
        stop-first, conservative)."""
        if self.storage is None:
            return None
        bucket = (timestamp // 3600) * 3600
        rows = self.storage.get_prices(symbol, limit=1, since=bucket, before=bucket + 1, timeframe="1h")
        if not rows:
            return None
        r = rows[-1]
        if float(r["close"]) <= 0:
            return None
        return float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])

    def _time_stop_reason(self, trade: BacktestTrade, timestamp: int) -> str | None:
        """The max-hold time_stop, split out of _exit_reason so the intra-bar
        stop/TP path (exits.evaluate_stop_exit) owns price triggers and this
        owns the horizon cap."""
        max_hours = config.max_hold_hours_for_style((trade.metadata or {}).get("style"))
        if max_hours > 0 and (timestamp - trade.opened_at) / 3600.0 >= max_hours:
            return "time_stop"
        return None

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
        fees = trade.notional * config.round_trip_cost_pct()
        # `price` here is already the observed market price at this timestamp
        # (the hourly close BacktestEngine passed in) -- this only adds
        # simulated execution slippage on top of it, mirroring
        # PaperTrader._close_trade exactly.
        fill_price, exit_cost_meta = self._adjusted_fill_price(
            trade.symbol, trade.direction, is_entry=False, raw_price=price,
            notional=trade.notional, ts=timestamp, reason=reason, atr_pct=trade.atr_pct,
        )
        primary_pnl = self._realized_pnl(trade, fill_price, timestamp)
        trade.slippage_cost += trade.notional * exit_cost_meta["slippage_pct"]

        hedge_notional, hedge_fields = self._close_hedge_leg(trade, timestamp, prices or {})
        pnl = hedge_engine.combined_trade_pnl(
            primary_pnl,
            fees,
            hedge_fields.get("hedge_pnl", 0.0),
            hedge_fields.get("hedge_fees", 0.0),
            hedge_fields.get("funding_pnl", 0.0),
        )

        trade.fees = fees
        trade.exit_price = fill_price
        trade.closed_at = timestamp
        trade.pnl = pnl
        trade.exit_reason = reason
        trade.mfe_pct, trade.mfe_capture = exits.mfe_capture(
            trade.direction, trade.entry_price, fill_price, trade.trail_price,
            atr_pct=trade.atr_pct,
        )
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
# The other strategies still test via generate_signal(); this does not
# change how they're validated or re-open the backtest<->evaluate_symbol
# path-divergence question in general (see research_decisions:
# backtest_evaluate_symbol_realignment_parked).
#
# Maps each member to the config flag that gates its OWN setup inside
# evaluate_symbol() -- excluded from the force-off list below when THAT
# strategy is the one under test, so isolation is correct no matter which
# of these is being validated (not just whichever came first).
_EVALUATION_ENGINE_STRATEGY_OWN_FLAG = {
    "rel_strength_rotation": "SETUP_REL_STRENGTH_ENABLED",
    "capitulation_bar": "SETUP_CAPITULATION_BAR_ENABLED",
    "volume_zscore_3plus": "SETUP_VOLUME_ZSCORE_ENABLED",
    "pump24_extreme": "SETUP_PUMP24_EXTREME_ENABLED",
    "failed_pump_long": "SETUP_FAILED_PUMP_LONG_ENABLED",
}
EVALUATION_ENGINE_STRATEGIES = frozenset(_EVALUATION_ENGINE_STRATEGY_OWN_FLAG)

# All config-attr-gated setup toggles inside evaluate_symbol() that CAN be
# isolated this way. Forcing every one of these off except the strategy
# under test's own flag is what makes evaluate_symbol's only possible
# verdict the one setup being tested -- otherwise a walk-forward run would
# be measuring the whole confluence ensemble, not the setup alone. Every
# entry here gates on a real config.py module attribute; SETUP_FAILED_PUMP_
# ENABLED has none -- _failed_pump_short_setup reads os.getenv directly
# every call (see evaluation.py's _env_bool_dynamic), so it only needs the
# env var forced, not a config attr (kept in the separate env-only tuple).
_ALL_ISOLATABLE_SETUP_FLAGS = (
    "SETUP_MEANREV_ENABLED", "SETUP_BREAKOUT_ENABLED", "SETUP_TSMOM_ENABLED",
    "SETUP_VOLUME_ENABLED", "SETUP_FUNDING_ENABLED", "SETUP_SWING_ENABLED",
    *_EVALUATION_ENGINE_STRATEGY_OWN_FLAG.values(),
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

    @staticmethod
    def _exec_direction(direction_value: str) -> str:
        """DIAGNOSTIC (config.INVERT_SIGNAL_DIRECTION): flip the direction at
        EXECUTION only, so the inverted-arm backtest opens the opposite side
        at the SAME entry points the original signals selected -- entry
        gates (confidence, category, tradable) still see the real signal and
        select identically. Flipping earlier would let the confidence gate
        re-reject the flipped direction, which tests a different question
        ('does the opposite setup fire') and produced zero trades. Sizing,
        stops, targets, and the hedge leg all derive from the value returned
        here, so they flip consistently. No-op unless the flag is set."""
        if not config.INVERT_SIGNAL_DIRECTION:
            return direction_value
        if direction_value == SignalDirection.LONG.value:
            return SignalDirection.SHORT.value
        if direction_value == SignalDirection.SHORT.value:
            return SignalDirection.LONG.value
        return direction_value

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

        own_flag = _EVALUATION_ENGINE_STRATEGY_OWN_FLAG[self.strategy_name]
        other_flags = tuple(f for f in _ALL_ISOLATABLE_SETUP_FLAGS if f != own_flag)
        all_flags = other_flags + _OTHER_LIVE_SETUP_ENV_ONLY_FLAGS
        saved_attr = {name: getattr(config, name) for name in other_flags}
        saved_env = {name: os.environ.get(name) for name in all_flags}
        try:
            for name in other_flags:
                setattr(config, name, False)
            for name in all_flags:
                os.environ[name] = "false"
            result = evaluate_symbol(
                snapshot, symbol, btc_rows, regime=regime, calibration={}, category=category
            )
        finally:
            for name in other_flags:
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
                        self._exec_direction(signal.direction.value),
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
                        self._exec_direction(signal.direction.value),
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
                    "exit_reason": getattr(t, "exit_reason", None),
                    "atr_pct": getattr(t, "atr_pct", None),
                    "mfe_pct": getattr(t, "mfe_pct", None),
                    "mfe_capture": getattr(t, "mfe_capture", None),
                    "pnl": round(float(t.pnl or 0.0), 6),
                    "fees": round(float(getattr(t, "fees", 0.0) or 0.0), 6),
                    "slippage_cost": round(float(getattr(t, "slippage_cost", 0.0) or 0.0), 6),
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
