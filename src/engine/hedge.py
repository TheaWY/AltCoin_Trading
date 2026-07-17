"""Shared market-neutral hedge math.

The ONE place both PaperTrader (live) and BacktestPortfolio (backtest)
compute hedge sizing, leg P&L, funding P&L, and realized basis risk, so the
two paths cannot silently diverge (see research_decisions,
subject='rel_strength_market_neutral').

The hedge leg has no independent exit condition -- callers close it in the
same call that closes the primary leg, for any exit reason. No function here
performs I/O; callers own fetching price/funding history so this stays
trivially testable and usable from both a live Storage and a point-in-time
backtest snapshot.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.engine import indicators
from src.strategies.base import SignalDirection

SETTLEMENT_SECONDS = 8 * 3600


def hedge_direction_for(primary_direction: str) -> str:
    """The hedge leg always trades opposite the primary leg."""
    return (
        SignalDirection.SHORT.value
        if primary_direction == SignalDirection.LONG.value
        else SignalDirection.LONG.value
    )


def ex_ante_beta(
    primary_rows: Sequence[dict[str, Any]],
    hedge_rows: Sequence[dict[str, Any]],
    lookback: int,
    min_points: int,
) -> float | None:
    """Point-in-time OLS beta of the primary symbol vs the hedge symbol over
    the trailing `lookback` candles. Caller must pass rows that already
    exclude anything at/after the signal timestamp (no lookahead). A
    non-positive beta means the hedge direction assumption (short BTC
    against a long alt) doesn't hold for this symbol right now -- callers
    must treat None as "no trade", not fall back to a default beta.
    """
    result = indicators.correlation_and_beta(
        primary_rows[-lookback:], hedge_rows[-lookback:], min_points=min_points
    )
    beta = result["beta"]
    return beta if beta is not None and beta > 0 else None


def position_caps(
    portfolio_value: float, max_position_pct: float, beta: float
) -> tuple[float, float]:
    """Split one MAX_POSITION_PCT slot between primary and hedge notional so
    the COMBINED exposure (not just the primary leg) respects the existing
    per-slot cap: primary + hedge = portfolio_value * max_position_pct.
    """
    primary_cap = portfolio_value * max_position_pct / (1.0 + beta)
    hedge_cap = primary_cap * beta
    return primary_cap, hedge_cap


def delta_neutral_hedge_leg(
    symbol: str,
    primary_direction: str,
    spot_price: float | None,
    perp_price: float | None,
    cash: float,
    portfolio_value: float,
    max_position_pct: float,
) -> dict[str, Any] | None:
    """Sizing for the delta-neutral funding-carry pair (short perp + long spot,
    SAME symbol). Unlike the market-neutral hedge (alt vs BTC, ex-ante beta), the
    two legs are the SAME asset so beta=1 and the notionals are EQUAL (dollar +
    delta neutral). The hedge leg is priced off the PERP series -- its funding
    accrual (funding_pnl_for_leg on a SHORT) IS the carry. Returns a hedge-leg
    dict with the SAME shape the market-neutral path produces (so open/close
    machinery is unchanged), or None if it can't be sized.

    Convention: primary leg = LONG SPOT (priced off '1h', the natural feed),
    hedge leg = SHORT PERP (priced off '1h_perp'). Net exposure ~0; P&L =
    N*(spot_ret - perp_ret) + funding - fees (see test_delta_neutral_carry)."""
    if not perp_price or perp_price <= 0 or not spot_price or spot_price <= 0:
        return None
    primary_cap, hedge_cap = position_caps(portfolio_value, max_position_pct, 1.0)
    available = min(primary_cap + hedge_cap, cash)
    leg_notional = available / 2.0
    if leg_notional <= 0:
        return None
    return {
        "hedge_symbol": symbol,
        "hedge_direction": hedge_direction_for(primary_direction),
        "hedge_entry_price": float(perp_price),
        "hedge_quantity": leg_notional / float(perp_price),
        "hedge_beta": 1.0,
        "primary_notional": leg_notional,
        "hedge_notional": leg_notional,
    }


def leg_pnl(direction: str, entry_price: float, exit_price: float, quantity: float) -> float:
    if direction == SignalDirection.LONG.value:
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity


def leg_position_value(direction: str, entry_price: float, price: float, quantity: float) -> float:
    """Cash-basis mark: notional locked at open, plus/minus running P&L --
    mirrors how this codebase has always marked SHORT legs (margin reserved,
    not a literal borrowed-share count)."""
    if direction == SignalDirection.LONG.value:
        return quantity * price
    notional = entry_price * quantity
    return notional + leg_pnl(direction, entry_price, price, quantity)


def leg_fees(notional: float, round_trip_cost_pct: float) -> float:
    return notional * round_trip_cost_pct


def funding_pnl_for_leg(
    direction: str,
    funding_rows: Sequence[dict[str, Any]],
    notional: float,
    entry_ts: int,
    exit_ts: int,
) -> float:
    """Net funding accrued on one leg between entry and exit (exclusive).

    Binance convention: funding_rate > 0 means longs pay shorts, so a SHORT
    RECEIVES rate*notional per settlement and a LONG PAYS it. funding_rows is
    collected at collection frequency (every cycle, not every settlement),
    so rows are bucketed into 8h settlement windows and only the last print
    per window -- the actual settlement rate -- counts.
    """
    sign = 1.0 if direction == SignalDirection.SHORT.value else -1.0
    buckets: dict[int, float] = {}
    for row in funding_rows:
        ts = int(row["timestamp"])
        if ts < entry_ts or ts >= exit_ts:
            continue
        bucket = ts // SETTLEMENT_SECONDS
        buckets[bucket] = float(row["funding_rate"])
    return sum(sign * rate * notional for rate in buckets.values())


def realized_beta_and_correlation(
    primary_rows: Sequence[dict[str, Any]],
    hedge_rows: Sequence[dict[str, Any]],
    entry_ts: int,
    exit_ts: int,
    min_points: int,
) -> dict[str, float | None]:
    """Realized beta/correlation over the ACTUAL holding window. Compared
    against the ex-ante beta used for sizing, this is the measured basis
    risk (research_decisions, subject='rel_strength_market_neutral',
    addition 3): did the alt actually move with BTC the way the hedge
    assumed it would, or did the relationship break down mid-trade?
    """
    window_primary = [r for r in primary_rows if entry_ts <= int(r["timestamp"]) <= exit_ts]
    window_hedge = [r for r in hedge_rows if entry_ts <= int(r["timestamp"]) <= exit_ts]
    return indicators.correlation_and_beta(window_primary, window_hedge, min_points=min_points)


def basis_risk_status(
    ex_ante: float | None, realized_beta: float | None, tolerance: float
) -> str:
    """'correlation_held' if realized beta stayed within `tolerance` relative
    deviation of the ex-ante beta used for sizing; 'correlation_spiked'
    otherwise; 'unknown' when there isn't enough data to tell either way.
    """
    if ex_ante is None or realized_beta is None or ex_ante == 0:
        return "unknown"
    relative_deviation = abs(realized_beta - ex_ante) / abs(ex_ante)
    return "correlation_held" if relative_deviation <= tolerance else "correlation_spiked"


def combined_trade_pnl(
    primary_pnl: float,
    primary_fees: float,
    hedge_pnl: float,
    hedge_fees: float,
    funding_pnl: float,
) -> float:
    """Single definition of 'what did this trade actually make', used
    identically by both paths so net-of-funding expectancy can never be
    computed two different ways.
    """
    return (primary_pnl - primary_fees) + (hedge_pnl - hedge_fees) + funding_pnl
