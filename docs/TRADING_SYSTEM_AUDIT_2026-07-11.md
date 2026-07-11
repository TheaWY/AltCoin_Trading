# Trading System Audit — 2026-07-11

## Verdict

The repository has a useful research skeleton, but it is **not ready for live capital**. The primary problem is not the absence of another indicator. It is that data timing, backtest execution, live-order reconciliation, and automatic promotion are not yet reliable enough to distinguish real edge from implementation artifacts.

The practical target is not an "optimal" strategy. It is a bounded adaptive system that:

1. uses only information available at decision time;
2. survives realistic costs and adverse execution;
3. promotes configurations only after independent out-of-sample evidence;
4. fails closed on stale data or uncertain exchange state; and
5. can reconcile every real order and position after a crash or restart.

Keep `LIVE_TRADING=false` until every P0 item below is complete and independently tested.

---

## P0 findings

### 1. Incomplete candles were persisted and used as completed observations

`fetch_ohlcv()` may return the still-forming final candle. The old collector inserted that row immediately. Because the price table used `INSERT OR IGNORE`, the first partial version could remain permanently even after the candle changed.

**Patch in this branch:** collection now filters out any candle whose full timeframe has not elapsed.

**Operational follow-up:** re-download or repair recent historical candles. Existing databases may already contain frozen partial rows.

### 2. The legacy backtest has same-bar look-ahead

The legacy backtest makes a candle visible at its timestamp, calculates a signal from that candle's final OHLCV values, and opens at that same candle's close. For exchange candles, the timestamp represents the interval start; the close/high/low/volume are only known after the interval ends.

**Required model:**

- At the end of bar `t`, compute a signal using bars through `t`.
- Queue the order.
- Execute no earlier than bar `t+1` open, with modeled spread/slippage.
- Evaluate stops and targets using the subsequent bar's high/low.
- When both stop and target occur inside one candle and tick ordering is unavailable, use the adverse-first convention or lower-resolution data.

Draft PR #9 improves the execution replay substantially, but it still exposes the current candle at `storage.now = ts` and then enters using that candle's close. It must be shifted before merge.

### 3. Live execution is not crash-safe or idempotent

Current live ordering submits the exchange order first and then records it in the local paper-trade table. A database failure after exchange acceptance can leave a real, untracked position.

Additional live risks:

- no deterministic client order ID;
- requested quantity is recorded instead of confirmed filled quantity;
- sizing prefers total balance over free collateral;
- no durable order state machine;
- no startup reconciliation between exchange positions and local records;
- no exchange-native protective stop immediately after entry;
- no explicit response to position-side mismatches or partial fills;
- a missing exchange position can leave a local trade open indefinitely;
- the `LiveTrader.process_signal()` override omits the paper trader's cooldown check.

**Required:** live execution must be rebuilt around a durable intent/order/fill/position journal before real funds are enabled.

### 4. Collection can fail per symbol while the cycle continues trading

`run_collection()` catches individual symbol errors and returns `{"error": true}`. The trading cycle only marks collection failed when an exception escapes the entire call. The cycle can therefore continue with stale prices after partial collection failure.

**Required:** entries must fail closed when:

- BTC/regime data is missing or stale;
- the candidate symbol's latest completed candle is stale;
- a required feature source fails;
- data-health evaluation itself throws an exception; or
- category/risk policy evaluation throws an exception.

Exits should continue using exchange state and the best available market price even while entries are blocked.

### 5. Automatic research baseline could be mislabeled

The generator previously used the first value in each search axis as the champion whenever an environment variable was absent. That was not necessarily the running default. For example, a grid beginning with `momentum` could be called champion while the configured strategy was `funding_rate`.

**Patched:** the baseline now resolves environment/promotion overrides first, then the actual value imported from `src.config`.

### 6. Research strategy overrides could be ignored

`ACTIVE_STRATEGIES` takes precedence over `ACTIVE_STRATEGY`. A subprocess experiment changing only `ACTIVE_STRATEGY` could still inherit the parent's `ACTIVE_STRATEGIES` value and test the wrong strategy.

**Patched:** challenger `ACTIVE_STRATEGY` also sets `ACTIVE_STRATEGIES` unless the challenger explicitly supplies the latter.

### 7. Research profit factor was calculated from windows, not trades

The previous aggregate divided the sum of profitable-window PnL by the absolute sum of losing-window PnL. That is not trade-level profit factor and can materially distort candidate ranking.

**Patched:** profit factor now uses positive and negative trade PnLs.

### 8. Concurrent research runners could execute the same candidate

Queue selection and status update were separate operations. Two runners could select the same queued experiment before either changed its status.

**Patched:** each experiment is claimed with a conditional `queued -> running` update and proceeds only if exactly one row changed.

### 9. Fresh-data promotion double-counted the same trades

Each fresh evaluation is an expanding replay from candidate creation to the current evaluation time. Stage 2 previously summed every snapshot, repeatedly counting the early period and inflating both trade count and total PnL.

**Patched:** Stage 2 uses only the latest cumulative snapshot and requires positive expectancy and positive total PnL.

### 10. Promotion health checked the wrong backtest and stayed active after rollback

Decay comparison previously read the newest champion-baseline metrics rather than the metrics for the configuration that had actually been promoted. The health check also selected the last promotion even when a later rollback had already deactivated it.

**Patched:** health uses the latest promotion/rollback action and resolves the promoted candidate's own historical expectancy.

---

## P1 architecture changes

### A. Build one event-driven execution model for backtest, paper, shadow, and live

Create shared domain objects:

- `MarketEvent`
- `SignalIntent`
- `OrderIntent`
- `OrderAck`
- `Fill`
- `Position`
- `RiskDecision`

The same strategy and risk functions should consume a point-in-time market snapshot in every environment. Only the broker adapter changes:

- historical fill simulator;
- paper/shadow broker;
- Binance testnet broker;
- Binance production broker.

Do not maintain separate sizing, exit, and fee logic in `scripts/backtest.py`, `PaperTrader`, and `LiveTrader`.

### B. Separate mutable market state from completed research bars

Use two explicit datasets:

1. `completed_candles` for strategy features and research;
2. real-time ticks/order book for dashboards, risk monitoring, and execution.

Never silently mix an in-progress candle into a completed-candle feature window.

Add data-quality assertions for:

- duplicate timestamp/timeframe rows;
- missing intervals;
- non-monotonic timestamps;
- impossible OHLC relationships;
- negative volume;
- stale symbol data;
- large discontinuities requiring review;
- insufficient warm-up history.

### C. Add realistic execution costs

For each trade, model and store separately:

- maker/taker fee;
- bid-ask spread;
- slippage;
- market impact;
- funding payment;
- borrow/basis cost where applicable;
- latency and delayed fill;
- partial-fill ratio;
- rejected/cancelled order probability for maker simulations.

Maker fees must not be enabled without a fill and adverse-selection model.

### D. Add portfolio-level risk allocation

Replace independent fixed-percentage trades with a portfolio allocator that considers:

- target portfolio volatility;
- strategy covariance;
- BTC beta and common-factor concentration;
- per-symbol liquidity limits;
- aggregate long/short exposure;
- per-category exposure;
- drawdown throttling;
- daily loss and operational loss limits.

Suggested initial limits for paper research:

- no leverage;
- 10–12% annualized volatility target;
- 0.25–0.50% equity at risk per trade;
- 5% maximum single-symbol allocation;
- 20% maximum correlated category allocation;
- 1% daily loss pause;
- 8% portfolio drawdown stop and manual review.

These are starting constraints, not optimized parameters.

### E. Use a small, structurally diverse strategy portfolio

Begin with explainable families rather than a large indicator grid:

1. medium-horizon trend following;
2. volatility breakout;
3. mean reversion gated to non-trending regimes;
4. funding/basis carry only after basis and both legs are modeled correctly.

Allocate by regime and realized risk. Avoid treating many slightly different parameterizations of the same signal as independent strategies.

### F. Strengthen validation

Required validation stack:

- next-bar execution;
- expanding or rolling walk-forward tests;
- purging and embargo where labels/holding periods overlap;
- non-overlapping final test windows;
- bull, bear, sideways, high-volatility, and low-liquidity slices;
- asset holdouts, not only time holdouts;
- parameter-neighborhood stability;
- doubled and tripled cost stress;
- block bootstrap confidence intervals;
- Deflated Sharpe / multiple-testing control;
- comparison against cash, BTC buy-and-hold, and simple trend baselines.

The current research default of BTC and ETH is not sufficient evidence for a live universe of many altcoins.

### G. Use staged promotion

A candidate should progress through:

1. historical research;
2. frozen unseen test;
3. live shadow orders with real quotes but no submission;
4. paper trading;
5. Binance testnet operational validation;
6. canary allocation;
7. staged capital increases.

Automatic research may propose and paper-promote configurations. It should not automatically move material real capital without a separately configured capital-stage gate.

---

## Ordered implementation plan

### Phase 0 — Current branch

- [x] Filter incomplete OHLCV candles.
- [x] Correct champion-baseline resolution.
- [x] Ensure strategy overrides reach subprocesses.
- [x] Correct trade-level profit factor.
- [x] Atomically claim experiments.
- [x] Stop fresh-evaluation double counting.
- [x] Correct promotion health state and candidate comparison.
- [x] Add regression tests.

### Phase 1 — Point-in-time backtest correctness

- [ ] Refactor PR #9 so features use only completed bars through `t`.
- [ ] Execute signals at `t+1` open, not at `t` close.
- [ ] Add spread/slippage to entry and exit fill prices.
- [ ] Use high/low for exits with adverse-first ambiguity handling.
- [ ] Use the same position sizing, trailing, and time-stop logic as paper.
- [ ] Include final liquidation and fees in the reported equity curve/drawdown.
- [ ] Prevent stale symbols from emitting repeated signals on another symbol's timestamp.
- [ ] Add deterministic synthetic tests that fail under same-bar look-ahead.

### Phase 2 — Data and cycle fail-closed behavior

- [ ] Inspect collection results and compute success/freshness per symbol/timeframe.
- [ ] Block all new entries when BTC/regime inputs are stale.
- [ ] Block a candidate when any required feature is stale or missing.
- [ ] Make data-health/category exceptions deny entries rather than allow them.
- [ ] Add a distributed or database cycle lock.
- [ ] Repair historical partial candles through a clean backfill.
- [ ] Persist explicit data-quality incidents and expose them in health endpoints.

### Phase 3 — Live broker safety

- [ ] Add `order_intents`, `orders`, `fills`, and `positions` tables.
- [ ] Generate deterministic client order IDs.
- [ ] Reconcile order and position state at startup and every cycle.
- [ ] Size from free collateral and portfolio risk, never raw total balance.
- [ ] Store confirmed fills and actual fees.
- [ ] Handle partial fills and rejected/cancelled orders.
- [ ] Place exchange-native protective stops immediately after entry.
- [ ] Add reduce-only and position-side verification.
- [ ] Implement emergency flatten and global kill switch.
- [ ] Require explicit two-factor configuration to enable production trading.

### Phase 4 — Robust adaptive strategy portfolio

- [ ] Implement simple trend, breakout, and regime-gated mean reversion baselines.
- [ ] Add covariance-aware risk allocation.
- [ ] Validate across assets, regimes, and stressed costs.
- [ ] Store immutable data/config/code hashes for every experiment.
- [ ] Add shadow/paper/testnet/canary promotion stages.
- [ ] Define retirement rules based on confidence intervals, drift, and operational health.

### Phase 5 — Live readiness review

Live capital remains prohibited until all are true:

- [ ] zero look-ahead tests pass;
- [ ] replay and paper use identical strategy/risk logic;
- [ ] exchange reconciliation survives forced crashes;
- [ ] protective stops exist independently of the scheduler;
- [ ] at least several months of paper/shadow evidence are positive after costs;
- [ ] performance is not concentrated in one symbol, month, or regime;
- [ ] canary loss is capped and manually recoverable;
- [ ] an operator runbook and rollback drill have been completed.

---

## Local verification commands

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/storage_smoke.py
python scripts/strategy_smoke.py
python scripts/test_research_stack.py
python scripts/test_promotion.py
python -m src.research.generator --dry-run
python -m src.research.runner --max-runs 1
python -m src.research.promotion status
```

After repairing the replay engine, run deterministic synthetic fixtures before using downloaded market history. A profitable historical report is not evidence if timing tests fail.
