"""Per-symbol trade evaluation — 단타/스윙 verdict with quant-metric reasons.

Produces, for every tracked symbol:
  - a full quant metric snapshot (see src/engine/indicators.py),
  - a verdict: tradable now (단타 or 스윙, with strategy + direction + short
    Korean reason) or not tradable (list of short Korean reasons why).

The gates and setups are threshold-based and fully explainable — every
verdict can be traced to the displayed metrics.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine import indicators

STYLE_SCALP = "단타"
STYLE_SWING = "스윙"


def _fmt(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _gate_blockers(metrics: dict[str, Any], funding_rate: float | None) -> list[str]:
    """Hard blockers that disqualify a symbol regardless of setups."""
    blockers: list[str] = []

    if metrics["candles"] < config.EVAL_MIN_CANDLES:
        blockers.append(
            f"데이터 수집 중 ({metrics['candles']}/{config.EVAL_MIN_CANDLES} 캔들) — 지표 신뢰도 부족"
        )
        return blockers

    dollar_vol = metrics["dollar_volume_24h"]
    if dollar_vol is not None and dollar_vol < config.EVAL_MIN_DOLLAR_VOLUME_24H:
        blockers.append(
            f"24h 거래대금 ${dollar_vol / 1e6:.1f}M — 유동성 부족, 슬리피지 위험"
        )

    atr = metrics["atr_pct"]
    if atr is not None and atr < config.EVAL_ATR_MIN_PCT:
        blockers.append(
            f"ATR {atr:.2f}% < {config.EVAL_ATR_MIN_PCT}% — 변동성 부족, 손익비 안 나옴"
        )

    return blockers


def _funding_setup(metrics: dict[str, Any], funding_rate: float | None) -> dict[str, Any] | None:
    """단타: funding-rate reversal (perp carry crowding)."""
    if funding_rate is None:
        return None
    rate_pct = funding_rate * 100
    rsi = metrics["rsi_14"]

    if funding_rate > config.FUNDING_RATE_SHORT_THRESHOLD:
        score = 0.6
        if rsi is not None and rsi >= 65:
            score += 0.1
        return {
            "style": STYLE_SCALP,
            "strategy": "funding_rate",
            "direction": "SHORT",
            "score": round(score, 2),
            "reason": f"펀딩비 +{rate_pct:.3f}% 과열 — 롱 쏠림, 되돌림 숏"
            + (f" (RSI {rsi:.0f} 과매수 동조)" if rsi is not None and rsi >= 65 else ""),
        }
    if funding_rate < config.FUNDING_RATE_LONG_THRESHOLD:
        score = 0.6
        if rsi is not None and rsi <= 35:
            score += 0.1
        return {
            "style": STYLE_SCALP,
            "strategy": "funding_rate",
            "direction": "LONG",
            "score": round(score, 2),
            "reason": f"펀딩비 {rate_pct:.3f}% 음수 — 숏이 롱에게 지불, 반등 롱"
            + (f" (RSI {rsi:.0f} 과매도 동조)" if rsi is not None and rsi <= 35 else ""),
        }
    return None


def _volume_setup(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """단타: abnormal volume with a directional move behind it."""
    ratio = metrics["volume_ratio"]
    pct_24h = metrics["pct_24h"]
    if ratio is None or pct_24h is None:
        return None
    if ratio < config.VOLUME_SPIKE_RATIO or abs(pct_24h) < 1.0:
        return None

    direction = "LONG" if pct_24h > 0 else "SHORT"
    rsi = metrics["rsi_14"]
    score = 0.55
    # don't chase a move that is already overextended
    if rsi is not None and ((direction == "LONG" and rsi >= 75) or (direction == "SHORT" and rsi <= 25)):
        score -= 0.15
    return {
        "style": STYLE_SCALP,
        "strategy": "volume_spike",
        "direction": direction,
        "score": round(score, 2),
        "reason": f"거래량 평소 {ratio:.1f}배 + 24h {pct_24h:+.1f}% — 수급 확인된 {'상승' if direction == 'LONG' else '하락'} 추종",
    }


def _swing_setup(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """스윙: 7d time-series momentum with trend structure confirmation."""
    pct_7d = metrics["pct_7d"]
    price_sma20 = metrics["sma_20"]
    price_sma50 = metrics["sma_50"]
    macd_state = metrics["macd"]
    if pct_7d is None or price_sma20 is None or price_sma50 is None:
        return None

    last = metrics.get("last_price")
    if last is None:
        return None

    uptrend_structure = last > price_sma20 > price_sma50
    downtrend_structure = last < price_sma20 < price_sma50
    macd_up = bool(macd_state and macd_state["hist"] > 0)
    macd_down = bool(macd_state and macd_state["hist"] < 0)
    sharpe = metrics["sharpe_7d"]
    threshold = config.MOMENTUM_7D_STRONG_PCT

    if pct_7d >= threshold and uptrend_structure and macd_up:
        score = 0.6
        if sharpe is not None and sharpe > 1.5:
            score += 0.1
        rsi = metrics["rsi_14"]
        if rsi is not None and rsi >= 78:
            score -= 0.1
        return {
            "style": STYLE_SWING,
            "strategy": "momentum",
            "direction": "LONG",
            "score": round(score, 2),
            "reason": f"7d +{pct_7d:.1f}% 추세 + 가격>SMA20>SMA50 정배열 + MACD 상방 — 추세 지속 롱",
        }
    if pct_7d <= -threshold and downtrend_structure and macd_down:
        score = 0.6
        if sharpe is not None and sharpe < -1.5:
            score += 0.1
        return {
            "style": STYLE_SWING,
            "strategy": "momentum",
            "direction": "SHORT",
            "score": round(score, 2),
            "reason": f"7d {pct_7d:.1f}% 하락 추세 + 역배열 + MACD 하방 — 추세 지속 숏",
        }
    return None


def _why_not(metrics: dict[str, Any], funding_rate: float | None) -> list[str]:
    """Short Korean reasons why neither 단타 nor 스윙 works right now."""
    reasons: list[str] = []

    rate_pct = (funding_rate or 0.0) * 100
    reasons.append(
        f"펀딩비 {rate_pct:+.3f}% 중립 구간 — 쏠림 없음"
        if funding_rate is not None
        else "펀딩비 데이터 없음"
    )

    ratio = metrics["volume_ratio"]
    if ratio is not None and ratio < config.VOLUME_SPIKE_RATIO:
        reasons.append(f"거래량 평소 {ratio:.1f}배 — 수급 신호 없음")

    pct_7d = metrics["pct_7d"]
    if pct_7d is not None and abs(pct_7d) < config.MOMENTUM_7D_STRONG_PCT:
        reasons.append(f"7d {pct_7d:+.1f}% — 스윙 추세 미형성 (±{config.MOMENTUM_7D_STRONG_PCT:.0f}% 필요)")
    elif pct_7d is not None:
        last = metrics.get("last_price")
        sma20 = metrics["sma_20"]
        macd_state = metrics["macd"]
        if last is not None and sma20 is not None and (
            (pct_7d > 0 and last < sma20) or (pct_7d < 0 and last > sma20)
        ):
            reasons.append(f"7d {pct_7d:+.1f}%지만 SMA20 이탈 — 추세 구조 붕괴")
        elif macd_state is not None:
            reasons.append(f"7d {pct_7d:+.1f}%지만 MACD 미확인 — 진입 대기")

    corr = metrics["btc_correlation"]
    if corr is not None and corr >= config.EVAL_BTC_CORR_MAX:
        reasons.append(f"BTC 상관 {corr:.2f} — 독립 움직임 없음, BTC 방향에 종속")

    rsi = metrics["rsi_14"]
    if rsi is not None and 40 <= rsi <= 60:
        reasons.append(f"RSI {rsi:.0f} 중립 — 과열/과매도 엣지 없음")

    return reasons[:4]


def evaluate_symbol(
    storage: Storage,
    symbol: str,
    btc_rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    rows = storage.get_prices(symbol, limit=720, timeframe="1h")
    funding = storage.get_latest_funding_rate(symbol)
    funding_rate = float(funding["funding_rate"]) if funding else None

    metrics = indicators.compute_all(rows, btc_rows)
    metrics["last_price"] = float(rows[-1]["close"]) if rows else None
    metrics["funding_rate"] = funding_rate
    metrics["funding_rate_pct"] = funding_rate * 100 if funding_rate is not None else None

    blockers = _gate_blockers(metrics, funding_rate)

    setups: list[dict[str, Any]] = []
    if not blockers:
        for setup in (
            _funding_setup(metrics, funding_rate),
            _volume_setup(metrics),
            _swing_setup(metrics),
        ):
            if setup:
                setups.append(setup)
        setups.sort(key=lambda s: -s["score"])

    best = setups[0] if setups else None
    tradable = best is not None
    return {
        "symbol": symbol,
        "base": symbol.split("/")[0],
        "tradable": tradable,
        "verdict": best,
        "other_setups": setups[1:],
        "why_not": blockers if blockers else (_why_not(metrics, funding_rate) if not tradable else []),
        "metrics": metrics,
    }


def evaluate_all(
    storage: Storage | None = None, symbols: list[str] | None = None
) -> list[dict[str, Any]]:
    from src.symbols import trading_symbols

    storage = storage or get_storage()
    symbols = symbols or trading_symbols()
    btc_rows = storage.get_prices(config.SYMBOL, limit=720, timeframe="1h")

    results = []
    for symbol in symbols:
        results.append(
            evaluate_symbol(storage, symbol, btc_rows if symbol != config.SYMBOL else None)
        )
    results.sort(
        key=lambda r: (
            0 if r["tradable"] else 1,
            -(r["verdict"]["score"] if r["verdict"] else 0.0),
            -(r["metrics"].get("dollar_volume_24h") or 0.0),
        )
    )
    return results
