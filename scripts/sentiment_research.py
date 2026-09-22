#!/usr/bin/env python3
"""Background sentiment research job (launchd: com.altcoin.sentiment, every 6h).

Loads the sentiment panel, answers the question grid plus follow-ups, shifts
the live weights by at most sentiment_lab.MAX_WEIGHT_SHIFT, re-validates the
composite out of sample, and writes:

  system_status["sentiment_weights"]           read by the live gate
  data/reports/sentiment/latest.md|json        human / dashboard report
  data/reports/sentiment/runs/<ts>.json        history of every run
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.engine import sentiment_gate as gate  # noqa: E402
from src.research import sentiment_lab as lab  # noqa: E402

LOOKBACK_DAYS = 180

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sentiment_research")


def main() -> int:
    storage = get_storage()
    since = int(time.time()) - LOOKBACK_DAYS * 86400
    panel = gate.load_panel(storage, since)
    log.info("panel: %d symbols x %d hours", panel["close"].shape[1], panel["close"].shape[0])
    if panel["close"].empty:
        log.warning("empty panel; nothing to research")
        return 0
    prev = gate.load_state(storage).get("weights") or {}
    result = lab.run_research(panel, prev_weights=prev, log=log.info)
    gate.save_state(storage, result)

    out = PROJECT_ROOT / "data" / "reports" / "sentiment"
    (out / "runs").mkdir(parents=True, exist_ok=True)
    payload = lab.to_json(result)
    (out / "runs" / f"{result['run_at']}.json").write_text(payload)
    (out / "latest.json").write_text(payload)
    (out / "latest.md").write_text(lab.render_markdown(result))

    sup = [q for q in result["questions"] if q["verdict"] == "supported"]
    log.info("answered %d questions + %d follow-ups; %d supported; weights %s; gate %s (%s)",
             len(result["questions"]), len(result["follow_ups"]), len(sup), result["weights"],
             "ENFORCE" if result["enforce_ok"] else "SHADOW", result["oos"].get("reason"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
