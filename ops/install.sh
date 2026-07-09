#!/bin/bash
# One-time setup on the Mac Mini. Run from repo root: bash ops/install.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/data/logs"
sudo pmset -a sleep 0 displaysleep 10
for P in com.altcoin.worker com.altcoin.research com.altcoin.watchdog; do
  sed "s|__REPO_PATH__|$REPO|g" "$REPO/ops/$P.plist" > "$HOME/Library/LaunchAgents/$P.plist"
  launchctl unload "$HOME/Library/LaunchAgents/$P.plist" 2>/dev/null || true
  launchctl load "$HOME/Library/LaunchAgents/$P.plist"
done
echo "installed: worker(KeepAlive) + research(01:00) + watchdog(60s). sleep disabled."
echo "check: launchctl list | grep altcoin"
