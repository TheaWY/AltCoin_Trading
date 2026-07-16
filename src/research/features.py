"""Point-in-time feature store -- ONE computation, one source of truth, for
every event study / signal / strategy (data inventory 2026-07-15: the per-symbol
feature set was 24 fields in indicators.compute_all; this triples it with the
candle-shape / multi-window-vol / return-moment / order-flow / time features the
inventory flagged missing, all free from data we already have).

POINT-IN-TIME CONTRACT: every function computes ONLY from the `rows` it is given.
The caller is responsible for passing rows with timestamp <= t (e.g. via
SnapshotStorage.get_prices(before=t) in the backtest, or a plain tail in live).
compute_features() asserts the rows are ascending so a mis-ordered slice fails
loudly instead of leaking a future bar into a past feature.

Reuses src.engine.indicators for everything it already computes (momentum,
RSI/MACD/Bollinger, ATR, realized vol 7d/30d, beta, amihud, donchian, ...) --
this module is strictly ADDITIVE, never a second definition of an existing one.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from src.engine import indicators

_HOURS_PER_YEAR = 24 * 365


# --------------------------------------------------------------------------
# candle shape -- computed transiently inside capitulation_bar today, thrown
# away; promoted here to reusable features.
# --------------------------------------------------------------------------
def candle_shape(rows: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    if len(rows) < 2:
        return {k: None for k in
                ("body_pct", "upper_wick_pct", "lower_wick_pct",
                 "close_position_in_range", "gap_pct", "true_range_pct")}
    cur, prev = rows[-1], rows[-2]
    o, h, l, c = float(cur["open"]), float(cur["high"]), float(cur["low"]), float(cur["close"])
    prev_close = float(prev["close"])
    rng = h - l
    out: dict[str, float | None] = {
        "body_pct": (abs(c - o) / rng) if rng > 0 else 0.0,
        "upper_wick_pct": ((h - max(o, c)) / rng) if rng > 0 else 0.0,
        "lower_wick_pct": ((min(o, c) - l) / rng) if rng > 0 else 0.0,
        "close_position_in_range": ((c - l) / rng) if rng > 0 else 0.5,
        "gap_pct": ((o - prev_close) / prev_close * 100.0) if prev_close else None,
        "true_range_pct": ((max(h - l, abs(h - prev_close), abs(l - prev_close))) / c * 100.0) if c else None,
    }
    return out


# --------------------------------------------------------------------------
# multi-window volatility -- indicators has 7d/30d realized vol only.
# --------------------------------------------------------------------------
def _log_returns(values: Sequence[float]) -> list[float]:
    out = []
    for prev, cur in zip(values, values[1:]):
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def volatility_features(rows: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    values = indicators.closes(rows)
    out: dict[str, float | None] = {}
    for label, w in (("6h", 6), ("12h", 12), ("24h", 24), ("3d", 72)):
        out[f"realized_vol_{label}"] = indicators.realized_vol_annualized(values, w)
    # vol-of-vol: std of a rolling 24h realized-vol series over ~7d
    vseries = []
    for i in range(len(values) - 168, len(values)):
        if i >= 24:
            v = indicators.realized_vol_annualized(values[max(0, i - 24):i + 1], 24)
            if v is not None:
                vseries.append(v)
    if len(vseries) >= 12:
        m = sum(vseries) / len(vseries)
        out["vol_of_vol"] = math.sqrt(sum((x - m) ** 2 for x in vseries) / len(vseries))
    else:
        out["vol_of_vol"] = None
    # ATR short/long ratio (regime: expanding vs contracting range)
    atr_s = indicators.atr_pct(rows, 6)
    atr_l = indicators.atr_pct(rows, 48)
    out["atr_ratio_short_long"] = (atr_s / atr_l) if (atr_s and atr_l) else None
    # Parkinson & Garman-Klass (OHLC range estimators, less noisy than close-to-close)
    win = rows[-24:]
    if len(win) >= 12:
        park = gk = 0.0
        for r in win:
            hi, lo, o, c = float(r["high"]), float(r["low"]), float(r["open"]), float(r["close"])
            if hi > 0 and lo > 0 and o > 0 and c > 0:
                lr = math.log(hi / lo)
                park += lr * lr
                gk += 0.5 * lr * lr - (2 * math.log(2) - 1) * (math.log(c / o) ** 2)
        n = len(win)
        out["parkinson_vol"] = math.sqrt(park / (4 * math.log(2) * n)) * math.sqrt(_HOURS_PER_YEAR) * 100.0
        out["garman_klass_vol"] = math.sqrt(max(0.0, gk / n)) * math.sqrt(_HOURS_PER_YEAR) * 100.0
    else:
        out["parkinson_vol"] = out["garman_klass_vol"] = None
    # up/down semivariance over 7d hourly returns
    rets = indicators.pct_returns(values[-169:])
    if len(rets) >= 24:
        down = [r for r in rets if r < 0]
        up = [r for r in rets if r > 0]
        out["downside_semivol"] = (math.sqrt(sum(r * r for r in down) / len(rets)) * math.sqrt(_HOURS_PER_YEAR) * 100.0)
        out["upside_semivol"] = (math.sqrt(sum(r * r for r in up) / len(rets)) * math.sqrt(_HOURS_PER_YEAR) * 100.0)
    else:
        out["downside_semivol"] = out["upside_semivol"] = None
    return out


# --------------------------------------------------------------------------
# multi-window returns + higher moments -- indicators has 24h/7d/30d.
# --------------------------------------------------------------------------
def return_features(rows: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    values = indicators.closes(rows)
    out: dict[str, float | None] = {}
    for label, p in (("1h", 1), ("6h", 6), ("12h", 12), ("3d", 72), ("14d", 336)):
        out[f"ret_{label}"] = indicators.momentum_pct(values, p)
    # momentum acceleration: last-6h momentum vs the 6h before it
    m_now = indicators.momentum_pct(values, 6)
    m_prev = indicators.momentum_pct(values[:-6], 6) if len(values) > 12 else None
    out["momentum_accel"] = (m_now - m_prev) if (m_now is not None and m_prev is not None) else None
    # skew / kurtosis / lag-1 autocorr of hourly returns over ~7d
    rets = indicators.pct_returns(values[-169:])
    if len(rets) >= 30:
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        sd = math.sqrt(var) if var > 0 else 0.0
        if sd > 0:
            out["return_skew"] = sum((r - m) ** 3 for r in rets) / len(rets) / sd ** 3
            out["return_kurtosis"] = sum((r - m) ** 4 for r in rets) / len(rets) / sd ** 4 - 3.0
            num = sum((rets[i] - m) * (rets[i - 1] - m) for i in range(1, len(rets)))
            out["return_autocorr_1"] = num / len(rets) / var
        else:
            out["return_skew"] = out["return_kurtosis"] = out["return_autocorr_1"] = None
    else:
        out["return_skew"] = out["return_kurtosis"] = out["return_autocorr_1"] = None
    return out


# --------------------------------------------------------------------------
# volume & ORDER FLOW -- the order-flow columns (taker_buy_base / num_trades /
# quote_volume) captured by A2 (2026-07-16) are used here for the first time.
# --------------------------------------------------------------------------
def volume_features(rows: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    win = rows[-24:]
    # VWAP distance (close vs 24h volume-weighted average price)
    num = den = 0.0
    for r in win:
        c, v = float(r["close"]), float(r.get("volume") or 0.0)
        num += c * v
        den += v
    vwap = (num / den) if den > 0 else None
    last_close = float(rows[-1]["close"])
    out["vwap_distance_pct"] = ((last_close - vwap) / vwap * 100.0) if vwap else None
    # dollar-volume z-score over 30d
    dvs = [float(r["close"]) * float(r.get("volume") or 0.0) for r in rows[-720:]]
    if len(dvs) >= 48:
        m = sum(dvs) / len(dvs)
        sd = math.sqrt(sum((x - m) ** 2 for x in dvs) / len(dvs))
        out["dollar_vol_zscore"] = ((dvs[-1] - m) / sd) if sd > 0 else 0.0
    else:
        out["dollar_vol_zscore"] = None
    # OBV slope over 7d (sign-of-return weighted cumulative volume, then trend)
    obv = 0.0
    obv_series = []
    prev_c = None
    for r in rows[-168:]:
        c, v = float(r["close"]), float(r.get("volume") or 0.0)
        if prev_c is not None:
            obv += v if c > prev_c else (-v if c < prev_c else 0.0)
        obv_series.append(obv)
        prev_c = c
    if len(obv_series) >= 24:
        n = len(obv_series)
        xs = list(range(n))
        mx = sum(xs) / n
        my = sum(obv_series) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, obv_series))
        varx = sum((x - mx) ** 2 for x in xs)
        out["obv_slope"] = (cov / varx) if varx > 0 else None
    else:
        out["obv_slope"] = None
    # ORDER-FLOW IMBALANCE from taker-buy volume (A2). None on bars predating it.
    tb = rows[-1].get("taker_buy_base")
    vol = rows[-1].get("volume")
    if tb is not None and vol not in (None, 0):
        out["order_flow_imbalance"] = (2.0 * float(tb) - float(vol)) / float(vol)
    else:
        out["order_flow_imbalance"] = None
    nt = rows[-1].get("num_trades")
    out["avg_trade_size"] = (float(vol) / float(nt)) if (nt not in (None, 0) and vol is not None) else None
    return out


# --------------------------------------------------------------------------
# time / calendar -- the inventory found ZERO time features except one weekend
# signal. All from the last bar's timestamp (UTC).
# --------------------------------------------------------------------------
def time_features(rows: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    ts = int(rows[-1]["timestamp"])
    hour = (ts // 3600) % 24
    # Mon=0..Sun=6 (matches datetime.weekday()). 1970-01-01 was a Thursday, so
    # days-since-epoch 0 must map to 3 -> offset +3. is_weekend (dow>=5) then
    # correctly means Sat/Sun, not Fri/Sat.
    dow = ((ts // 86400) + 3) % 7
    return {
        "hour_of_day": float(hour),
        "day_of_week": float(dow),
        "is_weekend": 1.0 if dow >= 5 else 0.0,
        # cyclical encodings so a model sees 23h and 0h as adjacent
        "hour_sin": math.sin(2 * math.pi * hour / 24),
        "hour_cos": math.cos(2 * math.pi * hour / 24),
    }


def compute_features(
    rows: Sequence[dict[str, Any]],
    btc_rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full point-in-time feature snapshot for one symbol from its 1h candles.

    Merges indicators.compute_all() (the existing 24) with the candle-shape,
    volatility, return-moment, order-flow and time blocks. Every value is a
    function of `rows` only -- pass rows with timestamp <= t.
    """
    if len(rows) >= 2:
        ts = [int(r["timestamp"]) for r in rows]
        assert all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1)), \
            "features.compute_features requires rows ascending by timestamp (PIT guard)"
    feats: dict[str, Any] = dict(indicators.compute_all(rows, btc_rows))
    feats.update(candle_shape(rows))
    feats.update(volatility_features(rows))
    feats.update(return_features(rows))
    feats.update(volume_features(rows))
    feats.update(time_features(rows))
    return feats


# Names of the features this module ADDS on top of indicators.compute_all --
# the discovery engine (Phase 3) enumerates candidate signals over these.
ADDED_FEATURES: tuple[str, ...] = (
    "body_pct", "upper_wick_pct", "lower_wick_pct", "close_position_in_range",
    "gap_pct", "true_range_pct",
    "realized_vol_6h", "realized_vol_12h", "realized_vol_24h", "realized_vol_3d",
    "vol_of_vol", "atr_ratio_short_long", "parkinson_vol", "garman_klass_vol",
    "downside_semivol", "upside_semivol",
    "ret_1h", "ret_6h", "ret_12h", "ret_3d", "ret_14d", "momentum_accel",
    "return_skew", "return_kurtosis", "return_autocorr_1",
    "vwap_distance_pct", "dollar_vol_zscore", "obv_slope",
    "order_flow_imbalance", "avg_trade_size",
    "hour_of_day", "day_of_week", "is_weekend", "hour_sin", "hour_cos",
)
