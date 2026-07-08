"""Application runtime jobs started alongside the API server."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from src import config
from src.api.websocket import broadcast_snapshot
from src.engine.cycle import run_trading_cycle
from src.health import get_health

logger = logging.getLogger(__name__)


def trading_cycle() -> None:
    """Collect data, generate signals, manage paper trades, update outcomes."""
    health = get_health()
    logger.info("Trading cycle started")
    result = run_trading_cycle()

    from src.api.dashboard_data import invalidate_payload_cache

    invalidate_payload_cache()

    try:
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


def start_scheduler() -> BackgroundScheduler:
    """Start the background trading scheduler and run the first cycle immediately."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        trading_cycle,
        "interval",
        minutes=config.COLLECTION_INTERVAL_MINUTES,
        id="trading_cycle",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — collecting every %s min",
        config.COLLECTION_INTERVAL_MINUTES,
    )
    return scheduler
