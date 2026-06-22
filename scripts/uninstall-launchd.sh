#!/bin/bash
# Stop and remove launchd services.
set -euo pipefail

LABEL_APP="com.altcoin.trading"
LABEL_HEALTH="com.altcoin.trading.health"
AGENTS_DIR="$HOME/Library/LaunchAgents"
UID_GUI="gui/$(id -u)"

launchctl bootout "$UID_GUI/${LABEL_APP}" 2>/dev/null || true
launchctl bootout "$UID_GUI/${LABEL_HEALTH}" 2>/dev/null || true
launchctl bootout "$UID_GUI/com.altcoin.trading.gitsync" 2>/dev/null || true

rm -f "$AGENTS_DIR/${LABEL_APP}.plist"
rm -f "$AGENTS_DIR/${LABEL_HEALTH}.plist"
rm -f "$AGENTS_DIR/com.altcoin.trading.gitsync.plist"

echo "LaunchAgents removed."
