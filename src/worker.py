"""Standalone trading worker process."""

from __future__ import annotations

import logging
import signal
import sys
import time

from src.runtime import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def main() -> None:
    stop = False

    def _stop(signum: int, frame: object) -> None:
        nonlocal stop
        logger.info("Worker received signal %s; shutting down", signum)
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # LIVE GUARD (plan Phase 5): refuse to trade unvalidated strategies unless
    # they're explicitly acknowledged eyes-open; warn on stale feature data.
    from src.data.storage import get_storage
    from src.engine import live_guard
    _storage = get_storage()
    live_guard.check_live_strategies(_storage)   # raises -> worker won't start
    live_guard.check_feature_freshness(_storage)

    scheduler = start_scheduler()
    logger.info("Trading worker started")
    try:
        while not stop:
            time.sleep(1)
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Trading worker stopped")


if __name__ == "__main__":
    main()
