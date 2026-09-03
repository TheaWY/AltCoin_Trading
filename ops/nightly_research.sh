#!/bin/bash
# Research batch: generate -> run -> category tests -> promotion gate -> gap scan -> backup
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
# launchd (and some interactive shells) can inherit PY=/usr/bin/python3 or the
# Command Line Tools interpreter. Those lack the venv packages (yaml, dotenv,
# psycopg) and silently skip generate/run/promote. Always use the repo venv.
PY="$REPO/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "ERROR: repo venv python missing: $PY" >&2
  exit 1
fi
MAX_RUNS=${RESEARCH_MAX_RUNS_PER_NIGHT:-20}
CATEGORY_DAYS=${CATEGORY_BACKTEST_DAYS:-365}

echo "=== research cycle $(date -u +%FT%TZ) ==="
echo "repo=$REPO"
echo "python=$PY"
"$PY" -c 'import sys; print("python_version", sys.version.split()[0], "executable", sys.executable)'
echo "max_runs=$MAX_RUNS category_days=$CATEGORY_DAYS"

# Initialize/queue experiments. This creates the experiments schema if missing.
$PY -m src.research.generator || echo "WARN: generator failed"

# Consume a bounded number of queued experiments. Keep this low on Mac mini until
# the historical dataset is large enough and the timing is understood.
$PY -m src.research.runner --max-runs "$MAX_RUNS" || echo "WARN: runner failed"

# Category movement research: do category/type changes actually predict returns?
# This writes JSON reports to data/ and makes the run visible in logs.
$PY scripts/backtest_categories.py --days "$CATEGORY_DAYS" --horizons 1,4,24 || echo "WARN: category backtest failed"

# Promotion remains gated by robust statistics/fresh evals. It should not blindly
# promote just because one backtest looked good.
$PY -m src.research.promotion promote-if-ready || echo "WARN: promotion gate failed"
$PY -m src.research.data_quality --scan || echo "WARN: data quality scan failed"

bash ops/backup.sh || echo "WARN: backup failed"
echo "=== done $(date -u +%FT%TZ) ==="
