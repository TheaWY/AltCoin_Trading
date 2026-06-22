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


def _price_momentum(storage: Storage, symbol: str) -> dict[str, float | None]:
    prices = storage.get_prices(symbol, limit=168)
    if len(prices) < 2:
        return {"pct_24h": None, "pct_7d": None}

    latest = float(prices[-1]["close"])
    ts_latest = int(prices[-1]["timestamp"])

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
    }


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
        latest_signal = self.storage.get_latest_signal(symbol=symbol)

        price = float(latest_price["close"]) if latest_price else None
        funding_rate = float(latest_funding["funding_rate"]) if latest_funding else None
        momentum = _price_momentum(self.storage, symbol)

        direction = latest_signal["direction"] if latest_signal else SignalDirection.NONE.value

        fr = funding_rate or 0.0
        short_term = _analyze_short_term(direction, fr, momentum["pct_24h"])
        swing = _analyze_swing(direction, fr, momentum["pct_24h"], momentum["pct_7d"])

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
