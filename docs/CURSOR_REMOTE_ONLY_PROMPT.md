# Cursor-only prompt for Mac mini work

Use this when you cannot touch the Mac mini terminal directly and can only instruct Cursor running on the Mac mini.

Paste everything inside the block below into Cursor chat.

```text
You are Cursor running on my Mac mini. You have access to the local repo and terminal. I cannot use the Mac mini terminal directly right now, so you must run the commands yourself in Cursor's terminal.

Repo path:
/Users/pc/Projects/AltCoin_Trading

Branch:
macmini-tailscale-server

Repository:
TheaWY/AltCoin_Trading

Goal:
Fix and verify the local Mac mini autonomous paper-trading research loop. This includes dashboard UI, portfolio PnL, evaluation logic, tests, and research/backtest execution.

Hard rules:
- This is money-related trading code. Do not change strategy, PnL, risk, backtest, portfolio, or promotion logic without tests.
- Do not commit or push unless tests pass locally.
- Do not enable LIVE_TRADING.
- Do not commit .env or secrets.
- Do not use Railway. Use local Mac mini Postgres only.
- Do not expose Postgres publicly.
- Prefer small, auditable changes over big rewrites.
- Keep the system PAPER ONLY.

First, run exactly:

cd /Users/pc/Projects/AltCoin_Trading
source .venv/bin/activate
git fetch origin
git switch macmini-tailscale-server
git pull origin macmini-tailscale-server

Then inspect these files before editing:
- docs/CURSOR_MACMINI_STRATEGY_PROMPT.md
- scripts/cursor_strategy_loop_check.sh
- scripts/run_safety_tests.sh
- tests/test_evaluation_setups.py
- src/engine/evaluation.py
- src/engine/paper_trader.py
- src/api/routes/alts.py
- src/api/main.py
- src/dashboard/index.html
- research_space.yaml
- src/research/generator.py
- src/research/runner.py
- src/research/promotion.py

Run baseline checks before editing:

bash scripts/run_safety_tests.sh
bash scripts/cursor_strategy_loop_check.sh

If either fails, read the error and fix the smallest possible thing. Do not skip tests.

Context from dashboard review:

Case A: T-like
- 24h +34.54%
- 7d +19.23%
- RSI 82
- funding around -2.00%
- category volume_surge
Expected behavior:
Do NOT short this automatically. It is a possible short-term squeeze/upside case, not 장투. Directional LONG trades are allowed when they are scalp/swing trades with bounded exits; it should appear as a 단타 상승 candidate unless risk/regime filters block it.

Case B: SKL-like
- 7d +29.54%
- 24h -6.99%
- RSI 42
- SMA20 break or MACD down
- category liquid_trend
Expected behavior:
Detect as failed_pump_short, style 단타, direction SHORT, if live metrics match that pattern.

UI requirements:
- Keep the old tabbed dashboard style.
- Bottom tabs should be exactly:
  홈 / 포트폴리오 / 전략 / 히스토리 / 실험
- Do not re-add 마켓 tab.
- Do not replace the whole UI with the single Home page.
- /dashboard must render in browser as HTML, not download as a file.
- /dashboard/full should also render the tabbed dashboard.
- /dashboard/home may remain as debug-only.

Portfolio requirements:
If open positions exist, portfolio must show:
- total paper value
- total PnL vs starting capital
- cash
- open position market value
- total unrealized PnL
- open position count
- each open position current price, entry, unrealized PnL, and unrealized PnL %
Important: PnL must come from open paper_trades + latest prices, not from whether the coin card is currently visible.

Strategy/research UI requirements:
The strategy tab should show:
- active/champion config
- experiment counts queued/running/done/failed
- recent experiment results
- failed_pump_short experimental status
- why configs were not promoted
- clear distinction between current paper logic and experimental candidate logic

Research requirements:
- Experiments must actually be queued/run.
- research_space.yaml should include SETUP_FAILED_PUMP_ENABLED as an experimental axis.
- Run:
  python -m src.research.generator --dry-run
  python -m src.research.generator
  bash scripts/run_research_now.sh
- Then inspect:
  tail -80 data/logs/research.manual.log
  tail -80 data/logs/research.err.log

API verification:
Run this and inspect output:

python - <<'PY'
import json, urllib.request
url = "http://127.0.0.1:8000/api/alts"
data = json.loads(urllib.request.urlopen(url, timeout=90).read())
print("portfolio", json.dumps(data.get("portfolio", {}), ensure_ascii=False, indent=2)[:3000])
for sym in ["T/USDT", "SKL/USDT"]:
    print("\n==", sym)
    found = False
    for e in data.get("evaluation", []):
        if e.get("symbol") == sym:
            found = True
            print(json.dumps({
                "symbol": e.get("symbol"),
                "tradable": e.get("tradable"),
                "confidence": e.get("confidence"),
                "verdict": e.get("verdict"),
                "policy_blocked_setups": e.get("policy_blocked_setups"),
                "why_not": e.get("why_not"),
                "metrics": {
                    "pct_24h": e.get("metrics", {}).get("pct_24h"),
                    "pct_7d": e.get("metrics", {}).get("pct_7d"),
                    "rsi_14": e.get("metrics", {}).get("rsi_14"),
                    "funding_rate_pct": e.get("metrics", {}).get("funding_rate_pct"),
                    "btc_correlation": e.get("metrics", {}).get("btc_correlation"),
                }
            }, ensure_ascii=False, indent=2))
    if not found:
        print("not found in current dashboard universe")
PY

Dashboard verification:
Run:

python - <<'PY'
import urllib.request
for path in ["/dashboard", "/dashboard/full", "/experiments"]:
    url = "http://127.0.0.1:8000" + path
    r = urllib.request.urlopen(url, timeout=20)
    print(path, r.status, r.headers.get("content-type"), r.read(120).decode("utf-8", "ignore")[:120])
PY

Expected:
- content-type includes text/html
- HTML begins with <!DOCTYPE html> or <html
- It does not download as a file in browser

If code changes are needed:
1. Write/update tests first when touching strategy/PnL/risk/backtest/promotion logic.
2. Make the smallest focused code change.
3. Run:
   bash scripts/run_safety_tests.sh
   bash scripts/cursor_strategy_loop_check.sh
4. Restart services only after tests pass:
   launchctl kickstart -k gui/$(id -u)/com.altcoin.dashboard
   launchctl kickstart -k gui/$(id -u)/com.altcoin.worker
   launchctl kickstart -k gui/$(id -u)/com.altcoin.research
5. Re-run API/dashboard verification.
6. Commit with a clear message only after tests pass.
7. Push to macmini-tailscale-server only after tests pass.

Success criteria:
- Safety tests pass.
- cursor_strategy_loop_check.sh passes.
- Dashboard renders as HTML, not download.
- Dashboard uses old tabbed style without 마켓 tab.
- Portfolio tab shows open-position PnL when positions exist.
- T-like pump + extreme negative funding is not shorted.
- SKL-like failed pump can be detected as failed_pump_short when live metrics match.
- Experiments are queued/run and visible in strategy/experiments UI.
- No live trading is enabled.

When done, summarize:
- commands run
- tests passed/failed
- files changed
- commit hash
- remaining risks
```
