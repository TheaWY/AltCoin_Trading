"""Research stack API — manual rollback and long-term history views."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from src.api.dashboard_data import invalidate_payload_cache
from src.data.storage import get_storage
from src.research.promotion import _ensure_schema
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


def _table_exists(name: str) -> bool:
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,),
        ).fetchone()
    return row is not None


def _max_drawdown_from_pnls(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _closed_trade_stats(start_ts: int, end_ts: int | None) -> dict[str, Any]:
    storage = get_storage()
    clauses = ["status = 'closed'", "closed_at >= ?"]
    params: list[Any] = [start_ts]
    if end_ts is not None:
        clauses.append("closed_at < ?")
        params.append(end_ts)
    sql = (
        "SELECT pnl FROM paper_trades "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY closed_at"
    )
    with storage._connect() as conn:  # noqa: SLF001
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    pnls = [float(r.get("pnl") or 0.0) for r in rows]
    trade_count = len(pnls)
    net_pnl = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    max_dd = _max_drawdown_from_pnls(pnls)
    return {
        "trade_count": trade_count,
        "net_pnl": round(net_pnl, 4),
        "expectancy": round(net_pnl / trade_count, 6) if trade_count else 0.0,
        "win_rate_pct": round(wins / trade_count * 100.0, 2) if trade_count else None,
        "max_drawdown": round(max_dd, 4),
    }


def _parse_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _config_diff(
    current: dict[str, Any], previous: dict[str, Any]
) -> list[dict[str, Any]]:
    diff: list[dict[str, Any]] = []
    keys = sorted(set(current.keys()) | set(previous.keys()))
    for key in keys:
        before = previous.get(key)
        after = current.get(key)
        if before != after:
            diff.append({"key": key, "from": before, "to": after})
    return diff


def _week_start(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


@router.get("/history")
def get_history() -> dict[str, Any]:
    """Promotion timeline + weekly long-term research report cards."""
    _ensure_schema()
    storage = get_storage()

    now_ts = int(time.time())
    with storage._connect() as conn:  # noqa: SLF001
        promotions = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM promotions ORDER BY created_at DESC"
            ).fetchall()
        ]

    timeline: list[dict[str, Any]] = []
    # Build reign windows from chronological sequence.
    chronological = sorted(promotions, key=lambda p: int(p["created_at"]))
    for idx, row in enumerate(chronological):
        start_ts = int(row["created_at"])
        end_ts = (
            int(chronological[idx + 1]["created_at"])
            if idx + 1 < len(chronological)
            else now_ts
        )
        current_overrides = _parse_json(row.get("overrides_json"))
        previous_overrides = _parse_json(row.get("previous_overrides_json"))
        timeline.append(
            {
                "id": row["id"],
                "created_at": start_ts,
                "action": row.get("action"),
                "label": "승격" if row.get("action") == "promote" else "롤백",
                "config_hash": row.get("config_hash"),
                "reason": row.get("reason") or "",
                "reign_start": start_ts,
                "reign_end": end_ts,
                "config_diff": _config_diff(current_overrides, previous_overrides),
                "reign_performance": _closed_trade_stats(start_ts, end_ts),
            }
        )
    timeline.sort(key=lambda item: int(item["created_at"]), reverse=True)

    # Weekly rollups: last 12 weeks (current week included), newest first.
    now_dt = datetime.now(timezone.utc)
    week0 = _week_start(now_dt)
    week_starts = [week0 - timedelta(days=7 * i) for i in range(12)]
    weekly: list[dict[str, Any]] = []

    decisions_enabled = _table_exists("research_decisions")
    for ws in week_starts:
        we = ws + timedelta(days=7)
        ws_ts = int(ws.timestamp())
        we_ts = int(we.timestamp())

        with storage._connect() as conn:  # noqa: SLF001
            done_failed = conn.execute(
                "SELECT status, COUNT(*) AS n FROM experiments "
                "WHERE finished_at >= ? AND finished_at < ? "
                "AND status IN ('done', 'failed') "
                "GROUP BY status",
                (ws_ts, we_ts),
            ).fetchall()
            best_row = conn.execute(
                "SELECT config_hash, metrics_json FROM experiments "
                "WHERE status = 'done' AND finished_at >= ? AND finished_at < ?",
                (ws_ts, we_ts),
            ).fetchall()

            decision_counts: dict[str, int] = {}
            if decisions_enabled:
                for r in conn.execute(
                    "SELECT action, COUNT(*) AS n FROM research_decisions "
                    "WHERE created_at >= ? AND created_at < ? "
                    "GROUP BY action",
                    (ws_ts, we_ts),
                ).fetchall():
                    decision_counts[str(r["action"])] = int(r["n"])

        by_status = {str(r["status"]): int(r["n"]) for r in done_failed}
        best_candidate: dict[str, Any] | None = None
        for row in best_row:
            metrics = _parse_json(row["metrics_json"])
            agg = metrics.get("aggregate", {}) if isinstance(metrics, dict) else {}
            exp = agg.get("expectancy")
            if exp is None:
                continue
            if best_candidate is None or float(exp) > float(best_candidate["expectancy"]):
                best_candidate = {
                    "config_hash": row["config_hash"],
                    "expectancy": float(exp),
                }

        champion_week = _closed_trade_stats(ws_ts, we_ts)
        weekly.append(
            {
                "week_start": ws_ts,
                "week_end": we_ts,
                "experiments": {
                    "done": by_status.get("done", 0),
                    "failed": by_status.get("failed", 0),
                },
                "decisions_by_action": decision_counts,
                "best_candidate": best_candidate,
                "champion_reign": champion_week,
            }
        )

    return {"timeline": timeline, "weekly": weekly, "generated_at": now_ts}
