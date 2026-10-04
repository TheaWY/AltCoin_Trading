#!/usr/bin/env python3
"""Hourly: record +/-10% hours with their pre-move features, score/settle model picks.
See src/engine/pump_watch.py."""

from __future__ import annotations

import logging
import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
warnings.filterwarnings("ignore")

from src.data.storage import get_storage  # noqa: E402
from src.engine import pump_watch  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    logging.info("pump watch: %s", pump_watch.run(get_storage()))
