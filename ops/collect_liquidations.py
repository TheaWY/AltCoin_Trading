#!/usr/bin/env python3
"""Standalone forced-liquidation collector — runs on a small host in a
Binance-permitted region and writes to the shared Postgres over Tailscale.

WHY STANDALONE (not part of the Mac mini worker): the Binance FUTURES websocket
data-plane is geo-blocked from the Mac mini's egress — the handshake succeeds
but zero data frames arrive (fstream), or the alt host returns HTTP 403
(fstream-mm), while futures REST works fine. `!forceOrder@arr` exists only on
that futures websocket, has no REST fallback (allForceOrders was removed), and
no spot equivalent (liquidations are a derivatives concept). So the stream must
originate from an unblocked region.

DEPLOYMENT (see ops/REMOTE_LIQUIDATION_COLLECTOR.md):
  - Provision a small VPS in a permitted region.
  - Point DATABASE_URL at the Mac mini's Postgres over Tailscale, e.g.
      DATABASE_URL=postgresql://altcoin:...@100.88.26.107:5432/altcoin_trading
    (requires Postgres listening on the tailnet interface + a pg_hba rule).
  - Run this via ops/liquidation-collector.service (systemd).

Run EXACTLY ONE instance: the 1h aggregate is additive per flush, so two live
collectors writing to the same DB would double-count it. Raw rows dedupe via a
UNIQUE constraint, but the aggregate does not.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("liquidation_collector")


def main() -> None:
    from src import config
    from src.data.collectors.liquidations import run_liquidation_stream
    from src.data.storage import get_storage

    if not config.DATABASE_URL:
        logger.error(
            "DATABASE_URL is not set. The remote collector must write to the "
            "shared Postgres over Tailscale, not a local SQLite file. Refusing "
            "to start so liquidation data is not stranded on this host."
        )
        sys.exit(2)

    stop = asyncio.Event()

    def _stop(signum: int, _frame: object) -> None:
        logger.info("received signal %s; shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    storage = get_storage()
    logger.info("liquidation collector starting (writing to shared Postgres)")

    async def _run() -> None:
        await run_liquidation_stream(storage, stop_check=stop.is_set)

    try:
        asyncio.run(_run())
    finally:
        logger.info("liquidation collector stopped")


if __name__ == "__main__":
    main()
