# Mac mini implementation checklist

Use this checklist when moving away from Railway.

## GitHub/repo changes in this branch

- [x] Add `.env.macmini.example` for local Postgres + Tailscale mode.
- [x] Add local Postgres setup helper: `scripts/setup_macmini_postgres.sh`.
- [x] Add local health/status checker: `scripts/macmini_status.py`.
- [x] Add one-shot bootstrap: `scripts/bootstrap_macmini_local.sh`.
- [x] Bind dashboard LaunchAgent to `127.0.0.1:8000` instead of `0.0.0.0`.
- [x] Add Tailscale Serve helper: `ops/serve_tailscale.sh`.
- [x] Add category refresh LaunchAgent: `ops/com.altcoin.category.plist`.
- [x] Add optional tick-bar LaunchAgent: `ops/com.altcoin.tickbars.plist`.
- [x] Update `ops/install.sh` to install dashboard/worker/research/watchdog/category.
- [x] Add full guide: `docs/MAC_MINI_TAILSCALE_SERVER.md`.

## Manual Mac mini steps

1. Pull branch:

   ```bash
   cd /Users/pc/Projects/AltCoin_Trading
   git fetch origin
   git switch macmini-tailscale-server
   git pull origin macmini-tailscale-server
   ```

2. Create local Postgres:

   ```bash
   bash scripts/setup_macmini_postgres.sh
   ```

3. Configure `.env`:

   ```bash
   cp .env.macmini.example .env
   open -e .env
   ```

   Replace `DATABASE_URL` with the generated local Postgres URL.

4. Bootstrap local database:

   ```bash
   source .venv/bin/activate
   bash scripts/bootstrap_macmini_local.sh
   ```

5. Start services:

   ```bash
   bash ops/install_macmini_server.sh
   ```

6. Start private dashboard URL through Tailscale:

   ```bash
   bash ops/serve_tailscale.sh
   tailscale serve status
   ```

7. Verify:

   ```bash
   python scripts/macmini_status.py
   launchctl list | grep altcoin
   ```

## Optional tick-bar service

Only install after the regular server is stable:

```bash
INSTALL_TICKBARS=true bash ops/install_macmini_server.sh
```

This starts `scripts/collect_tick_bars.py --limit 50 --bucket-seconds 1 --flush-seconds 5`.

## Rollback

Stop launchd services:

```bash
for P in com.altcoin.worker com.altcoin.research com.altcoin.watchdog com.altcoin.dashboard com.altcoin.category com.altcoin.tickbars; do
  launchctl unload "$HOME/Library/LaunchAgents/$P.plist" 2>/dev/null || true
done
```

Stop Tailscale Serve:

```bash
tailscale serve reset
```
