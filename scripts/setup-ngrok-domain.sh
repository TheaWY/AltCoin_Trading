#!/bin/bash
# One-time setup: claim your free static ngrok domain and save it to .env
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=".env"

echo "=== ngrok static domain setup ==="
echo ""
echo "1. Open: https://dashboard.ngrok.com/domains"
echo "2. Claim your FREE static domain (e.g. my-trading.ngrok-free.app)"
echo "   — every account gets one permanent dev domain"
echo ""

if command -v open >/dev/null 2>&1; then
  read -r -p "Open dashboard in browser now? [Y/n] " open_dash
  if [[ "${open_dash:-Y}" =~ ^[Yy]?$ ]]; then
    open "https://dashboard.ngrok.com/domains"
  fi
fi

echo ""
read -r -p "Paste your static domain (hostname only): " DOMAIN
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%%/*}"
DOMAIN="${DOMAIN%%/dashboard}"

if [[ -z "$DOMAIN" ]]; then
  echo "No domain entered — aborting"
  exit 1
fi

touch "$ENV_FILE"
if grep -q "^NGROK_STATIC_DOMAIN=" "$ENV_FILE"; then
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|^NGROK_STATIC_DOMAIN=.*|NGROK_STATIC_DOMAIN=$DOMAIN|" "$ENV_FILE"
  else
    sed -i "s|^NGROK_STATIC_DOMAIN=.*|NGROK_STATIC_DOMAIN=$DOMAIN|" "$ENV_FILE"
  fi
else
  printf '\n# Permanent phone URL (same after every restart)\nNGROK_STATIC_DOMAIN=%s\n' "$DOMAIN" >> "$ENV_FILE"
fi

grep -q "^NGROK_ENABLED=true" "$ENV_FILE" || echo "NGROK_ENABLED=true" >> "$ENV_FILE"

echo ""
echo "Saved NGROK_STATIC_DOMAIN=$DOMAIN"
echo "Permanent URL: https://$DOMAIN/dashboard"
echo ""
read -r -p "Restart app now? [Y/n] " restart
if [[ "${restart:-Y}" =~ ^[Yy]?$ ]]; then
  ./scripts/restart.sh
fi
