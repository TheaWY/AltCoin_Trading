#!/bin/bash
# Privately expose the local dashboard inside your Tailscale tailnet.
# Run after the dashboard works at http://127.0.0.1:8000/dashboard
set -euo pipefail

PORT="${API_PORT:-8000}"
TARGET="localhost:${PORT}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale CLI not found. Install and sign in to Tailscale first."
  exit 1
fi

if ! tailscale status >/dev/null 2>&1; then
  echo "Tailscale is not running or not logged in. Open the Tailscale app and sign in."
  exit 1
fi

echo "Serving AltCoin dashboard privately through Tailscale: ${TARGET}"
tailscale serve --bg "${TARGET}"
echo ""
echo "Tailscale Serve status:"
tailscale serve status || true
echo ""
echo "Open the HTTPS URL shown above from a device signed into your tailnet."
echo "Dashboard path: /dashboard"
