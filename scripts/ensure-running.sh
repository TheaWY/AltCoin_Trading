#!/bin/bash
# Start app + ngrok if either is down. Safe to run from cron or manually.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="${HOME:-$(eval echo ~$(whoami))}"

LOCAL_OK=false
if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  LOCAL_OK=true
fi

NGROK_OK=false
PUBLIC_BASE=$(grep -E '^NGROK_STATIC_DOMAIN=' .env 2>/dev/null | cut -d= -f2- | tr -d ' "' || true)
if [[ -n "$PUBLIC_BASE" ]]; then
  PUBLIC_BASE="https://${PUBLIC_BASE#https://}"
  PUBLIC_BASE="${PUBLIC_BASE#http://}"
  PUBLIC_BASE="${PUBLIC_BASE%%/*}"
  PUBLIC_BASE="https://${PUBLIC_BASE}"
  if curl -sf -H "ngrok-skip-browser-warning: 1" \
    "${PUBLIC_BASE}/api/health" >/dev/null 2>&1; then
    NGROK_OK=true
  fi
fi

if $LOCAL_OK && $NGROK_OK; then
  ./scripts/show-url.sh
  exit 0
fi

echo "Service down (local=$LOCAL_OK ngrok=$NGROK_OK) — restarting..."
./scripts/restart.sh
