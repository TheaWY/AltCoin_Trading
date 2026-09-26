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
from src.research.decisions import log as log_decision
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


def _space_size(axes: dict[str, list[Any]]) -> int:
    total = 1
    for values in axes.values():
        total *= max(len(values), 1)
    return total


def queue_capacity(remaining: int, queued_challengers: int, max_new_per_run: int) -> int:
    """How many NEW (non-champion) experiments may be inserted this run.

    Champion baseline is re-queued nightly and does not consume this cap.
    """
    if remaining <= 0 or max_new_per_run <= 0:
        return 0
    return min(int(max_new_per_run), max(0, int(remaining) - int(queued_challengers)))


def _iter_combos(
    axes: dict[str, list[Any]],
    constraints: dict[str, Any] | None = None,
):
    """Stream the cartesian product without materializing it.

    research_space.yaml is ~10^9 combos; listing them OOMs the nightly generator.
    Callers must stop after `max_new` unseen hashes.
    """
    keys = sorted(axes)
    lists: list[list[Any]] = []
    for k in keys:
        if constraints and k in constraints:
            val = constraints[k]
            if val not in axes[k]:
                return
            lists.append([val])
        else:
            lists.append(axes[k])
    for tup in itertools.product(*lists):
        yield dict(zip(keys, tup))


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


def _strategy_order(axes: dict[str, list[Any]], families: list[dict[str, Any]]) -> list[Any]:
    return sorted(
        axes["ACTIVE_STRATEGY"],
        key=lambda strategy: (
            _priority({"ACTIVE_STRATEGY": strategy}, families),
            axes["ACTIVE_STRATEGY"].index(strategy),
        ),
    )


def _strategy_stream(
    axes: dict[str, list[Any]],
    families: list[dict[str, Any]],
    strategy: Any,
):
    """Priority-family combos for `strategy` first, then the rest of that slice."""
    seen_local: set[str] = set()
    for family in families:
        match = dict(family.get("match") or {})
        if "ACTIVE_STRATEGY" in match and match["ACTIVE_STRATEGY"] != strategy:
            continue
        match["ACTIVE_STRATEGY"] = strategy
        for combo in _iter_combos(axes, match):
            h = config_hash(combo)
            if h in seen_local:
                continue
            seen_local.add(h)
            yield combo
    for combo in _iter_combos(axes, {"ACTIVE_STRATEGY": strategy}):
        h = config_hash(combo)
        if h in seen_local:
            continue
        seen_local.add(h)
        yield combo


def _ordered_candidates(
    axes: dict[str, list[Any]],
    families: list[dict[str, Any]],
    existing: set[str],
    champ_hash: str,
    limit: int,
) -> list[dict[str, Any]]:
    """First `limit` unseen combos, priority-family / strategy round-robin.

    Does not materialize the cartesian product — required because the live
    research_space is billions of rows.
    """
    if limit <= 0:
        return []
    seen = set(existing)
    ordered: list[dict[str, Any]] = []

    def _accept(combo: dict[str, Any]) -> bool:
        h = config_hash(combo)
        if h in seen or h == champ_hash:
            return False
        seen.add(h)
        ordered.append(combo)
        return True

    if "ACTIVE_STRATEGY" not in axes:
        for family in families:
            if len(ordered) >= limit:
                return ordered
            for combo in _iter_combos(axes, family.get("match") or {}):
                _accept(combo)
                if len(ordered) >= limit:
                    return ordered
        for combo in _iter_combos(axes):
            _accept(combo)
            if len(ordered) >= limit:
                return ordered
        return ordered

    iters = [_strategy_stream(axes, families, s) for s in _strategy_order(axes, families)]
    while len(ordered) < limit:
        progressed = False
        for it in iters:
            if len(ordered) >= limit:
                break
            for combo in it:
                if _accept(combo):
                    progressed = True
                    break
        if not progressed:
            break
    return ordered


def generate(space_path: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    _ensure_schema()
    space = _load_space(space_path)
    axes: dict[str, list[Any]] = space["axes"]
    total_space = _space_size(axes)
    budget = trial_budget_status()
    queued_count = int(budget.get("counts", {}).get("queued", 0))
    remaining_budget = int(budget.get("remaining", 0))
    storage = get_storage()
    with storage._connect() as conn:  # noqa: SLF001
        champ_row = conn.execute(
            "SELECT COUNT(*) AS n FROM experiments "
            "WHERE status = 'queued' AND is_champion_baseline = 1"
        ).fetchone()
        existing = {
            _row_value(row, "config_hash")
            for row in conn.execute("SELECT config_hash FROM experiments").fetchall()
        }
    champ_queued = int(_row_value(champ_row, "n", 0) or 0)
    queued_challengers = max(0, queued_count - champ_queued)
    max_per_run = int(space.get("limits", {}).get("max_new_per_run", 300))
    max_new = queue_capacity(remaining_budget, queued_challengers, max_per_run)
    if remaining_budget <= 0:
        report = build_report(limit=8)
        return {
            "total_space": total_space,
            "already_run_or_queued": budget["counts"]["done"] + queued_count,
            "newly_queued": 0,
            "champion_hash": "",
            "budget_exhausted": True,
            "trial_budget": budget,
            "next_candidates": len(report["recommended_narrowed_space"]["next_candidates"]),
        }
    if max_new <= 0 and not dry_run:
        # Still refresh the champion below; no new challengers fit.
        log_decision("generator", "generator_capped", "trial_budget", {
            "queued": queued_count,
            "queued_challengers": queued_challengers,
            "remaining_trial_budget": remaining_budget,
            "budget": budget.get("budget"),
            "tried": budget.get("tried"),
            "total_space": total_space,
        })
    families = space.get("priority_families", [])

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

    candidates = _ordered_candidates(axes, families, existing, champ_hash, max_new)

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
