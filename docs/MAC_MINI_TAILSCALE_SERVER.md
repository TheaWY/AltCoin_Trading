# Mac mini + Tailscale server mode

This project should run as a private Mac mini research server, not as a Railway-centered app.

## Target architecture

```text
Mac mini
├── local Postgres: candles, funding, OI, tick bars, paper trades, experiments
├── FastAPI dashboard: http://127.0.0.1:8000/dashboard
├── worker: normal candle/funding/OI collection + paper trading cycle
├── category worker: dynamic market categories every 30 minutes
├── optional tick-bar worker: 1s/5s bars for top liquid symbols
├── nightly research: generate → run → promote/rollback → data-quality scan
└── Tailscale Serve: private HTTPS dashboard inside your tailnet
```

Railway is no longer required for dashboard or database. Your Mac mini becomes the server hub.

## What to implement / run

### 1. Pull this branch on the Mac mini

```bash
cd /Users/pc/Projects/AltCoin_Trading
git fetch origin
git switch macmini-tailscale-server
git pull origin macmini-tailscale-server
source .venv/bin/activate
```

### 2. Install local Postgres

```bash
brew install postgresql@16
brew services start postgresql@16
```

Then run the helper:

```bash
bash scripts/setup_macmini_postgres.sh
```

It creates:

```text
DB:   altcoin_trading
User: altcoin
URL:  postgresql://altcoin:<password>@localhost:5432/altcoin_trading
```

### 3. Update `.env`

Copy the Mac-mini example:

```bash
cp .env.macmini.example .env
open -e .env
```

Set the generated local DB URL:

```env
DATABASE_URL=postgresql://altcoin:<password>@localhost:5432/altcoin_trading
```

Remove the old Railway URL. If `.env` still contains `railway`, `proxy.rlwy.net`, or `up.railway.app`, the app is not fully migrated.

### 4. Verify local DB mode

```bash
python scripts/macmini_status.py
```

You want:

```text
Storage: Postgres
Railway URL detected: no
Database query: ok
```

### 5. Bootstrap local data

Start fresh on local Postgres. This is usually cleaner than migrating Railway data.

```bash
python scripts/bootstrap_all_candles.py --limit 0 --candles 720
python scripts/refresh_all_metrics.py --limit 0 --market-metrics-limit 100
python scripts/update_market_categories.py --once --limit 0
python scripts/reset_paper_portfolio.py --krw 1000000 --krw-per-usdt 1400 --force
```

### 6. Run dashboard locally

Manual test:

```bash
uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://localhost:8000/dashboard
```

### 7. Serve privately through Tailscale

After the dashboard works locally:

```bash
bash ops/serve_tailscale.sh
```

Then check:

```bash
tailscale serve status
```

Open the Tailscale HTTPS URL from your phone/laptop while signed into the same tailnet.

### 8. Install launchd services

```bash
bash ops/install_macmini_server.sh
```

This installs and starts:

```text
com.altcoin.dashboard   FastAPI dashboard on 127.0.0.1:8000
com.altcoin.worker      candle/funding/OI collection + paper cycle
com.altcoin.category    market category refresh every 30 minutes
com.altcoin.research    nightly research batch
com.altcoin.watchdog    health/watchdog loop
```

Tick bars are heavier. Install them only after the basic server is stable:

```bash
INSTALL_TICKBARS=true bash ops/install_macmini_server.sh
```

The tick-bar worker defaults to top 50 symbols, 1-second bars, batch flush every 5 seconds.

## Daily commands

Check services:

```bash
launchctl list | grep altcoin
python scripts/macmini_status.py
```

Tail logs:

```bash
tail -f data/logs/dashboard.err.log
tail -f data/logs/worker.err.log
tail -f data/logs/category.err.log
tail -f data/logs/research.err.log
```

Restart one service:

```bash
launchctl kickstart -k gui/$(id -u)/com.altcoin.dashboard
launchctl kickstart -k gui/$(id -u)/com.altcoin.worker
launchctl kickstart -k gui/$(id -u)/com.altcoin.category
```

Stop all services:

```bash
for P in com.altcoin.worker com.altcoin.research com.altcoin.watchdog com.altcoin.dashboard com.altcoin.category com.altcoin.tickbars; do
  launchctl unload "$HOME/Library/LaunchAgents/$P.plist" 2>/dev/null || true
done
```

## Important design choices

### Keep Postgres local-only

Use:

```env
DATABASE_URL=postgresql://altcoin:<password>@localhost:5432/altcoin_trading
```

Do not expose Postgres publicly. Your dashboard/API reads the DB. Your phone should only access the dashboard through Tailscale.

### Do not store raw ticks for all 526 symbols

Use 1s/5s tick bars for top 30–50 liquid symbols first. Raw ticks for all markets can overwhelm your local DB and make research slower.

### Category filter should be evidence-gated

Market categories update every 30 minutes and are visible in Home/Experiments. Do not trust categories as a trading edge until `scripts/backtest_categories.py` shows that category membership/transitions predict forward returns after fees.

## Migration from Railway

Recommended: do not migrate old Railway rows unless you need old paper trades/experiments. Rebootstrap local data and start a clean paper account.

If you do need old rows later, export Railway Postgres with `pg_dump` and restore into local Postgres. Do not mix partial old state with new local state unless you intentionally audit it.
