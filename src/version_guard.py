"""Version-parity guard -- catches long-running processes on stale code.

This project has now had SIX instances of "two things that should be one
thing" silently diverging; the sixth (2026-07-14) was the worker and
dashboard running different versions of the same module for half a day --
the worker recorded benchmarks with fixed code while the dashboard's stale
copy failed on every read and showed "collecting" forever.

Each long-running process captures the repo's git SHA AT IMPORT TIME (the
code it actually loaded, not whatever HEAD says later). The worker
publishes its SHA to system_status every cycle; the dashboard compares its
own captured SHA against the worker's on every payload build, logs ONE loud
research_decisions warning per process lifetime on mismatch, and surfaces a
banner. A mismatch means: someone committed and restarted one process but
not the other -- restart the stale one.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_BUILD_KEY = "worker_build"


def current_git_sha(repo_root: Path = _PROJECT_ROOT) -> str | None:
    """HEAD's SHA read directly from .git (no subprocess): resolves the ref
    in .git/HEAD, or returns the raw SHA when detached."""
    try:
        head = (repo_root / ".git" / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:].strip()
            ref_path = repo_root / ".git" / ref
            if ref_path.exists():
                return ref_path.read_text().strip()
            packed = repo_root / ".git" / "packed-refs"
            if packed.exists():
                for line in packed.read_text().splitlines():
                    if line.endswith(ref) and not line.startswith("#"):
                        return line.split()[0]
            return None
        return head or None
    except Exception:  # noqa: BLE001
        return None


# Captured ONCE at import: the code version this process started with.
LOADED_SHA: str | None = current_git_sha()


def publish_worker_build(storage: Any) -> None:
    """Worker side: record which code this process is running. Called every
    cycle -- cheap, and self-heals if system_status was wiped."""
    try:
        storage.set_system_status(
            WORKER_BUILD_KEY,
            json.dumps({"sha": LOADED_SHA, "published_at": int(time.time())}),
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to publish worker build")


def check_version_parity(storage: Any) -> dict[str, Any]:
    """Dashboard side: compare this process's loaded SHA against the
    worker's published one. Returns a payload-ready dict; never raises."""
    worker_sha = None
    published_at = None
    try:
        with storage._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT value FROM system_status WHERE key = ?", (WORKER_BUILD_KEY,)
            ).fetchone()
        if row:
            info = json.loads(dict(row)["value"])
            worker_sha = info.get("sha")
            published_at = info.get("published_at")
    except Exception:  # noqa: BLE001
        logger.exception("failed to read worker build")

    match = bool(LOADED_SHA and worker_sha and LOADED_SHA == worker_sha)
    unknown = LOADED_SHA is None or worker_sha is None
    return {
        "worker_sha": worker_sha,
        "dashboard_sha": LOADED_SHA,
        "worker_published_at": published_at,
        "match": match,
        "unknown": unknown,
    }


_MISMATCH_LOGGED = False


def warn_once_on_mismatch(storage: Any, parity: dict[str, Any]) -> None:
    """One loud decision-feed warning per process lifetime."""
    global _MISMATCH_LOGGED  # noqa: PLW0603
    if _MISMATCH_LOGGED or parity.get("match") or parity.get("unknown"):
        return
    _MISMATCH_LOGGED = True
    try:
        from src.research import decisions

        decisions.log(
            "dashboard", "version_mismatch", "worker_vs_dashboard",
            detail={
                "worker_sha": parity.get("worker_sha"),
                "dashboard_sha": parity.get("dashboard_sha"),
                "fix": "restart the stale process (launchctl kickstart -k "
                       "gui/501/com.altcoin.worker or com.altcoin.dashboard)",
                "why_it_matters": "worker and dashboard running different code is "
                                  "divergence class #6 -- last time it made the "
                                  "benchmark panel read 'collecting' for half a day "
                                  "while data recorded fine",
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to log version mismatch")
    logger.warning("VERSION MISMATCH worker=%s dashboard=%s",
                   parity.get("worker_sha"), parity.get("dashboard_sha"))
