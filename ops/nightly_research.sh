#!/bin/bash
# Nightly research batch: generate -> run -> fresh evals -> promotion gate -> gap scan -> backup
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=${PY:-"$REPO/.venv/bin/python"}
echo "=== nightly research $(date -u +%FT%TZ) ==="
$PY -m src.research.generator
$PY -m src.research.runner --max-runs "${RESEARCH_MAX_RUNS_PER_NIGHT:-200}"
$PY -m src.research.promotion promote-if-ready
$PY -m src.research.data_quality --scan
bash ops/backup.sh
echo "=== done $(date -u +%FT%TZ) ==="
