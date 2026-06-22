"""Health check endpoint."""

from fastapi import APIRouter

from src.health import get_health

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    return get_health().get_status()
