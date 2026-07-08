"""Signal endpoints."""

from fastapi import APIRouter

from src import config
from src.data.storage import get_storage

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/current")
def current_signal() -> dict:
    storage = get_storage()
    signal = storage.get_latest_signal(symbol=config.SYMBOL, strategy=config.PRIMARY_STRATEGY)
    return {"signal": signal}


@router.get("/recent")
def recent_signals(limit: int = 20) -> dict:
    storage = get_storage()
    return {"signals": storage.get_recent_signals(limit)}
