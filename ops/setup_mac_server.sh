#!/bin/bash
# Configure this checkout as the Mac-hosted trading/dashboard server.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO/.env"

cd "$REPO"
mkdir -p "$REPO/data/logs"
touch "$ENV_FILE"

"$REPO/.venv/bin/python" - <<'PY'
from pathlib import Path

env_path = Path(".env")
updates = {
    "DATABASE_URL": "",
    "DATABASE_PATH": "data/trading.db",
    "SYMBOL_UNIVERSE": "static",
    "ACTIVE_TRADING_SYMBOLS_LIMIT": "20",
    "RUN_TRADING_SCHEDULER": "true",
}

lines = env_path.read_text().splitlines() if env_path.exists() else []
seen = set()
out = []
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        out.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f'{key}="{updates[key]}"')
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}="{value}"')
env_path.write_text("\n".join(out).rstrip() + "\n")
PY

bash "$REPO/ops/install.sh"

if ! command -v tailscale >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install --cask tailscale || true
  else
    echo "brew not found; install Tailscale manually from https://tailscale.com/download/mac"
  fi
fi

if ! pgrep -x Tailscale >/dev/null 2>&1; then
  open -a Tailscale 2>/dev/null || true
fi

echo
echo "Tailscale login step:"
echo "1. Open the Tailscale app from the menu bar."
echo "2. Sign in with the same account as your phone."
echo "3. Or run: tailscale up"
echo
if command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale IP:"
  tailscale ip -4 2>/dev/null || echo "(not logged in yet)"
else
  echo "Tailscale CLI is not on PATH yet. After login, check the Mac IP in the Tailscale app."
fi
