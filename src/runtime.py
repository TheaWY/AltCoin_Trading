"""Application runtime jobs started alongside the API server."""

from __future__ import annotations

import asyncio
import logging
import threading
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


def start_liquidation_stream() -> threading.Thread | None:
    """Start the forced-liquidation websocket consumer in its own daemon thread.

    Fault isolation is total and layered: (1) run_liquidation_stream() never
    raises into its caller — it catches every error, logs, and reconnects;
    (2) it runs on a separate daemon thread with its own asyncio loop, so even a
    hard crash of the stream cannot touch the APScheduler thread that drives the
    trading cycle; (3) any failure to even START the thread is swallowed here.
    A liquidation-stream problem can, at worst, cost liquidation data — never a
    trading cycle. Returns the thread (for tests) or None if disabled/failed.
    """
    if not config.LIQUIDATION_STREAM_ENABLED:
        logger.info("Liquidation stream disabled (LIQUIDATION_STREAM_ENABLED=false)")
        return None

    def _run() -> None:
        try:
            from src.data.collectors.liquidations import run_liquidation_stream
            from src.data.storage import get_storage

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(run_liquidation_stream(get_storage()))
        except Exception:
            logger.exception("liquidation stream thread exited (trading unaffected)")

    try:
        thread = threading.Thread(target=_run, name="liquidation-stream", daemon=True)
        thread.start()
        logger.info("Liquidation stream started (forward-only; clock running)")
        return thread
    except Exception:
        logger.exception("could not start liquidation stream (trading unaffected)")
        return None


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
    start_liquidation_stream()
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
