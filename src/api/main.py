"""FastAPI application."""

from fastapi import FastAPI
from fastapi.responses import FileResponse

from src import config
from src.api.routes import dashboard, signals, trades
from src.api.websocket import router as ws_router

app = FastAPI(title="BTC Auto Trading", version="0.1.0")

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
