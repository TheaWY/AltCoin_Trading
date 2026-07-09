# Deployment — Mac Mini worker + Railway dashboard (Option B)

Binance REST/fapi returns **HTTP 451** from Railway's US servers. Anything that
calls Binance (data collection, paper trading, research backtests on fresh data)
**must run on the Mac Mini** with a Korean residential IP.

Railway hosts the **dashboard only** (and optionally Postgres). The Mac Mini
runs the worker, nightly research, and watchdog.

## Architecture

```
┌─────────────────────────┐         ┌──────────────────────────┐
│  Mac Mini (Korea IP)    │         │  Railway (US)            │
│                         │         │                          │
│  com.altcoin.worker     │  write  │  Postgres (DATABASE_URL) │
│  com.altcoin.research   │ ──────► │                          │
│  com.altcoin.watchdog   │         │  Web service (dashboard) │
│                         │  read   │  RUN_TRADING_SCHEDULER=0 │
│  config_overrides.json  │ ◄────── │  No Binance API calls    │
│  restart.flag           │         │                          │
└─────────────────────────┘         └──────────────────────────┘
```

## One-time Railway setup

1. **Add Postgres** — Railway project → New → Database → PostgreSQL.
2. **Attach `DATABASE_URL`** to the web service (Railway usually does this
   automatically when Postgres is in the same project).
3. **Set web service variables:**

   ```
   DEPLOYMENT_MODE=cloud
   NGROK_ENABLED=false
   RUN_TRADING_SCHEDULER=false
   ```

   Do **not** set `BINANCE_API_KEY` on Railway unless you enjoy 451 errors in
   the logs. The scheduler is off by default on cloud hosts anyway.

4. **Start command** (already in `railway.json`):

   ```
   bash scripts/start-web.sh
   ```

5. **Public networking** — target port **8080** (Railway sets `PORT=8080`).

6. Open `https://YOUR-APP.up.railway.app/dashboard`.

## One-time Mac Mini setup

From the repo root:

```bash
bash ops/setup-mac.sh          # venv + deps + launchd plists
cp .env.mac.example .env       # then edit: Binance keys + DATABASE_URL
bash ops/install.sh            # load launchd services (needs sudo for pmset)
```

Verify:

```bash
launchctl list | grep altcoin
python -m src.research.promotion status
python -m src.research.data_quality
tail -f data/logs/worker.out.log
```

### Phone access (optional)

**Cloudflare quick tunnel** (free, no account):

```bash
bash ops/cloudflare-tunnel.sh
```

**Ngrok** (if you prefer the built-in integration):

```bash
# in .env
NGROK_ENABLED=true
NGROK_USE_CLI=true
```

## What runs where

| Component | Mac Mini | Railway |
|-----------|----------|---------|
| Data collection | ✓ | ✗ (451) |
| Paper trading | ✓ | ✗ |
| Nightly research | ✓ | ✗ |
| Promotion / rollback files | ✓ | ✗ |
| Dashboard UI | optional local | ✓ |
| Postgres | writes via `DATABASE_URL` | hosts |

## Troubleshooting

### Railway logs show `451` / `Collection failed`

The web service is still trying to collect. Confirm:

- `RUN_TRADING_SCHEDULER=false` in Railway variables, **or**
- Redeploy after pulling the latest `start-web.sh` / `config.py` (cloud hosts
  default the scheduler off).

### Dashboard empty

1. Mac worker running? `launchctl list | grep com.altcoin.worker`
2. Same `DATABASE_URL` on Mac `.env` and Railway Postgres?
3. Worker logs: `data/logs/worker.err.log`

### Promotion not taking effect

Promotion writes `data/config_overrides.json` and touches `data/restart.flag`.
The watchdog restarts the worker. Both files live on the Mac filesystem — this
is why the worker cannot move to Railway under Option B.
