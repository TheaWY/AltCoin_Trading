"""Entry point — starts scheduler + FastAPI."""

from __future__ import annotations

import logging
import sys

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler

from src import config
from src.api.main import app
from src.api.websocket import broadcast_snapshot
from src.data.collectors.binance import run_collection
from src.data.storage import get_storage
from src.engine.market_compare import MarketCompare
from src.engine.paper_trader import PaperTrader
from src.engine.signal import SignalEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def trading_cycle() -> None:
    """Collect data, generate signals, manage paper trades, update outcomes."""
    logger.info("Trading cycle started")
    try:
        run_collection()
    except Exception:
        logger.exception("Data collection failed")

    storage = get_storage()
    latest = storage.get_latest_price(config.SYMBOL)
    current_price = float(latest["close"]) if latest else None

    trader = PaperTrader(storage)
    if current_price:
        trader.ensure_portfolio(current_price)
        trader.check_open_trades(current_price)

    signal_result = SignalEngine(storage).run()
    if signal_result.get("ok") and current_price:
        MarketCompare(storage).register_from_signal(signal_result)
        trader.process_signal(signal_result, current_price)

    MarketCompare(storage).backfill_missing_stubs()
    MarketCompare(storage).update_pending()

    try:
        import asyncio

        loop = asyncio.new_event_loop()
        loop.run_until_complete(broadcast_snapshot())
        loop.close()
    except Exception:
        logger.debug("WebSocket broadcast skipped", exc_info=True)

    logger.info("Trading cycle finished")


def main() -> None:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        trading_cycle,
        "interval",
        minutes=config.COLLECTION_INTERVAL_MINUTES,
        id="trading_cycle",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — collecting every %s min",
        config.COLLECTION_INTERVAL_MINUTES,
    )

    # Run once at startup so dashboard has data immediately
    trading_cycle()

    logger.info("Dashboard: http://%s:%s/dashboard", config.API_HOST, config.API_PORT)
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_level="info")


if __name__ == "__main__":
    main()
