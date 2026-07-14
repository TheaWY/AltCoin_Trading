"""Per-symbol trade evaluation — 단타/스윙 verdict with quant-metric reasons.

Produces, for every tracked symbol:
  - a full quant metric snapshot (see src/engine/indicators.py),
  - a verdict: tradable now (단타 or 스윙, with strategy + direction + short
    Korean reason) or not tradable (list of short Korean reasons why).

The gates and setups are threshold-based and fully explainable — every
verdict can be traced to the displayed metrics.
"""

from __future__ import annotations

import os
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine import hedge as hedge_engine
from src.engine import indicators
from src.engine.calibration import build_calibration_map, calibrate_score
from src.engine.regime import btc_regime, direction_blocked
from src.research.candle_signals import MIN_HISTORY as _CANDLE_MIN_HISTORY
from src.research.candle_signals import PUMP24_PCTL
from src.research.candle_signals import ROLLING_WINDOW as _CANDLE_ROLLING_WINDOW
from src.research.candle_signals import capitulation_bar as _capitulation_bar_events
from src.research.event_study import VOLUME_ZSCORE_BASELINE_HOURS
from src.research.event_study import sig_volume_spike as _volume_zscore_events
from src.research.rel_strength import LOOKBACK_BARS as REL_STRENGTH_LOOKBACK_BARS
from src.research.rel_strength import MIN_HISTORY as REL_STRENGTH_MIN_HISTORY
from src.research.rel_strength import PCTL as REL_STRENGTH_PCTL
from src.research.rel_strength import SEVEN_DAYS_S as _SEVEN_DAYS_S
from src.research.rel_strength import percentile_rank, spread

STRATEGY_CATEGORY_MAP = {
    "momentum": {"liquid_trend", "large_beta"},
    "mean_reversion": {"range_meanrev"},
    "volume_spike": {"volume_surge", "liquid_trend"},
    "funding_rate": {"crowded_funding"},
    "funding_carry": {"crowded_funding"},
    "positioning_short": {"crowded_funding"},
    "failed_pump_short": {"liquid_trend", "volume_surge"},
    "failed_pump_long": {"liquid_trend", "volume_surge"},
    # rel_strength_rotation, capitulation_bar, volume_zscore_3plus,
    # pump24_extreme deliberately absent: none of their event studies were
    # run per-category, so in CATEGORY_STRATEGY_MODE="matched" each is
    # blocked (empty allowed set) rather than guessed at.
}

STYLE_SCALP = "단타"
STYLE_SWING = "스윙"
# Distinct from STYLE_SWING so cycle.py's STYLE_MAP routes it to
# config.normalize_holding_style's "rel_strength_neutral" (72h hold cap) --
# not the 720h swing cap _rel_strength_setup's evidence doesn't cover.
STYLE_REL_STRENGTH_NEUTRAL = "시장중립72h"
# Same pattern, one per candle_signals_round2 setup (research_decisions,
# subject='candle_signals_round2') -- hold hours match exactly the horizon
# that survived multiple-comparisons correction, not a generic bucket.
STYLE_CAPITULATION_BOUNCE = "반등72h"
STYLE_VOLUME_ZSCORE = "거래량72h"
STYLE_PUMP24_EXTREME = "펌프24h"
STYLE_FAILED_PUMP_LONG = "펌프반등"


def _direction_label(direction: str | None) -> str:
    if direction == "LONG":
        return "상승 포지션"
    if direction == "SHORT":
        return "하락 포지션"
    return "방향 미정"


def _style_label(style: str | None) -> str:
    normalized = config.normalize_holding_style(style)
    if normalized == "scalp":
        return "단타"
    if normalized == "swing":
        return "스윙"
    if normalized == "long_term_hold":
        return "장투"
    return str(style or "보유기간 미정")


def _setup_label(setup: dict[str, Any]) -> str:
    return f"{_style_label(setup.get('style'))} {_direction_label(setup.get('direction'))} 감지"


def _fmt(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _env_bool_dynamic(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in ("true", "1", "yes", "on")


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
    if not config.SETUP_FUNDING_ENABLED:
        return None
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
    """단타: abnormal volume with a directional move behind it.

    This is disabled by default because the last full-stack backtest showed that
    weak/high-turnover branches are fee-sensitive. With SETUP_VOLUME_ENABLED=false,
    volume still contributes inside _apply_confluence as confirmation only.
    """
    if not config.SETUP_VOLUME_ENABLED:
        return None
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
    """단타: BB(20,2) + RSI(14) mean reversion with regime filters."""
    if not config.SETUP_MEANREV_ENABLED:
        return None
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


def _failed_pump_detect(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """Shared detection for the failed-pump pattern: >=15% 7d pump, >=3%
    24h pullback, SMA20 break or MACD down, RSI not overheated. The
    CONDITION was validated (event study n=1660); only the DIRECTION is
    contested between the two variants that wrap this. Returns score +
    evidence + the two headline moves, or None when the pattern is absent.
    """
    pct_7d = metrics.get("pct_7d")
    pct_24h = metrics.get("pct_24h")
    last = metrics.get("last_price")
    sma20 = metrics.get("sma_20")
    macd_state = metrics.get("macd")
    rsi = metrics.get("rsi_14")
    corr = metrics.get("btc_correlation")

    if pct_7d is None or pct_24h is None or last is None or sma20 is None:
        return None

    had_pump = pct_7d >= max(config.MOMENTUM_7D_STRONG_PCT * 3, 15.0)
    intraday_reversal = pct_24h <= -3.0
    broke_structure = last < sma20
    macd_down = bool(macd_state and macd_state.get("hist", 0) < 0)
    rsi_not_overheated = rsi is None or rsi <= 60

    if not (had_pump and intraday_reversal and (broke_structure or macd_down) and rsi_not_overheated):
        return None

    score = 0.58
    if pct_7d >= 20:
        score += 0.05
    if pct_24h <= -5:
        score += 0.05
    if broke_structure:
        score += 0.04
    if macd_down:
        score += 0.04
    if rsi is not None and rsi <= 50:
        score += 0.02
    if corr is not None and corr >= 0.75:
        score -= 0.05

    evidence = []
    if broke_structure:
        evidence.append("SMA20 이탈")
    if macd_down:
        evidence.append("MACD 하방")
    if rsi is not None:
        evidence.append(f"RSI {rsi:.0f}")

    return {
        "score": round(min(0.82, max(0.05, score)), 2),
        "evidence": evidence,
        "pct_7d": pct_7d,
        "pct_24h": pct_24h,
    }


def _failed_pump_short_setup(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """PERMANENTLY DISABLED (default False), 2026-07-14: the exact same
    detection traded as a SHORT is ANTI-PREDICTIVE -- event study forward
    return +0.75%/24h, +1.07%/72h (price RISES after we short), n=1660,
    settled. See research_decisions subject='failed_pump_short_disabled'.
    The pattern is an oversold BOUNCE (see _failed_pump_long_setup), not a
    failing pump. Kept only so history/config references resolve."""
    if not _env_bool_dynamic("SETUP_FAILED_PUMP_ENABLED", False):
        return None
    d = _failed_pump_detect(metrics)
    if d is None:
        return None
    return {
        "style": STYLE_SCALP,
        "strategy": "failed_pump_short",
        "direction": "SHORT",
        "score": d["score"],
        "reason": f"7d +{d['pct_7d']:.1f}% 급등 후 24h {d['pct_24h']:.1f}% 되밀림 + "
                  f"{', '.join(d['evidence'])} — 펌프 실패 숏",
    }


def _failed_pump_long_setup(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """The inversion (research_decisions subject='failed_pump_as_long',
    pre-registered): the EXACT same detection as failed_pump_short, traded
    LONG. Economic story: a >=15% 7d pump already down >=3% with RSI 30-41
    is an oversold bounce, not a failing pump. Event study effect vs
    baseline +0.67%/24h [CI +0.33,+1.19], +0.82%/72h [CI +0.26,+2.00],
    positive across all regimes (strongest when BTC falls). Default OFF --
    earns its place only through the walk-forward gate net of costs."""
    if not _env_bool_dynamic("SETUP_FAILED_PUMP_LONG_ENABLED", False):
        return None
    d = _failed_pump_detect(metrics)
    if d is None:
        return None
    return {
        "style": STYLE_FAILED_PUMP_LONG,
        "strategy": "failed_pump_long",
        "direction": "LONG",
        "score": d["score"],
        "reason": f"7d +{d['pct_7d']:.1f}% 급등 후 24h {d['pct_24h']:.1f}% 눌림 + "
                  f"{', '.join(d['evidence'])} — 과매도 반등 롱",
    }


def _breakout_setup(metrics: dict[str, Any], funding_rate: float | None) -> dict[str, Any] | None:
    """스윙: 20일 돈찬 채널 돌파 (Turtle rules)."""
    if not config.SETUP_BREAKOUT_ENABLED:
        return None
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
    """스윙: 28일 시계열 모멘텀."""
    if not config.SETUP_TSMOM_ENABLED:
        return None
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


def _rel_strength_setup(
    storage: Storage, symbol: str, metrics: dict[str, Any]
) -> dict[str, Any] | None:
    """시장중립 72h: 7d relative-strength-vs-BTC rotation, hedged.

    2026-07-13 status -- market-neutral (long the alt, short beta-matched
    BTC), hold capped at REL_STRENGTH_NEUTRAL_HOLD_HOURS (72h). Only the 72h
    horizon survives neutralized re-testing (research_decisions,
    subject='rel_strength_market_neutral'): on 50 symbols the raw 24h/72h
    effect shrank from the original 6-symbol study, and the 24h version
    fails market-neutral re-testing entirely (was mostly BTC beta, not
    idiosyncratic rotation). The entry criterion itself (7d spread >= 95th
    pctl) is unchanged; what changed is the hedge leg and the hold cap.
    Ex-ante beta is a point-in-time OLS estimate (src.engine.hedge.
    ex_ante_beta, same lookback/min-points as
    scripts/event_study_market_neutral.py used to validate the effect) --
    if it can't be estimated or comes back non-positive, this returns None
    (no trade), never a default beta. Stays off by default
    (SETUP_REL_STRENGTH_ENABLED).
    """
    if not config.SETUP_REL_STRENGTH_ENABLED:
        return None
    if _regime_gate_blocks_entry(storage):
        return None
    if symbol == config.SYMBOL:
        return None

    sym_rows = storage.get_prices(symbol, limit=REL_STRENGTH_LOOKBACK_BARS, timeframe="1h")
    btc_rows = storage.get_prices(config.SYMBOL, limit=REL_STRENGTH_LOOKBACK_BARS, timeframe="1h")
    if not sym_rows or not btc_rows:
        return None

    sym_idx = {int(r["timestamp"]): float(r["close"]) for r in sym_rows}
    btc_idx = {int(r["timestamp"]): float(r["close"]) for r in btc_rows}

    history: list[float] = []
    current: float | None = None
    latest_ts = int(sym_rows[-1]["timestamp"])
    for r in sym_rows:
        ts = int(r["timestamp"])
        prior_ts = ts - _SEVEN_DAYS_S
        value = spread(sym_idx.get(ts), sym_idx.get(prior_ts), btc_idx.get(ts), btc_idx.get(prior_ts))
        if value is None:
            continue
        if ts == latest_ts:
            current = value
            break
        history.append(value)

    if current is None or len(history) < REL_STRENGTH_MIN_HISTORY:
        return None

    rank = percentile_rank(current, history)
    if rank < REL_STRENGTH_PCTL:
        return None

    beta = hedge_engine.ex_ante_beta(
        sym_rows, btc_rows, config.HEDGE_BETA_LOOKBACK_H, config.HEDGE_BETA_MIN_POINTS
    )
    if beta is None:
        return None

    return {
        "style": STYLE_REL_STRENGTH_NEUTRAL,
        "strategy": "rel_strength_rotation",
        "direction": "LONG",
        "score": 0.58,
        "reason": (
            f"7d 대비 BTC 상대강도 스프레드 {current:+.1%} (상위 {(1 - rank) * 100:.1f}%) — "
            f"BTC 숏 헤지(β={beta:.2f}) 시장중립 72h 로테이션 롱"
        ),
        "execution_mode": "market_neutral",
        "beta": beta,
        "hedge_symbol": config.SYMBOL,
    }


def _latest_bar_ts(storage: Storage, symbol: str) -> int | None:
    """Point-in-time latest 1h bar timestamp -- works identically against the
    live Storage and a backtest SnapshotStorage (both honor the same
    get_prices(before=...) cutoff), so callers never need to know which one
    they were handed."""
    rows = storage.get_prices(symbol, limit=1, timeframe="1h")
    return int(rows[-1]["timestamp"]) if rows else None


def _regime_gate_blocks_entry(storage: Storage) -> bool:
    """REGIME_GATE=low_vol_only: block new entries in the four volatility-
    dislocation setups (rel_strength_rotation, capitulation_bar,
    volume_zscore_3plus, pump24_extreme -- NOT a universal filter on every
    strategy) when BTC's trailing 30d annualized realized vol is at/above
    REGIME_GATE_VOL_THRESHOLD_PCT. See research_decisions,
    subject='regime_gate_diagnosis' for why this is LOW-vol-permissive, not
    high-vol-permissive as originally hypothesized -- the diagnosis found
    the opposite polarity. Same evaluate_symbol() call in both backtest and
    live, so this is identical in both paths by construction, not by
    separate implementation.
    """
    if config.REGIME_GATE != "low_vol_only":
        return False
    btc_rows = storage.get_prices(config.SYMBOL, limit=721, timeframe="1h")
    vol = indicators.realized_vol_annualized(indicators.closes(btc_rows), window=720)
    if vol is None:
        return False  # insufficient history -- don't block on missing data
    return vol >= config.REGIME_GATE_VOL_THRESHOLD_PCT


def _capitulation_bar_setup(
    storage: Storage, symbol: str, metrics: dict[str, Any]
) -> dict[str, Any] | None:
    """72h LONG: capitulation_bar (src.research.candle_signals) -- a single
    1h bar with range/open AND volume both >=3 std devs above the trailing
    168h mean, closing in the bottom 20% of its own range (liquidation-
    cascade proxy). Fires on the EXACT candle_signals.capitulation_bar
    definition, not a re-derivation.

    Event study (research_decisions, subject='candle_signals_round2'):
    +1.107% effect at 72h (n=2123, CI [+0.61%,+1.60%], p~0), PASS, survives
    Bonferroni across the full signal x horizon grid. The name reads
    bearish, but the measured forward return is POSITIVE -- this is a
    bounce/reversal signal, not continuation, so direction is LONG,
    matching the data. 24h/4h horizons do NOT survive (24h flips negative
    in flat BTC regime -- research_decisions,
    subject='capitulation_bar_regime_gate'), so this only ever fires the
    72h version.
    """
    if not config.SETUP_CAPITULATION_BAR_ENABLED:
        return None
    if _regime_gate_blocks_entry(storage):
        return None
    latest_ts = _latest_bar_ts(storage, symbol)
    if latest_ts is None:
        return None
    since = latest_ts - (_CANDLE_ROLLING_WINDOW + 24) * 3600
    events = _capitulation_bar_events(symbol, since, storage=storage)
    if latest_ts not in events:
        return None
    return {
        "style": STYLE_CAPITULATION_BOUNCE,
        "strategy": "capitulation_bar",
        "direction": "LONG",
        "score": 0.58,
        "reason": "캡추레이션 바 (3σ 레인지+거래량, 저점 마감) — 72h 반등 롱",
    }


def _volume_zscore_setup(
    storage: Storage, symbol: str, metrics: dict[str, Any]
) -> dict[str, Any] | None:
    """72h LONG: volume_zscore_3plus (src.research.event_study.sig_volume_spike)
    -- 1h volume >=3 std devs above the trailing 168h mean. Fires on the
    EXACT sig_volume_spike definition, not a re-derivation.

    Event study (research_decisions, subject='candle_signals_round2'):
    +0.374% effect at 72h (n=9363, CI [+0.17%,+0.59%], p~0), PASS, survives
    Bonferroni. Positive across all three BTC regimes (down/flat/up), though
    the flat-regime CI alone crosses zero. 24h/4h horizons do NOT survive
    (fail verdict at the aggregate level), so this only ever fires the 72h
    version.
    """
    if not config.SETUP_VOLUME_ZSCORE_ENABLED:
        return None
    if _regime_gate_blocks_entry(storage):
        return None
    latest_ts = _latest_bar_ts(storage, symbol)
    if latest_ts is None:
        return None
    since = latest_ts - (VOLUME_ZSCORE_BASELINE_HOURS + 24) * 3600
    events = _volume_zscore_events(symbol, since, storage=storage)
    if latest_ts not in events:
        return None
    return {
        "style": STYLE_VOLUME_ZSCORE,
        "strategy": "volume_zscore_3plus",
        "direction": "LONG",
        "score": 0.58,
        "reason": "거래량 3σ 스파이크 (168h 대비) — 72h 지속 롱",
    }


def _pump24_extreme_setup(
    storage: Storage, symbol: str, metrics: dict[str, Any]
) -> dict[str, Any] | None:
    """24h LONG: pump24_extreme (src.research.candle_signals) -- 24h return
    at/above the 99th percentile (PUMP24_PCTL) of its own expanding
    history. Same threshold, same window, same 24h-return definition as
    candle_signals.pump24_extreme() -- NOT a re-derivation -- but computed
    only for the current bar via percentile_rank directly (mirroring
    _rel_strength_setup's own pattern above), rather than calling
    pump24_extreme()'s batch _expanding_pctl_events. That batch helper
    recomputes percentile_rank for every point in the series every call --
    O(history^2) -- fine once for an offline event study, ruinous when
    called fresh on every backtest timestep (measured: >15 minutes for a
    single 60-day window before this fix).

    Event study (research_decisions, subject='candle_signals_round2'):
    +0.882% effect at 24h (n=1055, CI [+0.14%,+1.60%], p=0.014), PASS,
    survives Benjamini-Hochberg but NOT Bonferroni -- the weakest of the
    three candle_signals_round2 setups, tested LAST for exactly that
    reason. The function's own name/comment ("chase fade") suggests a
    reversal SHORT; the measured effect is POSITIVE continuation, so
    direction is LONG, following the data, not the name. 72h fails
    (inconsistent regime signs: down -0.7%, up +3.2%), so this only ever
    fires the 24h version.
    """
    if not config.SETUP_PUMP24_EXTREME_ENABLED:
        return None
    if _regime_gate_blocks_entry(storage):
        return None
    latest_ts = _latest_bar_ts(storage, symbol)
    if latest_ts is None:
        return None
    since = latest_ts - REL_STRENGTH_LOOKBACK_BARS * 3600
    rows = storage.get_prices(
        symbol, limit=REL_STRENGTH_LOOKBACK_BARS + 48, since=since, timeframe="1h"
    )
    if not rows:
        return None
    closes = [float(r["close"]) for r in rows]
    ts_list = [int(r["timestamp"]) for r in rows]
    if ts_list[-1] != latest_ts:
        return None

    history: list[float] = []
    current: float | None = None
    for i in range(24, len(closes)):
        prev = closes[i - 24]
        if prev <= 0:
            continue
        value = (closes[i] - prev) / prev
        if ts_list[i] == latest_ts:
            current = value
            break
        history.append(value)

    if current is None or len(history) < _CANDLE_MIN_HISTORY:
        return None

    if config.PUMP24_EARLY_ENTRY:
        # Deceleration variant (research axis): fire while the 24h pump is
        # still strong (>=15%) but the LAST 6h momentum has rolled over to
        # under half the prior 6h -- i.e. during the deceleration, before the
        # 99th-pctl bar completes. Latency study: catches more of the 72h
        # continuation (+2.59% vs +1.64%) at a higher false-positive rate --
        # the walk-forward decides if bigger-winners-more-losers nets out.
        if len(closes) < 13:
            return None
        c1, c7, c13 = closes[-1], closes[-7], closes[-13]
        r6 = (c1 - c7) / c7 if c7 > 0 else 0.0
        r6_prev = (c7 - c13) / c13 if c13 > 0 else 0.0
        if not (current >= 0.15 and r6 > 0 and r6 < 0.5 * r6_prev):
            return None
        return {
            "style": STYLE_PUMP24_EXTREME,
            "strategy": "pump24_extreme",
            "direction": "LONG",
            "score": 0.58,
            "reason": f"24h {current:+.1%} 가속 둔화 (조기 진입) — 24h 지속 롱",
        }

    rank = percentile_rank(current, history)
    if rank < PUMP24_PCTL:
        return None

    return {
        "style": STYLE_PUMP24_EXTREME,
        "strategy": "pump24_extreme",
        "direction": "LONG",
        "score": 0.58,
        "reason": f"24h 수익률 {current:+.1%} (상위 {(1 - rank) * 100:.2f}%) — 24h 지속 롱 (BH 통과, Bonferroni 미통과)",
    }


def _swing_setup(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """스윙: 7d time-series momentum with trend structure confirmation."""
    if not config.SETUP_SWING_ENABLED:
        return None
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
    """Graded 0-0.45 confidence for coins without an active setup."""
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
    """Final confidence: boost independent confirmation, penalize conflicts.

    Volume spike is intentionally treated here as a modifier by default rather
    than a standalone setup. This reduces churn while preserving useful flow
    confirmation.
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

    ratio = metrics.get("volume_ratio")
    pct_24h = metrics.get("pct_24h")
    if ratio is not None and pct_24h is not None and ratio >= config.VOLUME_SPIKE_RATIO:
        volume_aligns = (best["direction"] == "LONG" and pct_24h > 0) or (
            best["direction"] == "SHORT" and pct_24h < 0
        )
        volume_conflicts = (best["direction"] == "LONG" and pct_24h < 0) or (
            best["direction"] == "SHORT" and pct_24h > 0
        )
        if volume_aligns:
            score += 0.03
            parts.append(f"거래량 {ratio:.1f}배 방향확인 +0.03")
        elif volume_conflicts:
            score -= 0.06
            parts.append(f"거래량 {ratio:.1f}배 역방향 -0.06")

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


def _filter_setups_by_category(
    setups: list[dict[str, Any]],
    category: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if config.CATEGORY_STRATEGY_MODE != "matched" or not category:
        return setups, []
    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for setup in setups:
        strategy = str(setup.get("strategy") or "")
        allowed_categories = STRATEGY_CATEGORY_MAP.get(strategy, set())
        if category in allowed_categories:
            kept.append(setup)
        else:
            blocked.append(
                {
                    **setup,
                    "blocked_reason": (
                        f"strategy {strategy or 'unknown'} not matched to category {category}"
                    ),
                }
            )
    return kept, blocked


def evaluate_symbol(
    storage: Storage,
    symbol: str,
    btc_rows: list[dict[str, Any]] | None,
    regime: dict[str, Any] | None = None,
    calibration: dict[tuple[str, str], dict[str, Any]] | None = None,
    category: str | None = None,
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
    policy_blocked_setups: list[dict[str, Any]] = []
    category_strategy_blocked_setups: list[dict[str, Any]] = []
    if not blockers:
        for setup in (
            _funding_setup(metrics, funding_rate),
            _volume_setup(metrics),
            _meanrev_setup(metrics, funding_rate),
            _failed_pump_short_setup(metrics),
            _failed_pump_long_setup(metrics),
            _swing_setup(metrics),
            _breakout_setup(metrics, funding_rate),
            _tsmom28_setup(metrics),
            _rel_strength_setup(storage, symbol, metrics),
            _capitulation_bar_setup(storage, symbol, metrics),
            _volume_zscore_setup(storage, symbol, metrics),
            _pump24_extreme_setup(storage, symbol, metrics),
        ):
            if setup is None:
                continue
            direction = setup["direction"]
            if not config.holding_style_allowed(setup.get("style")):
                note = (
                    f"{_setup_label(setup)} — 장투/무기한 보유는 비활성화되어 보류"
                )
                policy_notes.append(note)
                policy_blocked_setups.append({**setup, "blocked_reason": note})
                continue
            if not config.direction_allowed(direction):
                note = f"{_setup_label(setup)} — 방향 정책으로 보류"
                policy_notes.append(note)
                policy_blocked_setups.append({**setup, "blocked_reason": note})
                continue
            if direction_blocked(regime, direction):
                note = f"{_setup_label(setup)} — {regime.get('reason', 'BTC 레짐 차단')}"
                policy_notes.append(note)
                policy_blocked_setups.append({**setup, "blocked_reason": note})
                continue
            setups.append(setup)
        setups, category_strategy_blocked_setups = _filter_setups_by_category(
            setups, category
        )
        for setup in setups:
            calibrated = calibrate_score(
                setup["score"], setup["strategy"], setup["direction"], calibration
            )
            setup.update(calibrated)
        setups.sort(key=lambda s: -s["score"])

    best = setups[0] if setups else None
    if best is not None:
        _apply_confluence(best, setups, metrics)
    confidence = (
        best["score"]
        if best
        else _proximity_confidence(metrics, funding_rate, blocked=bool(blockers))
    )
    min_confidence = config.ENTRY_MIN_CONFIDENCE
    tradable = best is not None and confidence >= min_confidence

    why_not: list[str] = []
    if not tradable:
        if blockers:
            why_not = blockers
        elif best is not None and confidence < min_confidence:
            why_not = [
                f"확신 {confidence:.0%} < 최소 {min_confidence:.0%} — 진입 기준 미달"
            ]
        else:
            seen: set[str] = set()
            category_notes = [
                setup.get("blocked_reason", "")
                for setup in category_strategy_blocked_setups
                if setup.get("blocked_reason")
            ]
            for reason in policy_notes + category_notes + _why_not(metrics, funding_rate):
                if reason not in seen:
                    seen.add(reason)
                    why_not.append(reason)
            why_not = why_not[:4]

    return {
        "symbol": symbol,
        "base": symbol.split("/")[0],
        "tradable": tradable,
        "verdict": best,
        "confidence": confidence,
        "other_setups": setups[1:],
        "policy_blocked_setups": policy_blocked_setups,
        "category_strategy_blocked": len(category_strategy_blocked_setups),
        "category_strategy_blocked_setups": category_strategy_blocked_setups,
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
    try:
        from src.research.market_categories import latest_category_map

        categories = latest_category_map(storage)
    except Exception:
        categories = {}

    results = []
    for symbol in symbols:
        results.append(
            evaluate_symbol(
                storage,
                symbol,
                btc_rows if symbol != config.SYMBOL else None,
                regime=regime,
                calibration=calibration,
                category=(categories.get(symbol) or {}).get("category"),
            )
        )
    results.sort(
        key=lambda r: (
            not r["tradable"],
            -(r.get("confidence") or 0),
            r["symbol"],
        )
    )
    return results
