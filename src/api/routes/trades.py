"""Trade history endpoints."""

from fastapi import APIRouter

from src.data.storage import get_storage

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("")
def list_trades(limit: int = 10) -> dict:
    storage = get_storage()
    return {"trades": storage.get_recent_trades(limit)}
