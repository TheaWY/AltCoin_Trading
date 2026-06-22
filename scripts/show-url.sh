#!/bin/bash
# Print the current phone dashboard URL (from live API).
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")/.."

HEALTH=$(curl -sf http://127.0.0.1:8000/api/health 2>/dev/null || echo '{}')
python3 - <<'PY' "$HEALTH"
import json, sys
d = json.loads(sys.argv[1])
url = d.get("ngrok_url")
if url:
    print(f"Phone dashboard: {url}/dashboard")
    print(f"Status: {d.get('status')} (app pid {d.get('pid')})")
else:
    print("Ngrok URL not set. Run: ./scripts/restart.sh")
    print(f"Local only: http://localhost:8000/dashboard")
PY
