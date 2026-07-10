#!/bin/bash
# Full Mac mini server installer: launchd services + optional Tailscale Serve.
# Run from repo root: bash ops/install_macmini_server.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

mkdir -p data/logs "$HOME/Library/LaunchAgents"

if [ ! -f ".env" ]; then
  echo "No .env found. Creating from .env.macmini.example."
  cp .env.macmini.example .env
  echo "Edit .env and set the local DATABASE_URL before running collectors."
fi

bash ops/install.sh

if [ "${START_TAILSCALE_SERVE:-false}" = "true" ]; then
  bash ops/serve_tailscale.sh
else
  echo "Tailscale Serve not started. Run manually when dashboard works: bash ops/serve_tailscale.sh"
fi

echo ""
echo "Mac mini server install complete."
echo "Next checks:"
echo "  source .venv/bin/activate"
echo "  python scripts/macmini_status.py"
echo "  open http://127.0.0.1:8000/dashboard"
