"""Volatility entry filters -- refuse entries on symbols too violent to stop
out of. Shared pure functions, both engines call the identical code (rule
#2), so backtest expectancy is evidence about them.

Origin (research_decisions, subject='atr_entry_filters', pre-registered): a
losing week concentrated in high-ATR names (corr(entry ATR%, realized
return) = -0.48; ATR>=3% avg -2.72% vs ATR<3% +2.76%; 4/10 stops gapped
through, all high-ATR). Suspect origin -- judged against 5y of walk-forward,
not adopted because it explains last week.

Both default to UNLIMITED (999), so live behavior is unchanged until the
walk-forward promotes a cap.
"""

from __future__ import annotations

UNLIMITED = 999.0


def volatility_entry_block(
    atr_pct: float | None,
    recent_max_range_pct: float | None,
    stop_distance_pct: float,
    *,
    max_entry_atr_pct: float,
    max_stop_gap_tolerance: float,
) -> tuple[str, str] | None:
    """Return (gate_name, reason) if the entry should be refused on
    volatility grounds, else None.

    - MAX_ENTRY_ATR_PCT: refuse if the symbol's 1h ATR% exceeds the cap.
    - MAX_STOP_GAP_TOLERANCE: refuse if the symbol's recent max 1h range
      exceeds N x the intended stop distance -- i.e. "a normal bar here
      already blows through my stop". Scales with the stop, so it is more
      targeted than a flat ATR cap.

    Missing inputs (None ATR / no range history) do NOT block -- absence of
    data is not evidence of violence, and a separate freshness/backfill gate
    already handles no-data symbols.
    """
    if (max_entry_atr_pct < UNLIMITED and atr_pct is not None
            and atr_pct > max_entry_atr_pct):
        return ("entry_atr_too_high",
                f"ATR {atr_pct:.2f}% > MAX_ENTRY_ATR_PCT {max_entry_atr_pct:g}%")

    if (max_stop_gap_tolerance < UNLIMITED and recent_max_range_pct is not None
            and stop_distance_pct > 0
            and recent_max_range_pct > max_stop_gap_tolerance * stop_distance_pct):
        return ("stop_gap_risk",
                f"recent max 1h range {recent_max_range_pct:.2f}% > "
                f"{max_stop_gap_tolerance:g} x stop distance {stop_distance_pct:.2f}%")

    return None
