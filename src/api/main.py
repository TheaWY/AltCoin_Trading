"""FastAPI application."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

from src import config
from src.api.routes import alts, categories, dashboard, health, research, signals, trades
from src.api.websocket import router as ws_router

logger = logging.getLogger(__name__)


def _log_background_failure(future: asyncio.Future) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("Background startup task failed")


def _start_ngrok_tunnel() -> None:
    from src.health import get_health
    from src.tunnel import get_saved_public_url, start_ngrok

    public_base = start_ngrok(config.API_PORT)
    get_health().mark_ngrok(public_base)
    logger.info("Phone dashboard: %s/dashboard", public_base)
    logger.info("Saved link: %s", get_saved_public_url())


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.health import get_health

    health = get_health()
    health.mark_started(os.getpid())
    scheduler = None
    if config.RUN_TRADING_SCHEDULER:
        from src.runtime import start_scheduler

        scheduler = start_scheduler()
    else:
        logger.info("Trading scheduler disabled for web process")
    health.mark_running()

    if config.NGROK_ENABLED:
        from src.tunnel import set_api_ready

        set_api_ready(True)
        loop = asyncio.get_running_loop()
        ngrok_future = loop.run_in_executor(None, _start_ngrok_tunnel)
        ngrok_future.add_done_callback(_log_background_failure)

    from src.api.price_stream import relay
    from src.api.websocket import manager

    price_task = asyncio.create_task(relay.run(manager))
    yield
    price_task.cancel()
    if scheduler:
        scheduler.shutdown(wait=False)
    if config.NGROK_ENABLED:
        from src.tunnel import set_api_ready, stop_ngrok

        set_api_ready(False)
        stop_ngrok()


app = FastAPI(title="BTC Auto Trading", version="0.1.0", lifespan=lifespan)

app.include_router(alts.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(research.router, prefix="/api")
app.include_router(categories.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(trades.router, prefix="/api")


@app.get("/experiments")
async def experiments_page() -> FileResponse:
    return FileResponse(config.DASHBOARD_DIR / "experiments.html")


app.include_router(ws_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "dashboard": "/dashboard", "experiments": "/experiments"}


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/readyz", include_in_schema=False)
async def readyz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/dashboard")
async def dashboard_page() -> FileResponse:
    """Home-first dashboard: entry decisions, portfolio, strategy, and categories."""
    return FileResponse(config.DASHBOARD_DIR / "home.html")


@app.get("/dashboard/full")
async def full_dashboard_page() -> FileResponse:
    """Compatibility route: serve the same Home UI so old links do not open legacy tabs."""
    return FileResponse(config.DASHBOARD_DIR / "home.html")


@app.get("/dashboard/legacy")
async def legacy_dashboard_page() -> FileResponse:
    """Old full dashboard kept only for debugging."""
    return FileResponse(config.DASHBOARD_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)
