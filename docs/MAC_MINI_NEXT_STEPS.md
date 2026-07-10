# Immediate next steps

Your Mac mini server is already using local Postgres if `python scripts/macmini_status.py` shows:

```text
Railway URL detected: no
Storage: Postgres
Database query: 1
```

## Next: build enough historical data for real backtesting

The initial bootstrap only loads about 720 hourly candles (~30 days). That is enough for dashboard/paper sanity checks, but not enough for parameter tuning or autonomous strategy promotion.

Run:

```bash
cd /Users/pc/Projects/AltCoin_Trading
source .venv/bin/activate
git pull origin macmini-tailscale-server
bash scripts/backfill_core_history.sh
```

This backfills:

```text
top 50 liquid crypto perps
1h candles from 2022-01-01
funding history from 2022-01-01 where available
```

It is safe to stop and rerun because inserts ignore rows already present.

## After backfill

```bash
python scripts/macmini_status.py
python scripts/backtest_fast.py --strategy mean_reversion --symbols BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT --start 2023-01-01
python scripts/backtest_categories.py --days 365 --horizons 1,4,24
```

Do not enable serious autonomous parameter promotion until this deeper history exists.

## Previous setup checklist

1. Pull `macmini-tailscale-server` on the Mac mini.
2. Run `bash scripts/setup_macmini_postgres.sh`.
3. Copy `.env.macmini.example` to `.env` and paste the generated local `DATABASE_URL`.
4. Run `bash scripts/bootstrap_macmini_local.sh`.
5. Run `bash ops/install_macmini_server.sh`.
6. Run `bash ops/serve_tailscale.sh`.
7. Open the Tailscale URL at `/dashboard`.

Do not use the old Railway dashboard URL after this migration.
