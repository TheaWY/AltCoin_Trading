"""Research API — experimentation feed + long-term history views.

Read-only except /api/research/rollback (manual override, POST).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter

from src.data.storage import get_storage
from src.research import decisions
from src.research.promotion import _ensure_schema
from src.research.robust_stats import correlation_matrix, effective_breadth, pbo_lite

router = APIRouter(prefix="/research", tags=["research"])


def _rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with get_storage()._connect() as conn:  # noqa: SLF001
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _table_exists(name: str) -> bool:
    try:
        _rows(f"SELECT 1 FROM {name} LIMIT 1")  # noqa: S608 — fixed internal names
        return True
    except Exception:
        return False


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


def _config_diff(current: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
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


@router.get("/summary")
def summary() -> dict[str, Any]:
    from src.research.promotion import OVERRIDES_PATH, _last_promotion, _read_json

    out: dict[str, Any] = {"generated_at": int(time.time())}
    if _table_exists("experiments"):
        counts = _rows("SELECT status, COUNT(*) AS n FROM experiments GROUP BY status")
        out["queue"] = {r["status"]: r["n"] for r in counts}
    else:
        out["queue"] = {}
    try:
        out["active_overrides"] = _read_json(OVERRIDES_PATH)
        out["last_promotion"] = _last_promotion()
    except Exception:
        out["active_overrides"], out["last_promotion"] = {}, None
    return out


@router.get("/decisions")
def recent_decisions(limit: int = 100, since: int | None = None) -> list[dict[str, Any]]:
    return decisions.recent(limit=min(limit, 500), since=since)


@router.get("/experiments")
def experiments(limit: int = 100) -> list[dict[str, Any]]:
    if not _table_exists("experiments"):
        return []
    rows = _rows(
        "SELECT id, config_hash, config_json, status, priority, "
        "is_champion_baseline, created_at, finished_at, metrics_json "
        "FROM experiments ORDER BY COALESCE(finished_at, created_at) DESC LIMIT ?",
        (min(limit, 500),),
    )
    out = []
    for r in rows:
        item = {
            k: r[k]
            for k in (
                "id",
                "config_hash",
                "status",
                "priority",
                "is_champion_baseline",
                "created_at",
                "finished_at",
            )
        }
        item["config"] = json.loads(r["config_json"] or "{}")
        metrics = json.loads(r["metrics_json"] or "{}")
        agg = metrics.get("aggregate", {})
        item["aggregate"] = {
            k: agg.get(k)
            for k in (
                "trade_count",
                "total_pnl",
                "gross_pnl",
                "total_fees",
                "expectancy",
                "profit_factor",
                "positive_windows",
                "window_count",
            )
        }
        item["windows"] = [
            {k: w.get(k) for k in ("start", "end", "total_pnl", "trade_count")}
            for w in metrics.get("windows", [])
        ]
        out.append(item)
    return out


@router.get("/event-study")
def event_study(limit: int = 200) -> list[dict[str, Any]]:
    if not _table_exists("event_study_results"):
        return []
    latest = _rows("SELECT MAX(run_at) AS m FROM event_study_results")
    run_at = latest[0]["m"] if latest and latest[0]["m"] else 0
    return _rows(
        "SELECT * FROM event_study_results WHERE run_at = ? "
        "ORDER BY signal, horizon_h, regime LIMIT ?",
        (run_at, min(limit, 500)),
    )


@router.get("/promotions")
def promotions(limit: int = 50) -> list[dict[str, Any]]:
    if not _table_exists("promotions"):
        return []
    rows = _rows(
        "SELECT * FROM promotions ORDER BY created_at DESC LIMIT ?",
        (min(limit, 200),),
    )
    for r in rows:
        for key in ("overrides_json", "previous_overrides_json"):
            try:
                r[key.replace("_json", "")] = json.loads(r.pop(key) or "{}")
            except (TypeError, json.JSONDecodeError):
                r[key.replace("_json", "")] = {}
    return rows


@router.get("/correlation")
def correlation() -> dict[str, Any]:
    """Setup-level daily-PnL correlation + effective breadth."""
    rows = _rows(
        "SELECT strategy, closed_at, pnl FROM paper_trades "
        "WHERE status='closed' AND closed_at IS NOT NULL"
    )
    series: dict[str, dict[str, float]] = {}
    for r in rows:
        name = r["strategy"] or "unknown"
        day = time.strftime("%Y-%m-%d", time.gmtime(int(r["closed_at"])))
        series.setdefault(name, {})
        series[name][day] = series[name].get(day, 0.0) + float(r["pnl"] or 0.0)
    matrix = correlation_matrix(series) if len(series) >= 2 else {}
    return {
        "matrix": matrix,
        "breadth": effective_breadth(matrix)
        if matrix
        else {"n": float(len(series)), "avg_corr": 0.0, "effective_breadth": float(len(series))},
        "trade_days": {k: len(v) for k, v in series.items()},
    }


@router.get("/overfit")
def overfit() -> dict[str, Any]:
    """PBO-lite across done experiments + current trial budget usage."""
    result: dict[str, Any] = {}
    if _table_exists("experiments"):
        rows = _rows("SELECT config_hash, metrics_json FROM experiments WHERE status='done'")
        per_config: dict[str, list[float]] = {}
        for r in rows:
            windows = json.loads(r["metrics_json"] or "{}").get("windows", [])
            if windows:
                per_config[r["config_hash"]] = [w.get("total_pnl", 0.0) for w in windows]
        result["pbo"] = pbo_lite(per_config)
        result["trials_done"] = len(rows) if rows else 0
    return result


@router.get("/history")
def get_history() -> dict[str, Any]:
    """Promotion timeline + weekly long-term research report cards."""
    _ensure_schema()
    storage = get_storage()
    now_ts = int(time.time())
    promotions_rows = _rows("SELECT * FROM promotions ORDER BY created_at DESC") if _table_exists("promotions") else []

    timeline: list[dict[str, Any]] = []
    chronological = sorted(promotions_rows, key=lambda p: int(p["created_at"]))
    for idx, row in enumerate(chronological):
        start_ts = int(row["created_at"])
        end_ts = int(chronological[idx + 1]["created_at"]) if idx + 1 < len(chronological) else now_ts
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

    now_dt = datetime.now(timezone.utc)
    week0 = _week_start(now_dt)
    week_starts = [week0 - timedelta(days=7 * i) for i in range(12)]
    weekly: list[dict[str, Any]] = []
    decisions_enabled = _table_exists("research_decisions")

    for ws in week_starts:
        we = ws + timedelta(days=7)
        ws_ts = int(ws.timestamp())
        we_ts = int(we.timestamp())
        done_failed = (
            _rows(
                "SELECT status, COUNT(*) AS n FROM experiments "
                "WHERE finished_at >= ? AND finished_at < ? "
                "AND status IN ('done', 'failed') GROUP BY status",
                (ws_ts, we_ts),
            )
            if _table_exists("experiments")
            else []
        )
        best_rows = (
            _rows(
                "SELECT config_hash, metrics_json FROM experiments "
                "WHERE status = 'done' AND finished_at >= ? AND finished_at < ?",
                (ws_ts, we_ts),
            )
            if _table_exists("experiments")
            else []
        )
        decision_counts: dict[str, int] = {}
        if decisions_enabled:
            for r in _rows(
                "SELECT action, COUNT(*) AS n FROM research_decisions "
                "WHERE created_at >= ? AND created_at < ? GROUP BY action",
                (ws_ts, we_ts),
            ):
                decision_counts[str(r["action"])] = int(r["n"])

        by_status = {str(r["status"]): int(r["n"]) for r in done_failed}
        best_candidate: dict[str, Any] | None = None
        for row in best_rows:
            metrics = _parse_json(row["metrics_json"])
            agg = metrics.get("aggregate", {}) if isinstance(metrics, dict) else {}
            exp = agg.get("expectancy")
            if exp is None:
                continue
            if best_candidate is None or float(exp) > float(best_candidate["expectancy"]):
                best_candidate = {"config_hash": row["config_hash"], "expectancy": float(exp)}

        weekly.append(
            {
                "week_start": ws_ts,
                "week_end": we_ts,
                "experiments": {"done": by_status.get("done", 0), "failed": by_status.get("failed", 0)},
                "decisions_by_action": decision_counts,
                "best_candidate": best_candidate,
                "champion_reign": _closed_trade_stats(ws_ts, we_ts),
            }
        )

    return {"timeline": timeline, "weekly": weekly, "generated_at": now_ts}


@router.post("/rollback")
def manual_rollback() -> dict[str, Any]:
    from src.research.promotion import rollback

    return rollback("manual rollback from dashboard")
