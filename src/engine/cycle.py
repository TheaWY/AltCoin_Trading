"""Multi-symbol trading cycle orchestration."""

from __future__ import annotations

import json
import logging
import time
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
from src.symbols import cycle_symbols, health_gate_scope, trading_symbols

logger = logging.getLogger(__name__)

STYLE_MAP = {"단타": "scalp", "스윙": "swing"}


def _portfolio_accounting_invariant(portfolio: dict[str, Any]) -> tuple[bool, dict[str, float]]:
    cash = float(portfolio.get("cash") or 0.0)
    equity = float(portfolio.get("equity", portfolio.get("paper_value") or 0.0))
    positions_value = float(
        portfolio.get("open_position_value", portfolio.get("total_open_value") or 0.0)
    )
    expected_equity = cash + positions_value
    ok = cash >= 0 and abs(equity - expected_equity) < 0.01
    return ok, {
        "cash": cash,
        "equity": equity,
        "positions_value": positions_value,
    }


def _new_entry_funnel(symbols: list[str]) -> dict[str, Any]:
    return {
        "ts": int(time.time()),
        "symbols_evaluated": 0,
        "blocked_freshness": 0,
        "blocked_backfill": 0,
        "setups_fired": 0,
        "killed_direction_policy": 0,
        "below_min_confidence": 0,
        "max_confidence_seen": 0.0,
        "blocked_regime": 0,
        "blocked_category": 0,
        "blocked_category_strategy": 0,
        "in_cooldown": 0,
        "entered": 0,
        "slots_full": 0,
        "open_rejected": 0,
        "symbols": len(symbols),
        "candidates": [],
    }


def _record_candidate_stop(
    funnel: dict[str, Any],
    symbol: str,
    gate: str,
    detail: str,
    confidence: float | None = None,
) -> None:
    row = {
        "symbol": symbol,
        "confidence": None if confidence is None else round(float(confidence), 4),
        "gate_stopped_at": gate,
        "detail": detail,
    }
    funnel.setdefault("candidates", []).append(row)
    logger.info(
        "ENTRY_CANDIDATE symbol=%s confidence=%s gate=%s detail=%s",
        symbol,
        "—" if confidence is None else f"{float(confidence):.4f}",
        gate,
        detail,
    )


def _record_funnel(funnel: dict[str, Any], storage: Storage) -> None:
    storage.set_system_status("entry_funnel", json.dumps(funnel, sort_keys=True))
    logger.info("ENTRY_FUNNEL %s", json.dumps(funnel, sort_keys=True))


def _ensure_shadow_entries(storage: Storage) -> None:
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_entries (
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                confidence DOUBLE PRECISION NOT NULL,
                setup TEXT,
                ts BIGINT NOT NULL,
                price DOUBLE PRECISION NOT NULL
            )
            """
        )


def _insert_shadow_entry(
    storage: Storage,
    symbol: str,
    verdict: dict[str, Any],
    confidence: float,
    price: float,
) -> None:
    _ensure_shadow_entries(storage)
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(
            """
            INSERT INTO shadow_entries (symbol, direction, confidence, setup, ts, price)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                verdict.get("direction"),
                confidence,
                verdict.get("strategy") or verdict.get("reason") or "",
                int(time.time()),
                price,
            ),
        )


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
        allowed, category, reason = _category_allowed(storage, symbol)
        result = evaluate_symbol(
            storage,
            symbol,
            btc_rows if symbol != config.SYMBOL else None,
            regime=regime,
            calibration=calibration,
            category=(category or {}).get("category") if category else None,
        )
        verdict = result.get("verdict")
        if not result.get("tradable") or not verdict:
            continue
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
    universe_symbols = trading_symbols()
    symbols = cycle_symbols(universe_symbols, storage)
    entry_funnel = _new_entry_funnel(symbols)
    if len(symbols) != len(universe_symbols):
        logger.info(
            "Trading cycle limited to %d/%d ranked symbols",
            len(symbols),
            len(universe_symbols),
        )
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
        from src.data.collectors.positioning import (
            positioning_collection_symbols,
            run_positioning_collection,
        )

        positioning_symbols = positioning_collection_symbols(symbols)
        logger.info(
            "Positioning collection limited to %d/%d ranked symbols",
            len(positioning_symbols),
            len(symbols),
        )
        run_positioning_collection(positioning_symbols, storage)
    except Exception:
        logger.exception("Positioning collection failed")

    # Orderbook snapshots (derived depth/imbalance/spread) -- cannot be
    # backfilled, so this must run every cycle regardless of research status.
    # Enrichment only: failures are logged inside and never break the cycle.
    try:
        from src.data.collectors.orderbook import (
            orderbook_collection_symbols,
            run_orderbook_collection,
        )

        orderbook_symbols = orderbook_collection_symbols(symbols)
        logger.info(
            "Orderbook collection limited to %d/%d ranked symbols",
            len(orderbook_symbols),
            len(symbols),
        )
        run_orderbook_collection(orderbook_symbols, storage)
        storage.cleanup_old_orderbook_snapshots()
    except Exception:
        logger.exception("Orderbook collection failed")

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

    ledger_ok = True
    ledger_values: dict[str, float] = {}
    try:
        portfolio = trader.summary(float(btc["close"]) if btc else None)
        ledger_ok, ledger_values = _portfolio_accounting_invariant(portfolio)
        if not ledger_ok:
            from src.research.decisions import log as log_research_decision

            log_research_decision(
                actor="worker",
                action="ledger_anomaly",
                subject="paper_portfolio",
                detail=ledger_values,
            )
            logger.warning(
                "Skipping new entries this cycle due to ledger anomaly: %s",
                ledger_values,
            )
    except Exception:
        ledger_ok = False
        logger.exception("Portfolio accounting invariant check failed")

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
        stale = ", ".join(
            f"{item.get('symbol')} {item.get('source')} age_s={item.get('age_s')}"
            for item in data_health.get("stale_sources", [])[:6]
        )
        detail = data_health.get("action", "data unhealthy")
        if stale:
            detail = f"{detail}; stale={stale}"
        for symbol in symbols:
            _record_candidate_stop(entry_funnel, symbol, "data_health_halt", detail)
    elif not ledger_ok:
        logger.warning("Skipping new entries this cycle — ledger accounting invariant failed")
        for symbol in symbols:
            _record_candidate_stop(
                entry_funnel,
                symbol,
                "ledger_anomaly",
                json.dumps(ledger_values, sort_keys=True),
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
                "style": config.normalize_holding_style(analysis.get("recommended_style")) or "swing",
            }
            opened = trader.process_signal(result, float(price_row["close"]), require_worth=True)
            if opened.get("opened"):
                trades_opened += 1
                entry_funnel["entered"] += 1
    else:
        btc_rows = storage.get_prices(config.SYMBOL, limit=720, timeframe="1h")
        calibration = build_calibration_map(storage)
        candidates: list[dict[str, Any]] = []
        available_slots = max(0, config.MAX_OPEN_POSITIONS - storage.count_open_trades())
        entry_funnel["available_slots"] = available_slots
        entry_funnel["open_positions"] = storage.count_open_trades()
        _, backfilling = health_gate_scope(storage, universe_symbols)
        for symbol in symbols:
            if available_slots <= 0:
                entry_funnel["slots_full"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "slots_full",
                    f"open_positions={entry_funnel['open_positions']} max={config.MAX_OPEN_POSITIONS}",
                )
                continue
            if storage.get_open_trade_for_symbol(symbol):
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "already_open",
                    "symbol already has an open paper trade",
                )
                continue
            price_row = storage.get_latest_price(symbol)
            if not price_row:
                entry_funnel["blocked_freshness"] += 1
                _record_candidate_stop(entry_funnel, symbol, "blocked_freshness", "no latest price")
                continue
            price_age = int(time.time()) - int(price_row["timestamp"])
            if price_age > 2 * 3600:
                entry_funnel["blocked_freshness"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "blocked_freshness",
                    f"latest price age {price_age}s > 7200s",
                )
                continue
            if symbol in backfilling:
                entry_funnel["blocked_backfill"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "blocked_backfill",
                    "symbol rotated into the universe < 48h ago, history still backfilling",
                )
                continue

            entry_funnel["symbols_evaluated"] += 1
            allowed, category, category_reason = _category_allowed(storage, symbol)
            candidate = evaluate_symbol(
                storage,
                symbol,
                btc_rows if symbol != config.SYMBOL else None,
                regime=regime,
                calibration=calibration,
                category=(category or {}).get("category") if category else None,
            )
            candidate["category"] = category
            candidate["category_filter_reason"] = category_reason
            verdict = candidate.get("verdict") or {}
            if not verdict:
                if int(candidate.get("category_strategy_blocked") or 0) > 0:
                    entry_funnel["setups_fired"] += 1
                    entry_funnel["blocked_category_strategy"] += 1
                    blocked = candidate.get("category_strategy_blocked_setups") or []
                    detail = "; ".join(
                        str(item.get("blocked_reason") or "")
                        for item in blocked[:3]
                        if item.get("blocked_reason")
                    )
                    _record_candidate_stop(
                        entry_funnel,
                        symbol,
                        "blocked_category_strategy",
                        detail or "strategy not matched to current category",
                        float(candidate.get("confidence") or 0.0),
                    )
                    continue
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "no_setup",
                    "; ".join(candidate.get("why_not") or ["no setup fired"])[:240],
                    float(candidate.get("confidence") or 0.0),
                )
                continue
            entry_funnel["setups_fired"] += 1
            confidence = float(candidate.get("confidence") or 0.0)
            entry_funnel["max_confidence_seen"] = max(
                float(entry_funnel["max_confidence_seen"]),
                confidence,
            )
            direction = verdict.get("direction")
            if direction not in ("LONG", "SHORT"):
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "invalid_direction",
                    f"direction={direction}",
                    confidence,
                )
                continue
            if not config.direction_allowed(direction):
                entry_funnel["killed_direction_policy"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "killed_direction_policy",
                    f"direction={direction} allow_long={config.ALLOW_LONG} allow_short={config.ALLOW_SHORT}",
                    confidence,
                )
                continue
            if direction_blocked(regime, direction):
                logger.info("Entry blocked by BTC regime: %s %s", symbol, direction)
                entry_funnel["blocked_regime"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "blocked_regime",
                    regime.get("reason", "BTC regime blocked direction"),
                    confidence,
                )
                continue
            if not allowed:
                logger.info("Entry blocked by market category: %s — %s", symbol, category_reason)
                entry_funnel["blocked_category"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "blocked_category",
                    category_reason or "market category blocked",
                    confidence,
                )
                continue
            if trader._symbol_in_cooldown(symbol):  # noqa: SLF001
                entry_funnel["in_cooldown"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "in_cooldown",
                    f"cooldown {config.COOLDOWN_HOURS_PER_SYMBOL}h per symbol",
                    confidence,
                )
                continue
            if confidence < config.ENTRY_MIN_CONFIDENCE:
                entry_funnel["below_min_confidence"] += 1
                _insert_shadow_entry(
                    storage,
                    symbol,
                    verdict,
                    confidence,
                    float(price_row["close"]),
                )
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "below_min_confidence",
                    f"confidence {confidence:.2f} < entry_min {config.ENTRY_MIN_CONFIDENCE:.2f}",
                    confidence,
                )
                continue
            candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                -float(item.get("confidence") or 0.0),
                -float((item.get("verdict") or {}).get("score") or 0.0),
                -float((item.get("metrics") or {}).get("dollar_volume_24h") or 0.0),
            )
        )
        for candidate in candidates:
            verdict = candidate.get("verdict") or {}
            symbol = candidate["symbol"]
            if available_slots <= 0:
                entry_funnel["slots_full"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "slots_full",
                    f"candidate passed gates but no slots remained after ranking; max={config.MAX_OPEN_POSITIONS}",
                    float(candidate.get("confidence") or 0.0),
                )
                continue
            price_row = storage.get_latest_price(symbol)
            if not price_row:
                _record_candidate_stop(entry_funnel, symbol, "blocked_freshness", "no latest price at open")
                continue
            signal_result = {
                "signal_id": None,
                "symbol": symbol,
                "strategy": verdict.get("strategy"),
                "direction": verdict.get("direction"),
                "reason": verdict.get("reason"),
                "entry_price": float(price_row["close"]),
                "style": config.normalize_holding_style(STYLE_MAP.get(verdict.get("style"))),
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
                entry_funnel["entered"] += 1
                available_slots -= 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "entered",
                    f"trade_id={opened.get('trade_id')}",
                    float(candidate.get("confidence") or 0.0),
                )
            else:
                entry_funnel["open_rejected"] += 1
                _record_candidate_stop(
                    entry_funnel,
                    symbol,
                    "open_rejected",
                    json.dumps(opened, sort_keys=True, ensure_ascii=False)[:240],
                    float(candidate.get("confidence") or 0.0),
                )

    _record_funnel(entry_funnel, storage)

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
        "ledger_ok": ledger_ok,
        "ledger": ledger_values,
        "entry_funnel": entry_funnel,
    }
