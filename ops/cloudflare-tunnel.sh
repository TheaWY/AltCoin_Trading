#!/bin/bash
# Expose the local dashboard for phone access via Cloudflare quick tunnel.
# Requires: cloudflared (brew install cloudflared)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${API_PORT:-8000}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Install cloudflared: brew install cloudflared" >&2
  exit 1
fi

if ! curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
  echo "Dashboard not running on :${PORT}. Start it first:"
  echo "  cd $REPO && .venv/bin/python -m uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT}"
  exit 1
fi

echo "Tunneling http://localhost:${PORT} — open the URL cloudflared prints below."
echo "Dashboard path: /dashboard"
exec cloudflared tunnel --url "http://localhost:${PORT}"
