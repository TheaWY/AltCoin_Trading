#!/usr/bin/env python3
"""Smoke test for research API endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes.research import get_history  # noqa: E402


def main() -> int:
    payload = get_history()
    assert "timeline" in payload, payload
    assert "weekly" in payload, payload
    assert "generated_at" in payload, payload
    if payload["timeline"]:
        row = payload["timeline"][0]
        assert "reign_performance" in row, row
    if payload["weekly"]:
        week = payload["weekly"][0]
        assert "experiments" in week, week
        assert "decisions_by_action" in week, week
        assert "champion_reign" in week, week
    print("research history API smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
