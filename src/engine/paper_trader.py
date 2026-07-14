"""Paper trading engine — simulates positions on Binance Testnet signals."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
import json
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine import entry_filters
from src.engine import execution_cost
from src.engine import exits
from src.engine import hedge as hedge_engine
from src.engine import risk_budget
from src.engine import indicators
from src.strategies.base import SignalDirection

logger = logging.getLogger(__name__)

# NOTE: round_trip_cost_pct() is now dynamic-in-spirit (fee-only, but the
# function itself could change with FEE_MODE) and slippage is no longer
# folded into it at all -- it must be called fresh at each fee computation
# site, never cached into a module constant (that was the exact silent-
# divergence trap: a stale constant captured at import time would never see
# a runtime FEE_MODE change, and would have been actively wrong once
# slippage moved to the price instead of the fee). Every call site below
# calls config.round_trip_cost_pct() directly.


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
        # (symbol, day-bucket) -> ex-ante beta, for the capital gate; betas
        # move slowly, one estimate per symbol per day is plenty.
        self._beta_cache: dict[tuple[str, int], float | None] = {}

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
            self._maybe_partial_tp(trade, current_price)
            exit_reason = self._check_exit(trade, current_price)
            if exit_reason:
                closed.append(self._close_trade(trade, current_price, exit_reason))
        return closed

    def _r_unit(self, trade: dict[str, Any]) -> float:
        """R = the trade's initial stop distance in price terms, reconstructed
        from the ATR stored at open (same formula _exit_levels used), so no
        schema change is needed. Falls back to the fixed stop pct when the
        trade had no ATR."""
        entry = float(trade["entry_price"])
        atr = trade.get("atr_pct")
        if atr:
            return entry * config.ATR_STOP_MULT * float(atr) / 100.0
        return entry * config.STOP_LOSS_PCT

    def _already_partialed(self, trade: dict[str, Any]) -> bool:
        """A partial close creates a closed 'partial_tp' sibling row sharing
        opened_at -- its presence marks the trade as already halved."""
        for t in self.storage.get_all_trades_for_symbol(trade["symbol"]):
            if (t.get("exit_reason") == "partial_tp"
                    and int(t.get("opened_at") or 0) == int(trade["opened_at"])):
                return True
        return False

    def _maybe_partial_tp(self, trade: dict[str, Any], price: float) -> None:
        """PARTIAL_TP_AT_R: at N R-multiples in favor, close HALF through the
        normal cost machinery and let the remainder run with the trail. Skips
        hedged trades (splitting a hedge pair is out of scope). 0 = off."""
        if config.PARTIAL_TP_AT_R <= 0 or trade.get("hedge_symbol"):
            return
        level = exits.partial_tp_level(
            trade["direction"], float(trade["entry_price"]),
            self._r_unit(trade), config.PARTIAL_TP_AT_R,
        )
        if level is None:
            return
        is_long = trade["direction"] == SignalDirection.LONG.value
        if (is_long and price < level) or (not is_long and price > level):
            return
        if self._already_partialed(trade):
            return

        half_qty = float(trade["quantity"]) / 2.0
        entry = float(trade["entry_price"])
        half_notional = half_qty * entry
        now_ts = int(datetime.now(timezone.utc).timestamp())
        fill_price, _meta = self._adjusted_fill_price(
            trade["symbol"], trade["direction"], is_entry=False, raw_price=price,
            notional=half_notional, ts=now_ts, reason="partial_tp",
            atr_pct=trade.get("atr_pct"),
        )
        pnl_per_unit = (fill_price - entry) if is_long else (entry - fill_price)
        fees = half_notional * config.round_trip_cost_pct()
        partial_pnl = pnl_per_unit * half_qty - fees

        partial_row = {
            "signal_id": trade.get("signal_id"), "symbol": trade["symbol"],
            "direction": trade["direction"], "entry_price": entry,
            "exit_price": fill_price, "quantity": half_qty,
            "stop_loss": float(trade["stop_loss"]), "take_profit": float(trade["take_profit"]),
            "status": "closed", "pnl": partial_pnl, "opened_at": int(trade["opened_at"]),
            "closed_at": now_ts, "strategy": trade.get("strategy"),
            "style": trade.get("style"), "atr_pct": trade.get("atr_pct"),
            "trail_price": trade.get("trail_price"), "exit_reason": "partial_tp",
            "fees": fees,
        }
        self.storage.insert_paper_trade(partial_row)
        self.storage.update_paper_trade(int(trade["id"]), {"quantity": half_qty})
        trade["quantity"] = half_qty

        state = self.storage.get_portfolio_state()
        cash = float(state["cash"]) if state else 0.0
        self.storage.update_portfolio_cash(cash + half_notional + partial_pnl)
        logger.info(
            "Partial TP id=%s %s half=%.6f @ %.6f pnl=%.4f (%.1fR reached)",
            trade["id"], trade["symbol"], half_qty, fill_price, partial_pnl,
            config.PARTIAL_TP_AT_R,
        )

    def _update_trailing_stop(self, trade: dict[str, Any], price: float) -> None:
        """Ratchet the stop behind the best price seen (swing trades).

        The ratchet math lives in src/engine/exits.py -- the SAME function
        BacktestPortfolio.check_exits calls -- so live and backtest can never
        trail differently (rule #2; this was divergence territory until
        2026-07-14: backtest had no trailing at all).
        """
        if not config.TRAILING_STOP_ENABLED:
            return
        updates = exits.trailing_stop_update(
            trade["direction"], float(trade["entry_price"]), price,
            float(trade.get("trail_price") or trade["entry_price"]),
            float(trade["stop_loss"]), trade.get("atr_pct"),
            trail_atr_mult=config.TRAIL_ATR_MULT,
            trail_arm_atr=config.TRAIL_ARM_ATR,
        )
        if "stop_loss" in updates:
            trade["stop_loss"] = updates["stop_loss"]
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

    def _recent_max_range_pct(self, symbol: str, lookback: int = 24) -> float | None:
        """Max (high-low)/close over the last `lookback` 1h bars, as a
        percent -- the 'how violent does a normal bar get here' input to the
        MAX_STOP_GAP_TOLERANCE entry filter. Mirrors BacktestPortfolio."""
        rows = self.storage.get_prices(symbol, limit=lookback, timeframe="1h")
        ranges = [
            (float(r["high"]) - float(r["low"])) / float(r["close"]) * 100.0
            for r in rows if float(r["close"]) > 0
        ]
        return max(ranges) if ranges else None

    def _bar_range_pct(self, symbol: str, ts: int) -> float | None:
        """(high-low)/close of the 1h bar containing `ts` -- used only for
        the stress-bar-range check (execution_cost.is_stress_bar), not for
        sizing. Floors to the hour bucket the same way the rest of this
        codebase does when matching a fill to its bar."""
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
        """The ONE place PaperTrader turns a raw observed price into an
        actual fill price -- slippage moves the PRICE here, never the fee
        (config.round_trip_cost_pct() stays fee-only; see its docstring).
        Mirrors scripts/backtest.py's BacktestPortfolio._adjusted_fill_price
        exactly (same execution_cost calls, same stress condition) so live
        and backtest can never silently diverge on cost.
        """
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

    def _exit_levels(
        self, direction: str, price: float, atr_pct: float | None
    ) -> tuple[float, float]:
        """ATR-scaled stop/target via the shared exits module (same function
        BacktestPortfolio.open_trade calls -- rule #2)."""
        return exits.exit_levels(
            direction, price, atr_pct,
            atr_stop_mult=config.ATR_STOP_MULT, atr_tp_mult=config.ATR_TP_MULT,
            fallback_stop_pct=config.STOP_LOSS_PCT, fallback_tp_pct=config.TAKE_PROFIT_PCT,
        )

    def _position_notional(
        self, portfolio: float, cash: float, price: float, stop_loss: float
    ) -> float:
        """Volatility-inverse sizing via the shared exits module (same
        function BacktestPortfolio.open_trade calls -- rule #2; backtest
        used a flat MAX_POSITION_PCT until 2026-07-14, divergence #4)."""
        return exits.position_notional(
            portfolio, cash, price, stop_loss,
            risk_per_trade_pct=config.RISK_PER_TRADE_PCT,
            max_position_pct=config.MAX_POSITION_PCT,
        )

    def _symbol_in_cooldown(self, symbol: str) -> bool:
        hours = config.COOLDOWN_HOURS_PER_SYMBOL
        if hours <= 0:
            return False
        trades = self.storage.get_all_trades_for_symbol(symbol)
        if not trades:
            return False
        # Cooldown is scoped to the CURRENT book: trades opened before the
        # baseline reset (benchmark_started_at) are archived and must not
        # lock the new book out. Found 2026-07-14: the reset's administrative
        # closes armed 48h cooldowns across 18/20 universe symbols, freezing
        # the fresh book at ~20% deployment. This also restores parity with
        # BacktestPortfolio._symbol_in_cooldown, which only ever sees its own
        # book by construction (every backtest starts empty). The 48h value
        # itself is unchanged and binds fully within the book.
        state = self.storage.get_portfolio_state()
        book_start = int(state["benchmark_started_at"] or 0) if state else 0
        opened_times = [
            int(t["opened_at"]) for t in trades
            if t.get("opened_at") is not None and int(t["opened_at"]) >= book_start
        ]
        if not opened_times:
            return False
        last_opened = max(opened_times)
        age_hours = (datetime.now(timezone.utc).timestamp() - last_opened) / 3600.0
        return age_hours < hours

    def _symbol_beta(self, symbol: str) -> float | None:
        """Ex-ante beta vs BTC over the trailing HEDGE_BETA_LOOKBACK_H, for
        the capital gate. None (insufficient history) is treated as 1.0 by
        risk_budget -- unknown correlation must not read as diversification."""
        if symbol == config.SYMBOL:
            return 1.0
        bucket = int(datetime.now(timezone.utc).timestamp()) // 86400
        key = (symbol, bucket)
        if key not in self._beta_cache:
            limit = config.HEDGE_BETA_LOOKBACK_H + 24
            sym_rows = self.storage.get_prices(symbol, limit=limit, timeframe="1h")
            btc_rows = self.storage.get_prices(config.SYMBOL, limit=limit, timeframe="1h")
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
    ) -> tuple[str, str] | None:
        """Risk-budget + beta-exposure gates (src/engine/risk_budget.py) --
        the binding capital constraints as of 2026-07-14 (MAX_OPEN_POSITIONS
        is only a safety ceiling now). Returns (gate_name, reason) on refusal,
        None when the entry fits. Quality gates are untouched: this runs
        AFTER confidence/regime/category/cooldown, and refusing here means
        idle cash by design, reported under its own funnel counter."""
        if not risk_budget.position_large_enough(notional, equity):
            return ("position_too_small",
                    f"notional {notional:.2f} < {risk_budget.MIN_POSITION_NOTIONAL_PCT:.0%} "
                    f"of equity {equity:.2f} (dust; cash nearly exhausted)")
        quantity = notional / price if price else 0.0
        cand_risk = risk_budget.position_risk(direction, price, stop_loss, quantity)
        open_risk = 0.0
        legs: list[dict[str, Any]] = []
        for t in self.storage.get_open_trades():
            row = self.storage.get_latest_price(t["symbol"])
            cur = float(row["close"]) if row else float(t["entry_price"])
            open_risk += risk_budget.position_risk(
                t["direction"], cur, float(t["stop_loss"]), float(t["quantity"])
            )
            legs.append({"direction": t["direction"], "notional": float(t["quantity"]) * cur,
                         "beta": self._symbol_beta(t["symbol"])})
            if t.get("hedge_symbol"):
                legs.append({"direction": t["hedge_direction"],
                             "notional": float(t["hedge_quantity"]) * float(t["hedge_entry_price"]),
                             "beta": self._symbol_beta(t["hedge_symbol"])})

        if not risk_budget.risk_budget_allows(open_risk, cand_risk, equity, config.TOTAL_RISK_BUDGET_PCT):
            return ("risk_budget",
                    f"risk budget: open {open_risk:.2f} + candidate {cand_risk:.2f} "
                    f"> {config.TOTAL_RISK_BUDGET_PCT:.0%} of equity {equity:.2f}")

        # Candidate contributes ALL its legs at once: a market-neutral entry's
        # hedge leg largely cancels its primary leg's beta, and the gate must
        # see that, not refuse the primary in isolation.
        cand_legs = [{"direction": direction, "notional": notional, "beta": self._symbol_beta(symbol)}]
        if hedge_leg is not None:
            cand_legs.append({"direction": hedge_leg["hedge_direction"],
                              "notional": hedge_leg["hedge_notional"],
                              "beta": self._symbol_beta(hedge_leg["hedge_symbol"])})
        if config.MAX_NET_BETA_EXPOSURE < risk_budget.BETA_UNLIMITED:
            net = risk_budget.net_beta_exposure([*legs, *cand_legs], equity)
            if abs(net) > config.MAX_NET_BETA_EXPOSURE:
                return ("beta_exposure",
                        f"net beta exposure {net:+.2f} exceeds cap {config.MAX_NET_BETA_EXPOSURE:g}")
        return None

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

        # Volatility entry filters (shared with backtest): refuse names too
        # violent to stop out of, BEFORE sizing. Quality gates already passed
        # upstream; this is a capital-preservation filter, so a refusal here
        # is idle-cash-by-design, reported under its own funnel counter.
        stop_dist_pct = abs(current_price - stop_loss) / current_price * 100.0 if current_price else 0.0
        vol_refusal = entry_filters.volatility_entry_block(
            atr, self._recent_max_range_pct(symbol), stop_dist_pct,
            max_entry_atr_pct=config.MAX_ENTRY_ATR_PCT,
            max_stop_gap_tolerance=config.MAX_STOP_GAP_TOLERANCE,
        )
        if vol_refusal is not None:
            gate, reason = vol_refusal
            return {"opened": False, "reason": reason, "gate": gate}

        hedge_leg = self._hedge_leg_for_open(signal_result, direction, portfolio, cash, current_price)
        if hedge_leg is not None:
            notional = hedge_leg["primary_notional"]
        else:
            notional = self._position_notional(portfolio, cash, current_price, stop_loss)
        if notional <= 0:
            return {"opened": False, "reason": "no cash / zero position size"}

        capital_refusal = self._capital_gate(
            symbol, direction, current_price, stop_loss, notional, portfolio, hedge_leg
        )
        if capital_refusal is not None:
            gate, reason = capital_refusal
            return {"opened": False, "reason": reason, "gate": gate}

        now_ts = int(datetime.now(timezone.utc).timestamp())

        # Slippage moves the FILL price away from the raw signal price;
        # stop/take-profit keep the SAME percentage distance, just re-anchored
        # to the actual fill (the trader's true cost basis) instead of the
        # raw price they were originally computed from.
        fill_price, cost_meta = self._adjusted_fill_price(
            symbol, direction, is_entry=True, raw_price=current_price,
            notional=notional, ts=now_ts,
        )
        price_shift = fill_price / current_price if current_price else 1.0
        stop_loss *= price_shift
        take_profit *= price_shift
        quantity = notional / fill_price

        trade_row = {
            "signal_id": signal_result.get("signal_id"),
            "symbol": symbol,
            "direction": direction,
            "entry_price": fill_price,
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
            "trail_price": fill_price,
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
            "Paper trade opened id=%s %s %s qty=%.6f @ %.6f (raw %.6f, slip %.4f%%%s)%s",
            trade_id,
            symbol,
            direction,
            quantity,
            fill_price,
            current_price,
            cost_meta["slippage_pct"] * 100,
            " fallback" if cost_meta["used_fallback"] else "",
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
            "entry_price": fill_price,
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
        hedge_fees = hedge_engine.leg_fees(notional, config.round_trip_cost_pct())

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
        fees = notional * config.round_trip_cost_pct()

        now_ts = int(datetime.now(timezone.utc).timestamp())
        # A stop must fill at the actual (possibly gapped) market price, not
        # the nominal stop level -- current_price already IS the observed
        # market price (see check_open_trades_for_symbol), so this only adds
        # simulated execution slippage on top of an already-real price; it
        # does not synthesize a "gap" that wasn't already in current_price.
        fill_price, cost_meta = self._adjusted_fill_price(
            trade["symbol"], trade["direction"], is_entry=False, raw_price=current_price,
            notional=notional, ts=now_ts, reason=reason, atr_pct=trade.get("atr_pct"),
        )
        primary_pnl = self._realized_pnl(trade, fill_price)

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
            "exit_price": fill_price,
            "status": "closed",
            "pnl": pnl,
            "closed_at": now_ts,
            "exit_reason": reason,
            "fees": fees,
            **hedge_fields,
        }
        self.storage.update_paper_trade(int(trade["id"]), update_fields)
        logger.info(
            "Paper trade closed id=%s pnl=%.2f reason=%s fill=%.6f (raw %.6f, slip %.4f%%%s%s)%s",
            trade["id"],
            pnl,
            reason,
            fill_price,
            current_price,
            cost_meta["slippage_pct"] * 100,
            " stress" if cost_meta["is_stress"] else "",
            " fallback" if cost_meta["used_fallback"] else "",
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
