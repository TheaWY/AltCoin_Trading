"""Entry point — starts scheduler + FastAPI."""

from __future__ import annotations

import logging
import sys

import uvicorn

from src import config
from src.api.main import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def main() -> None:
    local_url = f"http://localhost:{config.API_PORT}/dashboard"
    logger.info("Local dashboard: %s", local_url)
    logger.info("Ngrok will start after the API is listening (if NGROK_ENABLED=true)")

    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_level="info")


if __name__ == "__main__":
    main()
