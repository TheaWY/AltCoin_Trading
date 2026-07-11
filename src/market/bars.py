"""Point-in-time candle helpers.

Exchange OHLCV timestamps are bar-open times. A bar's high, low, close, and
volume are only knowable at ``open + timeframe``. Strategy decisions must use a
``decision_time`` and only expose bars whose close time is <= that value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_TIMEFRAME_UNITS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


@dataclass(frozen=True)
class BarValidationIssue:
    index: int
    timestamp: int | None
    reason: str


def timeframe_to_seconds(timeframe: str) -> int:
    """Parse timeframes like ``1m``, ``15m``, ``1h``, ``4h``, ``1d``."""
    value = (timeframe or "").strip().lower()
    if len(value) < 2:
        raise ValueError(f"invalid timeframe: {timeframe!r}")
    unit = value[-1]
    try:
        multiplier = int(value[:-1])
        seconds = _TIMEFRAME_UNITS[unit]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid timeframe: {timeframe!r}") from exc
    if multiplier <= 0:
        raise ValueError(f"invalid timeframe multiplier: {timeframe!r}")
    return multiplier * seconds


def bar_open_timestamp(bar: dict[str, Any]) -> int:
    return int(bar["timestamp"])


def bar_close_timestamp(bar: dict[str, Any], timeframe: str) -> int:
    return bar_open_timestamp(bar) + timeframe_to_seconds(timeframe)


def is_bar_closed(
    bar: dict[str, Any],
    timeframe: str,
    decision_time: int | float,
) -> bool:
    """True when this bar's full interval has elapsed by ``decision_time``."""
    return bar_close_timestamp(bar, timeframe) <= int(decision_time)


def visible_completed_bars(
    bars: Iterable[dict[str, Any]],
    timeframe: str,
    decision_time: int | float,
) -> list[dict[str, Any]]:
    """Return bars whose close time is <= ``decision_time``."""
    return [
        dict(bar)
        for bar in bars
        if is_bar_closed(bar, timeframe, decision_time)
    ]


def latest_completed_bar(
    bars: Iterable[dict[str, Any]],
    timeframe: str,
    decision_time: int | float,
) -> dict[str, Any] | None:
    visible = visible_completed_bars(bars, timeframe, decision_time)
    return visible[-1] if visible else None


def latest_visible_open_timestamp(timeframe: str, decision_time: int | float) -> int:
    """Latest bar-open timestamp whose close is <= ``decision_time``."""
    return int(decision_time) - timeframe_to_seconds(timeframe)


def next_execution_open(signal_bar_close: int, timeframe: str) -> int:
    """Earliest execution open for a signal generated after ``signal_bar_close``."""
    seconds = timeframe_to_seconds(timeframe)
    return ((int(signal_bar_close) + seconds - 1) // seconds) * seconds


def validate_bars(
    bars: Iterable[dict[str, Any]],
    timeframe: str,
) -> list[BarValidationIssue]:
    """Validate monotonic timestamps and OHLCV invariants."""
    issues: list[BarValidationIssue] = []
    previous_ts: int | None = None
    seconds = timeframe_to_seconds(timeframe)
    for index, bar in enumerate(bars):
        try:
            ts = int(bar["timestamp"])
        except Exception:
            issues.append(BarValidationIssue(index, None, "missing_timestamp"))
            continue

        if previous_ts is not None:
            if ts <= previous_ts:
                issues.append(BarValidationIssue(index, ts, "non_monotonic_timestamp"))
            elif ts - previous_ts != seconds:
                issues.append(BarValidationIssue(index, ts, "missing_interval"))
        if ts % seconds != 0:
            issues.append(BarValidationIssue(index, ts, "misaligned_timestamp"))
        previous_ts = ts

        try:
            open_ = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            volume = float(bar["volume"])
        except Exception:
            issues.append(BarValidationIssue(index, ts, "non_numeric_ohlcv"))
            continue

        if low > high:
            issues.append(BarValidationIssue(index, ts, "low_above_high"))
        if not (low <= open_ <= high):
            issues.append(BarValidationIssue(index, ts, "open_outside_high_low"))
        if not (low <= close <= high):
            issues.append(BarValidationIssue(index, ts, "close_outside_high_low"))
        if volume < 0:
            issues.append(BarValidationIssue(index, ts, "negative_volume"))

    return issues
