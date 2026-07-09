#!/bin/bash
# Restart the worker when the promotion engine touches data/restart.flag.
# Run from launchd every minute or as a loop. Also runs the hourly health check.
set -uo pipefail
cd "$(dirname "$0")/.."
FLAG="data/restart.flag"
if [ -f "$FLAG" ]; then
  echo "restart flag found -> kickstarting worker ($(date -u +%FT%TZ))"
  rm -f "$FLAG"
  launchctl kickstart -k "gui/$(id -u)/com.altcoin.worker"
fi
# hourly-ish health check (cheap; safe to run every minute, promotion has cooldowns)
python3 -m src.research.promotion health > /dev/null 2>&1 || true
