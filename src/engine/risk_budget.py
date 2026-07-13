"""Capital-constraint gates -- deployed risk and beta exposure, shared by
both engines (rule #2). Replaces MAX_OPEN_POSITIONS as the binding
constraint (2026-07-14): position COUNT was an arbitrary proxy; what needs
controlling is total risk and directional concentration. MAX_OPEN_POSITIONS
survives only as a high safety ceiling against runaway bugs.

Two gates, both applied at entry time in PaperTrader.process_signal and
BacktestPortfolio.open_trade:

  1. TOTAL RISK BUDGET: sum over open positions of dollars-at-risk-to-stop,
     plus the candidate's, must stay within TOTAL_RISK_BUDGET_PCT of equity.
     Risk uses the CURRENT stop (trailing ratchets free budget as stops
     tighten -- a position locked above entry contributes zero risk).
  2. NET BETA EXPOSURE: |sum of direction-signed, beta-weighted notional /
     equity| must stay within MAX_NET_BETA_EXPOSURE. This is the real
     diversification constraint -- measured effective breadth was 1.89,
     i.e. "five alt positions" collapse into ~two independent bets, mostly
     one leveraged BTC bet. A cap of 999+ means unlimited.

These constrain CAPITAL, not QUALITY: confidence/regime/category/cooldown
gates are untouched. If only two candidates pass quality, the book holds
two positions and idle cash -- correct, and the entry funnel says so with
named counters (blocked_risk_budget / blocked_beta_exposure).
"""

from __future__ import annotations

from typing import Any

BETA_UNLIMITED = 999.0

# A position smaller than this fraction of equity is dust: it can't move
# the book, but it consumes a cooldown, fees, and attention. Discovered via
# the capital-gate parity test (2026-07-14): with cash nearly exhausted,
# position_notional caps at remaining cash and produced a $0.34 "position"
# that slipped under the risk gate in one engine and float-noise-refused in
# the other. A NAMED floor -- visible in the entry funnel, never a silent
# rejection -- is the honest fix.
MIN_POSITION_NOTIONAL_PCT = 0.01


def position_large_enough(notional: float, equity: float) -> bool:
    return notional >= MIN_POSITION_NOTIONAL_PCT * equity


def position_risk(direction: str, price: float, stop_loss: float, quantity: float) -> float:
    """Dollars lost if the stop fills from here. Direction-aware and floored
    at zero: a ratcheted stop on the profitable side of price means this
    position can no longer lose -- it consumes no risk budget."""
    if direction == "LONG":
        per_unit = price - stop_loss
    else:
        per_unit = stop_loss - price
    return max(0.0, per_unit) * quantity


def risk_budget_allows(
    open_risk_total: float,
    candidate_risk: float,
    equity: float,
    budget_pct: float,
) -> bool:
    if equity <= 0:
        return False
    return (open_risk_total + candidate_risk) <= budget_pct * equity


def net_beta_exposure(legs: list[dict[str, Any]], equity: float) -> float:
    """Direction-signed, beta-weighted notional over equity.
    legs: [{"direction": "LONG"|"SHORT", "notional": float, "beta": float|None}].
    A missing beta conservatively counts as 1.0 (fully market-correlated) --
    an unknown correlation must not read as diversification."""
    if equity <= 0:
        return 0.0
    total = 0.0
    for leg in legs:
        beta = leg.get("beta")
        beta = 1.0 if beta is None else float(beta)
        sign = 1.0 if leg["direction"] == "LONG" else -1.0
        total += sign * float(leg["notional"]) * beta
    return total / equity


def beta_exposure_allows(
    open_legs: list[dict[str, Any]],
    candidate_leg: dict[str, Any],
    equity: float,
    cap: float,
) -> bool:
    if cap >= BETA_UNLIMITED:
        return True
    return abs(net_beta_exposure([*open_legs, candidate_leg], equity)) <= cap
