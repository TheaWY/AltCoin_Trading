#!/bin/bash
# Shared runtime helpers for ops scripts.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin:${PATH:-}"
export HOME="${HOME:-$(eval echo ~$(whoami))}"

env_value() {
  local key="$1"
  local file="$PROJECT_DIR/.env"
  local line value

  [[ -f "$file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ "$line" == "$key="* ]] || continue
    value="${line#*=}"
    value="${value%%#*}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    printf '%s\n' "$value"
    return 0
  done < "$file"
  return 1
}

resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" && -x "$PYTHON_BIN" ]]; then
    printf '%s\n' "$PYTHON_BIN"
  elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    printf '%s\n' "$PROJECT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    echo "No Python interpreter found. Create .venv or install python3." >&2
    return 1
  fi
}

normalize_ngrok_domain() {
  local domain="$1"
  domain="${domain#https://}"
  domain="${domain#http://}"
  domain="${domain%%/*}"
  printf '%s\n' "$domain"
}

unique_ngrok_web_ports() {
  local first="${NGROK_WEB_PORT:-4042}"
  printf '%s\n' "$first"
  for port in $(seq 4040 4054); do
    [[ "$port" == "$first" ]] || printf '%s\n' "$port"
  done
}

ngrok_tunnel_url() {
  local web_port
  for web_port in $(unique_ngrok_web_ports); do
    curl -sf "http://127.0.0.1:${web_port}/api/tunnels" 2>/dev/null | "$PYTHON_BIN" -c "
import json, sys
try:
    data = json.load(sys.stdin)
    tunnels = data.get('tunnels', [])
    for tunnel in tunnels:
        if tunnel.get('proto') == 'https' and tunnel.get('public_url'):
            print(tunnel['public_url'].rstrip('/'))
            raise SystemExit(0)
    for tunnel in tunnels:
        if tunnel.get('public_url'):
            print(tunnel['public_url'].rstrip('/'))
            raise SystemExit(0)
except Exception:
    pass
raise SystemExit(1)
" && return 0
  done
  return 1
}

API_PORT="${API_PORT:-$(env_value API_PORT || true)}"
API_PORT="${API_PORT:-8000}"
NGROK_WEB_PORT="${NGROK_WEB_PORT:-$(env_value NGROK_WEB_PORT || true)}"
NGROK_WEB_PORT="${NGROK_WEB_PORT:-4042}"
NGROK_STATIC_DOMAIN="${NGROK_STATIC_DOMAIN:-$(env_value NGROK_STATIC_DOMAIN || true)}"
NGROK_STATIC_DOMAIN="$(normalize_ngrok_domain "${NGROK_STATIC_DOMAIN:-}")"
PYTHON_BIN="$(resolve_python)"
