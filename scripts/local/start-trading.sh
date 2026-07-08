#!/bin/bash
# Wrapper for launchd — login shell avoids Desktop folder permission issues.
set -euo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_DIR"
exec "$PYTHON_BIN" main.py
