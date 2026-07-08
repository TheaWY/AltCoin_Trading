# Improvement Plan

Executable plan for the six architecture improvements identified in the review.
Each item lists the concrete steps, the files touched, and how to verify it.

---

## 1. Make data collection actually run on Railway

**Problem.** The Railway web service ran with `RUN_TRADING_SCHEDULER=false` by
default and no worker service existed, so nothing collected data and the
dashboard showed only empty values.

**Steps.**
1. Change the `RUN_TRADING_SCHEDULER` default to `true` everywhere
   (`src/config.py`). The scheduler runs in a background thread and never
   blocks the web server from binding its port, so a single Railway service is
   safe and collects data out of the box.
2. Remove the forced `RUN_TRADING_SCHEDULER=false` cloud default from
   `scripts/start-web.sh`.
3. Keep `scripts/start-worker.sh` + `src/worker.py` as the dedicated worker
   entrypoint. When a second Railway service is added with start command
   `bash scripts/start-worker.sh`, set `RUN_TRADING_SCHEDULER=false` on the
   web service to split the roles cleanly.

**Verify.** Boot the web app locally with no env overrides and confirm the
log line `Scheduler started — collecting every 5 min` appears while `/healthz`
still responds immediately.

---

## 2. Postgres support (durable, shareable database)

**Problem.** SQLite lives on the container filesystem: every Railway redeploy
wipes it, and a web service and worker service cannot share one file.

**Steps.**
1. Add a `DATABASE_URL` setting to `src/config.py`. When set (Railway's
   Postgres plugin injects it automatically), storage uses Postgres; when
   empty, SQLite keeps working exactly as before.
2. Extend `src/data/storage.py` with a Postgres backend:
   - a Postgres version of the schema (`BIGSERIAL` ids, `TIMESTAMPTZ`
     timestamps, `ON CONFLICT DO NOTHING` instead of `INSERT OR IGNORE`);
   - a SQL translator so every existing query written for SQLite
     (`?` placeholders, `:name` placeholders, `datetime('now')`) runs
     unchanged on Postgres;
   - `RETURNING id` instead of `cursor.lastrowid` for inserts.
3. Add `psycopg[binary]` to `requirements.txt` and `requirements-cloud.txt`.

**Operator step (Railway UI, one time).** Add the Postgres plugin to the
project and attach its `DATABASE_URL` variable to the service(s). No code or
config change needed after that.

**Verify.** Run the storage smoke test against both SQLite and a real
Postgres container: insert prices/funding/signals/trades, read them back,
check portfolio state and accuracy queries.

---

## 3. More strategies in the registry

**Problem.** Only `funding_rate` existed even though the registry and the
signal engine were designed for many strategies.

**Steps.**
1. Add `src/strategies/momentum.py` — trend following on 24h price change
   (`MOMENTUM_ENTRY_PCT` threshold, long above, short below).
2. Add `src/strategies/volume_spike.py` — volume surge confirmation
   (`VOLUME_SPIKE_RATIO` × average volume plus candle direction).
3. Register both in `src/strategies/registry.py`.
4. Add the new data keys the strategies need (`recent_prices`,
   `volume_stats`) to the signal engine's data fetchers.
5. Support running several strategies per cycle: new `ACTIVE_STRATEGIES`
   config (comma-separated, defaults to `ACTIVE_STRATEGY`). The first listed
   strategy is the *primary* one that drives trading; all listed strategies
   record signals so their accuracy can be compared on real data.

**Verify.** Unit-run each strategy against synthetic data snapshots and
confirm LONG/SHORT/NONE come out at the right thresholds.

---

## 4. Backtests through the same pipeline as live trading

**Problem.** `scripts/backtest.py` hand-built its own data dictionaries, so a
strategy needing new data keys would backtest differently than it trades.

**Steps.**
1. Extract the engine's data gathering into a shared
   `gather_strategy_data(storage, strategy, symbol)` function in
   `src/engine/signal.py`.
2. Use that same function in the backtester's per-timestamp loop, feeding it
   the time-frozen `SnapshotStorage` so strategies see only past data.
3. Any strategy registered in the registry is now backtestable with
   `--strategy <name>` and behaves identically live and in backtest.

**Verify.** Seed a SQLite database with synthetic candles and funding rates,
run the backtester for all three strategies, and confirm trades open/close.

---

## 5. Cheaper, faster Binance collection

**Problem.** Every 5-minute cycle made ~40 REST calls: one funding-rate call
per symbol and full 100-candle fetches per symbol per timeframe, re-fetching
data already stored.

**Steps.**
1. Fetch funding rates for **all** symbols in a single
   `fetch_funding_rates()` batch call, with per-symbol fallback if the batch
   fails.
2. Make OHLCV collection incremental: look up the newest stored candle per
   symbol/timeframe and request only the candles missed since then (plus a
   two-candle overlap), instead of 100 every time.

**Verify.** Run a collection pass twice in a row and confirm the second pass
requests only 2–3 candles per timeframe and a single funding-rate call.

---

## 6. Quarantine Mac-local legacy ops

**Problem.** ngrok tunnels, launchd agents, and restart scripts are for the
old "runs on my Mac" setup. Mixed in with the cloud entrypoints they made the
repo confusing.

**Steps.**
1. Move the Mac-only scripts into `scripts/local/`:
   `restart.sh`, `ensure-running.sh`, `show-url.sh`, `start-trading.sh`,
   `setup-ngrok-domain.sh`, `install-launchd.sh`, `uninstall-launchd.sh`,
   `install-gitsync.sh`, `git-sync.sh`, `common.sh`.
2. Move the launchd templates into `scripts/local/launchd/` and update the
   installer's template paths.
3. Add `scripts/local/README.md` explaining these are legacy local-ops only,
   not used by Railway.
4. Cloud-relevant files stay at the top level: `start-web.sh`,
   `start-worker.sh`, `deploy-cloud.sh`, `health_check.py`, `backfill.py`,
   `backtest.py`.

**Verify.** `bash -n` every moved script (syntax + path resolution) and grep
the repo for stale references to old paths.
