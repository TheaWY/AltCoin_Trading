#!/bin/bash
# Local pre-push safety tests. Run before using new strategy code.
# These tests are intentionally network-free and DB-free.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi
# Hermetic: config reads env at import time, so without this the machine's
# .env decides what the suite tests. See src/config.py SKIP_DOTENV.
export CONFIG_SKIP_DOTENV=1
python -m unittest discover -s tests -p 'test_*.py' -v
