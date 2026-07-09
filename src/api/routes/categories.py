"""Market category API — dynamic coin-type snapshots for dashboard/research."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.data.storage import get_storage
from src.research import market_categories

router = APIRouter(prefix="/research/categories", tags=["research-categories"])


@router.get("/summary")
def category_summary() -> dict[str, Any]:
    return market_categories.summary(get_storage())


@router.get("/latest")
def latest_categories() -> dict[str, Any]:
    latest = market_categories.latest_category_map(get_storage())
    return {
        "count": len(latest),
        "categories": {
            symbol: {
                "category": row.get("category"),
                "cluster_id": row.get("cluster_id"),
                "trade_allowed": bool(row.get("trade_allowed")),
                "confidence": row.get("confidence"),
                "reason": row.get("reason"),
                "timestamp": row.get("timestamp"),
                "features": row.get("features", {}),
            }
            for symbol, row in latest.items()
        },
    }


@router.get("/transitions")
def transitions(limit: int = 100) -> list[dict[str, Any]]:
    storage = get_storage()
    market_categories.ensure_schema(storage)
    with storage._connect() as conn:  # noqa: SLF001
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM market_category_transitions ORDER BY to_ts DESC LIMIT ?",
                (min(limit, 500),),
            ).fetchall()
        ]
