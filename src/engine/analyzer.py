"""Investment analysis — short-term and swing ratings per symbol."""

from __future__ import annotations

from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.strategies.base import SignalDirection
from src.symbols import base_asset

RATINGS = ("AVOID", "WAIT", "NEUTRAL", "GOOD", "STRONG")


def _pct_change(current: float, past: float) -> float | None:
    if not past:
        return None
    return ((current - past) / past) * 100.0


def _price_momentum(storage: Storage, symbol: str) -> dict[str, Any]:
    prices = storage.get_prices(symbol, limit=168)
    if len(prices) < 2:
        return {
            "pct_24h": None,
            "pct_7d": None,
            "volume_spike": False,
            "volume_ratio": 0.0,
            "volume_trend": "flat",
        }

    latest = float(prices[-1]["close"])
    ts_latest = int(prices[-1]["timestamp"])
    latest_volume = float(prices[-1]["volume"])
    volume_stats = storage.get_volume_stats(symbol, timeframe="1h", lookback=24)
    avg_volume = volume_stats["avg"]
    volume_ratio = (latest_volume / float(avg_volume)) if avg_volume else 0.0
    volume_spike = volume_ratio >= 2.0

    volumes_24h = [float(row["volume"]) for row in prices[-24:]]
    recent_volume = volumes_24h[-6:]
    previous_volume = volumes_24h[:-6]
    volume_trend = "flat"
    if recent_volume and previous_volume:
        recent_avg = sum(recent_volume) / len(recent_volume)
        previous_avg = sum(previous_volume) / len(previous_volume)
        if previous_avg and recent_avg > previous_avg * 1.05:
            volume_trend = "rising"
        elif previous_avg and recent_avg < previous_avg * 0.95:
            volume_trend = "falling"

    def close_near(seconds_ago: int) -> float | None:
        target = ts_latest - seconds_ago
        candidate = None
        for row in prices:
            if int(row["timestamp"]) <= target:
                candidate = float(row["close"])
        return candidate

    past_24h = close_near(24 * 3600) or float(prices[max(0, len(prices) - 25)]["close"])
    past_7d = close_near(7 * 24 * 3600) or float(prices[0]["close"])

    return {
        "pct_24h": _pct_change(latest, past_24h),
        "pct_7d": _pct_change(latest, past_7d),
        "volume_spike": volume_spike,
        "volume_ratio": round(volume_ratio, 2),
        "volume_trend": volume_trend,
    }


def _apply_volume_signal(
    direction: str,
    pct_24h: float | None,
    confidence: float,
    reason_parts: list[str],
    volume_spike: bool,
) -> float:
    if not volume_spike:
        return confidence

    pct = pct_24h or 0.0
    aligned = (
        direction == SignalDirection.LONG.value and pct > 0
    ) or (
        direction == SignalDirection.SHORT.value and pct < 0
    )
    against = (
        direction == SignalDirection.LONG.value and pct < 0
    ) or (
        direction == SignalDirection.SHORT.value and pct > 0
    )

    if aligned:
        confidence += 0.1
        reason_parts.append("volume spike confirms direction")
    elif against:
        confidence -= 0.1
        reason_parts.append("volume spike against direction")
    return confidence


def _rating_from_score(score: float) -> str:
    if score >= 0.8:
        return "STRONG"
    if score >= 0.65:
        return "GOOD"
    if score >= 0.45:
        return "NEUTRAL"
    if score >= 0.3:
        return "WAIT"
    return "AVOID"


def _analyze_short_term(
    direction: str,
    funding_rate: float,
    pct_24h: float | None,
    volume_spike: bool,
) -> dict[str, Any]:
    """1–3 day horizon — funding reversal + 24h momentum alignment."""
    if direction == SignalDirection.NONE.value:
        return {
            "timeframe": "short_term",
            "horizon": "1-3 days",
            "rating": "NEUTRAL",
            "action": "HOLD",
            "confidence": 0.35,
            "worth_it": False,
            "reason": "No funding-rate edge — wait for setup",
        }

    pct = pct_24h or 0.0
    confidence = 0.5
    reason_parts = []

    if direction == SignalDirection.LONG.value:
        confidence += 0.15 if funding_rate < config.FUNDING_RATE_LONG_THRESHOLD else 0.05
        if pct > 0:
            confidence += 0.1
            reason_parts.append(f"24h +{pct:.1f}% supports long")
        elif pct < -config.MOMENTUM_COUNTER_TREND_PCT:
            confidence -= 0.25
            reason_parts.append(f"24h {pct:.1f}% — falling knife risk")
        else:
            reason_parts.append("24h flat — mean-reversion long")
        action = "LONG"
    else:
        confidence += 0.15 if funding_rate > config.FUNDING_RATE_SHORT_THRESHOLD else 0.05
        if pct < 0:
            confidence += 0.1
            reason_parts.append(f"24h {pct:.1f}% supports short")
        elif pct > config.MOMENTUM_COUNTER_TREND_PCT:
            confidence -= 0.25
            reason_parts.append(f"24h +{pct:.1f}% — shorting into strength")
        else:
            reason_parts.append("24h flat — fade crowded longs")
        action = "SHORT"

    confidence = _apply_volume_signal(
        direction, pct_24h, confidence, reason_parts, volume_spike
    )
    confidence = max(0.0, min(1.0, confidence))
    rating = _rating_from_score(confidence)
    worth = rating in ("GOOD", "STRONG") and confidence >= config.SHORT_TERM_MIN_CONFIDENCE

    return {
        "timeframe": "short_term",
        "horizon": "1-3 days",
        "rating": rating,
        "action": action,
        "confidence": round(confidence, 2),
        "worth_it": worth,
        "reason": "; ".join(reason_parts) or "Funding signal active",
    }


def _analyze_swing(
    direction: str,
    funding_rate: float,
    pct_24h: float | None,
    pct_7d: float | None,
    volume_spike: bool,
) -> dict[str, Any]:
    """1–2 week horizon — 7d trend + funding alignment."""
    if direction == SignalDirection.NONE.value:
        return {
            "timeframe": "swing",
            "horizon": "1-2 weeks",
            "rating": "NEUTRAL",
            "action": "HOLD",
            "confidence": 0.3,
            "worth_it": False,
            "reason": "No clear swing setup",
        }

    d7 = pct_7d or 0.0
    d24 = pct_24h or 0.0
    confidence = 0.45
    reason_parts = []

    if direction == SignalDirection.LONG.value:
        action = "LONG"
        if d7 >= config.MOMENTUM_7D_STRONG_PCT:
            confidence += 0.2
            reason_parts.append(f"7d uptrend +{d7:.1f}%")
        elif d7 <= -config.MOMENTUM_7D_STRONG_PCT:
            confidence -= 0.2
            reason_parts.append(f"7d downtrend {d7:.1f}% — counter-trend long")
        if d24 > 0:
            confidence += 0.05
        if funding_rate < config.FUNDING_RATE_LONG_THRESHOLD:
            confidence += 0.1
    else:
        action = "SHORT"
        if d7 <= -config.MOMENTUM_7D_STRONG_PCT:
            confidence += 0.2
            reason_parts.append(f"7d downtrend {d7:.1f}%")
        elif d7 >= config.MOMENTUM_7D_STRONG_PCT:
            confidence -= 0.2
            reason_parts.append(f"7d uptrend +{d7:.1f}% — counter-trend short")
        if d24 < 0:
            confidence += 0.05
        if funding_rate > config.FUNDING_RATE_SHORT_THRESHOLD:
            confidence += 0.1

    confidence = _apply_volume_signal(
        direction, pct_24h, confidence, reason_parts, volume_spike
    )
    confidence = max(0.0, min(1.0, confidence))
    rating = _rating_from_score(confidence)
    worth = rating in ("GOOD", "STRONG") and confidence >= config.SWING_MIN_CONFIDENCE

    return {
        "timeframe": "swing",
        "horizon": "1-2 weeks",
        "rating": rating,
        "action": action,
        "confidence": round(confidence, 2),
        "worth_it": worth,
        "reason": "; ".join(reason_parts) or "Trend + funding mixed",
    }


class AltAnalyzer:
    """Build per-symbol investment view for dashboard and paper trading."""

    def __init__(self, storage: Storage | None = None) -> None:
        self.storage = storage or get_storage()

    def analyze(self, symbol: str, strategy_name: str | None = None) -> dict[str, Any]:
        latest_price = self.storage.get_latest_price(symbol)
        latest_funding = self.storage.get_latest_funding_rate(symbol)
        # Only the primary strategy's signal drives the trading view; secondary
        # strategies record signals for accuracy comparison but must not flip
        # the recommended direction.
        latest_signal = self.storage.get_latest_signal(
            symbol=symbol, strategy=strategy_name or config.PRIMARY_STRATEGY
        )

        price = float(latest_price["close"]) if latest_price else None
        funding_rate = float(latest_funding["funding_rate"]) if latest_funding else None
        momentum = _price_momentum(self.storage, symbol)

        direction = latest_signal["direction"] if latest_signal else SignalDirection.NONE.value

        fr = funding_rate or 0.0
        short_term = _analyze_short_term(
            direction,
            fr,
            momentum["pct_24h"],
            momentum["volume_spike"],
        )
        swing = _analyze_swing(
            direction,
            fr,
            momentum["pct_24h"],
            momentum["pct_7d"],
            momentum["volume_spike"],
        )

        best = short_term if short_term["confidence"] >= swing["confidence"] else swing
        worth = (
            direction in (SignalDirection.LONG.value, SignalDirection.SHORT.value)
            and (
                short_term["worth_it"]
                or swing["worth_it"]
            )
            and best["confidence"] >= config.PAPER_MIN_CONFIDENCE
        )

        open_trade = self.storage.get_open_trade_for_symbol(symbol)

        return {
            "symbol": symbol,
            "base": base_asset(symbol),
            "price": price,
            "funding_rate": funding_rate,
            "funding_rate_pct": (funding_rate * 100) if funding_rate is not None else None,
            "pct_24h": momentum["pct_24h"],
            "pct_7d": momentum["pct_7d"],
            "volume_spike": momentum["volume_spike"],
            "volume_ratio": momentum["volume_ratio"],
            "volume_trend": momentum["volume_trend"],
            "signal": latest_signal,
            "direction": direction,
            "short_term": short_term,
            "swing": swing,
            "worth_investing": worth,
            "recommended_style": best["timeframe"] if worth else None,
            "recommended_action": best["action"] if worth else "HOLD",
            "confidence": best["confidence"],
            "open_trade": open_trade,
        }

    def analyze_all(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        from src.symbols import trading_symbols

        symbols = symbols or trading_symbols()
        results = [self.analyze(sym) for sym in symbols]
        results.sort(
            key=lambda r: (
                0 if r["worth_investing"] else 1,
                -r["confidence"],
            )
        )
        return results
