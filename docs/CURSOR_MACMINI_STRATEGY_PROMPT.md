# Cursor prompt — Mac mini strategy loop

Paste this into Cursor running on the Mac mini.

```text
You are working locally on my Mac mini repo:
/Users/pc/Projects/AltCoin_Trading
Branch: macmini-tailscale-server
Repo: TheaWY/AltCoin_Trading

Goal:
Make the autonomous strategy loop safer, test-driven, and visible in the UI.
The system should:
1. collect all relevant crypto data locally,
2. evaluate every tracked crypto for enter/hold/skip,
3. show all decision evidence in the dashboard,
4. run research/backtests automatically,
5. promote only configs that pass tests/backtests/fresh-data gates,
6. remain PAPER ONLY.

Hard safety rules:
- This is money-related code. Do not change strategy, PnL, risk, backtest, portfolio, or promotion logic without tests.
- Do not commit or push unless tests pass locally.
- Do not enable LIVE_TRADING.
- Do not commit .env or secrets.
- Do not use Railway. Use local Mac mini Postgres only.
- Do not expose Postgres publicly.
- Prefer small, auditable changes over big rewrites.

First commands:
cd /Users/pc/Projects/AltCoin_Trading
source .venv/bin/activate
git fetch origin
git switch macmini-tailscale-server
git pull origin macmini-tailscale-server

Read these files first:
- src/engine/evaluation.py
- src/engine/paper_trader.py
- src/api/routes/alts.py
- src/dashboard/index.html
- research_space.yaml
- src/research/generator.py
- src/research/runner.py
- src/research/promotion.py
- tests/test_evaluation_setups.py
- scripts/run_safety_tests.sh
- scripts/cursor_strategy_loop_check.sh
- docs/STRATEGY_REVIEW_ACTION_PLAN.md

Context from dashboard review:
Case A: T-like
- 24h +34.54%
- 7d +19.23%
- RSI 82
- funding about -2.00%
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
Detect as failed_pump_short, style 단타, direction SHORT, if the live metrics match that pattern.

What was already added:
- _failed_pump_short_setup() in src/engine/evaluation.py
- policy_blocked_setups in evaluation output
- tests/test_evaluation_setups.py with T-like and SKL-like cases
- SETUP_FAILED_PUMP_ENABLED in research_space.yaml
- scripts/run_safety_tests.sh
- scripts/cursor_strategy_loop_check.sh

Your tasks:

1. Run safety tests first:
   bash scripts/run_safety_tests.sh

2. Run the local strategy-loop check:
   bash scripts/cursor_strategy_loop_check.sh

3. Inspect /api/alts output for T and SKL. Use this exact snippet:
   python - <<'PY'
import json, urllib.request
url = "http://127.0.0.1:8000/api/alts"
data = json.loads(urllib.request.urlopen(url, timeout=90).read())
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

4. If API/dashboard is stale, restart only after tests pass:
   launchctl kickstart -k gui/$(id -u)/com.altcoin.dashboard
   launchctl kickstart -k gui/$(id -u)/com.altcoin.worker

5. Run research generation and one research cycle:
   python -m src.research.generator --dry-run
   python -m src.research.generator
   bash scripts/run_research_now.sh

6. Watch logs:
   tail -80 data/logs/research.manual.log
   tail -80 data/logs/research.err.log
   tail -80 data/logs/dashboard.err.log

7. UI requirements:
   Keep the old tabbed dashboard style.
   Bottom tabs should be:
   - 홈
   - 포트폴리오
   - 전략
   - 히스토리
   - 실험
   Do not re-add 마켓 tab.
   Do not replace the whole UI with the single Home page.

8. Portfolio requirements:
   If open positions exist, portfolio must show:
   - total paper value
   - total PnL vs starting capital
   - cash
   - open position market value
   - total unrealized PnL
   - open position count
   - each open position current price, entry, unrealized PnL, and unrealized PnL %
   PnL must come from open paper_trades + latest price, not from whether the coin card is currently visible.

9. Strategy/research UI requirements:
   Strategy tab should show:
   - active/champion config
   - experiment counts queued/running/done/failed
   - recent experiment results
   - failed_pump_short experimental status
   - why configs were not promoted
   - clear distinction between current paper logic and experimental candidate logic

10. If you change strategy logic:
   - Add or update tests in tests/test_evaluation_setups.py first.
   - Run bash scripts/run_safety_tests.sh.
   - Run bash scripts/cursor_strategy_loop_check.sh.
   - Only then commit.

11. If you change portfolio/PnL logic:
   - Add tests that verify open SHORT and open LONG unrealized PnL math.
   - Confirm /api/alts portfolio.open_positions has current_price and unrealized_pnl.

12. If you change research/promotion logic:
   - Add tests or a deterministic dry-run check.
   - Never promote based on one good window.
   - Keep promotion gated by walk-forward and fresh-data evidence.

Success criteria:
- Safety tests pass.
- cursor_strategy_loop_check.sh passes.
- Dashboard renders in browser, not as a download.
- Dashboard uses old tabbed style without 마켓 tab.
- Portfolio tab shows nonzero open-position PnL when positions exist.
- T-like pump + extreme negative funding is not shorted.
- SKL-like failed pump can be detected as failed_pump_short when live metrics match.
- Experiments are queued/run and visible in strategy/experiments UI.
- No live trading is enabled.

Commit rule:
Before committing, run:
  bash scripts/run_safety_tests.sh
  bash scripts/cursor_strategy_loop_check.sh

Then commit with a clear message. Do not push if either command fails.
```
