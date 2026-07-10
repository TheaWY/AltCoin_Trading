# Mac mini command cheat sheet

```bash
cd /Users/pc/Projects/AltCoin_Trading
source .venv/bin/activate
```

## First setup

```bash
git fetch origin
git switch macmini-tailscale-server
git pull origin macmini-tailscale-server
bash scripts/setup_macmini_postgres.sh
cp .env.macmini.example .env
open -e .env
bash scripts/bootstrap_macmini_local.sh
bash ops/install_macmini_server.sh
bash ops/serve_tailscale.sh
python scripts/macmini_status.py
```

## Regular checks

```bash
python scripts/macmini_status.py
launchctl list | grep altcoin
tailscale serve status
```

## Manual data refresh

```bash
python scripts/bootstrap_all_candles.py --limit 0 --candles 720
python scripts/refresh_all_metrics.py --limit 0 --market-metrics-limit 100
python scripts/update_market_categories.py --once --limit 0
```

## Optional tick bars

```bash
python scripts/collect_tick_bars.py --limit 50 --bucket-seconds 1 --flush-seconds 5
```

or permanent launchd:

```bash
INSTALL_TICKBARS=true bash ops/install_macmini_server.sh
```
