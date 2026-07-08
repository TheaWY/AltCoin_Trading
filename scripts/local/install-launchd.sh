#!/bin/bash
# Install and start launchd services for 24/7 operation.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL_APP="com.altcoin.trading"
LABEL_HEALTH="com.altcoin.trading.health"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOGS_DIR="$PROJECT_DIR/logs"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Virtualenv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ "$PROJECT_DIR" == "$HOME/Desktop/"* ]]; then
  echo "⚠️  Project is on Desktop — macOS blocks launchd from accessing Desktop files."
  echo "   Choose one fix before relying on 24/7 auto-start:"
  echo "   1. Move project:  mv '$PROJECT_DIR' '$HOME/AltCoin_Trading' && cd '$HOME/AltCoin_Trading' && ./scripts/local/install-launchd.sh"
  echo "   2. Grant Full Disk Access to /bin/bash in System Settings → Privacy & Security"
  echo ""
fi

mkdir -p "$LOGS_DIR" "$AGENTS_DIR" "$PROJECT_DIR/data"

render_plist() {
  local template="$1"
  local dest="$2"
  sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__HOME__|$HOME|g" "$template" > "$dest"
}

echo "Installing LaunchAgents..."

render_plist "$PROJECT_DIR/scripts/local/launchd/com.altcoin.trading.plist.template" \
  "$AGENTS_DIR/${LABEL_APP}.plist"

render_plist "$PROJECT_DIR/scripts/local/launchd/com.altcoin.trading.health.plist.template" \
  "$AGENTS_DIR/${LABEL_HEALTH}.plist"

chmod +x "$PROJECT_DIR/scripts/health_check.py"
chmod +x "$PROJECT_DIR/scripts/local/start-trading.sh"

# Unload if already loaded (ignore errors)
launchctl bootout "gui/$(id -u)/${LABEL_APP}" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/${LABEL_HEALTH}" 2>/dev/null || true

# Load services
launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/${LABEL_APP}.plist"
launchctl bootstrap "gui/$(id -u)" "$AGENTS_DIR/${LABEL_HEALTH}.plist"

launchctl enable "gui/$(id -u)/${LABEL_APP}"
launchctl enable "gui/$(id -u)/${LABEL_HEALTH}"

launchctl kickstart -k "gui/$(id -u)/${LABEL_APP}"
launchctl kickstart -k "gui/$(id -u)/${LABEL_HEALTH}"

# GitHub auto-sync (optional — commits & pushes every 3 min)
if [[ -x "$PROJECT_DIR/scripts/local/install-gitsync.sh" ]]; then
  "$PROJECT_DIR/scripts/local/install-gitsync.sh" || echo "⚠️  Git sync install failed (see logs/git-sync.log)"
fi

echo ""
echo "Services started:"
echo "  App:    $LABEL_APP"
echo "  Health: $LABEL_HEALTH (probe every 3 min)"
echo ""
echo "Logs:"
echo "  $LOGS_DIR/trading.stdout.log"
echo "  $LOGS_DIR/trading.stderr.log"
echo "  $LOGS_DIR/health.stdout.log"
echo ""
echo "Commands:"
echo "  launchctl print gui/$(id -u)/${LABEL_APP}"
echo "  launchctl kickstart -k gui/$(id -u)/${LABEL_APP}"
echo "  $VENV_PYTHON $PROJECT_DIR/scripts/health_check.py"
