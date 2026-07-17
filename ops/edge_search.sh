#!/bin/bash
# Autonomous edge search: run one batch of strategy hypotheses through the honest
# gate methodology, persist results, flag gate-passers. State lives in
# edge_search_results, so each invocation resumes and makes progress. Runs on a
# StartInterval via com.altcoin.edgesearch.plist. Read-only research: it NEVER
# touches the live book (only writes edge_search_results + the perp cache).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY=${PY:-"$REPO/.venv/bin/python"}
BATCH=${EDGE_SEARCH_BATCH:-8}

echo "=== edge_search $(date -u +%FT%TZ) ==="
$PY scripts/run_edge_search.py --batch "$BATCH" || echo "WARN: edge_search batch failed"
echo "=== done $(date -u +%FT%TZ) ==="
