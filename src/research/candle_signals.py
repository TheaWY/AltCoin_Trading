"""Round-2 candle signals — five 1h-only event definitions for event_study.py.

Same contract as the SIGNALS in event_study.py: each function takes
(symbol, since) and returns event timestamps (bar close times). Every
threshold is computed from PAST data only (rolling or expanding windows,
current bar excluded from its own baseline) — no lookahead.

Kept separate from event_study.py so future signal rounds never touch the
core module; event_study.py merges EXTRA_SIGNALS into SIGNALS at import time.
"""

from __future__ import annotations

import time
from typing import Any

from src.data.storage import get_storage
from src.research.rel_strength import percentile_rank, spread

ROLLING_WINDOW = 168  # 1 week of 1h bars
MIN_HISTORY = 720     # 30 days of 1h bars


def _load_prices(symbol: str, since: int) -> list[dict[str, Any]]:
    return get_storage().get_prices(symbol, limit=1_000_000, since=since, timeframe="1h")


def _std_floor(sd: float, mean: float) -> float:
    return max(sd, abs(mean) * 0.01, 1e-9)


def _expanding_pctl_events(
    series: list[tuple[int, float]], pctl: float, min_history: int
) -> list[int]:
    """Fire when value exceeds the expanding (all-past) percentile. Same
    semantics as event_study._rolling_pctl_events(high=True), duplicated
    here to avoid importing event_study (which imports this module)."""
    events: list[int] = []
    values: list[float] = []
    for ts, value in series:
        if len(values) >= min_history and percentile_rank(value, values) >= pctl:
            events.append(ts)
        values.append(value)
    return events


# --------------------------------------------------------------------------
# 1. capitulation_bar — liquidation-cascade proxy
# --------------------------------------------------------------------------

def capitulation_bar(symbol: str, since: int) -> list[int]:
    prices = _load_prices(symbol, since)
    events: list[int] = []
    ro_hist: list[float] = []
    vol_hist: list[float] = []
    for p in prices:
        ts = int(p["timestamp"])
        o, h, l, c = float(p["open"]), float(p["high"]), float(p["low"]), float(p["close"])
        v = float(p.get("volume") or 0.0)
        range_over_open = (h - l) / o if o > 0 else 0.0

        if len(ro_hist) >= ROLLING_WINDOW:
            ro_window = ro_hist[-ROLLING_WINDOW:]
            vol_window = vol_hist[-ROLLING_WINDOW:]
            ro_mean = sum(ro_window) / ROLLING_WINDOW
            ro_sd = _std_floor(
                (sum((x - ro_mean) ** 2 for x in ro_window) / ROLLING_WINDOW) ** 0.5, ro_mean
            )
            vol_mean = sum(vol_window) / ROLLING_WINDOW
            vol_sd = _std_floor(
                (sum((x - vol_mean) ** 2 for x in vol_window) / ROLLING_WINDOW) ** 0.5, vol_mean
            )
            z_range = (range_over_open - ro_mean) / ro_sd
            z_vol = (v - vol_mean) / vol_sd
            if z_range >= 3.0 and z_vol >= 3.0 and h > l:
                close_pos = (c - l) / (h - l)
                if close_pos <= 0.20:
                    events.append(ts)

        ro_hist.append(range_over_open)
        vol_hist.append(v)
    return events


# --------------------------------------------------------------------------
# 2. pump24_extreme — chase fade
# --------------------------------------------------------------------------

def pump24_extreme(symbol: str, since: int) -> list[int]:
    prices = _load_prices(symbol, since)
    closes = [float(p["close"]) for p in prices]
    ts_list = [int(p["timestamp"]) for p in prices]
    series: list[tuple[int, float]] = []
    for i in range(24, len(closes)):
        prev = closes[i - 24]
        if prev > 0:
            series.append((ts_list[i], (closes[i] - prev) / prev))
    return _expanding_pctl_events(series, 0.99, MIN_HISTORY)


# --------------------------------------------------------------------------
# 3. consec_green_8plus — one-sided run exhaustion
# --------------------------------------------------------------------------

def consec_green_8plus(symbol: str, since: int) -> list[int]:
    prices = _load_prices(symbol, since)
    events: list[int] = []
    streak = 0
    for p in prices:
        if float(p["close"]) > float(p["open"]):
            streak += 1
            if streak == 8:
                events.append(int(p["timestamp"]))
        else:
            streak = 0
    return events


# --------------------------------------------------------------------------
# 4. weekend_open_utc — calendar/liquidity effect
# --------------------------------------------------------------------------

def weekend_open_utc(symbol: str, since: int) -> list[int]:
    prices = _load_prices(symbol, since)
    events = []
    for p in prices:
        ts = int(p["timestamp"])
        t = time.gmtime(ts)
        if t.tm_wday == 5 and t.tm_hour == 0:
            events.append(ts)
    return events


# --------------------------------------------------------------------------
# 5. rel_strength_95_vs_btc — alt rotation froth
# --------------------------------------------------------------------------

def rel_strength_95_vs_btc(symbol: str, since: int) -> list[int]:
    if symbol == "BTC/USDT":
        return []
    sym_prices = _load_prices(symbol, since)
    btc_prices = _load_prices("BTC/USDT", since)
    btc_idx = {int(p["timestamp"]): float(p["close"]) for p in btc_prices}
    sym_idx = {int(p["timestamp"]): float(p["close"]) for p in sym_prices}

    series: list[tuple[int, float]] = []
    for p in sym_prices:
        ts = int(p["timestamp"])
        prior_ts = ts - 7 * 86400
        value = spread(sym_idx.get(ts), sym_idx.get(prior_ts), btc_idx.get(ts), btc_idx.get(prior_ts))
        if value is None:
            continue
        series.append((ts, value))
    return _expanding_pctl_events(series, 0.95, MIN_HISTORY)


EXTRA_SIGNALS = {
    "capitulation_bar": capitulation_bar,
    "pump24_extreme": pump24_extreme,
    "consec_green_8plus": consec_green_8plus,
    "weekend_open_utc": weekend_open_utc,
    "rel_strength_95_vs_btc": rel_strength_95_vs_btc,
}
