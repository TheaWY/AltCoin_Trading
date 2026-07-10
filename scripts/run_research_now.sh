#!/bin/bash
# Manually trigger the same research/test cycle used by launchd.
# Run from repo root:
#   bash scripts/run_research_now.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi
mkdir -p data/logs
bash ops/nightly_research.sh 2>&1 | tee -a data/logs/research.manual.log
