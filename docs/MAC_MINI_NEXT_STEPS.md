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

## Then run research/tests now

The launchd research agent runs at 01:00 and also once when loaded. To trigger the same test cycle manually:

```bash
bash scripts/run_research_now.sh
```

This runs:

```text
research generator -> experiment runner -> category backtest -> promotion gate -> data-quality scan -> backup
```

Watch logs:

```bash
tail -f data/logs/research.manual.log
tail -f data/logs/research.out.log
tail -f data/logs/research.err.log
```

Restart the scheduled research service after pulling updates:

```bash
bash ops/install_macmini_server.sh
launchctl kickstart -k gui/$(id -u)/com.altcoin.research
```

## After backfill

```bash
python scripts/macmini_status.py
python scripts/backtest_fast.py --strategy mean_reversion --symbols BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT --start 2023-01-01
python scripts/backtest_categories.py --days 365 --horizons 1,4,24
```

Do not enable serious autonomous parameter promotion until this deeper history exists.

## UI note

The Home bottom nav should stay inside `/dashboard` now:

```text
홈 -> top of Home
포트폴리오 -> portfolio section on Home
전략 -> strategy section on Home
실험 -> /experiments
```

It should no longer jump to `/dashboard/full` when you press 포트폴리오.

## Previous setup checklist

1. Pull `macmini-tailscale-server` on the Mac mini.
2. Run `bash scripts/setup_macmini_postgres.sh`.
3. Copy `.env.macmini.example` to `.env` and paste the generated local `DATABASE_URL`.
4. Run `bash scripts/bootstrap_macmini_local.sh`.
5. Run `bash ops/install_macmini_server.sh`.
6. Run `bash ops/serve_tailscale.sh`.
7. Open the Tailscale URL at `/dashboard`.

Do not use the old Railway dashboard URL after this migration.
