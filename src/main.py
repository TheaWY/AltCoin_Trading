"""Entry point — starts scheduler + FastAPI."""

from __future__ import annotations

import logging
import sys

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler

from src import config
from src.api.main import app
from src.api.websocket import broadcast_snapshot
from src.health import get_health
from src.engine.cycle import run_trading_cycle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def trading_cycle() -> None:
    """Collect data, generate signals, manage paper trades, update outcomes."""
    health = get_health()
    logger.info("Trading cycle started")
    result = run_trading_cycle()

    try:
        import asyncio

        loop = asyncio.new_event_loop()
        loop.run_until_complete(broadcast_snapshot())
        loop.close()
    except Exception:
        logger.debug("WebSocket broadcast skipped", exc_info=True)

    health.mark_cycle(result.get("ok", False), result.get("error"))
    logger.info(
        "Trading cycle finished — %s symbols, %s signals, %s new trades",
        result.get("symbols", 0),
        result.get("signals_run", 0),
        result.get("trades_opened", 0),
    )


def main() -> None:
    import os

    health = get_health()
    health.mark_started(os.getpid())
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

    local_url = f"http://localhost:{config.API_PORT}/dashboard"
    logger.info("Local dashboard: %s", local_url)
    if config.is_cloud_runtime():
        pub = config.public_base_url()
        if pub:
            logger.info("Cloud dashboard (24/7): %s/dashboard", pub)
        else:
            logger.info("Cloud mode — public URL will appear after host assigns a domain")
    else:
        logger.info("Ngrok will start after the API is listening (if NGROK_ENABLED=true)")

    health.mark_running()
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_level="info")


if __name__ == "__main__":
    main()
