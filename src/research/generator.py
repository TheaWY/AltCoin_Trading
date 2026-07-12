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

SPACE_PATH = (
    Path(config.DATA_DIR).parent / "research_space.yaml"
    if str(config.DATA_DIR).endswith("data")
    else Path("research_space.yaml")
)


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def _load_space(path: Path | None = None) -> dict[str, Any]:
    p = path or SPACE_PATH
    if not p.exists():
        p = Path(__file__).resolve().parents[2] / "research_space.yaml"
    loaded = yaml.safe_load(p.read_text())
    if not isinstance(loaded, dict) or not isinstance(loaded.get("axes"), dict):
        raise ValueError(f"Invalid research space: {p}")
    return loaded


def _combos(axes: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = sorted(axes)
    return [dict(zip(keys, values)) for values in itertools.product(*(axes[k] for k in keys))]


def _priority(combo: dict[str, Any], families: list[dict[str, Any]]) -> int:
    for rank, family in enumerate(families):
        match = family.get("match", {})
        if all(combo.get(k) == v for k, v in match.items()):
            return rank
    return 100


def _coerce_like(value: Any, sample: Any) -> Any:
    """Coerce env/config values to the type used by the search axis."""
    if isinstance(sample, bool):
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)
    if isinstance(sample, int) and not isinstance(sample, bool):
        return int(float(value))
    if isinstance(sample, float):
        return float(value)
    return str(value)


def _champion_config(axes: dict[str, list[Any]]) -> dict[str, Any]:
    """Resolve the configuration that is actually active.

    Promotion overrides are loaded into ``os.environ`` by ``src.config`` before
    this module runs, so an explicit environment/override value wins. When no
    environment value exists, read the resolved constant from ``src.config``
    instead of incorrectly treating the first search-grid value as the live
    champion. The first candidate is only a last-resort fallback for axes that
    do not have a corresponding config constant.
    """
    champion: dict[str, Any] = {}
    for key, candidates in axes.items():
        if not candidates:
            raise ValueError(f"Research axis {key!r} has no candidates")
        sample = candidates[0]
        raw: Any = os.getenv(key)
        if raw is None:
            raw = getattr(config, key, candidates[0])
        champion[key] = _coerce_like(raw, sample)
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

    champion = _champion_config(axes)
    champ_hash = config_hash(champion)
    if not dry_run:
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT OR IGNORE INTO experiments (config_hash, config_json, status, "
                "priority, is_champion_baseline, created_at) VALUES (?, ?, 'queued', -1, 1, ?)",
                (champ_hash, json.dumps(champion, sort_keys=True), now),
            )
            conn.execute(
                "UPDATE experiments SET status = 'queued', config_json = ?, "
                "is_champion_baseline = 1 WHERE config_hash = ?",
                (json.dumps(champion, sort_keys=True), champ_hash),
            )

    candidates = _ordered_candidates(axes, families, existing, champ_hash)[:max_new]

    if not dry_run and candidates:
        rows = [
            {
                "config_hash": config_hash(combo),
                "config_json": json.dumps(combo, sort_keys=True),
                "priority": queue_priority
                if "ACTIVE_STRATEGY" in axes
                else _priority(combo, families),
                "created_at": now,
            }
            for queue_priority, combo in enumerate(candidates)
        ]
        with storage._connect() as conn:  # noqa: SLF001
            conn.executemany(
                "INSERT OR IGNORE INTO experiments (config_hash, config_json, status, "
                "priority, is_champion_baseline, created_at) "
                "VALUES (:config_hash, :config_json, 'queued', :priority, 0, :created_at)",
                rows,
            )
        queued = len(rows)
    elif dry_run:
        queued = len(candidates)

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
