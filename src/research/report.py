"""Research budget and completed-experiment analysis.

This module is read-only: it explains what the overfitting guard is doing and
extracts the useful evidence from completed experiments without raising the
trial budget or promoting anything.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from typing import Any

from src.data.storage import Storage, get_storage
from src.research.promotion import S1_MIN_PROFIT_FACTOR, S1_MIN_TRADES, S1_MIN_WINDOW_WIN_FRACTION, _SCHEMA
from src.research.robust_stats import max_trials_for_history
from src.research.runner import WINDOW_COUNT, WINDOW_STEP_DAYS, WINDOW_TEST_DAYS


MIN_PF_TRADES = 30


def _ensure_schema_for(storage: Storage) -> None:
    with storage._connect() as conn:  # noqa: SLF001
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)


def trial_budget_status(storage: Storage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    _ensure_schema_for(storage)
    history_years = ((WINDOW_COUNT - 1) * WINDOW_STEP_DAYS + WINDOW_TEST_DAYS) / 365.0
    budget_override = int(os.getenv("RESEARCH_TRIAL_BUDGET", "0"))
    budget = budget_override if budget_override > 0 else max_trials_for_history(history_years)
    with storage._connect() as conn:  # noqa: SLF001
        tried_row = conn.execute(
            "SELECT COUNT(*) AS n FROM experiments WHERE status IN ('done','failed')"
        ).fetchone()
        counts = {
            str(r["status"]): int(r["n"])
            for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM experiments GROUP BY status"
            ).fetchall()
        }
    tried = int(tried_row["n"] if isinstance(tried_row, dict) else tried_row[0])
    remaining = max(budget - tried, 0)
    return {
        "budget": budget,
        "tried": tried,
        "remaining": remaining,
        "history_years": round(history_years, 2),
        "exhausted": remaining <= 0,
        "reason": "실험 예산 소진 — overfitting guard active" if remaining <= 0 else "실험 예산 사용 가능",
        "next_safe_action": (
            "완료된 실험을 분석해 축을 좁히고, 더 긴 히스토리/새 데이터가 쌓일 때까지 실행은 보류"
            if remaining <= 0
            else "근거가 좋은 소수 후보만 실행"
        ),
        "counts": {
            "done": counts.get("done", 0),
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "failed": counts.get("failed", 0),
        },
    }


def _parse_json(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _done_rows(storage: Storage) -> list[dict[str, Any]]:
    _ensure_schema_for(storage)
    with storage._connect() as conn:  # noqa: SLF001
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM experiments WHERE status = 'done' ORDER BY finished_at DESC"
            ).fetchall()
        ]


def _queued_failed_pump_count(storage: Storage) -> int:
    _ensure_schema_for(storage)
    count = 0
    with storage._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT config_json FROM experiments WHERE status = 'queued'"
        ).fetchall()
    for row in rows:
        cfg = _parse_json(row["config_json"])
        if cfg.get("SETUP_FAILED_PUMP_ENABLED") is True:
            count += 1
    return count


def _experiment_item(row: dict[str, Any]) -> dict[str, Any]:
    config = _parse_json(row.get("config_json"))
    metrics = _parse_json(row.get("metrics_json"))
    agg = metrics.get("aggregate", {}) if isinstance(metrics, dict) else {}
    windows = metrics.get("windows", []) if isinstance(metrics, dict) else []
    positive_windows = int(agg.get("positive_windows") or sum(1 for w in windows if float(w.get("total_pnl", 0) or 0) > 0))
    window_count = int(agg.get("window_count") or len(windows) or 0)
    trades = int(agg.get("trade_count") or 0)
    total_fees = float(agg.get("total_fees") or 0.0)
    gross_pnl = float(agg.get("gross_pnl") or 0.0)
    total_pnl = float(agg.get("total_pnl") or 0.0)
    fee_ratio = abs(total_fees / gross_pnl) if gross_pnl else (999.0 if total_fees else 0.0)
    return {
        "hash": row.get("config_hash"),
        "config": config,
        "strategy": config.get("ACTIVE_STRATEGY"),
        "is_champion": bool(row.get("is_champion_baseline")),
        "trades": trades,
        "expectancy": float(agg.get("expectancy") or 0.0),
        "profit_factor": float(agg.get("profit_factor") or 0.0),
        "total_pnl": total_pnl,
        "gross_pnl": gross_pnl,
        "total_fees": total_fees,
        "fee_to_gross_abs": round(fee_ratio, 4),
        "positive_windows": positive_windows,
        "window_count": window_count,
        "positive_window_fraction": positive_windows / window_count if window_count else 0.0,
    }


def _rejection_reasons(item: dict[str, Any], champion: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []
    if item["trades"] < S1_MIN_TRADES:
        reasons.append(f"low_trade_count {item['trades']}<{S1_MIN_TRADES}")
    if item["positive_window_fraction"] < S1_MIN_WINDOW_WIN_FRACTION:
        reasons.append(
            f"bad_windows {item['positive_windows']}/{item['window_count']}<{S1_MIN_WINDOW_WIN_FRACTION:.0%}"
        )
    if item["profit_factor"] < S1_MIN_PROFIT_FACTOR:
        reasons.append(f"profit_factor {item['profit_factor']:.2f}<{S1_MIN_PROFIT_FACTOR}")
    if item["expectancy"] <= 0:
        reasons.append("expectancy<=0")
    if item["fee_to_gross_abs"] > 0.35:
        reasons.append(f"high_fees fee/gross={item['fee_to_gross_abs']:.2f}")
    if champion and item["expectancy"] <= champion["expectancy"]:
        reasons.append(
            f"does_not_beat_champion {item['expectancy']:.4f}<={champion['expectancy']:.4f}"
        )
    return reasons or ["promotion_candidate_needs_fresh_eval"]


def _axis_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scores: dict[str, dict[Any, list[float]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        if item["trades"] < 10:
            continue
        for key, value in item["config"].items():
            scores[key][value].append(item["expectancy"])
    out = []
    for key, values in scores.items():
        ranked = sorted(
            (
                {
                    "value": value,
                    "samples": len(vals),
                    "avg_expectancy": round(sum(vals) / len(vals), 6),
                }
                for value, vals in values.items()
            ),
            key=lambda r: (-r["avg_expectancy"], -r["samples"], str(r["value"])),
        )
        out.append({"axis": key, "ranked_values": ranked[:4]})
    return sorted(out, key=lambda r: r["axis"])


def build_report(storage: Storage | None = None, limit: int = 8) -> dict[str, Any]:
    storage = storage or get_storage()
    budget = trial_budget_status(storage)
    items = [_experiment_item(row) for row in _done_rows(storage)]
    champion = next((i for i in items if i["is_champion"]), None)
    non_champ = [i for i in items if not i["is_champion"]]

    top_expectancy = sorted(non_champ, key=lambda i: (-i["expectancy"], -i["trades"]))[:limit]
    top_pf = sorted(
        [i for i in non_champ if i["trades"] >= MIN_PF_TRADES],
        key=lambda i: (-i["profit_factor"], -i["expectancy"], -i["trades"]),
    )[:limit]
    failed_pump = [
        i for i in non_champ if i["config"].get("SETUP_FAILED_PUMP_ENABLED") is True
    ]
    failed_pump_top = sorted(failed_pump, key=lambda i: (-i["expectancy"], -i["trades"]))[:limit]
    beat_champion = (
        [i for i in non_champ if i["expectancy"] > champion["expectancy"]]
        if champion
        else []
    )
    beat_champion = sorted(beat_champion, key=lambda i: (-i["expectancy"], -i["trades"]))[:limit]

    rejected = []
    for item in sorted(non_champ, key=lambda i: (-i["expectancy"], -i["trades"])):
        reasons = _rejection_reasons(item, champion)
        if reasons != ["promotion_candidate_needs_fresh_eval"]:
            rejected.append({**item, "reasons": reasons})
        if len(rejected) >= limit:
            break

    useful = [i for i in non_champ if i["trades"] >= 10 and i["expectancy"] > 0]
    strategy_counts = Counter(i["strategy"] for i in useful)
    recommended_axes = _axis_summary(useful)
    next_candidates = []
    for item in sorted(useful, key=lambda i: (-i["expectancy"], -i["positive_window_fraction"], -i["trades"]))[: min(12, limit * 2)]:
        next_candidates.append(
            {
                "hash": item["hash"],
                "strategy": item["strategy"],
                "config": item["config"],
                "why": (
                    f"expectancy {item['expectancy']:.4f}, PF {item['profit_factor']:.2f}, "
                    f"windows {item['positive_windows']}/{item['window_count']}, trades {item['trades']}"
                ),
            }
        )

    return {
        "budget": budget,
        "completed_analysis_label": f"완료된 {budget['counts']['done']}개 분석 결과",
        "champion": champion,
        "top_by_expectancy": top_expectancy,
        "top_by_profit_factor": top_pf,
        "failed_pump_short": {
            "completed_count": len(failed_pump),
            "queued_count": _queued_failed_pump_count(storage),
            "count": len(failed_pump),
            "top": failed_pump_top,
        },
        "beat_champion": beat_champion,
        "rejected": rejected,
        "recommended_narrowed_space": {
            "note": "예산을 늘리지 말고, 다음 독립 예산/추가 히스토리에서 아래 축만 우선 검증",
            "strategy_counts_among_positive": dict(strategy_counts),
            "axes": recommended_axes,
            "next_candidates": next_candidates,
        },
    }
