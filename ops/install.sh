#!/bin/bash
# One-time setup on the Mac Mini. Run from repo root: bash ops/install.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/data/logs"

if [[ -x "$REPO/.venv/bin/python" ]]; then
  PYTHON="$REPO/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.12)"
else
  PYTHON="$(command -v python3)"
fi

echo "Using Python: $PYTHON"
for P in com.altcoin.worker com.altcoin.research com.altcoin.watchdog; do
  sed -e "s|__REPO_PATH__|$REPO|g" -e "s|__PYTHON_BIN__|$PYTHON|g" \
    "$REPO/ops/$P.plist" > "$HOME/Library/LaunchAgents/$P.plist"
  launchctl unload "$HOME/Library/LaunchAgents/$P.plist" 2>/dev/null || true
  launchctl load "$HOME/Library/LaunchAgents/$P.plist"
done
if sudo -n pmset -a sleep 0 displaysleep 10 2>/dev/null; then
  echo "pmset: sleep disabled for 24/7 worker."
else
  echo "pmset: skipped (run manually with sudo if the Mac must stay awake):"
  echo "  sudo pmset -a sleep 0 displaysleep 10"
fi
# Retire legacy plists that pointed at Desktop/AltCoin_Trading.
for OLD in com.altcoin.trading com.altcoin.trading.health com.altcoin.trading.gitsync; do
  if [[ -f "$HOME/Library/LaunchAgents/$OLD.plist" ]]; then
    launchctl unload "$HOME/Library/LaunchAgents/$OLD.plist" 2>/dev/null || true
    echo "unloaded legacy $OLD (use com.altcoin.worker instead)"
  fi
done
echo "installed: worker(KeepAlive) + research(01:00) + watchdog(60s). sleep disabled."
echo "check: launchctl list | grep altcoin"
echo "logs:  tail -f $REPO/data/logs/worker.out.log"
