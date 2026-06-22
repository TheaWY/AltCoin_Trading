"""FastAPI application."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse

from src import config
from src.api.routes import alts, dashboard, health, signals, trades
from src.api.websocket import router as ws_router

logger = logging.getLogger(__name__)


def _start_ngrok_tunnel() -> None:
    from src.health import get_health
    from src.tunnel import get_saved_public_url, start_ngrok

    public_base = start_ngrok(config.API_PORT)
    get_health().mark_ngrok(public_base)
    logger.info("Phone dashboard: %s/dashboard", public_base)
    logger.info("Saved link: %s", get_saved_public_url())


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.NGROK_ENABLED:
        from src.tunnel import set_api_ready

        set_api_ready(True)
        await asyncio.sleep(0.5)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _start_ngrok_tunnel)
        except Exception:
            logger.exception("Ngrok tunnel failed — running local only")
    yield
    if config.NGROK_ENABLED:
        from src.tunnel import set_api_ready, stop_ngrok

        set_api_ready(False)
        stop_ngrok()


app = FastAPI(title="BTC Auto Trading", version="0.1.0", lifespan=lifespan)

app.include_router(alts.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(ws_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "dashboard": "/dashboard"}


@app.get("/dashboard")
async def dashboard_page() -> FileResponse:
    index = config.DASHBOARD_DIR / "index.html"
    return FileResponse(index)
