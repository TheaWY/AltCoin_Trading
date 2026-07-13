"""Shared execution-cost math -- slippage that moves the FILL PRICE, not a
fee, so both PaperTrader (live) and BacktestPortfolio (backtest) apply the
identical simulated cost. Mirrors src/engine/hedge.py's shape: pure
functions, no storage access -- callers own fetching whatever orderbook/
history data they pass in -- with ONE explicitly-marked exception below
(symbol_fallback_stats) for the shared fallback lookup, so both paths use
the same fallback instead of each writing their own.

Fees are unchanged and separate: config.round_trip_cost_pct() is fee-only
as of this change (see config.py -- slippage used to be folded into it,
which would have double-counted the cost this module now applies to the
price). A trade's total cost is exactly fees + slippage, each counted once.
"""

from __future__ import annotations

from typing import Any

BPS = 1.0 / 10_000.0

# Last-resort defaults when a symbol has NO orderbook history at all (not
# even from a non-contemporaneous period) -- e.g. a brand-new deployment.
# Deliberately conservative, round numbers -- not derived from any prior
# flat slippage assumption (this system had none decomposed into
# spread/depth terms before orderbook collection existed).
DEFAULT_FALLBACK_SPREAD_BPS = 6.0
DEFAULT_FALLBACK_DEPTH_USD = 50_000.0


def side_for(direction: str, is_entry: bool) -> str:
    """LONG entry / SHORT exit both buy the base asset; SHORT entry / LONG
    exit both sell it. Callers translate direction+entry-or-exit into the
    correct side once, here, so "buy pays more / sell gets less" logic
    downstream never has to reason about direction directly."""
    is_long = direction == "LONG"
    return "buy" if (is_long == is_entry) else "sell"


def resolve_spread_and_depth(
    side: str,
    ob_snapshot: dict[str, Any] | None,
    fallback_spread_bps: float,
    fallback_depth_usd: float,
) -> tuple[float, float, bool]:
    """Pick spread_bps/depth_usd from a real snapshot at/before the fill
    time if present, else the caller-supplied fallback. Returns
    (spread_bps, depth_usd, used_fallback) -- used_fallback must never be
    silently dropped by callers; it is what makes A3's "print when the
    fallback fired" requirement possible.

    bid_depth_1pct/ask_depth_1pct in orderbook_snapshots are BASE-ASSET
    UNITS, not USD (verified against src/data/collectors/orderbook.py) --
    converted here via the snapshot's own mid_price.
    """
    if ob_snapshot is not None:
        mid = float(ob_snapshot.get("mid_price") or 0.0)
        depth_units = float(
            ob_snapshot.get("ask_depth_1pct" if side == "buy" else "bid_depth_1pct") or 0.0
        )
        depth_usd = depth_units * mid
        spread_bps = float(ob_snapshot.get("spread_bps") or fallback_spread_bps)
        if depth_usd > 0 and mid > 0:
            return spread_bps, depth_usd, False
    return fallback_spread_bps, fallback_depth_usd, True


def slippage_pct(
    notional: float,
    spread_bps: float,
    depth_usd: float,
    is_stress: bool = False,
    stress_mult: float = 1.0,
) -> float:
    """One-sided slippage as a fraction of price (0.001 = 0.1%) for filling
    `notional` USD against a book with the given spread and same-side
    1%-band depth (already resolved by the caller via
    resolve_spread_and_depth, from a real snapshot or its fallback).

    Model: half-spread (cost of crossing the touch) + a linear depth-walk
    approximation -- the 1% band's outer edge is ~1% away from mid by
    definition, so consuming the whole band costs ~1%, half the band
    ~0.5%, etc. First-order approximation, adequate for order-of-magnitude
    estimates (matches the methodology already used and accepted in the
    ledger audit's slippage estimate), not a calibrated market-impact model.
    """
    if notional <= 0:
        return 0.0
    half_spread = (spread_bps / 2.0) * BPS
    depth_fraction = (notional / depth_usd) if depth_usd > 0 else 1.0
    walk = 0.01 * depth_fraction
    total = half_spread + walk
    if is_stress:
        total *= stress_mult
    return total


def adjusted_fill_price(mid_or_close: float, side: str, slip_pct: float) -> float:
    """Apply slippage AGAINST the trader: a buy fills higher, a sell fills
    lower, regardless of whether this is an entry or an exit."""
    if side == "buy":
        return mid_or_close * (1 + slip_pct)
    if side == "sell":
        return mid_or_close * (1 - slip_pct)
    raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")


def is_stress_bar(bar_range_pct: float | None, atr_pct: float | None, stress_atr_mult: float) -> bool:
    """True when the bar the fill happens in has a range wider than
    stress_atr_mult x the symbol's own ATR -- an unusually violent bar,
    independent of whether the exit reason was stop_loss."""
    if bar_range_pct is None or atr_pct is None or atr_pct <= 0:
        return False
    return bar_range_pct > stress_atr_mult * atr_pct


def symbol_fallback_stats(storage: Any, symbol: str) -> dict[str, float]:
    """The one function in this module that touches storage -- both paths
    call this SAME lookup for the fallback spread/depth when no orderbook
    snapshot exists at/before the fill time. True for effectively all of
    this system's history: real orderbook collection began 2026-07-13, so
    every walk-forward window (all ending at or before the 2026-06-01
    holdout boundary) has zero contemporaneous coverage -- this
    necessarily draws on out-of-sample, more recent data as a proxy for
    what spreads/depth "might have been" historically. Falls back further
    to a fixed conservative default if the symbol has no snapshots of its
    own at all (e.g. delisted/renamed, or never in the collected universe).
    """
    rows = storage.get_orderbook_snapshots(symbol, limit=100_000)
    if not rows:
        return {"spread_bps": DEFAULT_FALLBACK_SPREAD_BPS, "depth_usd": DEFAULT_FALLBACK_DEPTH_USD}
    spreads = sorted(float(r["spread_bps"]) for r in rows)
    depths_usd = sorted(
        (float(r["bid_depth_1pct"]) + float(r["ask_depth_1pct"])) / 2.0 * float(r["mid_price"])
        for r in rows
        if float(r["mid_price"]) > 0
    )
    if not depths_usd:
        return {"spread_bps": spreads[len(spreads) // 2], "depth_usd": DEFAULT_FALLBACK_DEPTH_USD}
    return {
        "spread_bps": spreads[len(spreads) // 2],
        "depth_usd": depths_usd[len(depths_usd) // 2],
    }
