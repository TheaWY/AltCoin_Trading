"""Decision log — every choice the self-correcting loop makes, persisted.

The dashboard's 실험 page polls this to show a real-time feed of what the
system decided and WHY: experiments started/finished, gates passed/blocked
(with the exact reason), promotions, rollbacks, decay flags, trial-budget
refusals. Nothing the loop does is invisible.
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.data.storage import get_storage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_decisions_ts ON research_decisions(ts)
"""


def _ensure() -> None:
    with get_storage()._connect() as conn:  # noqa: SLF001
        for stmt in _SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(stmt)


def log(actor: str, action: str, subject: str | None = None,
        detail: dict[str, Any] | str | None = None) -> None:
    """actor: runner|promotion|generator|health|data_quality
    action: e.g. experiment_started, experiment_done, gate1_blocked,
            gate_dsr_blocked, promoted, rolled_back, decay_flagged,
            trial_budget_refused ...
    Never raises — a logging failure must not break the loop."""
    try:
        _ensure()
        payload = json.dumps(detail) if isinstance(detail, dict) else (detail or "")
        with get_storage()._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO research_decisions (ts, actor, action, subject, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), actor, action, subject or "", payload),
            )
    except Exception:
        pass


def recent(limit: int = 100, since: int | None = None) -> list[dict[str, Any]]:
    _ensure()
    with get_storage()._connect() as conn:  # noqa: SLF001
        if since:
            rows = conn.execute(
                "SELECT * FROM research_decisions WHERE ts >= ? "
                "ORDER BY ts DESC, id DESC LIMIT ?", (since, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM research_decisions ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d["detail"]) if d["detail"] else {}
            except json.JSONDecodeError:
                pass
            out.append(d)
        return out
