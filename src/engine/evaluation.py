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
from src.engine.calibration import build_calibration_map, calibrate_score
from src.engine.regime import btc_regime, direction_blocked

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


def _meanrev_setup(metrics: dict[str, Any], funding_rate: float | None) -> dict[str, Any] | None:
    """단타: BB(20,2) + RSI(14) mean reversion with regime filters.

    Evidence: BB+RSI was the top scalp performer in fee-inclusive forward tests
    (53.8% win rate); regime filters (no squeeze, no strong trend) cut the
    largest losses (Vantixs 2023-25 backtest: Sharpe 0.48 -> 1.39 with filters).
    """
    rsi = metrics["rsi_14"]
    boll = metrics["bollinger"]
    pct_7d = metrics["pct_7d"]
    if rsi is None or not boll:
        return None
    # regime filters: skip squeezes (breakout risk) and strong trends
    if boll["bandwidth_pct"] < config.MEANREV_MIN_BANDWIDTH_PCT:
        return None
    if pct_7d is not None and abs(pct_7d) > config.MEANREV_MAX_TREND_7D_PCT:
        return None

    if rsi >= config.MEANREV_RSI_HIGH and boll["percent_b"] >= 1.0:
        score = 0.55
        if funding_rate is not None and funding_rate > 0:
            score += 0.05  # crowded longs strengthen the fade
        return {
            "style": STYLE_SCALP,
            "strategy": "mean_reversion",
            "direction": "SHORT",
            "score": round(score, 2),
            "reason": f"RSI {rsi:.0f} 과매수 + 볼린저 상단 이탈 — 통계적 과열, 평균회귀 숏",
        }
    if rsi <= config.MEANREV_RSI_LOW and boll["percent_b"] <= 0.0:
        score = 0.55
        if funding_rate is not None and funding_rate < 0:
            score += 0.05
        return {
            "style": STYLE_SCALP,
            "strategy": "mean_reversion",
            "direction": "LONG",
            "score": round(score, 2),
            "reason": f"RSI {rsi:.0f} 과매도 + 볼린저 하단 이탈 — 통계적 과냉, 평균회귀 롱",
        }
    return None


def _breakout_setup(metrics: dict[str, Any], funding_rate: float | None) -> dict[str, Any] | None:
    """스윙: 20일 돈찬 채널 돌파 (Turtle rules).

    Evidence: 20d Donchian breakout on BTC 2017-2026 — CAGR 48.2% vs 37.3%
    buy&hold, max DD -53.7% vs -83.2%. Funding filter per the combined
    trend+funding framework: skip when the move is already crowded.
    """
    donchian = metrics.get("donchian_20d")
    if not donchian:
        return None

    if donchian["broke_low"]:
        score = 0.6
        # crowded shorts (very negative funding) = squeeze risk, weaker signal
        if funding_rate is not None and funding_rate < config.FUNDING_RATE_LONG_THRESHOLD:
            score -= 0.1
        return {
            "style": STYLE_SWING,
            "strategy": "breakout",
            "direction": "SHORT",
            "score": round(score, 2),
            "reason": "20일 최저가 하향 돌파 (Turtle) — 신규 하락 추세 진입 숏",
        }
    if donchian["broke_high"]:
        score = 0.6
        if funding_rate is not None and funding_rate > config.FUNDING_RATE_SHORT_THRESHOLD:
            score -= 0.1
        return {
            "style": STYLE_SWING,
            "strategy": "breakout",
            "direction": "LONG",
            "score": round(score, 2),
            "reason": "20일 최고가 상향 돌파 (Turtle) — 신규 상승 추세 진입 롱",
        }
    return None


def _tsmom28_setup(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """스윙: 28일 시계열 모멘텀.

    Evidence: crypto TS-momentum studies find ~28d lookback optimal
    (Sharpe 1.51 vs 0.84 market on BTC with 28d/5d parameters).
    """
    pct_30d = metrics["pct_30d"]
    last = metrics.get("last_price")
    sma20 = metrics["sma_20"]
    if pct_30d is None or last is None or sma20 is None:
        return None

    if pct_30d <= -config.MOMENTUM_28D_STRONG_PCT and last < sma20:
        return {
            "style": STYLE_SWING,
            "strategy": "tsmom_28d",
            "direction": "SHORT",
            "score": 0.58,
            "reason": f"28d {pct_30d:.1f}% 하락 모멘텀 + SMA20 아래 — 중기 추세 지속 숏",
        }
    if pct_30d >= config.MOMENTUM_28D_STRONG_PCT and last > sma20:
        return {
            "style": STYLE_SWING,
            "strategy": "tsmom_28d",
            "direction": "LONG",
            "score": 0.58,
            "reason": f"28d +{pct_30d:.1f}% 상승 모멘텀 + SMA20 위 — 중기 추세 지속 롱",
        }
    return None


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


def _proximity_confidence(
    metrics: dict[str, Any], funding_rate: float | None, blocked: bool
) -> float:
    """Graded 0-0.45 confidence for coins without an active setup.

    Measures how close each setup trigger is to firing so 'waiting' coins can
    still be ranked — a coin at 90% of the volume-spike threshold shows a
    higher number than a dead one.
    """
    if blocked:
        return 0.05

    scores = [0.1]
    if funding_rate is not None:
        threshold = max(
            abs(config.FUNDING_RATE_SHORT_THRESHOLD),
            abs(config.FUNDING_RATE_LONG_THRESHOLD),
        )
        if threshold:
            scores.append(min(1.0, abs(funding_rate) / threshold) * 0.45)

    ratio = metrics.get("volume_ratio")
    if ratio is not None and config.VOLUME_SPIKE_RATIO:
        scores.append(min(1.0, ratio / config.VOLUME_SPIKE_RATIO) * 0.4)

    pct_7d = metrics.get("pct_7d")
    if pct_7d is not None and config.MOMENTUM_7D_STRONG_PCT:
        scores.append(min(1.0, abs(pct_7d) / config.MOMENTUM_7D_STRONG_PCT) * 0.4)

    return round(min(0.45, max(scores)), 2)


def _apply_confluence(
    best: dict[str, Any], setups: list[dict[str, Any]], metrics: dict[str, Any]
) -> None:
    """Final confidence: boost when independent setups agree, penalize conflict.

    "우주 정렬" — each additional setup pointing the same direction adds a
    bonus; setups pointing the other way subtract. Crowded retail positioning
    (long/short account ratio) against the trade direction adds a contrarian
    bonus, per the funding+OI confluence framework.
    """
    aligned = [s for s in setups if s is not best and s["direction"] == best["direction"]]
    conflicting = [s for s in setups if s["direction"] != best["direction"]]

    score = best["score"]
    parts: list[str] = [f"{best['strategy']} {best['base_score']:.2f}"]
    if aligned:
        bonus = config.CONFLUENCE_ALIGNED_BONUS * len(aligned)
        score += bonus
        parts.append(f"정렬 +{bonus:.2f} ({', '.join(s['strategy'] for s in aligned)})")
    if conflicting:
        penalty = config.CONFLUENCE_CONFLICT_PENALTY * len(conflicting)
        score -= penalty
        parts.append(f"역방향 -{penalty:.2f} ({', '.join(s['strategy'] for s in conflicting)})")

    lsr = metrics.get("long_short_ratio")
    if lsr is not None:
        if best["direction"] == "SHORT" and lsr >= config.LSR_CROWDED_LONG:
            score += 0.04
            parts.append(f"롱쏠림 {lsr:.2f} +0.04")
        elif best["direction"] == "LONG" and lsr <= config.LSR_CROWDED_SHORT:
            score += 0.04
            parts.append(f"숏쏠림 {lsr:.2f} +0.04")

    best["score"] = round(min(0.95, max(0.05, score)), 2)
    best["confluence"] = {
        "aligned": len(aligned),
        "conflicting": len(conflicting),
        "aligned_strategies": [s["strategy"] for s in aligned],
        "conflicting_strategies": [s["strategy"] for s in conflicting],
        "breakdown": parts,
    }


def _attach_market_metrics(storage: Storage, symbol: str, metrics: dict[str, Any]) -> None:
    """Add open interest / long-short ratio when collected (core symbols)."""
    metrics["open_interest_usd"] = None
    metrics["long_short_ratio"] = None
    try:
        row = storage.get_latest_market_metrics(symbol)
    except Exception:
        return
    if row:
        metrics["open_interest_usd"] = row.get("open_interest_usd")
        metrics["long_short_ratio"] = row.get("long_short_ratio")


def evaluate_symbol(
    storage: Storage,
    symbol: str,
    btc_rows: list[dict[str, Any]] | None,
    regime: dict[str, Any] | None = None,
    calibration: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    regime = regime or {"blocked_directions": []}
    calibration = calibration or {}

    rows = storage.get_prices(symbol, limit=720, timeframe="1h")
    funding = storage.get_latest_funding_rate(symbol)
    funding_rate = float(funding["funding_rate"]) if funding else None

    metrics = indicators.compute_all(rows, btc_rows)
    metrics["last_price"] = float(rows[-1]["close"]) if rows else None
    metrics["funding_rate"] = funding_rate
    metrics["funding_rate_pct"] = funding_rate * 100 if funding_rate is not None else None
    _attach_market_metrics(storage, symbol, metrics)

    blockers = _gate_blockers(metrics, funding_rate)

    setups: list[dict[str, Any]] = []
    policy_notes: list[str] = []
    if not blockers:
        for setup in (
            _funding_setup(metrics, funding_rate),
            _volume_setup(metrics),
            _meanrev_setup(metrics, funding_rate),
            _swing_setup(metrics),
            _breakout_setup(metrics, funding_rate),
            _tsmom28_setup(metrics),
        ):
            if setup is None:
                continue
            direction = setup["direction"]
            if not config.direction_allowed(direction):
                policy_notes.append(
                    f"{setup['style']} {direction} 셋업 감지 — 롱 금지 정책으로 스킵"
                    if direction == "LONG"
                    else f"{setup['style']} {direction} 셋업 감지 — 숏 금지 정책으로 스킵"
                )
                continue
            if direction_blocked(regime, direction):
                policy_notes.append(
                    f"{setup['style']} {direction} 셋업 감지 — {regime.get('reason', 'BTC 레짐 차단')}"
                )
                continue
            calibrated = calibrate_score(
                setup["score"], setup["strategy"], direction, calibration
            )
            setup.update(calibrated)
            setups.append(setup)
        setups.sort(key=lambda s: -s["score"])

    best = setups[0] if setups else None
    if best is not None:
        _apply_confluence(best, setups, metrics)
    tradable = best is not None

    why_not: list[str] = []
    if not tradable:
        if blockers:
            why_not = blockers
        else:
            seen: set[str] = set()
            for reason in policy_notes + _why_not(metrics, funding_rate):
                if reason not in seen:
                    seen.add(reason)
                    why_not.append(reason)
            why_not = why_not[:4]

    confidence = (
        best["score"]
        if best
        else _proximity_confidence(metrics, funding_rate, blocked=bool(blockers))
    )

    return {
        "symbol": symbol,
        "base": symbol.split("/")[0],
        "tradable": tradable,
        "verdict": best,
        "confidence": confidence,
        "other_setups": setups[1:],
        "why_not": why_not,
        "metrics": metrics,
    }


def evaluate_all(
    storage: Storage | None = None, symbols: list[str] | None = None
) -> list[dict[str, Any]]:
    from src.symbols import trading_symbols

    storage = storage or get_storage()
    symbols = symbols or trading_symbols()
    btc_rows = storage.get_prices(config.SYMBOL, limit=720, timeframe="1h")
    regime = btc_regime(storage)
    calibration = build_calibration_map(storage)

    results = []
    for symbol in symbols:
        results.append(
            evaluate_symbol(
                storage,
                symbol,
                btc_rows if symbol != config.SYMBOL else None,
                regime=regime,
                calibration=calibration,
            )
        )
    results.sort(
        key=lambda r: (
            0 if r["tradable"] else 1,
            -r["confidence"],
            -(r["metrics"].get("dollar_volume_24h") or 0.0),
        )
    )
    return results
