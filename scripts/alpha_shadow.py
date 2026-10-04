#!/usr/bin/env python3
"""Hourly: make today's alpha-candidate picks (once) and settle finished ones.
See src/engine/alpha_shadow.py."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.engine import alpha_shadow  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    logging.info("alpha shadow: %s", alpha_shadow.run(get_storage()))
