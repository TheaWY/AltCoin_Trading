#!/usr/bin/env python3
"""Every-5-minute job (launchd: com.altcoin.indicators): latest value of every
indicator in src/research/indicators.py for every coin -> coin_snapshot."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research import indicators  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    logging.info("snapshot: %s", indicators.snapshot(get_storage()))
