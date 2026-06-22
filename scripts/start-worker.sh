#!/bin/bash
# Background trading worker entrypoint. Do not use this for the public web service.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON_BIN:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
  else
    echo "No Python interpreter found on PATH" >&2
    exit 1
  fi
fi

export RUN_TRADING_SCHEDULER=true
export NGROK_ENABLED="${NGROK_ENABLED:-false}"

echo "Starting trading worker with ${PYTHON}"
exec "$PYTHON" -m src.worker
