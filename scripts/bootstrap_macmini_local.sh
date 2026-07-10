#!/bin/bash
# One-shot local bootstrap after DATABASE_URL points to local Postgres.
# Run from repo root: bash scripts/bootstrap_macmini_local.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi

echo "Checking Mac mini status before bootstrap..."
python scripts/macmini_status.py || true

echo "Bootstrapping all crypto 1h candles into local Postgres..."
python scripts/bootstrap_all_candles.py --limit 0 --candles 720

echo "Refreshing funding/OI/long-short metrics..."
python scripts/refresh_all_metrics.py --limit 0 --market-metrics-limit 100

echo "Creating first market category snapshot..."
python scripts/update_market_categories.py --once --limit 0

echo "Resetting paper portfolio to KRW 1,000,000 equivalent..."
python scripts/reset_paper_portfolio.py --krw 1000000 --krw-per-usdt 1400 --force

echo "Final status..."
python scripts/macmini_status.py || true

echo "Done. Start dashboard with: uvicorn src.api.main:app --host 127.0.0.1 --port 8000"
