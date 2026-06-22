#!/bin/bash
# Install auto GitHub sync: post-commit push hook + launchd every 3 min.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.altcoin.trading.gitsync"
AGENTS_DIR="$HOME/Library/LaunchAgents"
HOOKS_DIR="$PROJECT_DIR/.git/hooks"

chmod +x "$PROJECT_DIR/scripts/git-sync.sh"
chmod +x "$PROJECT_DIR/scripts/hooks/post-commit"

mkdir -p "$HOOKS_DIR" "$PROJECT_DIR/logs"
cp "$PROJECT_DIR/scripts/hooks/post-commit" "$HOOKS_DIR/post-commit"
chmod +x "$HOOKS_DIR/post-commit"

sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__HOME__|$HOME|g" \
  "$PROJECT_DIR/launchd/com.altcoin.trading.gitsync.plist.template" \
  > "$AGENTS_DIR/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/${LABEL}.plist"
launchctl enable "gui/$(id -u)/${LABEL}"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo "GitHub auto-sync installed:"
echo "  • Post-commit hook → pushes after every commit"
echo "  • LaunchAgent ${LABEL} → commits & pushes every 3 min if files changed"
echo "  • Log: $PROJECT_DIR/logs/git-sync.log"
echo ""
echo "Run manual sync now: $PROJECT_DIR/scripts/git-sync.sh"

# First sync
"$PROJECT_DIR/scripts/git-sync.sh" || true
tail -3 "$PROJECT_DIR/logs/git-sync.log" 2>/dev/null || true
