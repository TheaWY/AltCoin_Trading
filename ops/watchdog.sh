#!/bin/bash
# Restart the worker when the promotion engine touches data/restart.flag.
# Run from launchd every minute or as a loop. Also runs the hourly health check.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=${PY:-"$REPO/.venv/bin/python"}
FLAG="data/restart.flag"
if [ -f "$FLAG" ]; then
  echo "restart flag found -> kickstarting worker ($(date -u +%FT%TZ))"
  rm -f "$FLAG"
  launchctl kickstart -k "gui/$(id -u)/com.altcoin.worker"
fi
# hourly-ish health check (cheap; safe to run every minute, promotion has cooldowns)
$PY -m src.research.promotion health > /dev/null 2>&1 || true

# --- disk guard (added 2026-09-30 after an 18GB runaway error log filled the disk) ---
# Every minute: any single log over 1GB is archived (gzip) and emptied at once by
# rotate_logs.sh; below 10GB free the rotation threshold drops to 20MB; below 3GB
# free the tick recorders (the fastest-growing writers) are paused and a flag is left
# for the dashboard/status message. Nothing outside data/logs is ever touched.
LOGS="data/logs"
free_gb=$(df -g "$REPO" | awk 'NR==2 {print $4}')
big=$(find "$LOGS" -maxdepth 1 -name '*.log' -size +1024M 2>/dev/null | head -1)
if [ -n "$big" ]; then
  echo "disk guard: oversized log $big -> rotating now ($(date -u +%FT%TZ))"
  LOG_ROTATE_MAX_MB=200 bash ops/rotate_logs.sh
fi
if [ "${free_gb:-99}" -lt 10 ]; then
  echo "disk guard: ${free_gb}GB free -> aggressive rotation ($(date -u +%FT%TZ))"
  LOG_ROTATE_MAX_MB=20 LOG_ROTATE_KEEP=2 bash ops/rotate_logs.sh
fi
if [ "${free_gb:-99}" -lt 3 ]; then
  if [ ! -f data/disk_alert.flag ]; then
    echo "disk guard: ${free_gb}GB free -> pausing tick recorders ($(date -u +%FT%TZ))"
    for svc in com.altcoin.koreaticks com.altcoin.tickbars; do
      launchctl bootout "gui/$(id -u)/$svc" 2>/dev/null || true
    done
  fi
  echo "free_gb=${free_gb} at $(date -u +%FT%TZ)" > data/disk_alert.flag
elif [ -f data/disk_alert.flag ] && [ "${free_gb:-0}" -ge 10 ]; then
  echo "disk guard: recovered (${free_gb}GB free) -> resuming tick recorders"
  for svc in com.altcoin.koreaticks com.altcoin.tickbars; do
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$svc.plist" 2>/dev/null || true
  done
  mv -f data/disk_alert.flag "data/disk_alert.cleared.flag"
fi
