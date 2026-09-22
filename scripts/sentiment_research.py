#!/usr/bin/env python3
"""Background hypothesis research (launchd: com.altcoin.sentiment, every 2h).

Each run forms a fresh batch of null hypotheses (src/research/
hypothesis_engine.py), tests them, applies one Benjamini-Hochberg pass over
every p-value ever recorded, shifts the live sentiment weights by at most
MAX_WEIGHT_SHIFT, and re-validates the composite out of sample. Writes:

  system_status["hypothesis_registry"]   every H0 ever tested + its answer
  system_status["sentiment_weights"]     read by the live sentiment gate
  data/reports/hypotheses/latest.md|json and runs/<ts>.json
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.engine import sentiment_gate as gate  # noqa: E402
from src.research import hypothesis_engine as engine  # noqa: E402

LOOKBACK_DAYS = 180

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("hypothesis_research")


def main() -> int:
    storage = get_storage()
    panel = gate.load_panel(storage, int(time.time()) - LOOKBACK_DAYS * 86400)
    log.info("panel: %d symbols x %d hours", panel["close"].shape[1], panel["close"].shape[0])
    if panel["close"].empty:
        log.warning("empty panel; nothing to research")
        return 0
    registry = engine.load_registry(storage)
    prev = gate.load_state(storage).get("weights") or {}
    result = engine.run(panel, registry, prev_weights=prev, log=log.info)
    engine.save_registry(storage, registry)
    gate.save_state(storage, result)

    out = PROJECT_ROOT / "data" / "reports" / "hypotheses"
    (out / "runs").mkdir(parents=True, exist_ok=True)
    payload = json.dumps({k: v for k, v in result.items() if k != "supported"} |
                         {"supported": result["supported"]}, default=float, ensure_ascii=False)
    (out / "runs" / f"{result['run_at']}.json").write_text(payload)
    (out / "latest.json").write_text(payload)
    (out / "latest.md").write_text(engine.render_markdown(result))
    log.info("run done: %d tested, %d/%d explored, %d supported, weights %s, gate %s (%s)",
             len(result["tested"]), result["registry_size"], result["space_size"],
             len(result["supported"]), result["weights"],
             "ENFORCE" if result["enforce_ok"] else "SHADOW", result["oos"].get("reason"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
