"""Durable broker state interfaces for future live/testnet execution.

This module does not enable production orders. It provides deterministic order
intent IDs and reconciliation hooks so any future live adapter can fail closed
before submitting or trusting exchange state.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from src.data.storage import Storage


ORDER_STATES = (
    "created",
    "submitted",
    "acknowledged",
    "partially_filled",
    "filled",
    "closing",
    "closed",
    "rejected",
    "cancelled",
    "reconciliation_required",
)


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    incidents: list[dict[str, Any]]


def deterministic_client_order_id(
    *,
    strategy: str | None,
    symbol: str,
    side: str,
    signal_time: int,
    execution_bar_open: int,
    intent_type: str = "entry",
) -> str:
    payload = {
        "strategy": strategy or "",
        "symbol": symbol,
        "side": side,
        "signal_time": int(signal_time),
        "execution_bar_open": int(execution_bar_open),
        "intent_type": intent_type,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"alt-{digest[:28]}"


def create_order_intent(
    storage: Storage,
    *,
    strategy: str | None,
    symbol: str,
    side: str,
    signal_time: int,
    execution_bar_open: int,
    quantity: float | None = None,
    limit_price: float | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    client_order_id = deterministic_client_order_id(
        strategy=strategy,
        symbol=symbol,
        side=side,
        signal_time=signal_time,
        execution_bar_open=execution_bar_open,
    )
    storage.insert_order_intent(
        {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "intent_type": "entry",
            "quantity": quantity,
            "limit_price": limit_price,
            "status": "created",
            "reason": reason,
            "metadata": json.dumps(metadata or {}, sort_keys=True),
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }
    )
    return client_order_id


def reconcile_startup(storage: Storage, exchange: Any | None = None) -> ReconciliationResult:
    """Startup reconciliation placeholder.

    Until live/testnet adapters provide normalized exchange order/position
    snapshots, any existing open incident keeps entries blocked.
    """
    incidents = storage.open_reconciliation_incidents()
    return ReconciliationResult(ok=not incidents, incidents=incidents)


def reconcile_cycle(storage: Storage, exchange: Any | None = None) -> ReconciliationResult:
    """Per-cycle reconciliation placeholder for live/testnet adapters."""
    incidents = storage.open_reconciliation_incidents()
    return ReconciliationResult(ok=not incidents, incidents=incidents)

