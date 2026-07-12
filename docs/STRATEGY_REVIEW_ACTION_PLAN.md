# Strategy review action plan

## Current review

The system is now doing useful work:

- It evaluates every tracked crypto with visible reasons.
- It runs paper-only trades.
- It has a research queue with completed experiments.
- It has category/type labels such as `volume_surge` and `liquid_trend`.
- It writes to local Mac mini Postgres.

The weakness is that the strategy layer still misses some human-obvious cases because it only had these families:

- funding reversal
- volume spike, disabled as standalone by default
- mean reversion
- 7d momentum
- optional breakout / 28d momentum

That means the old engine could detect:

```text
strong downtrend -> SHORT momentum
extreme positive funding -> SHORT funding fade
RSI+BB overbought -> SHORT mean reversion
```

But it did not explicitly detect:

```text
coin pumped hard over 7d -> starts dumping intraday -> loses SMA20/MACD structure -> failed pump short
```

## Case review

### T-like case

Observed:

```text
24h +34.5%
7d +19.2%
RSI 82
funding about -2.0%
category volume_surge
```

Interpretation:

This is not automatically a short. Extreme negative funding means shorts are paying longs, so the market may be short-crowded while price is pumping. That is a possible short squeeze / short-term upside setup, not 장투.

The correct behavior is:

```text
detect 단타 상승/funding squeeze candidate
only hold it back if confidence, risk, or regime filters fail
show the setup and any risk/regime block clearly in UI/logs
```

### SKL-like case

Observed:

```text
7d +29.5%
24h -7.0%
RSI 42
below/near broken short-term structure
MACD down
category liquid_trend
```

Interpretation:

This should be detected as a failed-pump short candidate, not ignored as just "7d positive but SMA20 broken".

The new setup added in this branch:

```text
failed_pump_short
```

requires:

```text
7d pump >= 15%
24h reversal <= -3%
SMA20 break or MACD down
RSI <= 60
```

It scores higher when:

```text
7d pump >= 20%
24h dump <= -5%
SMA20 is broken
MACD is down
RSI <= 50
BTC correlation is not too high
```

## Testing rule

Before any strategy code is trusted, run:

```bash
bash scripts/run_safety_tests.sh
```

This currently covers:

- SKL-like failed-pump rollover -> SHORT 단타 setup
- T-like squeeze/pump with negative funding -> not short; detects short-term upside funding setup

## Next engineering steps

1. Pull the branch on Mac mini.
2. Run safety tests.
3. Restart dashboard/worker.
4. Run research cycle.
5. Confirm dashboard now labels SKL-like cases as `failed_pump_short` when the conditions appear.
6. Do not promote this setup to real money. It is paper-only until walk-forward experiments show positive expectancy after fees.

## Commands

```bash
cd /Users/pc/Projects/AltCoin_Trading
source .venv/bin/activate
git pull origin macmini-tailscale-server
bash scripts/run_safety_tests.sh
launchctl kickstart -k gui/$(id -u)/com.altcoin.dashboard
launchctl kickstart -k gui/$(id -u)/com.altcoin.worker
bash scripts/run_research_now.sh
```

## Cursor guardrail

Any future Cursor edit must add/update a test first when it changes:

- entry signal logic
- PnL/portfolio math
- backtesting/promotion logic
- risk sizing
- execution/live-trading code

If tests cannot be run locally, do not merge/use the change for trading.
