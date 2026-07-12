#!/usr/bin/env python
"""Print the current research budget and completed-experiment analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.report import build_report  # noqa: E402


def _line_item(item: dict) -> str:
    return (
        f"{item.get('hash')} {item.get('strategy')} "
        f"trades={item.get('trades')} exp={item.get('expectancy'):.6f} "
        f"PF={item.get('profit_factor'):.4f} "
        f"windows={item.get('positive_windows')}/{item.get('window_count')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    report = build_report(limit=args.limit)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    budget = report["budget"]
    print("=== research budget ===")
    print(
        f"{budget['reason']} | budget={budget['budget']} tried={budget['tried']} "
        f"remaining={budget['remaining']} history_years={budget['history_years']}"
    )
    print(
        "counts "
        + " ".join(f"{k}={v}" for k, v in sorted(budget["counts"].items()))
    )
    print(f"next_safe_action: {budget['next_safe_action']}")

    print("\n=== champion baseline ===")
    champion = report.get("champion")
    print(_line_item(champion) if champion else "no completed champion baseline")

    for title, key in (
        ("top by expectancy", "top_by_expectancy"),
        ("top by profit factor with sufficient trades", "top_by_profit_factor"),
        ("failed_pump_short configs", "failed_pump_short"),
        ("configs that beat champion", "beat_champion"),
    ):
        print(f"\n=== {title} ===")
        rows = report[key]["top"] if key == "failed_pump_short" else report[key]
        if key == "failed_pump_short":
            print(
                f"completed={report[key].get('completed_count', 0)} "
                f"queued={report[key].get('queued_count', 0)}"
            )
        if not rows:
            print("none")
        for item in rows:
            print(_line_item(item))

    print("\n=== rejected configs ===")
    for item in report["rejected"]:
        print(_line_item(item) + " | " + "; ".join(item["reasons"]))
    if not report["rejected"]:
        print("none")

    narrowed = report["recommended_narrowed_space"]
    print("\n=== recommended narrowed next search space ===")
    print(narrowed["note"])
    print("positive_strategy_counts:", narrowed["strategy_counts_among_positive"])
    for axis in narrowed["axes"]:
        values = ", ".join(
            f"{v['value']}({v['samples']} samples avg={v['avg_expectancy']})"
            for v in axis["ranked_values"]
        )
        print(f"{axis['axis']}: {values}")
    print("\nnext experiment candidates:")
    for cand in narrowed["next_candidates"][: args.limit]:
        print(f"{cand['hash']} {cand['strategy']} | {cand['why']}")
    if not narrowed["next_candidates"]:
        print("none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
