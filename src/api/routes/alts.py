"""Altcoin overview and analysis endpoints."""

from fastapi import APIRouter

from src.api.dashboard_data import build_alts_payload

router = APIRouter(prefix="/alts", tags=["alts"])


@router.get("")
def list_alts() -> dict:
    return build_alts_payload()
