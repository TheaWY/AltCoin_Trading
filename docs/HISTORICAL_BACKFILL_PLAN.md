# Historical backfill plan

Your local Mac mini database currently has enough data for dashboard/paper sanity checks, but not enough to trust parameter changes or autonomous promotion.

The bootstrap script loads roughly 720 hourly candles, which is about 30 days. That is useful for UI and sanity testing only.

## Goal

Build a deeper local Postgres dataset for backtesting:

```text
Top 50 liquid crypto perps
1h candles from 2022-01-01 to now
funding history from 2022-01-01 to now where Binance returns it
```

Then expand later:

```text
Top 100 since 2021
Then selected clusters/categories
Then 15m data for shorter-term strategies
Then tick bars for top 30-50 only
```

## Recommended first command

```bash
cd /Users/pc/Projects/AltCoin_Trading
source .venv/bin/activate
git pull origin macmini-tailscale-server
bash scripts/backfill_core_history.sh
```

This runs:

```bash
python scripts/backfill_history.py --limit 50 --start 2022-01-01 --timeframes 1h --funding
```

## Why top 50 first

Do not backfill all 526 symbols to 2022 immediately. It can be slow, many newer symbols do not have old history, and most illiquid symbols are not useful for your current strategy.

Start with the top 50 to test:

- production-style backtesting
- funding/crowding logic
- category research
- parameter experiments
- data quality

Then expand only if the first dataset works.

## Commands

Dry-run plan:

```bash
python scripts/backfill_history.py --limit 50 --start 2022-01-01 --funding --dry-run
```

Backfill top 50:

```bash
python scripts/backfill_history.py --limit 50 --start 2022-01-01 --timeframes 1h --funding
```

Backfill specific symbols:

```bash
python scripts/backfill_history.py --only BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT --start 2020-01-01 --timeframes 1h --funding
```

Expand later:

```bash
python scripts/backfill_history.py --limit 100 --start 2021-01-01 --timeframes 1h --funding
```

Add 15m only for top liquid symbols:

```bash
python scripts/backfill_history.py --limit 30 --start 2024-01-01 --timeframes 15m
```

## Safety

The script refuses to run if `DATABASE_URL` looks like Railway unless you pass `--allow-railway`. In normal Mac mini mode, it should write only to local Postgres.

The script is idempotent. Re-running it is safe because inserts ignore rows already present.

## After backfill

Check local DB status:

```bash
python scripts/macmini_status.py
```

Then run backtests/experiments. Do not seriously tune parameters until you have at least multiple market regimes in the local DB.
