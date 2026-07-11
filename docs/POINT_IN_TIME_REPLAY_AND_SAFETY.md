# Point-in-Time Replay and Fail-Closed Safety

This branch keeps `LIVE_TRADING=false` and does not claim profitability.

## Timing Contract

- Candle timestamps are bar-open times.
- A bar is complete only when `bar_open + timeframe <= decision_time`.
- Strategy features may only see completed bars through `decision_time`.
- A signal produced after `signal_bar_close` is queued for
  `execution_bar_open`, which is no earlier than the next bar open.
- Historical fills use next-bar open plus modeled spread and slippage.
- If a candle contains both stop and target, the replay uses adverse-first
  ordering and records the bar as ambiguous.
- If a candle opens beyond a stop, the fill occurs at the adverse open plus
  exit-side costs, not at the stop trigger.

## Modules

- `src/market/bars.py`: canonical timeframe parsing, bar-close helpers,
  completed-bar visibility, and OHLCV validation.
- `scripts/backtest.py`: research replay path used by `run_backtest_once.py`,
  now event-driven and next-bar.
- `scripts/backtest_eval.py`: production-stack replay entry point backed by the
  same corrected event loop.
- `scripts/repair_candles.py`: dry-run-first candle scan/repair command.
- `src/engine/broker_state.py`: deterministic client order IDs and
  reconciliation interfaces for future testnet/live adapters.

## Fail-Closed Runtime

- `run_collection()` returns per-symbol/timeframe reports with success,
  latest completed candle timestamp, expected next timestamp, age, fetched and
  inserted row counts, and error category.
- `run_trading_cycle()` uses a database-backed expiring lease so multiple
  worker processes do not run the same cycle concurrently.
- Stale/missing BTC 1h data blocks all new entries.
- Stale/missing candidate 1h data blocks that candidate.
- Data-health exceptions block entries.
- Category/risk-policy exceptions block affected entries.
- Reconciliation and exits still run while new entries are blocked.

## Remaining Execution Differences

- Historical replay uses OHLCV candles and deterministic adverse-first
  ordering; paper trading sees latest stored prices and cannot know intrabar
  path.
- Historical replay models spread and slippage from configured percentages;
  paper trading currently applies costs through the paper trade fee model.
- Testnet execution is still interface/schema-only in this branch and must be
  wired to normalized exchange order/fill snapshots before use.
- Production execution remains disabled and requires exchange-native protective
  orders, free-collateral sizing, partial-fill handling, and startup/per-cycle
  reconciliation before any capital is enabled.
- Funding/basis costs are only approximated for existing carry logic; spot/perp
  basis and borrow costs are not fully modeled.

