"""Tests for the robust/self-correcting layer: statistics, DSR gate,
decision log, and the research API endpoints.

    python scripts/test_robust_layer.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_PATH"] = str(_tmp / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["DATA_DIR"] = str(_tmp)
os.environ["PROMO_S2_MIN_FRESH_DAYS"] = "14"

NOW = int(time.time())
DAY = 86_400
passed = 0


def check(name, cond, detail=""):
    global passed
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)
    passed += 1


# ------------------------------------------------------------- robust_stats
from src.research.robust_stats import (  # noqa: E402
    correlation_matrix, deflated_sharpe, effective_breadth,
    max_trials_for_history, pbo_lite, sharpe)

rng = random.Random(1)
good = [rng.gauss(0.002, 0.01) for _ in range(300)]     # real edge
noise = [rng.gauss(0.0, 0.01) for _ in range(300)]      # no edge

d_few = deflated_sharpe(good, n_trials=5)
d_many = deflated_sharpe(good, n_trials=5000)
check("DSR falls as trials N grows", d_many["dsr"] < d_few["dsr"],
      f"N=5:{d_few['dsr']} N=5000:{d_many['dsr']}")
d_noise = deflated_sharpe(noise, n_trials=500)
check("DSR rejects pure noise at high N", d_noise["dsr"] < 0.95, str(d_noise["dsr"]))
check("sharpe sign sanity", sharpe(good) > 0 and abs(sharpe(noise)) < 0.15)

b_small = max_trials_for_history(1.0)
b_paper = max_trials_for_history(5.0)
b_large = max_trials_for_history(6.0)
check("trial budget grows with history and matches paper anchor",
      b_small < b_paper < b_large and 40 <= b_paper <= 50,
      f"1y:{b_small} 5y:{b_paper} 6y:{b_large}")

# pbo: overfit universe = window pnls are iid noise -> IS winner ~ random -> PBO ~ 0.5
overfit_universe = {f"c{i}": [rng.gauss(0, 1) for _ in range(6)] for i in range(30)}
p1 = pbo_lite(overfit_universe)
# skilled universe = one config truly better in every window
skilled = {f"c{i}": [rng.gauss(0, 1) for _ in range(6)] for i in range(30)}
skilled["hero"] = [3.0 + rng.gauss(0, 0.2) for _ in range(6)]
p2 = pbo_lite(skilled)
check("PBO high for noise, lower with true skill", p1["pbo"] > p2["pbo"],
      f"noise:{p1['pbo']} skilled:{p2['pbo']}")

m = correlation_matrix({
    "a": {"2026-01-01": 1, "2026-01-02": -1, "2026-01-03": 2, "2026-01-04": -2, "2026-01-05": 1},
    "b": {"2026-01-01": 1, "2026-01-02": -1, "2026-01-03": 2, "2026-01-04": -2, "2026-01-05": 1},
    "c": {"2026-01-01": -1, "2026-01-02": 1, "2026-01-03": -2, "2026-01-04": 2, "2026-01-05": -1},
})
check("correlation matrix identifies clones and inverses",
      m["a"]["b"] > 0.99 and m["a"]["c"] < -0.99, f"ab={m['a']['b']} ac={m['a']['c']}")
eb = effective_breadth({"a": {"a": 1, "b": 0.5}, "b": {"a": 0.5, "b": 1}})
check("effective breadth shrinks under correlation",
      eb["effective_breadth"] == 1.33, str(eb))

# -------------------------------------------------- promotion with DSR gate
from src.data.storage import get_storage  # noqa: E402
from src.research import promotion  # noqa: E402

promotion._ensure_schema()


def metrics_with_pnls(window_pnls, per_window_trades, seed=7, edge=0.9):
    r = random.Random(seed)
    windows = []
    for wp in window_pnls:
        pnls = [r.gauss(wp / per_window_trades, abs(wp) / per_window_trades * edge or 1)
                for _ in range(per_window_trades)]
        windows.append({"total_pnl": wp, "trade_pnls": [round(x, 4) for x in pnls]})
    trades = per_window_trades * len(window_pnls)
    total = sum(window_pnls)
    return json.dumps({
        "windows": windows,
        "aggregate": {"trade_count": trades, "expectancy": total / trades,
                      "profit_factor": 1.6, "positive_windows":
                      sum(1 for w in window_pnls if w > 0),
                      "window_count": len(window_pnls)},
    })


def insert_exp(h, cfg, m, champion=False, created=NOW - 20 * DAY):
    with get_storage()._connect() as conn:
        conn.execute(
            "INSERT INTO experiments (config_hash, config_json, status, "
            "is_champion_baseline, created_at, finished_at, metrics_json) "
            "VALUES (?, ?, 'done', ?, ?, ?, ?)",
            (h, json.dumps(cfg), 1 if champion else 0, created, NOW, m))


insert_exp("champ000", {"MEANREV_RSI_HIGH": 70},
           metrics_with_pnls([10, 5, -2, 8], 15, edge=2.0), champion=True)
# strong consistent challenger with real per-trade edge
insert_exp("winner00", {"MEANREV_RSI_HIGH": 68, "CONFLUENCE_ALIGNED_BONUS": 0.05},
           metrics_with_pnls([30, 24, 18, 22], 20, seed=3, edge=0.5))
with get_storage()._connect() as conn:
    conn.execute(
        "INSERT INTO fresh_evals (config_hash, eval_start, eval_end, trade_count, "
        "expectancy, profit_factor, total_pnl, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("winner00", NOW - 16 * DAY, NOW - 1 * DAY, 12, 2.0, 1.5, 24.0, NOW))

result = promotion.promote_if_ready()
check("DSR-gated promotion still promotes a real edge",
      result.get("promoted") is True, json.dumps(result.get("blocked", result))[:120])

# a lucky-noise challenger must be blocked by the DSR gate specifically
os.environ["PROMO_COOLDOWN_HOURS"] = "0"
import importlib  # noqa: E402
importlib.reload(promotion)
lucky_windows = [4, 3, 5, 2]
r = random.Random(11)
windows = [{"total_pnl": wp,
            "trade_pnls": [round(r.gauss(0.0, 8.0), 4) for _ in range(20)]}
           for wp in lucky_windows]
lucky_metrics = json.dumps({
    "windows": windows,
    "aggregate": {"trade_count": 80, "expectancy": 0.9, "profit_factor": 1.5,
                  "positive_windows": 4, "window_count": 4}})
insert_exp("lucky000", {"MEANREV_RSI_HIGH": 74}, lucky_metrics)
# inflate trial count so the luck hurdle is high
with get_storage()._connect() as conn:
    for i in range(300):
        conn.execute(
            "INSERT INTO experiments (config_hash, config_json, status, created_at) "
            "VALUES (?, '{}', 'failed', ?)", (f"junk{i:04d}", NOW))
result2 = promotion.promote_if_ready()
blocked = {b["hash"]: b for b in result2.get("blocked", [])}
check("noise challenger blocked at DSR gate",
      not result2.get("promoted") and blocked.get("lucky000", {}).get("stage") == "dsr",
      str(blocked.get("lucky000", {}).get("why", ""))[:80])

# ------------------------------------------------------------ decisions log
from src.research import decisions  # noqa: E402

feed = decisions.recent(limit=20)
actions = {d["action"] for d in feed}
check("decision feed captured gate events and promotion",
      "promoted" in actions and "gate_dsr_blocked" in actions, str(sorted(actions)))

# ------------------------------------------------------------------ API
from fastapi.testclient import TestClient  # noqa: E402
from src.api.main import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)
for path in ("/api/research/summary", "/api/research/decisions",
             "/api/research/experiments", "/api/research/event-study",
             "/api/research/promotions", "/api/research/correlation",
             "/api/research/overfit"):
    resp = client.get(path)
    check(f"GET {path}", resp.status_code == 200, f"status={resp.status_code}")
resp = client.get("/experiments")
check("GET /experiments page serves html",
      resp.status_code == 200 and "결정 피드" in resp.text)

print(f"\nAll {passed} checks passed.")
