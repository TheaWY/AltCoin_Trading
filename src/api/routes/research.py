"""Research stack API — manual rollback and diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.dashboard_data import invalidate_payload_cache
from src.research.promotion import rollback

router = APIRouter(prefix="/research", tags=["research"])


@router.post("/rollback")
def post_rollback() -> dict:
    """Manual rollback to previous config_overrides snapshot."""
    try:
        result = rollback("dashboard manual rollback")
        invalidate_payload_cache()
        return {"ok": True, **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
