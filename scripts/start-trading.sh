#!/bin/bash
# Wrapper for launchd — login shell avoids Desktop folder permission issues.
set -euo pipefail
cd "$(dirname "$0")/.."
export HOME="${HOME:-/Users/$(whoami)}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
exec .venv/bin/python main.py
