#!/bin/bash
# Recommended first serious historical dataset for strategy backtesting.
# Run from repo root on the Mac mini after local Postgres is working:
#   bash scripts/backfill_core_history.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi

echo "Backfilling top 50 crypto perps from 2022-01-01 with 1h candles + funding."
echo "This can take a while. It is resumable/idempotent."
python scripts/backfill_history.py --limit 50 --start 2022-01-01 --timeframes 1h --funding

echo "Refreshing categories after history backfill."
python scripts/update_market_categories.py --once --limit 0 || true

echo "Status after backfill:"
python scripts/macmini_status.py || true
