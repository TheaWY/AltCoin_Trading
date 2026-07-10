# Mac mini ops quick reference

## Start / install services

```bash
bash ops/install_macmini_server.sh
```

Installs:

- `com.altcoin.dashboard` — FastAPI on `127.0.0.1:8000`
- `com.altcoin.worker` — collection + paper trading cycle
- `com.altcoin.category` — market categories every 30 minutes
- `com.altcoin.research` — nightly research
- `com.altcoin.watchdog` — watchdog

Optional:

```bash
INSTALL_TICKBARS=true bash ops/install_macmini_server.sh
```

## Tailscale dashboard

```bash
bash ops/serve_tailscale.sh
tailscale serve status
```

## Status

```bash
launchctl list | grep altcoin
python scripts/macmini_status.py
```

## Logs

```bash
tail -f data/logs/dashboard.err.log
tail -f data/logs/worker.err.log
tail -f data/logs/category.err.log
tail -f data/logs/research.err.log
tail -f data/logs/watchdog.err.log
tail -f data/logs/tickbars.err.log
```

## Restart one service

```bash
launchctl kickstart -k gui/$(id -u)/com.altcoin.dashboard
launchctl kickstart -k gui/$(id -u)/com.altcoin.worker
launchctl kickstart -k gui/$(id -u)/com.altcoin.category
```

## Stop all

```bash
for P in com.altcoin.worker com.altcoin.research com.altcoin.watchdog com.altcoin.dashboard com.altcoin.category com.altcoin.tickbars; do
  launchctl unload "$HOME/Library/LaunchAgents/$P.plist" 2>/dev/null || true
done
```
