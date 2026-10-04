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
MIN_DVOL = float(getattr(config, "PAIRS_MIN_DVOL", 8_000_000.0))
COST_LEG = float(getattr(config, "PAIRS_COST_LEG", 0.0010))        # per execution; 4 per round-trip
FUND_HR = float(getattr(config, "PAIRS_FUND_HR", 0.0000125))       # short-leg funding/borrow per hour
COVERAGE = float(getattr(config, "PAIRS_MIN_COVERAGE", 0.7))       # min finite fraction in select window
MIN_LISTING_DAYS = float(getattr(config, "PAIRS_MIN_LISTING_DAYS", 180))
# 1.0: live book avg win +6.76 vs avg loss -10.59 was driven by a 15% dollar
# stop that overshoots the z-revert target (Z_IN→Z_OUT ≈ 1.5σ). User allowed
# entry-z +1.5 or a tighter stop. On the corrected daily-volume universe
# (K=60, 2026-03–05), +1.0 is the loosest delta with |avgL| ≤ avgW and the
# best expectancy among those that pass; +1.5 still has avgL > avgW.
Z_STOP_DELTA = float(getattr(config, "PAIRS_Z_STOP_DELTA", 1.0))
STOP_PCT = float(getattr(config, "PAIRS_STOP_PCT", 0.15))          # last-resort dollar/spread stop


def _norm_symbol(symbol: str) -> str:
    n = (symbol or "").strip().upper()
    if not n:
        return ""
    return n if "/" in n else f"{n}/USDT"


def _exclude_set() -> frozenset[str]:
    raw = getattr(config, "PAIRS_EXCLUDE_SYMBOLS", ("BTW/USDT", "PUMP/USDT"))
    items = raw.split(",") if isinstance(raw, str) else raw
    return frozenset(_norm_symbol(str(s)) for s in items if str(s).strip())


EXCLUDE = _exclude_set()


def is_excluded(symbol: str | None) -> bool:
    """True for configured meme/denylist names (BTW, PUMP, ...)."""
    return _norm_symbol(symbol or "") in EXCLUDE


def median_daily_dvol(hourly: np.ndarray) -> float:
    """Median of non-overlapping 24h dollar-volume sums.

    Callers pass hourly quote-volume (or close*base_volume). Comparing that
    hourly series to MIN_DVOL=8e6 collapsed the live universe to ~10 names;
    this is the daily figure the filter is named after.
    """
    arr = np.asarray(hourly, dtype=float)
    n = (len(arr) // 24) * 24
    if n < 24:
        return 0.0
    days = arr[:n].reshape(-1, 24)
    hours = np.isfinite(days).sum(axis=1)
    totals = np.nansum(np.where(np.isfinite(days), days, 0.0), axis=1)
    ok = hours >= 12
    if not np.any(ok):
        return 0.0
    return float(np.median(totals[ok]))


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


def listing_hours(lp: np.ndarray, asof_index: int) -> int:
    """Hours of history visible at `asof_index` (no future bars). 0 if none."""
    if asof_index <= 0:
        return 0
    first = np.flatnonzero(np.isfinite(lp[:asof_index]))
    if len(first) == 0:
        return 0
    return int(asof_index - int(first[0]))


def liquid_universe(
    logp: dict[str, np.ndarray],
    dvol: dict[str, np.ndarray],
    sel: slice,
) -> list[str]:
    """POINT-IN-TIME tradeable universe for a rebalance: symbols with enough
    price coverage AND trailing median *daily* dollar-volume >= MIN_DVOL over
    the SELECT window ONLY (never future info). When the series is long enough
    to observe it, also require MIN_LISTING_DAYS of history as of sel.stop so
    newly listed memes cannot enter. `EXCLUDE` drops named memes (BTW, PUMP)
    that still clear the volume floor. Survivorship-safe: pass in every symbol
    that existed then, incl. ones that later delisted."""
    sel_len = (sel.stop - sel.start)
    min_listing_hours = MIN_LISTING_DAYS * 24
    live: list[str] = []
    for s, lp in logp.items():
        if is_excluded(s):
            continue
        seg = lp[sel]
        if np.isfinite(seg).sum() < sel_len * COVERAGE:
            continue
        if s not in dvol or median_daily_dvol(dvol[s][sel]) < MIN_DVOL:
            continue
        # Only enforceable when the array actually spans the listing floor;
        # a 90d live panel cannot prove 180d of history (caller pre-filters).
        if len(lp) >= min_listing_hours and listing_hours(lp, sel.stop) < min_listing_hours:
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


def should_dollar_stop(spread_pnl: float, stop_pct: float | None = None) -> bool:
    """Last-resort stop in spread-return units (≈ primary-notional P&L)."""
    pct = STOP_PCT if stop_pct is None else stop_pct
    if pct <= 0:
        return False
    return spread_pnl <= -abs(pct)


def should_stop(z: float | None, entry_z: float | None,
                delta: float | None = None) -> bool:
    """Adverse z-stop: the spread moved `delta` further from the mean in the
    same direction as entry. Default delta=1.0: entry at z=+2.2 (short rich
    spread) stops at z>=+3.2; entry at z=-2.2 stops at z<=-3.2. Sized so the
    typical losing spread move is no larger than the z-revert take
    (Z_IN -> Z_OUT = 1.5 sigma of the *winning* side). Default +1.0 is the
    loosest cut that still keeps |avg loss| ≤ avg win on the live-like
    historical panels."""
    if z is None or entry_z is None:
        return False
    d = Z_STOP_DELTA if delta is None else delta
    if d <= 0:
        return False
    if entry_z > 0:
        return z >= entry_z + d
    if entry_z < 0:
        return z <= entry_z - d
    return False


def round_trip_cost(hold_hours: float) -> float:
    """Realistic cost of one pair round-trip: 4 executions (enter 2 legs, exit 2
    legs) + short-leg funding over the hold. In spread-return units."""
    return 4 * COST_LEG + max(0.0, hold_hours) * FUND_HR
