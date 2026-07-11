"""Hypothesis queue generator.

Reads research_space.yaml, enumerates the cartesian product of axes,
dedupes against already-run experiments by config_hash, assigns priority
(priority_families first), and inserts new combos as status='queued'.

Also (re)inserts tonight's champion baseline: the CURRENT live values of
every axis key, marked is_champion_baseline=1, so every gate comparison
has a same-night, same-window reference point.

    python -m src.research.generator            # enqueue new combos
    python -m src.research.generator --dry-run  # show what would be queued
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import time
from pathlib import Path
from typing import Any

import yaml

from src import config
from src.data.storage import get_storage
from src.research.promotion import _ensure_schema, config_hash
from src.research.report import build_report, trial_budget_status

SPACE_PATH = Path(config.DATA_DIR).parent / "research_space.yaml" \
    if str(config.DATA_DIR).endswith("data") else Path("research_space.yaml")


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def _load_space(path: Path | None = None) -> dict[str, Any]:
    p = path or SPACE_PATH
    if not p.exists():
        p = Path(__file__).resolve().parents[2] / "research_space.yaml"
    return yaml.safe_load(p.read_text())


def _combos(axes: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = sorted(axes)
    return [dict(zip(keys, values)) for values in itertools.product(*(axes[k] for k in keys))]


def _priority(combo: dict[str, Any], families: list[dict[str, Any]]) -> int:
    for rank, family in enumerate(families):
        match = family.get("match", {})
        if all(combo.get(k) == v for k, v in match.items()):
            return rank  # lower = earlier
    return 100


def _champion_config(axes: dict[str, list[Any]]) -> dict[str, Any]:
    """Current live value for each axis key: env/overrides if set, else the
    first candidate in the axis (treated as the conservative default)."""
    champion: dict[str, Any] = {}
    for key, candidates in axes.items():
        raw = os.getenv(key)
        if raw is None:
            champion[key] = candidates[0]
        else:
            sample = candidates[0]
            if isinstance(sample, bool):
                champion[key] = raw.lower() in ("true", "1", "yes", "on")
            elif isinstance(sample, (int, float)):
                champion[key] = type(sample)(float(raw))
            else:
                champion[key] = raw
    return champion


def _ordered_candidates(
    axes: dict[str, list[Any]],
    families: list[dict[str, Any]],
    existing: set[str],
    champ_hash: str,
) -> list[dict[str, Any]]:
    candidates = [
        c
        for c in _combos(axes)
        if config_hash(c) not in existing and config_hash(c) != champ_hash
    ]
    if "ACTIVE_STRATEGY" not in axes:
        return sorted(candidates, key=lambda c: _priority(c, families))

    strategy_order = sorted(
        axes["ACTIVE_STRATEGY"],
        key=lambda strategy: (
            _priority({"ACTIVE_STRATEGY": strategy}, families),
            axes["ACTIVE_STRATEGY"].index(strategy),
        ),
    )
    grouped = {
        strategy: sorted(
            (c for c in candidates if c.get("ACTIVE_STRATEGY") == strategy),
            key=lambda c: (_priority(c, families), json.dumps(c, sort_keys=True)),
        )
        for strategy in strategy_order
    }
    ordered: list[dict[str, Any]] = []
    while any(grouped.values()):
        for strategy in strategy_order:
            if grouped[strategy]:
                ordered.append(grouped[strategy].pop(0))
    return ordered


def generate(space_path: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    _ensure_schema()
    space = _load_space(space_path)
    axes: dict[str, list[Any]] = space["axes"]
    total_space = 1
    for values in axes.values():
        total_space *= len(values)
    budget = trial_budget_status()
    if budget.get("exhausted"):
        report = build_report(limit=8)
        return {
            "total_space": total_space,
            "already_run_or_queued": budget["counts"]["done"] + budget["counts"]["queued"],
            "newly_queued": 0,
            "champion_hash": "",
            "budget_exhausted": True,
            "trial_budget": budget,
            "next_candidates": len(report["recommended_narrowed_space"]["next_candidates"]),
        }
    families = space.get("priority_families", [])
    max_new = int(space.get("limits", {}).get("max_new_per_run", 300))

    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        existing = {
            _row_value(row, "config_hash")
            for row in conn.execute("SELECT config_hash FROM experiments").fetchall()
        }

    now = int(time.time())
    queued = 0

    # champion baseline: refresh nightly (delete stale queued baseline, insert new)
    champ = _champion_config(axes)
    champ_hash = config_hash(champ)
    if not dry_run:
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT OR IGNORE INTO experiments (config_hash, config_json, status, "
                "priority, is_champion_baseline, created_at) VALUES (?, ?, 'queued', -1, 1, ?)",
                (champ_hash, json.dumps(champ, sort_keys=True), now),
            )
            # a previously-done baseline stays done; runner re-runs baseline nightly
            conn.execute(
                "UPDATE experiments SET status = 'queued' "
                "WHERE config_hash = ? AND is_champion_baseline = 1",
                (champ_hash,),
            )

    candidates = _ordered_candidates(axes, families, existing, champ_hash)[:max_new]

    for queue_priority, combo in enumerate(candidates):
        if dry_run:
            queued += 1
            continue
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT OR IGNORE INTO experiments (config_hash, config_json, status, "
                "priority, is_champion_baseline, created_at) VALUES (?, ?, 'queued', ?, 0, ?)",
                (config_hash(combo), json.dumps(combo, sort_keys=True),
                 queue_priority if "ACTIVE_STRATEGY" in axes else _priority(combo, families), now),
            )
        queued += 1

    return {
        "total_space": total_space,
        "already_run_or_queued": len(existing),
        "newly_queued": queued,
        "champion_hash": champ_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--space", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = generate(Path(args.space) if args.space else None, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
