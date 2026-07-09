#!/bin/bash
# Daily DB snapshot: local + iCloud Drive. sqlite only (Railway postgres has its own backups).
set -uo pipefail
cd "$(dirname "$0")/.."
DB="${DATABASE_PATH:-data/trading.db}"
[ -f "$DB" ] || { echo "no sqlite db at $DB (postgres mode?) — skipping"; exit 0; }
STAMP=$(date -u +%Y%m%d)
mkdir -p data/backups
sqlite3 "$DB" ".backup data/backups/trading_${STAMP}.db"
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs/AltCoinBackups"
mkdir -p "$ICLOUD" && cp "data/backups/trading_${STAMP}.db" "$ICLOUD/" 2>/dev/null || true
ls -t data/backups/trading_*.db | tail -n +15 | xargs rm -f 2>/dev/null || true  # keep 14
echo "backup done: trading_${STAMP}.db"
