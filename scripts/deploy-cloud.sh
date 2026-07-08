#!/bin/bash
# Deploy AltCoin Trading to the cloud for 24/7 uptime (Mac can sleep).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== 24/7 Cloud Deploy (Railway) ==="
echo ""
echo "Your Mac cannot keep the app running while asleep."
echo "This deploys to Railway (~\$5/mo) — always on, public URL, no ngrok."
echo ""

if ! command -v railway >/dev/null 2>&1; then
  echo "Install Railway CLI:"
  echo "  brew install railway"
  echo "  railway login"
  echo ""
  echo "Or deploy via browser:"
  echo "  1. https://railway.com/new → Deploy from GitHub"
  echo "  2. Select repo: AltCoin_Trading (branch cursor/init-foundation)"
  echo "  3. Add a Volume mounted at /data"
  echo "  4. Set environment variables (see below)"
  echo ""
else
  read -r -p "Deploy with Railway CLI now? [Y/n] " go
  if [[ "${go:-Y}" =~ ^[Yy]?$ ]]; then
    railway link 2>/dev/null || railway init
    echo "Creating volume (skip if already exists)..."
    railway volume add --mount-path /data 2>/dev/null || true
    railway up --detach
    echo ""
    echo "Set secrets in Railway dashboard → Variables:"
  fi
fi

cat <<'VARS'

Required environment variables (Railway → Service → Variables):

  BINANCE_API_KEY=your_testnet_key
  BINANCE_API_SECRET=your_testnet_secret
  BINANCE_TESTNET=true
  DEPLOYMENT_MODE=cloud
  DATA_DIR=/data
  DATABASE_PATH=/data/trading.db
  NGROK_ENABLED=false
  RUN_TRADING_SCHEDULER=false

Railway public networking:

  Target port must match the web server log. For Railway this is usually 8080.
  If the app logs "Starting web server on 0.0.0.0:8080" but the public URL
  returns 502 with x-railway-fallback=true, open Railway → Service → Networking
  → Public Networking → edit the domain → set Target Port to 8080.

Optional:
  RUN_TRADING_SCHEDULER=true   # only on a separate worker/service
  TRADING_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT,...
  PAPER_STARTING_CAPITAL=10000

After deploy, open:
  https://YOUR-APP.up.railway.app/dashboard

Pin that URL in Slack — works on your phone 24/7.

VARS

if command -v railway >/dev/null 2>&1; then
  DOMAIN=$(railway domain 2>/dev/null | head -1 || true)
  if [[ -n "$DOMAIN" ]]; then
    echo "Your dashboard: https://${DOMAIN}/dashboard"
  fi
fi
