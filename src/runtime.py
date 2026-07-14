"""Application runtime jobs started alongside the API server."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from src import config
from src.api.websocket import broadcast_snapshot
from src.engine.cycle import run_exit_poll, run_trading_cycle
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
    try:
        # cross-process heartbeat: the dashboard (separate process) keys its
        # online/offline dot on this, not on candle bar-open times.
        from src.data.storage import get_storage

        get_storage().set_system_status("worker_heartbeat", "ok")
    except Exception:
        logger.debug("heartbeat write skipped", exc_info=True)
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
    if config.EXIT_POLL_INTERVAL_MINUTES > 0:
        scheduler.add_job(
            _exit_poll_job,
            "interval",
            minutes=config.EXIT_POLL_INTERVAL_MINUTES,
            id="exit_poll",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    logger.info(
        "Scheduler started — collecting every %s min, exit-poll every %s min",
        config.COLLECTION_INTERVAL_MINUTES,
        config.EXIT_POLL_INTERVAL_MINUTES,
    )
    return scheduler


def _exit_poll_job() -> None:
    """Faster exit-only pass between full cycles (see cycle.run_exit_poll)."""
    try:
        result = run_exit_poll()
        if result.get("closed"):
            logger.info("Exit poll closed %s position(s) across %s symbol(s)",
                        result["closed"], result["symbols"])
    except Exception:
        logger.exception("exit poll job failed")
