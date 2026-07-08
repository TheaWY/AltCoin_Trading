"""Altcoin overview and analysis endpoints."""

from fastapi import APIRouter

from src.api.dashboard_data import build_alts_payload
from src.engine.evaluation import evaluate_all

router = APIRouter(prefix="/alts", tags=["alts"])


@router.get("")
def list_alts() -> dict:
    return build_alts_payload()


@router.get("/evaluation")
def list_evaluation() -> dict:
    """단타/스윙 verdict + quant metrics per symbol."""
    return {"evaluation": evaluate_all()}
