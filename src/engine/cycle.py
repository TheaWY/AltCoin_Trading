"""Multi-symbol trading cycle orchestration."""

from __future__ import annotations

import logging
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine.analyzer import AltAnalyzer
from src.engine.calibration import build_calibration_map
from src.engine.evaluation import evaluate_symbol
from src.engine.market_compare import MarketCompare
from src.engine.paper_trader import PaperTrader
from src.engine.regime import btc_regime, direction_blocked
from src.engine.signal import SignalEngine
from src.symbols import trading_symbols

logger = logging.getLogger(__name__)

STYLE_MAP = {"단타": "scalp", "스윙": "swing"}


def _category_allowed(storage: Storage, symbol: str) -> tuple[bool, dict[str, Any] | None, str]:
    try:
        from src.research.market_categories import is_symbol_trade_allowed

        allowed, reason, category = is_symbol_trade_allowed(storage, symbol)
        return allowed, category, reason
    except Exception:
        logger.exception("Category filter failed for %s; allowing legacy behaviour", symbol)
        return True, None, "category filter unavailable"


def _entry_candidates_from_evaluation(
    storage: Storage,
    symbols: list[str],
    regime: dict[str, Any],
) -> list[dict[str, Any]]:
    """Use evaluate_symbol() as the single source of truth for new entries.

    Previously, the dashboard/evaluation engine explained rich setups while the
    paper trader used AltAnalyzer's separate worth_investing route. That made
    backtests and live paper behavior hard to reason about. This path uses the
    same verdict object the dashboard shows.
    """
    btc_rows = storage.get_prices(config.SYMBOL, limit=720, timeframe="1h")
    calibration = build_calibration_map(storage)
    candidates: list[dict[str, Any]] = []
    for symbol in symbols:
        if storage.get_open_trade_for_symbol(symbol):
            continue
        result = evaluate_symbol(
            storage,
            symbol,
            btc_rows if symbol != config.SYMBOL else None,
            regime=regime,
            calibration=calibration,
        )
        verdict = result.get("verdict")
        if not result.get("tradable") or not verdict:
            continue
        allowed, category, reason = _category_allowed(storage, symbol)
        result["category"] = category
        result["category_filter_reason"] = reason
        if not allowed:
            logger.info("Entry blocked by market category: %s — %s", symbol, reason)
            continue
        candidates.append(result)

    candidates.sort(
        key=lambda item: (
            -float(item.get("confidence") or 0.0),
            -float((item.get("verdict") or {}).get("score") or 0.0),
            -float((item.get("metrics") or {}).get("dollar_volume_24h") or 0.0),
        )
    )
    return candidates[: config.MAX_OPEN_POSITIONS]


def _entry_candidates_from_legacy_analyzer(
    storage: Storage,
    symbols: list[str],
) -> list[dict[str, Any]]:
    """Legacy primary-signal + AltAnalyzer route, kept as an opt-in fallback."""
    analyzer = AltAnalyzer(storage)
    ranked_candidates = [
        analysis
        for analysis in analyzer.analyze_all(symbols)
        if analysis["worth_investing"]
    ]
    filtered: list[dict[str, Any]] = []
    for analysis in ranked_candidates:
        allowed, category, reason = _category_allowed(storage, analysis["symbol"])
        analysis["category"] = category
        analysis["category_filter_reason"] = reason
        if allowed:
            filtered.append(analysis)
        else:
            logger.info("Legacy entry blocked by market category: %s — %s", analysis["symbol"], reason)
    filtered.sort(key=lambda analysis: analysis["confidence"], reverse=True)
    return filtered[: config.MAX_OPEN_POSITIONS]


def run_trading_cycle(storage: Storage | None = None) -> dict[str, Any]:
    """Full cycle: collect → signal → evaluate → paper trade → outcomes."""
    storage = storage or get_storage()
    symbols = trading_symbols()
    cycle_ok = True
    cycle_error = None

    try:
        from src.data.collectors.binance import run_collection

        run_collection(symbols)
        storage.cleanup_old_prices()
    except Exception as exc:
        cycle_ok = False
        cycle_error = str(exc)
        logger.exception("Data collection failed")

    # Positioning data (long/short ratio, open interest) for positioning_short.
    # Enrichment only: failures are logged inside and never break the cycle.
    try:
        from src.data.collectors.positioning import run_positioning_collection

        run_positioning_collection(symbols, storage)
    except Exception:
        logger.exception("Positioning collection failed")

    if config.LIVE_TRADING:
        from src.engine.live_trader import LiveTrader

        trader = LiveTrader(storage)
    else:
        trader = PaperTrader(storage)
    signal_engine = SignalEngine(storage)
    market = MarketCompare(storage)

    btc = storage.get_latest_price(config.SYMBOL)
    if btc:
        trader.ensure_portfolio(float(btc["close"]))

    # Check exits per open trade with correct symbol price.
    # Exits always run, even when data health later blocks new entries.
    for trade in storage.get_open_trades():
        sym = trade["symbol"]
        row = storage.get_latest_price(sym)
        if row:
            trader.check_open_trades_for_symbol(sym, float(row["close"]))

    data_health: dict[str, Any] = {"healthy": True, "action": "ok to trade"}
    try:
        from src.research.data_quality import verdict as data_health_verdict

        data_health = data_health_verdict()
    except Exception:
        logger.exception("Data health check failed")

    signals_run = 0
    trades_opened = 0
    signal_results_by_symbol: dict[str, dict[str, Any]] = {}

    for symbol in symbols:
        # Run every active strategy so their signal accuracy can be compared;
        # only the primary signal is used by the legacy entry route.
        for strategy_name in config.ACTIVE_STRATEGIES:
            result = signal_engine.run_for_symbol(symbol, strategy_name)
            if not result.get("ok"):
                continue
            signals_run += 1
            if strategy_name == config.PRIMARY_STRATEGY:
                signal_results_by_symbol[symbol] = result
                market.register_from_signal(result)

    regime = btc_regime(storage)
    if regime.get("reason"):
        logger.info("BTC regime filter active: %s", regime["reason"])

    if not data_health.get("healthy", True):
        logger.warning(
            "Skipping new entries this cycle — %s",
            data_health.get("action", "data unhealthy"),
        )
    elif config.ENTRY_DECISION_ENGINE == "signal":
        for analysis in _entry_candidates_from_legacy_analyzer(storage, symbols):
            symbol = analysis["symbol"]
            result = signal_results_by_symbol.get(symbol)
            if not result or result.get("direction") not in ("LONG", "SHORT"):
                continue
            if not config.direction_allowed(result["direction"]):
                continue
            if direction_blocked(regime, result["direction"]):
                logger.info("Entry blocked by BTC regime: %s %s", symbol, result["direction"])
                continue
            price_row = storage.get_latest_price(symbol)
            if not price_row:
                continue
            result = result | {
                "style": "scalp" if analysis.get("recommended_style") == "short_term" else "swing",
            }
            opened = trader.process_signal(result, float(price_row["close"]), require_worth=True)
            if opened.get("opened"):
                trades_opened += 1
    else:
        for candidate in _entry_candidates_from_evaluation(storage, symbols, regime):
            verdict = candidate.get("verdict") or {}
            symbol = candidate["symbol"]
            direction = verdict.get("direction")
            if direction not in ("LONG", "SHORT"):
                continue
            if not config.direction_allowed(direction):
                continue
            if direction_blocked(regime, direction):
                logger.info("Entry blocked by BTC regime: %s %s", symbol, direction)
                continue
            price_row = storage.get_latest_price(symbol)
            if not price_row:
                continue
            signal_result = {
                "signal_id": None,
                "symbol": symbol,
                "strategy": verdict.get("strategy"),
                "direction": direction,
                "reason": verdict.get("reason"),
                "entry_price": float(price_row["close"]),
                "style": STYLE_MAP.get(verdict.get("style"), "swing"),
                "metadata": {
                    "decision_engine": "evaluation",
                    "confidence": candidate.get("confidence"),
                    "confluence": verdict.get("confluence"),
                    "market_category": (candidate.get("category") or {}).get("category") if candidate.get("category") else None,
                    "category_reason": candidate.get("category_filter_reason"),
                },
            }
            opened = trader.process_signal(signal_result, float(price_row["close"]), require_worth=False)
            if opened.get("opened"):
                trades_opened += 1

    market.backfill_missing_stubs()
    market.update_pending()

    if config.NGROK_ENABLED:
        try:
            from src.tunnel import ensure_ngrok_running

            ensure_ngrok_running()
        except Exception:
            logger.exception("Ngrok keepalive check failed")

    return {
        "ok": cycle_ok,
        "error": cycle_error,
        "symbols": len(symbols),
        "signals_run": signals_run,
        "trades_opened": trades_opened,
        "data_health": data_health,
        "entry_decision_engine": config.ENTRY_DECISION_ENGINE,
    }
