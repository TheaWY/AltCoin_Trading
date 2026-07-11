# Cursor task: make the trading replay and live cycle point-in-time safe

You are working in `TheaWY/AltCoin_Trading` from the latest `cursor/init-foundation` plus the audit-safety branch changes. Read `docs/TRADING_SYSTEM_AUDIT_2026-07-11.md` before editing.

## Non-negotiable constraints

- Keep `LIVE_TRADING=false`.
- Do not merge or delete the existing draft PR #9 blindly. Reuse useful code from it, but fix its same-candle look-ahead.
- Do not optimize strategy parameters in this task.
- Do not report profitability until all point-in-time and execution tests pass.
- Use only completed candles for features.
- Signals generated after bar `t` closes may execute no earlier than bar `t+1` open.
- Make failures deny new entries by default. Exits and reconciliation must continue.
- Preserve SQLite and Postgres support.

## Task 1 — create a canonical point-in-time bar model

Add a small shared module, for example `src/market/bars.py`, with:

- timeframe-to-seconds parsing;
- `bar_open_ts` and `bar_close_ts` helpers;
- `is_bar_closed(bar, timeframe, now_ts)`;
- functions that return only bars whose close time is `<= decision_time`;
- validation for monotonic timestamps and valid OHLC values.

Use explicit names such as `decision_time`, `signal_bar_close`, and `execution_bar_open`. Do not use a generic `now` where timing semantics matter.

## Task 2 — repair the production-path replay

Refactor `scripts/backtest_eval.py` from draft PR #9, or create a replacement, so the loop is event-driven:

1. At execution bar `t+1` open, execute orders queued from completed bar `t`.
2. During bar `t+1`, evaluate existing stops/targets from high/low.
3. At bar `t+1` close, mark equity and generate new signals using data through that close.
4. Queue those signals for the next available bar open.

Requirements:

- Never expose current bar high/low/close/volume before its close.
- Entry price is next-bar open plus spread/slippage, not signal-bar close.
- For ambiguous bars containing both stop and target, use adverse-first by default and record the ambiguity count.
- Apply gap logic: if a bar opens beyond a stop, fill at the bar open plus adverse slippage rather than the stop trigger.
- Use `PaperTrader` risk sizing, ATR exits, trailing stops, time stops, and cost logic through shared functions instead of duplicating them.
- Include final liquidation costs in the final equity point and maximum drawdown.
- Do not generate repeated decisions for a symbol when only another symbol has advanced.
- Record every signal time, intended execution time, actual execution price, fee, slippage, and exit reason.

## Task 3 — add look-ahead and execution tests first

Create deterministic synthetic fixtures before running real history. At minimum test:

1. A signal changes only because bar `t` closes; assert no position exists during bar `t`.
2. The position opens exactly at bar `t+1` open.
3. Changing bar `t+1` close cannot change its own entry decision.
4. A current incomplete candle is invisible to features.
5. A gap through a stop fills at the adverse open.
6. Both stop and target inside one candle uses adverse-first ordering.
7. A symbol with no new candle does not emit another signal.
8. Final liquidation is included in return and drawdown.
9. Running the same fixture twice produces byte-identical results.

Tests must fail against the old replay and pass after the refactor.

## Task 4 — make the live cycle fail closed

Refactor `run_collection()` and `run_trading_cycle()` to produce and consume an explicit collection report containing, for every required symbol/timeframe:

- success/failure;
- latest completed candle timestamp;
- expected next timestamp;
- age/staleness;
- rows fetched and rows inserted;
- error category.

Entry rules:

- If BTC 1h data is missing/stale, block all entries.
- If a candidate's required timeframe or feature source is stale, block that candidate.
- If data-health evaluation throws, block entries.
- If category/risk policy evaluation throws, block the affected entry.
- Partial collection success must not be interpreted as full health.
- Continue exchange reconciliation and exits even when entries are blocked.

Add a database-backed cycle lease so two worker processes cannot run the same cycle concurrently. Include lease expiry for crash recovery.

## Task 5 — add a historical-data repair command

Create `scripts/repair_candles.py` that:

- scans for duplicate, missing, invalid, and potentially partial candles;
- optionally downloads authoritative replacements;
- writes changes transactionally;
- supports `--dry-run`;
- emits before/after counts and a JSON report;
- never modifies the production database unless `--apply` is explicitly passed.

## Task 6 — prepare, but do not enable, a safe live broker state machine

Do not place production orders in this task. Add schema and interfaces for:

- `order_intents`;
- `exchange_orders`;
- `fills`;
- `position_snapshots`;
- reconciliation incidents.

Every order intent needs a deterministic client order ID and lifecycle states such as:

`created -> submitted -> acknowledged -> partially_filled -> filled -> closing -> closed`

plus `rejected`, `cancelled`, and `reconciliation_required`.

Add a startup reconciliation service that compares exchange open orders/positions against local state and blocks new entries on any unexplained mismatch.

## Task 7 — verification

Run and report exact commands and results:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/storage_smoke.py
python scripts/strategy_smoke.py
python scripts/test_research_stack.py
python scripts/test_promotion.py
```

Then run the repaired replay on a short deterministic fixture and one small real-data period. Report:

- signal count;
- queued orders;
- filled orders;
- ambiguous bars;
- gross PnL;
- each cost component;
- net PnL;
- maximum drawdown;
- cash and BTC benchmarks.

## Deliverables

- code and tests;
- migration-safe schema changes;
- updated operational documentation;
- one draft PR with a precise summary;
- no claim that the strategy is profitable or live-ready merely because tests pass.

In the PR description, explicitly list any remaining differences between replay, paper, testnet, and production execution.
