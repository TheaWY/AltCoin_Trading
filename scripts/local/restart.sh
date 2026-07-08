#!/bin/bash
# Restart app + ngrok tunnel.
set -euo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_DIR"

LOCAL_HEALTH_URL="http://127.0.0.1:${API_PORT}/api/health"
STATIC_HEALTH_URL=""
if [[ -n "$NGROK_STATIC_DOMAIN" ]]; then
  STATIC_HEALTH_URL="https://${NGROK_STATIC_DOMAIN}/api/health"
fi

LOCK_DIR="${TMPDIR:-/tmp}/altcoin-trading-restart.lock.d"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another restart is in progress — wait and run ./scripts/local/show-url.sh"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if command -v lsof >/dev/null 2>&1; then
  lsof -ti :"$API_PORT" | xargs kill -9 2>/dev/null || true
fi
pkill -f "python.*[ /]main.py" 2>/dev/null || true
pkill -f "Python.*[ /]main.py" 2>/dev/null || true
pkill -f "ngrok http.*${API_PORT}" 2>/dev/null || true
sleep 1

mkdir -p logs data
if command -v setsid >/dev/null 2>&1; then
  setsid "$PYTHON_BIN" main.py >> logs/trading.stdout.log 2>> logs/trading.stderr.log </dev/null &
else
  nohup "$PYTHON_BIN" main.py >> logs/trading.stdout.log 2>> logs/trading.stderr.log </dev/null &
  disown -h "$!" 2>/dev/null || true
fi
echo "Starting server (pid $!)..."

for i in $(seq 1 45); do
  sleep 1
  if curl -sf "$LOCAL_HEALTH_URL" >/dev/null 2>&1; then
    break
  fi
done

for i in $(seq 1 25); do
  sleep 1
  HEALTH=$(curl -sf "$LOCAL_HEALTH_URL" 2>/dev/null || echo '{}')
  NGROK_LIVE=$(echo "$HEALTH" | "$PYTHON_BIN" -c "import sys,json; print('yes' if json.load(sys.stdin).get('ngrok_live') else 'no')" 2>/dev/null || echo "no")
  if [[ "$NGROK_LIVE" == "yes" ]]; then
    break
  fi
  if [[ -n "$STATIC_HEALTH_URL" ]]; then
    CODE=$(curl -sf -o /dev/null -w "%{http_code}" -H "ngrok-skip-browser-warning: 1" \
      "$STATIC_HEALTH_URL" 2>/dev/null || echo "000")
    if [[ "$CODE" == "200" ]]; then
      break
    fi
  fi
done

URL=""
[[ -f data/ngrok.url ]] && URL="$(<data/ngrok.url)"
HEALTH=$(curl -sf "$LOCAL_HEALTH_URL" 2>/dev/null || echo '{}')
NGROK=$(echo "$HEALTH" | "$PYTHON_BIN" -c "import sys,json; print(json.load(sys.stdin).get('ngrok_url') or '')" 2>/dev/null || true)

echo ""
echo "Local:  http://localhost:${API_PORT}/dashboard"
if [[ -n "$NGROK" ]]; then
  echo "Phone:  ${NGROK}/dashboard"
  NGROK_LIVE=$(echo "$HEALTH" | "$PYTHON_BIN" -c "import sys,json; print('yes' if json.load(sys.stdin).get('ngrok_live') else 'no')" 2>/dev/null || echo "no")
  PUB=$(curl -sf -o /dev/null -w "%{http_code}" -H "ngrok-skip-browser-warning: 1" "${NGROK}/api/health" 2>/dev/null || echo "000")
  if [[ "$NGROK_LIVE" == "yes" || "$PUB" == "200" ]]; then
    echo "Tunnel: OK"
  else
    echo "Warning: tunnel not confirmed (public HTTP $PUB) — run ./scripts/local/show-url.sh in ~10s"
    tail -5 logs/ngrok.log 2>/dev/null || true
  fi
elif [[ -n "$URL" ]]; then
  echo "Phone:  $URL"
else
  echo "Ngrok:  not running — check logs/trading.stdout.log"
fi

if ! curl -sf "$LOCAL_HEALTH_URL" >/dev/null 2>&1; then
  echo "ERROR: API is not running — see logs/trading.stderr.log"
  exit 1
fi
