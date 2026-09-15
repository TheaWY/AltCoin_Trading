"""Cross-sectional (relative-value) entry selection.

Why this exists
---------------
Alt perps move together. With average pairwise correlation around 0.7-0.8, the
Grinold-Kahn effective breadth of five simultaneous directional positions is

    BR_eff = N / (1 + (N-1)*rho) = 5 / (1 + 4*0.8) = 1.19

so a five-position directional book is one bet cut into five pieces, not five
bets. Adding symbols barely helps: twenty candidates at rho=0.8 gives 1.23.
Under ALLOW_LONG=false it is worse still, because every position shares a sign
and strategy-return correlation collapses onto price correlation.

A large common component is an argument against directional betting, not for a
smaller universe. If everything moves together there is no information in the
common part, only in the residual. This module bets the residual: at each
decision time it ranks the cohort by a crowding metric and takes the extremes
against each other. The market beta appears on both sides and cancels, which is
also why this path does not need the BTC regime filter to protect it.

Metric choice
-------------
Funding rate, not long/short ratio. Binance serves ~30 days of L/S ratio and it
cannot be backfilled, so a 6.4-year walk-forward cannot evaluate it — using it
would make the axis untestable, which is the failure mode this repo keeps
guarding against. Funding history goes back as far as the klines do.

Funding is a direct crowding price: positive means longs are paying to hold, so
the highest-funding names are where leveraged longs are most crowded, and the
lowest are where shorts are. Short the crowded longs, long the crowded shorts.

Purity
------
`rank_cohort` is a pure function over observations the caller gathered, so the
caller owns the point-in-time boundary and backtest, paper and live share this
one implementation. It cannot reach into storage or the future.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from src import config
from src.strategies.base import SignalDirection

MODE_OFF = "off"
MODE_FUNDING_RANK = "funding_rank"
VALID_MODES = (MODE_OFF, MODE_FUNDING_RANK)


@dataclass(frozen=True)
class CohortLeg:
    symbol: str
    direction: str
    metric: float
    rank: int
    cohort_size: int
    dispersion: float

    def as_metadata(self) -> dict[str, Any]:
        return {
            "cross_sectional": True,
            "cohort_metric": round(self.metric, 8),
            "cohort_rank": self.rank,
            "cohort_size": self.cohort_size,
            "cohort_dispersion": round(self.dispersion, 8),
        }


@dataclass(frozen=True)
class CohortRanking:
    """Result of ranking one cohort at one decision time."""

    legs: dict[str, CohortLeg] = field(default_factory=dict)
    cohort_size: int = 0
    dispersion: float = 0.0
    skipped_reason: str | None = None

    def direction_for(self, symbol: str) -> str | None:
        leg = self.legs.get(symbol)
        return leg.direction if leg else None

    def leg_for(self, symbol: str) -> CohortLeg | None:
        return self.legs.get(symbol)

    @property
    def is_empty(self) -> bool:
        return not self.legs


def _leg_count(cohort_size: int) -> int:
    """How many names to take on each side.

    Capped by MAX_OPEN_POSITIONS split across two legs so the book stays
    balanced, and by a fraction of the cohort so the "extreme" stays extreme:
    taking the top 5 of 8 is not a tail, it is most of the market.
    """
    by_book = max(1, config.MAX_OPEN_POSITIONS // 2)
    by_cohort = max(1, int(cohort_size * config.CROSS_LEG_FRACTION))
    return max(1, min(by_book, by_cohort))


def rank_cohort(
    observations: Iterable[dict[str, Any]],
    *,
    mode: str | None = None,
) -> CohortRanking:
    """Rank a cohort by crowding and return the legs to trade.

    `observations` is [{"symbol": str, "metric": float}, ...] gathered by the
    caller at one decision time. Returns an empty ranking with a reason when the
    cohort cannot support a relative-value bet — too few names, or too little
    spread between the extremes for the ranking to be anything but noise.
    """
    mode = (mode or config.CROSS_SECTIONAL_MODE).strip().lower()
    if mode == MODE_OFF:
        return CohortRanking(skipped_reason="mode_off")
    if mode not in VALID_MODES:
        raise ValueError(f"unknown CROSS_SECTIONAL_MODE: {mode!r}")

    rows: list[tuple[str, float]] = []
    for obs in observations or []:
        symbol = obs.get("symbol")
        metric = obs.get("metric")
        if not symbol or metric is None:
            continue
        try:
            rows.append((str(symbol), float(metric)))
        except (TypeError, ValueError):
            continue

    cohort_size = len(rows)
    if cohort_size < config.CROSS_MIN_COHORT:
        return CohortRanking(
            cohort_size=cohort_size,
            skipped_reason=f"cohort {cohort_size} < min {config.CROSS_MIN_COHORT}",
        )

    # Sort by metric descending, then symbol, so ties are deterministic and a
    # replay produces the same book as the live cycle did.
    rows.sort(key=lambda item: (-item[1], item[0]))
    dispersion = rows[0][1] - rows[-1][1]
    if dispersion < config.CROSS_MIN_DISPERSION:
        return CohortRanking(
            cohort_size=cohort_size,
            dispersion=dispersion,
            skipped_reason=(
                f"dispersion {dispersion:.6f} < min {config.CROSS_MIN_DISPERSION}"
            ),
        )

    per_leg = _leg_count(cohort_size)
    legs: dict[str, CohortLeg] = {}

    # Highest funding = most crowded longs paying to hold -> short them.
    for rank, (symbol, metric) in enumerate(rows[:per_leg], start=1):
        legs[symbol] = CohortLeg(
            symbol=symbol,
            direction=SignalDirection.SHORT.value,
            metric=metric,
            rank=rank,
            cohort_size=cohort_size,
            dispersion=dispersion,
        )
    # Lowest funding = shorts paying -> long them.
    for offset, (symbol, metric) in enumerate(rows[-per_leg:], start=1):
        if symbol in legs:
            # Cohort too small to keep the legs disjoint; drop the overlap
            # rather than betting both sides of one name.
            continue
        legs[symbol] = CohortLeg(
            symbol=symbol,
            direction=SignalDirection.LONG.value,
            metric=metric,
            rank=cohort_size - per_leg + offset,
            cohort_size=cohort_size,
            dispersion=dispersion,
        )

    return CohortRanking(legs=legs, cohort_size=cohort_size, dispersion=dispersion)


def gather_observations(
    storage: Any,
    symbols: Iterable[str],
    *,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """Read the crowding metric for each symbol from a storage snapshot.

    The caller passes whichever storage view enforces its point-in-time
    boundary (SnapshotStorage in the backtest, live Storage in the cycle), so
    this helper never decides what "now" means.
    """
    mode = (mode or config.CROSS_SECTIONAL_MODE).strip().lower()
    if mode == MODE_OFF:
        return []
    observations: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            row = storage.get_latest_funding_rate(symbol)
        except Exception:
            continue
        if not row:
            continue
        rate = row.get("funding_rate")
        if rate is None:
            continue
        try:
            observations.append({"symbol": symbol, "metric": float(rate)})
        except (TypeError, ValueError):
            continue
    return observations


def beta_cancelling(ranking: CohortRanking) -> bool:
    """True when both legs are present, which is the point of the whole thing.

    ALLOW_LONG=false drops the long leg, and a short-only "cross-sectional"
    book is just a directional short on the crowded names — the market beta it
    was supposed to cancel comes straight back. Callers record this so the
    research results can separate the two cases instead of scoring them alike.
    """
    directions = {leg.direction for leg in ranking.legs.values()}
    return {
        SignalDirection.LONG.value,
        SignalDirection.SHORT.value,
    }.issubset(directions)
