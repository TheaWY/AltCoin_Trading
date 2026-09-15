"""Perpetual funding accrual — one implementation for backtest, paper and live.

Every perpetual position pays or receives funding every settlement interval it
is held through, not just the delta-neutral carry trade. Before this module the
accrual was implemented twice (paper_trader._carry_pnl and the backtest's
_realized_pnl) and applied only when strategy == "funding_carry", so a
directional position held up to SWING_MAX_HOLD_HOURS (720h = 30 days) accrued
nothing. At 0.01% per 8h that is ~0.9%/month of notional; in a hot market at
0.1% it is ~9%/month. The omission always biased results optimistically.

Sign convention (Binance): funding_rate > 0 means longs pay shorts.

    long  position PnL = -sum(rates) * notional
    short position PnL = +sum(rates) * notional

Only settlements strictly after the entry bucket count: the settlement the
position was opened inside was already paid by whoever held it at that stamp.
Rows are bucketed by settlement interval and the last print per bucket wins,
because funding_rates rows are collection-frequency, not settlement-frequency.

Pure functions over rows the caller fetched, so the caller owns the
point-in-time boundary (`before=`) and this module cannot reach into the future.
"""

from __future__ import annotations

from typing import Any, Iterable

from src import config
from src.strategies.base import SignalDirection

SETTLEMENT_SECONDS = 8 * 3600


def settlement_sum(
    rows: Iterable[dict[str, Any]] | None,
    opened_at: int | float | None,
    *,
    settlement_seconds: int = SETTLEMENT_SECONDS,
) -> float:
    """Sum of funding rates over complete settlements after `opened_at`."""
    if not rows or opened_at is None:
        return 0.0
    try:
        opened_bucket = int(opened_at) // settlement_seconds
    except (TypeError, ValueError):
        return 0.0

    buckets: dict[int, float] = {}
    for row in rows:
        try:
            bucket = int(row["timestamp"]) // settlement_seconds
            buckets[bucket] = float(row["funding_rate"])
        except (KeyError, TypeError, ValueError):
            continue

    return sum(rate for bucket, rate in buckets.items() if bucket > opened_bucket)


def direction_sign(direction: str | None) -> float:
    """-1 for a long (pays positive funding), +1 for a short (receives it).

    Case-insensitive, and an unrecognized direction raises rather than
    defaulting: silently treating an unknown side as short would flip the sign
    of a cost on every trade, which is worse than a loud failure.
    """
    normalized = str(direction or "").strip().upper()
    if normalized == SignalDirection.LONG.value:
        return -1.0
    if normalized == SignalDirection.SHORT.value:
        return 1.0
    raise ValueError(f"unknown position direction for funding: {direction!r}")


def funding_pnl(
    rows: Iterable[dict[str, Any]] | None,
    direction: str | None,
    notional: float,
    opened_at: int | float | None,
    *,
    settlement_seconds: int = SETTLEMENT_SECONDS,
) -> float:
    """Funding PnL in quote currency for a perpetual position.

    Negative for a long paying positive funding, positive for a short receiving
    it. Returns 0.0 when FUNDING_PNL_ENABLED is off or no settlement is
    observed — a missing funding history costs nothing rather than guessing.
    """
    if not config.FUNDING_PNL_ENABLED:
        return 0.0
    rate_sum = settlement_sum(rows, opened_at, settlement_seconds=settlement_seconds)
    if rate_sum == 0.0:
        return 0.0
    return direction_sign(direction) * rate_sum * float(notional)


def fetch_funding_pnl(
    storage: Any,
    symbol: str,
    direction: str | None,
    notional: float,
    opened_at: int | float | None,
    *,
    before: int | None = None,
    limit: int = 800,
) -> float:
    """funding_pnl() over rows read from storage, clamped to `before`.

    `before` is the point-in-time boundary: in a backtest it is the candle
    timestamp being processed, so no settlement past the simulated clock is
    visible. Storage failures return 0.0 — funding must never break a cycle.
    """
    if storage is None or opened_at is None or not config.FUNDING_PNL_ENABLED:
        return 0.0
    try:
        rows = (
            storage.get_funding_rates(
                symbol, limit=limit, since=int(opened_at), before=before
            )
            or []
        )
    except Exception:
        return 0.0
    return funding_pnl(rows, direction, notional, opened_at)
