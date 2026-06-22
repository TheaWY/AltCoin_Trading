#!/bin/bash
# Restart app + ngrok tunnel with correct PATH for Homebrew.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="${HOME:-$(eval echo ~$(whoami))}"

lsof -ti :8000 | xargs kill -9 2>/dev/null || true
pkill -f "ngrok http" 2>/dev/null || true
sleep 1

mkdir -p logs data
nohup .venv/bin/python main.py >> logs/trading.stdout.log 2>> logs/trading.stderr.log &
echo "Starting server (pid $!)..."

for i in $(seq 1 30); do
  sleep 1
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    break
  fi
done

sleep 3
URL=$(cat data/ngrok.url 2>/dev/null || true)
HEALTH=$(curl -sf http://127.0.0.1:8000/api/health 2>/dev/null || echo '{}')
NGROK=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ngrok_url') or '')" 2>/dev/null || true)

echo ""
echo "Local:  http://localhost:8000/dashboard"
if [[ -n "$NGROK" ]]; then
  echo "Phone:  ${NGROK}/dashboard"
elif [[ -n "$URL" ]]; then
  echo "Phone:  $URL"
else
  echo "Ngrok:  not running — check logs/trading.stdout.log for errors"
  tail -5 logs/trading.stderr.log 2>/dev/null || true
fi
