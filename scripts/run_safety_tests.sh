#!/bin/bash
# Local pre-push safety tests. Run before using new strategy code.
# These tests are intentionally network-free and DB-free.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi
python -m unittest discover -s tests -p 'test_*.py' -v
