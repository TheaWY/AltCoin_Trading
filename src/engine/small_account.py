"""Small-account execution economics for paper-only strategy filters.

The research target account is 1,000,000 KRW / about 730 USDT. At this size,
fees, slippage, and exchange minimum notional dominate weak theoretical edges.
These helpers are intentionally conservative and are used by paper strategies
before emitting actionable signals.
"""

from __future__ import annotations

from dataclasses import dataclass
import os

from src import config


@dataclass(frozen=True)
class CostCheck:
    ok: bool
    notional: float
    expected_edge_pct: float
    round_trip_cost_pct: float
    expected_net_pct: float
    expected_net_usd: float
    reason: str


def min_notional() -> float:
    return float(getattr(config, "MIN_ORDER_NOTIONAL_USDT", 5.0))


def account_capital() -> float:
    """Capital used by cost gates.

    The legacy app default is 10,000. For research strategy filtering, default to
    the paper's target size (730 USDT) unless PAPER_STARTING_CAPITAL is explicitly
    provided by the environment.
    """
    if os.getenv("PAPER_STARTING_CAPITAL") is None:
        return 730.0
    return float(getattr(config, "PAPER_STARTING_CAPITAL", 730.0))


def max_strategy_notional(default_pct: float = 0.20) -> float:
    cap_pct = float(getattr(config, "MAX_POSITION_PCT", default_pct))
    return account_capital() * cap_pct


def round_trip_cost_pct(*, maker: bool = False, spot_plus_futures: bool = False) -> float:
    """Return decimal cost, e.g. 0.003 = 0.30%.

    - spot_plus_futures=True is for cash-and-carry entry+exit.
    - maker=True uses a lower paper fee assumption for grid-like orders.
    """
    if spot_plus_futures:
        return float(getattr(config, "CARRY_FEE_ROUNDTRIP", 0.003))
    fee = float(getattr(config, "FEE_PCT_PER_SIDE", 0.0005))
    slippage = 0.0 if maker else float(getattr(config, "SLIPPAGE_PCT_PER_SIDE", 0.0003))
    return 2 * (fee + slippage)


def check_edge(
    *,
    expected_edge_pct: float,
    notional: float | None = None,
    maker: bool = False,
    spot_plus_futures: bool = False,
    min_net_usd: float | None = None,
) -> CostCheck:
    """Check whether an edge clears costs for a small account.

    expected_edge_pct is decimal return on notional, e.g. 0.006 = 0.60%.
    """
    notional = float(notional if notional is not None else max_strategy_notional())
    min_net_usd = float(min_net_usd if min_net_usd is not None else getattr(config, "MIN_EXPECTED_NET_USD", 0.25))
    cost = round_trip_cost_pct(maker=maker, spot_plus_futures=spot_plus_futures)
    net_pct = expected_edge_pct - cost
    net_usd = notional * net_pct

    if notional < min_notional():
        return CostCheck(False, notional, expected_edge_pct, cost, net_pct, net_usd, f"notional ${notional:.2f} below min ${min_notional():.2f}")
    if net_usd < min_net_usd:
        return CostCheck(False, notional, expected_edge_pct, cost, net_pct, net_usd, f"expected net ${net_usd:.2f} below ${min_net_usd:.2f} after costs")
    return CostCheck(True, notional, expected_edge_pct, cost, net_pct, net_usd, f"expected net ${net_usd:.2f} after {cost * 100:.2f}% round-trip cost")
