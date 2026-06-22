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
if pgrep -f "ngrok http.*8000" >/dev/null 2>&1; then
  TUNNEL_URL=$(curl -sf http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for t in data.get('tunnels', []):
        if t.get('proto') == 'https' and t.get('public_url'):
            print(t['public_url'].rstrip('/'))
            break
except Exception:
    pass
" 2>/dev/null || true)
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
  .venv/bin/python -c "
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
