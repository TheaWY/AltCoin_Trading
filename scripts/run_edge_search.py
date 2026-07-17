"""Run one batch of the autonomous edge search, then exit (launchd-friendly:
state lives in edge_search_results, so each invocation resumes and makes
progress). Prints any gate-passing edges and a running leaderboard.

    python scripts/run_edge_search.py --batch 8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=8, help="configs to test this run")
    ap.add_argument("--leaderboard", type=int, default=10, help="top-N to print")
    args = ap.parse_args()

    from src.data.storage import get_storage
    from src.research import edge_search

    storage = get_storage()
    edge_search.ensure_schema(storage)
    done_before = edge_search.trial_count(storage)
    total = len(edge_search.all_configs())
    print(f"edge_search: {done_before}/{total} configs tested; running batch of {args.batch}", flush=True)

    results = edge_search.run_batch(n=args.batch, log=lambda m: print(m, flush=True))
    passers = [r for r in results if r["passed"]]
    if passers:
        print(f"\n=== {len(passers)} GATE-PASSING edge(s) this batch ===", flush=True)
        for r in passers:
            print(json.dumps({"config": r["config"], "metrics": r["metrics"]}, indent=2), flush=True)

    # leaderboard across ALL results so far
    with storage._connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT family, config_json, metrics_json, sharpe, dsr, window_frac, passed "
            "FROM edge_search_results WHERE metrics_json IS NOT NULL AND metrics_json != '{}' "
            "ORDER BY dsr DESC, sharpe DESC LIMIT ?".replace("?", "%s" if storage.is_postgres else "?"),
            (args.leaderboard,)).fetchall()]
    print(f"\n=== leaderboard (top {args.leaderboard} by DSR) ===", flush=True)
    for r in rows:
        cfg = json.loads(r["config_json"])
        m = json.loads(r["metrics_json"]) if r["metrics_json"] else {}
        tag = "PASS" if r["passed"] else "    "
        cfg_s = " ".join(f"{k}={v}" for k, v in cfg.items() if k != "family")
        print(f"  [{tag}] {r['family']:16} DSR={r['dsr']:.3f} Sh={r['sharpe']:+.2f} "
              f"win={m.get('window_frac')} | {cfg_s}", flush=True)

    done_after = edge_search.trial_count(storage)
    print(f"\nedge_search: now {done_after}/{total} tested "
          f"({total - done_after} remaining)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
