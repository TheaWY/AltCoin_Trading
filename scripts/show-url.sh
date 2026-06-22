#!/bin/bash
# Print the current phone dashboard URL (from live API).
set -euo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_DIR"

HEALTH=$(curl -sf "http://127.0.0.1:${API_PORT}/api/health" 2>/dev/null || echo '{}')
"$PYTHON_BIN" - <<'PY' "$HEALTH" "$API_PORT"
import json, sys
d = json.loads(sys.argv[1])
port = sys.argv[2]
url = d.get("ngrok_url")
if url:
    print(f"Phone dashboard: {url}/dashboard")
    print(f"Status: {d.get('status')} (app pid {d.get('pid')})")
else:
    print("Ngrok URL not set. Run: ./scripts/restart.sh")
    print(f"Local only: http://localhost:{port}/dashboard")
PY
