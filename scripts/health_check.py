#!/usr/bin/env python3
"""External health probe — pings /api/health and writes data/health_probe.json."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROBE_FILE = PROJECT_ROOT / "data" / "health_probe.json"
DEFAULT_PORT = 8000


def _env_value(key: str) -> str | None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(f"{key}="):
            continue
        value = line.split("=", 1)[1].split("#", 1)[0].strip()
        return value.strip("\"'")
    return None


def main() -> int:
    port = int(
        sys.argv[1]
        if len(sys.argv) > 1
        else (os.getenv("API_PORT") or _env_value("API_PORT") or DEFAULT_PORT)
    )
    url = f"http://127.0.0.1:{port}/api/health"
    now = int(datetime.now(timezone.utc).timestamp())

    result: dict = {"checked_at": now, "ok": False, "status": "offline", "error": None}

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode())
            result["ok"] = bool(body.get("live"))
            result["status"] = body.get("status", "unknown")
            result["pid"] = body.get("pid")
    except urllib.error.URLError as exc:
        result["error"] = str(exc.reason)
    except Exception as exc:
        result["error"] = str(exc)

    PROBE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROBE_FILE.write_text(json.dumps(result, indent=2))

    if result["ok"]:
        print(f"OK — service is {result['status']} (pid {result.get('pid')})")
        return 0

    print(f"FAIL — {result['error'] or result['status']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
