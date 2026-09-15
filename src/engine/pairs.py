"""Cointegration PAIRS stat-arb — shared core.

This is the first strategy to clear the honest gate (DSR 0.997, positive every
year 2021-2026 across two bull-bear cycles, survivorship-controlled, robust to
realistic 4-leg costs and short-leg funding). See memory
`pairs-statarb-gate-pass`.

The strategy is structurally different from every per-symbol setup in this
system: it is CROSS-SECTIONAL and PAIRWISE. Each rebalance it selects, from a
point-in-time liquid universe, the pairs whose log-spread mean-reverts fastest,
then trades the z-score of each spread (enter |z|>=Z_IN long-under/short-over,
exit |z|<=Z_OUT). A trade is market-neutral: LONG leg A + SHORT beta*leg B.

CRITICAL — one code path: BOTH the pairs backtest driver and the live pairs
runner import these functions. Selection, spread math, and the entry/exit rule
live here and NOWHERE else, so backtest and live cannot diverge (project rule:
backtest and live share one code path). Everything here is pure/numpy and
I/O-free so it is unit-testable and identical in both callers.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from src import config

# --- validated parameters (config-backed; defaults = the gate-passing config) ---
HOUR = 3600
SEL_HOURS = int(getattr(config, "PAIRS_SEL_HOURS", 90 * 24))       # trailing select window
TRADE_HOURS = int(getattr(config, "PAIRS_TRADE_HOURS", 30 * 24))   # forward trade window / rebalance
ZWIN_HOURS = int(getattr(config, "PAIRS_ZWIN_HOURS", 20 * 24))     # rolling spread z-score window
Z_IN = float(getattr(config, "PAIRS_Z_IN", 2.0))
Z_OUT = float(getattr(config, "PAIRS_Z_OUT", 0.5))
HL_MIN_HOURS = float(getattr(config, "PAIRS_HL_MIN_HOURS", 12.0))
HL_MAX_HOURS = float(getattr(config, "PAIRS_HL_MAX_HOURS", 20 * 24))
BETA_LO = float(getattr(config, "PAIRS_BETA_LO", 0.2))
BETA_HI = float(getattr(config, "PAIRS_BETA_HI", 5.0))
MIN_DVOL = float(getattr(config, "PAIRS_MIN_DVOL", 500_000.0))
COST_LEG = float(getattr(config, "PAIRS_COST_LEG", 0.0010))        # per execution; 4 per round-trip
FUND_HR = float(getattr(config, "PAIRS_FUND_HR", 0.0000125))       # short-leg funding/borrow per hour
COVERAGE = float(getattr(config, "PAIRS_MIN_COVERAGE", 0.7))       # min finite fraction in select window


@dataclass(frozen=True)
class PairSpec:
    """A selected pair: LONG `a`, SHORT `beta`*`b`. Ranked for MAX RETURN =
    high spread-volatility AND fast reversion (per-trade return ~= 1.5 x
    spread_std, so higher vol = bigger wins, as long as it still reverts)."""

    a: str
    b: str
    beta: float
    half_life: float
    spread_std: float = 0.0


def half_life(spread: np.ndarray) -> float:
    """Ornstein-Uhlenbeck mean-reversion half-life (hours) from an AR(1) fit
    d(spread) = k*spread_lag. Returns +inf when non-mean-reverting or too
    little data. Matches the validated scan exactly."""
    s = spread[np.isfinite(spread)]
    if len(s) < 200:
        return float("inf")
    ds = np.diff(s)
    lag = s[:-1]
    m = np.isfinite(ds) & np.isfinite(lag)
    if m.sum() < 100:
        return float("inf")
    b = np.polyfit(lag[m], ds[m], 1)[0]
    return -np.log(2) / b if b < 0 else float("inf")


def spread(la: np.ndarray, lb: np.ndarray, beta: float) -> np.ndarray:
    """Log-spread of the pair: log(A) - beta*log(B)."""
    return la - beta * lb


def liquid_universe(
    logp: dict[str, np.ndarray],
    dvol: dict[str, np.ndarray],
    sel: slice,
) -> list[str]:
    """POINT-IN-TIME tradeable universe for a rebalance: symbols with enough
    price coverage AND trailing median dollar-volume >= MIN_DVOL over the SELECT
    window ONLY (never future info). Survivorship-safe: pass in every symbol
    that existed then, incl. ones that later delisted."""
    sel_len = (sel.stop - sel.start)
    live: list[str] = []
    for s, lp in logp.items():
        seg = lp[sel]
        if np.isfinite(seg).sum() < sel_len * COVERAGE:
            continue
        dv = dvol[s][sel]
        dv = dv[np.isfinite(dv)]
        if len(dv) == 0 or np.median(dv) < MIN_DVOL:
            continue
        live.append(s)
    return live


def select_pairs(
    names: list[str],
    logp: dict[str, np.ndarray],
    sel: slice,
) -> list[PairSpec]:
    """From `names`, score every combination on the trailing SELECT window and
    keep the mean-reverting ones (hedge ratio beta in range, half-life within
    [HL_MIN, HL_MAX]), sorted fastest-reverting first. FULL breadth — the caller
    trades all of them (breadth is the edge's lever, per the gate study)."""
    sel_len = (sel.stop - sel.start)
    scored: list[PairSpec] = []
    for a, b in itertools.combinations(names, 2):
        la, lb = logp[a][sel], logp[b][sel]
        m = np.isfinite(la) & np.isfinite(lb)
        if m.sum() < sel_len * COVERAGE:
            continue
        beta = float(np.polyfit(lb[m], la[m], 1)[0])
        if not (BETA_LO < beta < BETA_HI):
            continue
        sp = spread(la, lb, beta)
        hl = half_life(sp)
        if HL_MIN_HOURS < hl < HL_MAX_HOURS:
            sd = float(np.nanstd(sp))
            scored.append(PairSpec(a=a, b=b, beta=beta, half_life=hl, spread_std=sd))
    # MAX-RETURN ranking: spread_std / half_life descending — prefer pairs that
    # swing WIDE (bigger per-trade capture) AND revert FAST. Pure high-vol alone
    # degrades reliability (DSR 0.627); the blend keeps it (DSR 0.979) while
    # lifting per-trade return +1.78% -> +2.75% vs half-life-only ranking.
    scored.sort(key=lambda p: -(p.spread_std / p.half_life) if p.half_life > 0 else 0.0)
    return scored


def zscore(spread_window: np.ndarray, current: float) -> float | None:
    """Trailing z-score of `current` spread vs its rolling window. None when the
    window has too few finite points or zero dispersion (undefined signal)."""
    w = spread_window[np.isfinite(spread_window)]
    if len(w) < ZWIN_HOURS * 0.6 or w.std() == 0 or not np.isfinite(current):
        return None
    return (current - w.mean()) / w.std()


def entry_side(z: float) -> int:
    """Direction to take the SPREAD given its z: spread rich (z>0) -> SHORT the
    spread (short A / long B) = -1; spread cheap -> LONG the spread = +1."""
    return int(-np.sign(z))


def should_open(z: float | None) -> bool:
    return z is not None and abs(z) >= Z_IN


def should_close(z: float | None) -> bool:
    return z is not None and abs(z) <= Z_OUT


def round_trip_cost(hold_hours: float) -> float:
    """Realistic cost of one pair round-trip: 4 executions (enter 2 legs, exit 2
    legs) + short-leg funding over the hold. In spread-return units."""
    return 4 * COST_LEG + max(0.0, hold_hours) * FUND_HR
