"""Multi-symbol trading cycle orchestration."""

from __future__ import annotations

import logging
from typing import Any

from src import config
from src.data.storage import Storage, get_storage
from src.engine.analyzer import AltAnalyzer
from src.engine.market_compare import MarketCompare
from src.engine.paper_trader import PaperTrader
from src.engine.signal import SignalEngine
from src.symbols import trading_symbols

logger = logging.getLogger(__name__)


def run_trading_cycle(storage: Storage | None = None) -> dict[str, Any]:
    """Full cycle: collect → signal → analyze → paper trade → outcomes."""
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

    if config.LIVE_TRADING:
        from src.engine.live_trader import LiveTrader

        trader = LiveTrader(storage)
    else:
        trader = PaperTrader(storage)
    signal_engine = SignalEngine(storage)
    analyzer = AltAnalyzer(storage)
    market = MarketCompare(storage)

    btc = storage.get_latest_price(config.SYMBOL)
    if btc:
        trader.ensure_portfolio(float(btc["close"]))

    # Check exits per open trade with correct symbol price
    for trade in storage.get_open_trades():
        sym = trade["symbol"]
        row = storage.get_latest_price(sym)
        if row:
            trader.check_open_trades_for_symbol(sym, float(row["close"]))

    signals_run = 0
    trades_opened = 0
    signal_results_by_symbol: dict[str, dict[str, Any]] = {}

    for symbol in symbols:
        # Run every active strategy so their signal accuracy can be compared;
        # only the primary (first listed) strategy drives trading decisions.
        for strategy_name in config.ACTIVE_STRATEGIES:
            result = signal_engine.run_for_symbol(symbol, strategy_name)
            if not result.get("ok"):
                continue
            signals_run += 1
            if strategy_name == config.PRIMARY_STRATEGY:
                signal_results_by_symbol[symbol] = result
                market.register_from_signal(result)

    ranked_candidates = [
        analysis
        for analysis in analyzer.analyze_all(symbols)
        if analysis["worth_investing"]
    ]
    ranked_candidates.sort(key=lambda analysis: analysis["confidence"], reverse=True)
    ranked_candidates = ranked_candidates[: config.MAX_OPEN_POSITIONS]

    for analysis in ranked_candidates:
        symbol = analysis["symbol"]
        result = signal_results_by_symbol.get(symbol)
        if not result or result.get("direction") not in ("LONG", "SHORT"):
            continue

        price_row = storage.get_latest_price(symbol)
        if not price_row:
            continue
        price = float(price_row["close"])

        opened = trader.process_signal(result, price, require_worth=True)
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
    }
