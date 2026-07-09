#!/bin/bash
# Print Railway variable checklist for dashboard-only deploy (Option B).
# Requires: railway login && railway link (from repo root).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Railway dashboard-only checklist ==="
echo ""
echo "Required variables on the WEB service:"
echo "  DEPLOYMENT_MODE=cloud"
echo "  NGROK_ENABLED=false"
echo "  RUN_TRADING_SCHEDULER=false"
echo "  DATABASE_URL=<from Postgres plugin>"
echo ""
echo "Do NOT set BINANCE_API_KEY on Railway (HTTP 451 from US IPs)."
echo "Mac Mini worker uses .env.mac.example → .env with same DATABASE_URL."
echo ""

if command -v railway >/dev/null 2>&1 && railway whoami >/dev/null 2>&1; then
  echo "Linked project:"
  railway status 2>/dev/null || true
  echo ""
  echo "To set variables (example):"
  echo "  railway variables set DEPLOYMENT_MODE=cloud RUN_TRADING_SCHEDULER=false NGROK_ENABLED=false"
else
  echo "Railway CLI not logged in. Run: railway login && railway link"
fi
