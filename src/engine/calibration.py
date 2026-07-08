"""Empirical confidence calibration from realized trade outcomes.

Setup scores in evaluation.py start as hand-set priors (0.55-0.7). Once real
closed trades accumulate, the displayed confidence should converge to the
realized win rate of that strategy+direction. This uses a Beta-binomial style
shrinkage: the prior score is worth CALIBRATION_PRIOR_WEIGHT virtual trades,
so few real trades barely move the number and many real trades dominate it.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.data.storage import Storage, get_storage


def build_calibration_map(storage: Storage | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """(strategy, direction) -> {trades, wins, win_rate} from closed trades."""
    storage = storage or get_storage()
    stats = {}
    for row in storage.get_strategy_stats():
        stats[(row["strategy"], row["direction"])] = {
            "trades": row["trades"],
            "wins": row["wins"],
            "win_rate": (row["wins"] / row["trades"]) if row["trades"] else None,
        }
    return stats


def calibrate_score(
    base_score: float,
    strategy: str,
    direction: str,
    calibration: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Blend a hand-set setup score with the realized win rate.

    Returns the calibrated score plus the evidence behind it, so the UI can
    show "확신 62% (실측 12전 7승)" instead of an unexplained number.
    """
    stats = calibration.get((strategy, direction))
    trades = stats["trades"] if stats else 0
    wins = stats["wins"] if stats else 0

    if trades < config.CALIBRATION_MIN_TRADES:
        return {
            "score": round(base_score, 2),
            "base_score": round(base_score, 2),
            "sample_trades": trades,
            "sample_wins": wins,
            "calibrated": False,
        }

    weight = config.CALIBRATION_PRIOR_WEIGHT
    blended = (base_score * weight + wins) / (weight + trades)
    return {
        "score": round(min(0.95, max(0.05, blended)), 2),
        "base_score": round(base_score, 2),
        "sample_trades": trades,
        "sample_wins": wins,
        "calibrated": True,
    }
