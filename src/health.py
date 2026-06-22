"""Service health tracking — heartbeat file + status for dashboard."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import config

HEALTH_FILE = config.DATA_DIR / "health.json"
PROBE_FILE = config.DATA_DIR / "health_probe.json"


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


class HealthMonitor:
    """Writes app heartbeat to disk and builds a live status snapshot."""

    def __init__(self) -> None:
        self._state = _read_json(HEALTH_FILE)

    def _save(self) -> None:
        self._state["updated_at"] = _now_ts()
        _write_json(HEALTH_FILE, self._state)

    def mark_started(self, pid: int | None = None) -> None:
        now = _now_ts()
        self._state.update(
            {
                "status": "starting",
                "pid": pid or os.getpid(),
                "started_at": now,
                "last_cycle_at": None,
                "last_cycle_ok": None,
                "last_cycle_error": None,
                "cycle_count": 0,
                "ngrok_url": self._state.get("ngrok_url"),
                "launchd": os.getenv("LAUNCHD_SERVICE") == "1",
            }
        )
        self._save()

    def mark_running(self) -> None:
        self._state["status"] = "online"
        self._save()

    def mark_ngrok(self, url: str) -> None:
        self._state["ngrok_url"] = url.rstrip("/")
        self._save()

    def mark_cycle(self, ok: bool, error: str | None = None) -> None:
        self._state.update(
            {
                "status": "online" if ok else "degraded",
                "last_cycle_at": _now_ts(),
                "last_cycle_ok": ok,
                "last_cycle_error": error,
                "cycle_count": int(self._state.get("cycle_count", 0)) + 1,
            }
        )
        self._save()

    def get_status(self) -> dict[str, Any]:
        state = _read_json(HEALTH_FILE)
        probe = _read_json(PROBE_FILE)
        now = _now_ts()

        started_at = state.get("started_at")
        last_cycle_at = state.get("last_cycle_at")
        uptime = (now - started_at) if started_at else None

        stale_limit = config.HEALTH_STALE_SECONDS
        cycle_stale = (
            last_cycle_at is not None and (now - int(last_cycle_at)) > stale_limit
        )
        no_cycle_yet = last_cycle_at is None and started_at and (now - started_at) > stale_limit

        status = state.get("status", "offline")
        if status == "online" and (cycle_stale or no_cycle_yet):
            status = "degraded"

        live = status in ("online", "degraded", "starting") and bool(state.get("pid"))

        return {
            "live": live,
            "status": status,
            "pid": state.get("pid"),
            "started_at": started_at,
            "uptime_seconds": uptime,
            "uptime_human": _format_uptime(uptime),
            "last_cycle_at": last_cycle_at,
            "last_cycle_ago": _format_ago(now, last_cycle_at),
            "last_cycle_ok": state.get("last_cycle_ok"),
            "last_cycle_error": state.get("last_cycle_error"),
            "cycle_count": state.get("cycle_count", 0),
            "ngrok_url": state.get("ngrok_url"),
            "launchd": state.get("launchd", False),
            "probe_ok": probe.get("ok"),
            "probe_at": probe.get("checked_at"),
            "probe_ago": _format_ago(now, probe.get("checked_at")),
            "probe_error": probe.get("error"),
        }


_monitor: HealthMonitor | None = None


def get_health() -> HealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = HealthMonitor()
    return _monitor


def _format_uptime(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_ago(now: int, ts: int | None) -> str | None:
    if ts is None:
        return None
    delta = max(0, now - int(ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h ago"
