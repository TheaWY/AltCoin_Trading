#!/bin/bash
# Railway/web entrypoint: bind the HTTP server to the platform-provided port.
set -euo pipefail

cd "$(dirname "$0")/.."

WEB_HOST="${API_HOST:-0.0.0.0}"
WEB_PORT="${PORT:-${API_PORT:-8000}}"
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

echo "Starting web server on ${WEB_HOST}:${WEB_PORT} with ${PYTHON}"
exec "$PYTHON" -m uvicorn src.api.main:app --host "$WEB_HOST" --port "$WEB_PORT"
