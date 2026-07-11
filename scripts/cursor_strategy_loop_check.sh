#!/bin/bash
# Cursor/Mac-mini pre-commit verification for strategy-loop work.
# This is intentionally a local check: it verifies local Postgres/dashboard state
# without touching live trading or external order endpoints.
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi

echo "=== 1) safety unit tests ==="
bash scripts/run_safety_tests.sh

echo "=== 2) Mac mini status ==="
python scripts/macmini_status.py

echo "=== 3) dashboard API smoke test ==="
python - <<'PY'
import json
import urllib.request

url = "http://127.0.0.1:8000/api/alts"
data = json.loads(urllib.request.urlopen(url, timeout=90).read())
portfolio = data.get("portfolio") or {}
evaluation = data.get("evaluation") or []

print("symbols_tracked:", data.get("symbols_tracked"))
print("evaluation_count:", len(evaluation))
print("open_trades:", portfolio.get("open_trades"))
print("paper_value:", portfolio.get("paper_value"))
print("total_unrealized_pnl:", portfolio.get("total_unrealized_pnl"))
print("open_positions_count:", len(portfolio.get("open_positions") or []))

if not isinstance(evaluation, list) or not evaluation:
    raise SystemExit("FAIL: /api/alts returned no evaluation rows")
if "paper_value" not in portfolio:
    raise SystemExit("FAIL: /api/alts portfolio missing paper_value")

for pos in portfolio.get("open_positions") or []:
    required = ["symbol", "direction", "entry_price", "current_price", "unrealized_pnl", "unrealized_pnl_pct"]
    missing = [k for k in required if k not in pos]
    if missing:
        raise SystemExit(f"FAIL: open position missing {missing}: {pos}")

for sym in ["T/USDT", "SKL/USDT"]:
    found = next((e for e in evaluation if e.get("symbol") == sym), None)
    if found:
        print("\n==", sym)
        print(json.dumps({
            "tradable": found.get("tradable"),
            "confidence": found.get("confidence"),
            "verdict": found.get("verdict"),
            "policy_blocked_setups": found.get("policy_blocked_setups"),
            "why_not": found.get("why_not"),
            "metrics": {
                "pct_24h": (found.get("metrics") or {}).get("pct_24h"),
                "pct_7d": (found.get("metrics") or {}).get("pct_7d"),
                "rsi_14": (found.get("metrics") or {}).get("rsi_14"),
                "funding_rate_pct": (found.get("metrics") or {}).get("funding_rate_pct"),
            },
        }, ensure_ascii=False, indent=2))
    else:
        print("\n==", sym, "not in current dashboard universe")
PY

echo "=== 4) research dry-run ==="
python -m src.research.generator --dry-run

echo "=== strategy-loop check passed ==="
