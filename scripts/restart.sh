#!/bin/bash
# Restart app + ngrok tunnel with correct PATH for Homebrew.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="${HOME:-$(eval echo ~$(whoami))}"

LOCK_DIR="${TMPDIR:-/tmp}/altcoin-trading-restart.lock.d"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another restart is in progress — wait and run ./scripts/show-url.sh"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if command -v lsof >/dev/null 2>&1; then
  lsof -ti :8000 | xargs kill -9 2>/dev/null || true
fi
pkill -f "\.venv/bin/python main.py" 2>/dev/null || true
pkill -f "Python main.py" 2>/dev/null || true
pkill -f "ngrok http.*8000" 2>/dev/null || true
sleep 1

mkdir -p logs data
# Fully detach from the launching shell (macOS has no setsid by default).
nohup bash -c 'cd "$0" && exec .venv/bin/python main.py' "$(pwd)" \
  >> logs/trading.stdout.log 2>> logs/trading.stderr.log </dev/null >/dev/null 2>&1 &
SERVER_PID=$!
disown "$SERVER_PID" 2>/dev/null || true
echo "Starting server (pid $SERVER_PID)..."

for i in $(seq 1 45); do
  sleep 1
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    break
  fi
done

for i in $(seq 1 25); do
  sleep 1
  HEALTH=$(curl -sf http://127.0.0.1:8000/api/health 2>/dev/null || echo '{}')
  NGROK_LIVE=$(echo "$HEALTH" | python3 -c "import sys,json; print('yes' if json.load(sys.stdin).get('ngrok_live') else 'no')" 2>/dev/null || echo "no")
  if [[ "$NGROK_LIVE" == "yes" ]]; then
    break
  fi
  CODE=$(curl -sf -o /dev/null -w "%{http_code}" -H "ngrok-skip-browser-warning: 1" \
    "$(grep -E '^NGROK_STATIC_DOMAIN=' .env 2>/dev/null | cut -d= -f2- | tr -d ' \"' | awk '{print "https://" $0}')/api/health" 2>/dev/null || echo "000")
  if [[ "$CODE" == "200" ]]; then
    break
  fi
done

URL=$(cat data/ngrok.url 2>/dev/null || true)
HEALTH=$(curl -sf http://127.0.0.1:8000/api/health 2>/dev/null || echo '{}')
NGROK=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ngrok_url') or '')" 2>/dev/null || true)

echo ""
echo "Local:  http://localhost:8000/dashboard"
if [[ -n "$NGROK" ]]; then
  echo "Phone:  ${NGROK}/dashboard"
  NGROK_LIVE=$(echo "$HEALTH" | python3 -c "import sys,json; print('yes' if json.load(sys.stdin).get('ngrok_live') else 'no')" 2>/dev/null || echo "no")
  PUB=$(curl -sf -o /dev/null -w "%{http_code}" -H "ngrok-skip-browser-warning: 1" "${NGROK}/api/health" 2>/dev/null || echo "000")
  if [[ "$NGROK_LIVE" == "yes" || "$PUB" == "200" ]]; then
    echo "Tunnel: OK"
  else
    echo "Warning: tunnel not confirmed (public HTTP $PUB) — run ./scripts/show-url.sh in ~10s"
    tail -5 logs/ngrok.log 2>/dev/null || true
  fi
elif [[ -n "$URL" ]]; then
  echo "Phone:  $URL"
else
  echo "Ngrok:  not running — check logs/trading.stdout.log"
fi

if ! curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo "ERROR: API is not running — see logs/trading.stderr.log"
  exit 1
fi
