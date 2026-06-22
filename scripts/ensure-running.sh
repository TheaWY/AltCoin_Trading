#!/bin/bash
# Start app + ngrok if either is down. Safe to run from cron or manually.
set -euo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_DIR"

LOCAL_HEALTH_URL="http://127.0.0.1:${API_PORT}/api/health"

LOCAL_OK=false
if curl -sf "$LOCAL_HEALTH_URL" >/dev/null 2>&1; then
  LOCAL_OK=true
fi

NGROK_OK=false
if pgrep -f "ngrok http.*${API_PORT}" >/dev/null 2>&1; then
  TUNNEL_URL="$(ngrok_tunnel_url || true)"
  if [[ -n "$TUNNEL_URL" ]] && $LOCAL_OK; then
    NGROK_OK=true
  fi
fi

if $LOCAL_OK && $NGROK_OK; then
  ./scripts/show-url.sh
  exit 0
fi

if $LOCAL_OK && ! $NGROK_OK; then
  echo "App is up but ngrok tunnel is down — restarting tunnel only..."
  "$PYTHON_BIN" -c "
from src.tunnel import set_api_ready, start_ngrok
from src.health import get_health
from src import config
set_api_ready(True)
url = start_ngrok(config.API_PORT)
get_health().mark_ngrok(url)
print(f'Tunnel: {url}/dashboard')
"
  exit 0
fi

echo "Service down (local=$LOCAL_OK ngrok=$NGROK_OK) — full restart..."
./scripts/restart.sh
