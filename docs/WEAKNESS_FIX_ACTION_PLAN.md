# Weakness Fix Action Plan

This document is the implementation artifact for the strategy/backtesting weaknesses found in the current repo review.

## Objective

Move the system away from a high-turnover strategy soup and toward a lower-turnover, evidence-gated paper research system. The target is not "beat BTC in a crash". The target is **positive absolute expectancy after fees and slippage**, starting from the actual research capital size.

## Step-by-step fixes applied

### Step 1 — Use the richer evaluation engine as the entry source

**Weakness:** The dashboard/evaluation path and paper-entry path were different. `evaluate_symbol()` explained rich setup/confluence logic, but `run_trading_cycle()` opened trades through `AltAnalyzer` and the latest primary signal.

**Fix:** `run_trading_cycle()` now defaults to `ENTRY_DECISION_ENGINE=evaluation`. New entries are selected directly from `evaluate_symbol()` verdicts. The old primary-signal + `AltAnalyzer` route remains available with `ENTRY_DECISION_ENGINE=signal`.

**Files:**

- `src/config.py`
- `src/engine/cycle.py`

### Step 2 — Lower turnover by default

**Weakness:** The last full-stack replay showed that hourly churn and fees killed the system.

**Fix:** Conservative defaults now raise the hurdle before a trade can open:

```env
PAPER_STARTING_CAPITAL=730
MIN_CONFIDENCE=0.70
COOLDOWN_HOURS_PER_SYMBOL=48
```

**Files:**

- `src/config.py`
- `.env.example`
- `research_space.yaml`

### Step 3 — Disable weak/noisy branches by default

**Weakness:** Breakout and 28d momentum were likely weak branches; volume spike was too noisy as a standalone entry.

**Fix:**

```env
SETUP_BREAKOUT_ENABLED=false
SETUP_TSMOM_ENABLED=false
SETUP_VOLUME_ENABLED=false
```

Volume spikes are still used as a confluence modifier inside `_apply_confluence()`: aligned volume adds a small bonus, opposite volume subtracts a larger penalty.

**Files:**

- `src/config.py`
- `src/engine/evaluation.py`
- `research_space.yaml`

### Step 4 — Keep maker-fee mode as sensitivity only

**Weakness:** Maker-fee assumptions can overstate performance unless missed fills and adverse selection are modeled.

**Fix:** `FEE_MODE=taker` remains the conservative default. `.env.example` explicitly warns not to use maker mode as evidence until maker-fill simulation exists.

**Files:**

- `src/config.py`
- `.env.example`
- `research_space.yaml`

### Step 5 — Add cash/no-trade benchmark reporting

**Weakness:** "Beats BTC" was not enough. A strategy that loses less than BTC is still not investable if cash/no-trade wins.

**Fix:** Added `scripts/annotate_backtest_benchmarks.py`, which annotates any backtest JSON with:

- cash/no-trade final value
- strategy edge versus cash
- whether the strategy beats cash
- strategy edge versus BTC buy-hold when available

**File:**

- `scripts/annotate_backtest_benchmarks.py`

### Step 6 — Tighten the research queue

**Weakness:** The hypothesis space could still waste runs on branches already suspected to be bad.

**Fix:** `research_space.yaml` now prioritizes the conservative core:

```yaml
SETUP_BREAKOUT_ENABLED: false
SETUP_TSMOM_ENABLED: false
SETUP_VOLUME_ENABLED: false
MIN_CONFIDENCE: 0.70
COOLDOWN_HOURS_PER_SYMBOL: 48
FEE_MODE: taker
```

### Step 7 — Make Home the primary dashboard

**Weakness:** The old mobile dashboard put Home and Market side by side. For this project, generic market browsing is less useful than a decision page that answers: hold, enter, or wait?

**Fix:** `/dashboard` now serves a lean Home-only decision dashboard. The generic market table is no longer the default UI. The old full dashboard remains available at `/dashboard/full` for portfolio, strategy, history, and market table access.

**Files:**

- `src/dashboard/home.html`
- `src/api/main.py`

### Step 8 — Fix Home price freshness and missing-candle confusion

**Weakness:** The new Home-only dashboard initially polled `/api/alts` every 30 seconds and did not subscribe to `/ws` live ticks, so prices looked stale. Also, `0/48 candles` often happened because market-data collection could point at Binance futures testnet, which has sparse/non-real alt history.

**Fix:**

- `home.html` now subscribes to `/ws`, sends a watch list for visible cards, and updates `[data-tick]` / `[data-tick-pct]` elements from live price messages.
- Full snapshot polling is reduced to a background refresh; real price updates come from the websocket.
- Data collection now separates order safety from market-data source:
  - `BINANCE_TESTNET=true` can still protect live-order endpoints if live trading is ever enabled.
  - `BINANCE_MARKET_DATA_TESTNET=false` uses real public Binance futures data for paper/backtest collection.

**Files:**

- `src/dashboard/home.html`
- `src/config.py`
- `src/data/collectors/binance.py`
- `.env.example`

## Recommended Mac Mini command sequence

Run this after pulling the branch locally:

```bash
# 1. Keep worker/paper mode conservative
export LIVE_TRADING=false
export PAPER_STARTING_CAPITAL=730
export ENTRY_DECISION_ENGINE=evaluation
export MIN_CONFIDENCE=0.70
export COOLDOWN_HOURS_PER_SYMBOL=48
export SETUP_BREAKOUT_ENABLED=false
export SETUP_TSMOM_ENABLED=false
export SETUP_VOLUME_ENABLED=false
export FEE_MODE=taker
export BINANCE_MARKET_DATA_TESTNET=false

# 2. Confirm data health before opening new paper trades
python -m src.research.data_quality --scan --days 30

# 3. Load/update history
python scripts/load_history.py --start 2020-01

# 4. Run single-strategy smoke backtests
python scripts/backtest.py --strategy funding_rate --symbols BTC/USDT,ETH/USDT,SOL/USDT
python scripts/backtest.py --strategy mean_reversion --symbols BTC/USDT,ETH/USDT,SOL/USDT

# 5. Annotate latest report with cash benchmark
python scripts/annotate_backtest_benchmarks.py data/backtest_YYYYMMDD.json --write
```

## If dashboard cards still show 0/48 candles

1. Confirm the Mac Mini worker is actually running, not only the Railway web app.
2. Confirm `RUN_TRADING_SCHEDULER=true` on the Mac Mini worker.
3. Confirm `BINANCE_MARKET_DATA_TESTNET=false`.
4. Watch logs for `OHLCV collected for <symbol> 1h`.
5. If a delisted/unsupported symbol still fails, remove it from the auto/static universe.

## What still must not be treated as solved

- A maker-fee run is not valid evidence until maker-fill simulation exists.
- Funding carry still needs spot/perp basis and margin-buffer modeling before being trusted as a true cash-and-carry test.
- A configuration that only loses less than BTC is not good enough; it must beat cash/no-trade.
- Any promoted configuration still needs walk-forward + fresh-data validation through `src.research.promotion`.

## Acceptance criteria before live capital

A candidate configuration must satisfy all of these:

1. Positive return after fees/slippage.
2. Beats cash/no-trade.
3. Positive in most walk-forward windows.
4. Profit factor above the promotion floor.
5. Deflated Sharpe gate passes.
6. Fresh-data evaluation remains positive for at least the configured fresh period.
7. Paper-trade health does not trigger rollback.

Until then, the correct status is **paper only**.
