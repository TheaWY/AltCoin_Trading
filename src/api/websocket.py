"""WebSocket — real-time price and signal updates."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src import config
from src.data.storage import get_storage
from src.engine.paper_trader import PaperTrader

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


def build_snapshot() -> dict[str, Any]:
    storage = get_storage()
    latest_price = storage.get_latest_price(config.SYMBOL)
    latest_funding = storage.get_latest_funding_rate(config.SYMBOL)
    latest_signal = storage.get_latest_signal(symbol=config.SYMBOL)
    accuracy = storage.get_signal_accuracy(config.SIGNAL_ACCURACY_ROLLING_DAYS)

    current_price = float(latest_price["close"]) if latest_price else None
    portfolio = {}
    if current_price:
        portfolio = PaperTrader(storage).summary(current_price)

    return {
        "type": "snapshot",
        "symbol": config.SYMBOL,
        "price": current_price,
        "funding_rate": (
            float(latest_funding["funding_rate"]) if latest_funding else None
        ),
        "funding_rate_pct": (
            float(latest_funding["funding_rate"]) * 100 if latest_funding else None
        ),
        "signal": latest_signal,
        "portfolio": portfolio,
        "accuracy": accuracy,
        "trades": storage.get_recent_trades(10),
        "prices": storage.get_prices(config.SYMBOL, limit=48),
    }


async def broadcast_snapshot() -> None:
    if not manager.active:
        return
    await manager.broadcast(build_snapshot())


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        await websocket.send_json(build_snapshot())
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=3.0)
            except asyncio.TimeoutError:
                await websocket.send_json(build_snapshot())
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.debug("WebSocket closed: %s", exc)
        manager.disconnect(websocket)
