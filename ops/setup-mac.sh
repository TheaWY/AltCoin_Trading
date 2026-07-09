#!/bin/bash
# Bootstrap the Mac Mini: Python venv, dependencies, data dirs.
# Run from repo root: bash ops/setup-mac.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

echo "=== AltCoin Trading — Mac Mini setup ==="

# Prefer Homebrew Python 3.12+ (repo uses PEP 604 type syntax).
PYTHON=""
for candidate in \
  "$REPO/.venv/bin/python" \
  /opt/homebrew/opt/python@3.12/bin/python3.12 \
  /opt/homebrew/bin/python3.12 \
  "$(command -v python3.12 2>/dev/null || true)" \
  "$(command -v python3 2>/dev/null || true)"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    ver="$("$candidate" -c 'import sys; print(sys.version_info[:2])')"
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "Python 3.10+ required. Install with: brew install python@3.12" >&2
  exit 1
fi

echo "Using Python: $PYTHON ($("$PYTHON" --version))"

if [[ ! -x "$REPO/.venv/bin/python" ]]; then
  echo "Creating virtualenv..."
  "$PYTHON" -m venv "$REPO/.venv"
fi

echo "Installing dependencies..."
"$REPO/.venv/bin/pip" install -q --upgrade pip
"$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"

mkdir -p "$REPO/data/logs"

if [[ ! -f "$REPO/.env" ]]; then
  if [[ -f "$REPO/.env.mac.example" ]]; then
    cp "$REPO/.env.mac.example" "$REPO/.env"
    echo "Created .env from .env.mac.example — edit Binance keys and DATABASE_URL."
  else
    echo "No .env found. Copy .env.mac.example to .env and fill in secrets."
  fi
fi

echo ""
echo "Running verification tests..."
"$REPO/.venv/bin/python" "$REPO/scripts/test_promotion.py"
"$REPO/.venv/bin/python" "$REPO/scripts/test_research_stack.py"

echo ""
echo "Next steps:"
echo "  1. Edit .env — BINANCE_API_KEY, BINANCE_API_SECRET, DATABASE_URL (Railway Postgres)"
echo "  2. bash ops/install.sh     # launchd: worker + research + watchdog"
echo "  3. bash ops/cloudflare-tunnel.sh   # optional phone access to local dashboard"
echo "  4. Railway dashboard: set RUN_TRADING_SCHEDULER=false (see docs/DEPLOYMENT.md)"
