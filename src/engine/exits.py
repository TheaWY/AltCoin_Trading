"""Shared exit-level math -- stop-loss / take-profit placement and the
trailing-stop ratchet -- so PaperTrader (live) and BacktestPortfolio
(backtest) compute exits through ONE code path. Mirrors src/engine/hedge.py
and src/engine/execution_cost.py's shape: pure functions, no config reads,
no storage access -- callers pass the config values in, which is what makes
live/backtest parity trivially assertable in tests.

Why this module exists (2026-07-13): live sized stops ATR-scaled
(ATR_STOP_MULT/ATR_TP_MULT x the symbol's own 1h ATR%) while backtest used a
FLAT STOP_LOSS_PCT/TAKE_PROFIT_PCT for every symbol, and live trailed stops
behind the high-water mark while backtest had no trailing logic at all. A
flat 3% stop is ~6 ATR wide on a 0.5%-ATR symbol (nearly never hit) and
~0.6 ATR on a 5%-ATR symbol (hit constantly) -- so every walk-forward
window-consistency number recorded before this fix was measured on an exit
mechanism nobody actually trades. Rule #2 (one code path for backtest and
live), enforced here the same way hedge.py enforced it for hedge sizing.
"""

from __future__ import annotations

from typing import Any

# MFE below this many ATRs of favorable move doesn't count as "was in
# profit" for the capture metric -- see mfe_capture's docstring.
MFE_MATERIALITY_ATR = 0.5


def exit_levels(
    direction: str,
    price: float,
    atr_pct: float | None,
    *,
    atr_stop_mult: float,
    atr_tp_mult: float,
    fallback_stop_pct: float,
    fallback_tp_pct: float,
) -> tuple[float, float]:
    """(stop_loss, take_profit) anchored at `price`. ATR-scaled when the
    symbol's 1h ATR% is available -- stop = atr_stop_mult x ATR%, target =
    atr_tp_mult x ATR%, so the gross R:R is always atr_tp_mult/atr_stop_mult
    regardless of the symbol's volatility -- with the fixed-percent fallback
    only when ATR can't be computed (matches PaperTrader's long-standing
    behavior; the fallback is the exception, not a separate regime).
    """
    if atr_pct:
        stop_frac = atr_stop_mult * atr_pct / 100.0
        tp_frac = atr_tp_mult * atr_pct / 100.0
    else:
        stop_frac = fallback_stop_pct
        tp_frac = fallback_tp_pct

    if direction == "LONG":
        return price * (1 - stop_frac), price * (1 + tp_frac)
    return price * (1 + stop_frac), price * (1 - tp_frac)


def position_notional(
    portfolio_value: float,
    cash: float,
    price: float,
    stop_loss: float,
    *,
    risk_per_trade_pct: float,
    max_position_pct: float,
) -> float:
    """Volatility-inverse position sizing, shared by both engines
    (divergence #4, found by tests/test_engine_parity.py 2026-07-13: live
    sized risk-based while backtest took a flat max_position_pct of equity
    -- invisible at low ATR where the risk-based number caps out at the same
    max, ~50% oversized in backtest exactly on wide-stop/high-ATR names).

    notional = risk budget / stop distance, so a coin with a 4% stop gets
    half the size of a coin with a 2% stop. Capped by max_position_pct and
    available cash. Zero when the stop distance is degenerate -- a trade
    whose risk can't be computed doesn't get a default size, it gets none.
    """
    stop_frac = abs(price - stop_loss) / price if price else 0.0
    if stop_frac <= 0:
        return 0.0
    notional = (portfolio_value * risk_per_trade_pct) / stop_frac
    return max(0.0, min(notional, portfolio_value * max_position_pct, cash))


def trailing_stop_update(
    direction: str,
    entry_price: float,
    price: float,
    best_price: float,
    stop_loss: float,
    atr_pct: float | None,
    *,
    trail_atr_mult: float,
    trail_arm_atr: float = 1.0,
) -> dict[str, Any]:
    """The trailing-stop ratchet, direction-aware, as a pure function.

    Once price has moved trail_arm_atr x ATR in favor of the trade
    (research axis; was hardcoded 1.0 until 2026-07-14), the stop trails
    trail_atr_mult x ATR behind the best price seen, and only ever moves in
    the risk-REDUCING direction (up for LONG, down for SHORT) -- never
    widens (system rule #6).

    Geometry note (the DEXE finding): with stop 1.5 / arm 1.0 / trail 2.0 /
    TP 2.5 (all x ATR), any peak in [arm, TP) that fully reverses exits at
    ~breakeven -- profit requires reaching TP without a reversal. These are
    exactly the knobs the exit-geometry research axes vary.

    Returns a dict with any of {"trail_price", "stop_loss"} that changed;
    empty dict means nothing to update. Callers persist however they store
    trades (DB row for live, dataclass fields for backtest).
    """
    if not atr_pct:
        return {}
    atr_frac = float(atr_pct) / 100.0
    best = float(best_price or entry_price)
    stop = float(stop_loss)
    updates: dict[str, Any] = {}

    if direction == "LONG":
        if price > best:
            best = price
            updates["trail_price"] = best
        if best >= entry_price * (1 + trail_arm_atr * atr_frac):
            new_stop = best * (1 - trail_atr_mult * atr_frac)
            if new_stop > stop:
                updates["stop_loss"] = new_stop
    else:
        if price < best:
            best = price
            updates["trail_price"] = best
        if best <= entry_price * (1 - trail_arm_atr * atr_frac):
            new_stop = best * (1 + trail_atr_mult * atr_frac)
            if new_stop < stop:
                updates["stop_loss"] = new_stop

    return updates


def partial_tp_level(
    direction: str,
    entry_price: float,
    r_unit: float,
    at_r: float,
) -> float | None:
    """Price at which the partial take-profit (half off, trail the rest)
    triggers: at_r R-multiples in favor of the trade, where R = the initial
    stop distance in price terms. at_r <= 0 disables (the axis's 'off').

    This directly attacks the narrow-capture-window geometry: half the
    position banked at 1R means a full reversal from any later peak still
    leaves a profitable trade.
    """
    if at_r <= 0 or r_unit <= 0:
        return None
    if direction == "LONG":
        return entry_price + at_r * r_unit
    return entry_price - at_r * r_unit


def mfe_capture(
    direction: str,
    entry_price: float,
    exit_price: float,
    best_price: float | None,
    atr_pct: float | None = None,
) -> tuple[float | None, float | None]:
    """(mfe_pct, capture_fraction): how far the trade went in our favor at
    its best, and what fraction of that favorable move the realized exit
    captured. capture is None when the favorable move was immaterial --
    below MATERIALITY_ATR x ATR (or nonpositive): a straight loser with a
    0.2% wiggle is an entry problem, and dividing by near-zero MFE turns
    it into a -10.0 outlier that pollutes the distribution (found on the
    first real comparison, 2026-07-14). The standing metric for judging
    exit geometry."""
    if not best_price or not entry_price:
        return None, None
    if direction == "LONG":
        mfe = (float(best_price) / entry_price - 1.0) * 100.0
        realized = (float(exit_price) / entry_price - 1.0) * 100.0
    else:
        mfe = (entry_price / float(best_price) - 1.0) * 100.0
        realized = (entry_price / float(exit_price) - 1.0) * 100.0
    materiality = MFE_MATERIALITY_ATR * float(atr_pct) if atr_pct else 0.0
    if mfe <= max(materiality, 1e-9):
        return round(mfe, 4), None
    return round(mfe, 4), round(max(-10.0, min(1.0, realized / mfe)), 4)
