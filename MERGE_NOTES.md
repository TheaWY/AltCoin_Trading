# MERGE NOTES — 3 new strategies + positioning data pipeline

Drop these files into the repo at the same paths (5 new, 6 modified),
or hand this whole folder to Cursor with: "apply these files onto the
current repo, they were built against commit HEAD of main".

## New files
- `src/strategies/mean_reversion.py` — RSI+Bollinger both directions (%B from the existing indicators module)
- `src/strategies/funding_carry.py` — persistent-funding carry with settlement bucketing and fee hurdle
- `src/strategies/positioning_short.py` — L/S ratio percentile extreme + hot funding + OI near 30d high
- `src/data/collectors/positioning.py` — fapi /futures/data collector (requests, not ccxt — these endpoints aren't in ccxt's unified API), failures never break the cycle
- `scripts/test_new_strategies.py` — 15 checks through the real gather_strategy_data path, all passing

## Modified files (additive only, no existing lines changed)
- `src/data/storage.py` — `long_short_ratio` + `open_interest` tables (both sqlite and postgres schema blocks; `_init_schema` is idempotent so existing DBs get them on next start), plus insert/get methods in the repo's `INSERT OR IGNORE` idiom so `_translate_sql` handles postgres
- `src/engine/signal.py` — 3 new `_DATA_FETCHERS` keys: `funding_history`, `ls_ratio_history`, `open_interest_history`
- `src/strategies/registry.py` — registers the 3 strategies
- `src/config.py` — MEANREV_*, CARRY_*, POS_SHORT_* env-backed thresholds
- `src/engine/cycle.py` — positioning collection after main collection, wrapped so failure can't fail the cycle
- `scripts/backtest.py` — SnapshotStorage passthroughs for the 3 new data keys, all clamped by `before=self.timestamp` like the existing ones (look-ahead protection preserved)

## Run
```bash
python scripts/test_new_strategies.py          # 15/15 must pass
PRIMARY_STRATEGY=mean_reversion python -m src.worker   # or set in .env
```

## Things to know
1. **funding_carry emits SHORT with `metadata.execution_mode="delta_neutral"`.**
   True carry = this perp short + equal spot long. The execution layer
   doesn't support two-leg positions yet, so paper trading runs it as a
   plain short timed by persistent-funding conditions. Building the spot
   leg into PaperTrader/LiveTrader is the natural next task.
2. **positioning_short needs history it can't backfill.** Binance serves
   ~30 days of L/S ratio; percentile mode needs 90. The collector now runs
   every cycle — the absolute fallback (>2.5) applies until ~90 days
   accumulate. Deploy this before anything else so the clock starts.
3. **ALLOW_LONG=false (repo default) filters mean_reversion's LONG leg**
   downstream. The strategy still emits LONG so backtests can evaluate
   both legs when the policy allows.
4. **funding_rates rows are collection-frequency, not settlement-frequency.**
   funding_carry buckets rows into 8h windows and takes the last print per
   window. If collection had gaps, consecutiveness is measured over
   observed settlements (conservative).
5. **The 30-trade / expectancy / profit-factor promotion gates** live in
   your evaluation layer, unchanged. positioning_short will take months to
   reach 30 trades — that's expected, not a bug.
