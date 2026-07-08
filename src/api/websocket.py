"""WebSocket — real-time price and signal updates."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.api.dashboard_data import build_alts_payload

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
    payload = build_alts_payload()
    payload["type"] = "snapshot"
    return payload


async def broadcast_snapshot() -> None:
    if not manager.active:
        return
    await manager.broadcast(build_snapshot())


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    # The payload cache returns the same object until new data invalidates it,
    # so identity comparison tells us when a resend is actually needed.
    last_sent_id: int | None = None
    try:
        while True:
            snapshot = await asyncio.to_thread(build_snapshot)
            if id(snapshot) != last_sent_id:
                await websocket.send_json(snapshot)
                last_sent_id = id(snapshot)
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=3.0)
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.debug("WebSocket closed: %s", exc)
        manager.disconnect(websocket)
