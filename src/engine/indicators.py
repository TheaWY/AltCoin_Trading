"""Quant indicators computed from stored OHLCV rows.

Metric selection follows the crypto asset-pricing literature:

- Time-series momentum (24h/7d/30d returns): the strongest documented crypto
  return predictor (Liu & Tsyvinski, "Risks and Returns of Cryptocurrency",
  RFS 2021; Moskowitz, Ooi & Pedersen, "Time Series Momentum", JFE 2012).
- Realized volatility / ATR: volatility scaling for position sizing and stops
  (Moreira & Muir, "Volatility-Managed Portfolios", JF 2017).
- BTC correlation & beta: alts are priced against the crypto market factor
  (Liu, Tsyvinski & Wu, "Common Risk Factors in Cryptocurrency", JF 2022) —
  an alt tightly coupled to BTC offers no independent trade.
- Amihud illiquidity & dollar volume: liquidity screens used in the same
  cross-sectional literature (Amihud, JFM 2002).
- Distance from rolling high: anchoring effect (George & Hwang, "The 52-Week
  High and Momentum Investing", JF 2004).
- RSI / MACD / Bollinger %B: standard technical state descriptors used as
  features in crypto ML forecasting studies.
- Sharpe ratio & max drawdown: risk-adjusted quality of the recent trend.

All functions take candle rows as dicts (ascending by timestamp) with
open/high/low/close/volume keys, as returned by Storage.get_prices().
"""

from __future__ import annotations

import math
from typing import Any, Sequence

HOURS_PER_YEAR = 24 * 365


def closes(rows: Sequence[dict[str, Any]]) -> list[float]:
    return [float(r["close"]) for r in rows]


def pct_returns(values: Sequence[float]) -> list[float]:
    out = []
    for prev, cur in zip(values, values[1:]):
        out.append((cur - prev) / prev if prev else 0.0)
    return out


def momentum_pct(values: Sequence[float], periods: int) -> float | None:
    """Simple time-series momentum: % change over the last `periods` candles."""
    if len(values) <= periods:
        return None
    past = values[-1 - periods]
    if not past:
        return None
    return (values[-1] - past) / past * 100.0


def sma(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema_series(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * k + out[-1] * (1 - k))
    return out


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    """Wilder-smoothed Relative Strength Index."""
    if len(values) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for prev, cur in zip(values[:period], values[1 : period + 1]):
        change = cur - prev
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    for prev, cur in zip(values[period:], values[period + 1 :]):
        change = cur - prev
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr_pct(rows: Sequence[dict[str, Any]], period: int = 14) -> float | None:
    """Average True Range as % of the last close (volatility for stop sizing)."""
    if len(rows) < period + 1:
        return None
    trs = []
    for prev, cur in zip(rows, rows[1:]):
        prev_close = float(prev["close"])
        high, low = float(cur["high"]), float(cur["low"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    last_close = float(rows[-1]["close"])
    return (atr / last_close * 100.0) if last_close else None


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float] | None:
    if len(values) < slow + signal:
        return None
    fast_ema = ema_series(values, fast)
    slow_ema = ema_series(values, slow)
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema_series(macd_line, signal)
    hist = macd_line[-1] - signal_line[-1]
    prev_hist = macd_line[-2] - signal_line[-2]
    scale = values[-1] or 1.0
    return {
        "macd": macd_line[-1],
        "signal": signal_line[-1],
        "hist": hist,
        "hist_pct": hist / scale * 100.0,
        "hist_rising": hist > prev_hist,
    }


def bollinger(values: Sequence[float], period: int = 20, num_std: float = 2.0) -> dict[str, float] | None:
    if len(values) < period:
        return None
    window = values[-period:]
    mid = sum(window) / period
    var = sum((v - mid) ** 2 for v in window) / period
    std = math.sqrt(var)
    upper = mid + num_std * std
    lower = mid - num_std * std
    last = values[-1]
    width = upper - lower
    return {
        "percent_b": ((last - lower) / width) if width else 0.5,
        "bandwidth_pct": (width / mid * 100.0) if mid else 0.0,
    }


def realized_vol_annualized(values: Sequence[float], window: int) -> float | None:
    """Annualized realized volatility (%) from hourly log returns."""
    if len(values) < window + 1:
        window = len(values) - 1
    if window < 12:
        return None
    rets = []
    for prev, cur in zip(values[-window - 1 : -1], values[-window:]):
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 12:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(HOURS_PER_YEAR) * 100.0


def sharpe_ratio(values: Sequence[float], window: int) -> float | None:
    """Annualized Sharpe of hourly returns over the window (risk-free ~ 0)."""
    if len(values) < window + 1:
        window = len(values) - 1
    if window < 24:
        return None
    rets = pct_returns(values[-window - 1 :])
    if not rets:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var)
    if std == 0:
        return None
    return mean / std * math.sqrt(HOURS_PER_YEAR)


def max_drawdown_pct(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100.0)
    return max_dd


def correlation_and_beta(
    asset_rows: Sequence[dict[str, Any]],
    market_rows: Sequence[dict[str, Any]],
    min_points: int = 24,
) -> dict[str, float | None]:
    """Pearson correlation and OLS beta of hourly returns vs the market (BTC).

    Rows are aligned on candle timestamps so gaps don't skew the estimate.
    """
    market_by_ts = {int(r["timestamp"]): float(r["close"]) for r in market_rows}
    pairs: list[tuple[float, float]] = []
    prev_a: float | None = None
    prev_m: float | None = None
    for row in asset_rows:
        ts = int(row["timestamp"])
        m_close = market_by_ts.get(ts)
        a_close = float(row["close"])
        if m_close is not None:
            if prev_a and prev_m:
                pairs.append(((a_close - prev_a) / prev_a, (m_close - prev_m) / prev_m))
            prev_a, prev_m = a_close, m_close
    if len(pairs) < min_points:
        return {"correlation": None, "beta": None, "points": len(pairs)}

    a_rets = [p[0] for p in pairs]
    m_rets = [p[1] for p in pairs]
    mean_a = sum(a_rets) / len(a_rets)
    mean_m = sum(m_rets) / len(m_rets)
    cov = sum((a - mean_a) * (m - mean_m) for a, m in pairs) / len(pairs)
    var_a = sum((a - mean_a) ** 2 for a in a_rets) / len(pairs)
    var_m = sum((m - mean_m) ** 2 for m in m_rets) / len(pairs)
    if var_a <= 0 or var_m <= 0:
        return {"correlation": None, "beta": None, "points": len(pairs)}
    return {
        "correlation": cov / math.sqrt(var_a * var_m),
        "beta": cov / var_m,
        "points": len(pairs),
    }


def amihud_illiquidity(rows: Sequence[dict[str, Any]], window: int = 168) -> float | None:
    """Amihud (2002) price impact: mean(|return| / dollar volume), scaled 1e9.

    Higher = more illiquid (a given trade moves price more).
    """
    rows = rows[-window - 1 :]
    if len(rows) < 24:
        return None
    ratios = []
    for prev, cur in zip(rows, rows[1:]):
        prev_close = float(prev["close"])
        dollar_vol = float(cur["close"]) * float(cur["volume"])
        if prev_close > 0 and dollar_vol > 0:
            ret = abs(float(cur["close"]) - prev_close) / prev_close
            ratios.append(ret / dollar_vol)
    if not ratios:
        return None
    return sum(ratios) / len(ratios) * 1e9


def dollar_volume_24h(rows: Sequence[dict[str, Any]]) -> float | None:
    window = rows[-24:]
    if not window:
        return None
    return sum(float(r["close"]) * float(r["volume"]) for r in window)


def distance_from_extremes(rows: Sequence[dict[str, Any]], window: int = 720) -> dict[str, float | None]:
    """% distance of last close from the rolling high/low (anchoring metric)."""
    rows = rows[-window:]
    if len(rows) < 24:
        return {"from_high_pct": None, "from_low_pct": None}
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    last = float(rows[-1]["close"])
    hi, lo = max(highs), min(lows)
    return {
        "from_high_pct": ((last - hi) / hi * 100.0) if hi else None,
        "from_low_pct": ((last - lo) / lo * 100.0) if lo else None,
    }


def donchian_break(rows: Sequence[dict[str, Any]], window: int = 480) -> dict[str, Any] | None:
    """Donchian channel breakout state (Turtle rules; 480 x 1h = 20 days).

    Compares the last close against the channel formed by the *prior* candles,
    so a fresh breakout is detected the moment it happens.
    """
    if len(rows) < window // 2:
        return None
    prior = rows[-window - 1 : -1]
    if len(prior) < 24:
        return None
    channel_high = max(float(r["high"]) for r in prior)
    channel_low = min(float(r["low"]) for r in prior)
    last = float(rows[-1]["close"])
    return {
        "high": channel_high,
        "low": channel_low,
        "broke_high": last > channel_high,
        "broke_low": last < channel_low,
    }


def volume_ratio(rows: Sequence[dict[str, Any]], lookback: int = 24) -> float | None:
    if len(rows) < 2:
        return None
    window = rows[-lookback - 1 : -1] or rows[:-1]
    avg = sum(float(r["volume"]) for r in window) / len(window)
    if not avg:
        return None
    return float(rows[-1]["volume"]) / avg


def compute_all(
    rows: Sequence[dict[str, Any]],
    btc_rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full metric snapshot for one symbol from 1h candles."""
    values = closes(rows)
    corr = (
        correlation_and_beta(rows[-720:], btc_rows[-720:])
        if btc_rows
        else {"correlation": None, "beta": None, "points": 0}
    )
    extremes = distance_from_extremes(rows)
    boll = bollinger(values)
    macd_state = macd(values)
    # Storage retains exactly 720 x 1h candles for the extended universe, but
    # momentum_pct(720) needs 721 points — accept >=29d of history as "30d".
    pct_30d = momentum_pct(values, 720)
    if pct_30d is None and len(values) >= 696:
        pct_30d = momentum_pct(values, len(values) - 1)
    return {
        "candles": len(rows),
        "pct_24h": momentum_pct(values, 24),
        "pct_7d": momentum_pct(values, 168),
        "pct_30d": pct_30d,
        "rsi_14": rsi(values, 14),
        "atr_pct": atr_pct(rows, 14),
        "realized_vol_7d": realized_vol_annualized(values, 168),
        "realized_vol_30d": realized_vol_annualized(values, 720),
        "sharpe_7d": sharpe_ratio(values, 168),
        "sharpe_30d": sharpe_ratio(values, 720),
        "max_drawdown_30d": max_drawdown_pct(values[-720:]),
        "sma_20": sma(values, 20),
        "sma_50": sma(values, 50),
        "macd": macd_state,
        "bollinger": boll,
        "btc_correlation": corr["correlation"],
        "btc_beta": corr["beta"],
        "amihud_illiq": amihud_illiquidity(rows),
        "dollar_volume_24h": dollar_volume_24h(rows),
        "volume_ratio": volume_ratio(rows),
        "from_high_30d_pct": extremes["from_high_pct"],
        "from_low_30d_pct": extremes["from_low_pct"],
        "donchian_20d": donchian_break(rows, 480),
    }
