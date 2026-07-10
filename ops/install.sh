#!/bin/bash
# One-time setup on the Mac Mini. Run from repo root: bash ops/install.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/data/logs" "$HOME/Library/LaunchAgents"
if sudo -n true 2>/dev/null; then
  sudo pmset -a sleep 0 displaysleep 10
else
  echo "skip: pmset sleep change requires sudo password"
fi
SERVICES=(com.altcoin.worker com.altcoin.research com.altcoin.watchdog com.altcoin.dashboard com.altcoin.category)
if [ "${INSTALL_TICKBARS:-false}" = "true" ]; then
  SERVICES+=(com.altcoin.tickbars)
fi
for P in "${SERVICES[@]}"; do
  sed "s|__REPO_PATH__|$REPO|g" "$REPO/ops/$P.plist" > "$HOME/Library/LaunchAgents/$P.plist"
  launchctl unload "$HOME/Library/LaunchAgents/$P.plist" 2>/dev/null || true
  launchctl load "$HOME/Library/LaunchAgents/$P.plist"
done
echo "installed: ${SERVICES[*]}"
echo "dashboard binds to 127.0.0.1:8000; use: bash ops/serve_tailscale.sh"
echo "check: launchctl list | grep altcoin && python scripts/macmini_status.py"
