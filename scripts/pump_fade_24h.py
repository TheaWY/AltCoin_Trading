#!/usr/bin/env python3
"""The retrospective's strongest pattern, as a trade: short coins that are at
the top of the 24h leaderboard (and optionally had open interest surge), hold
24h. One entry per coin per 24h, entries on the hour, 0.3% round trip, short
funding ignored (pumped coins usually pay shorts). Also the long side: buy the
calmest coins that are far below their 90-day high.

Reports mean / median net, share of trades making >= +10% and losing >= 10%,
worst trade, per half of time, and a few stop-loss variants (checked on 1m
highs/lows)."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.research import indicators as ind  # noqa: E402

H = 1440
COST = 0.003


def main() -> int:
    ctx = ind.build_ctx(get_storage(), 14 * 1440)
    c, hi, lo = (ctx.p[k].to_numpy() for k in ("close", "high", "low"))
    n = len(c)
    rows = np.arange(1440, n - H, 60)
    liquid = ctx.qv_sum(60).to_numpy() >= 100_000
    r24 = ctx.ret(1440).to_numpy()
    rank = ctx.ret(1440).rank(axis=1, pct=True).to_numpy()
    oi24 = ind._oi_chg(ctx, 1440).to_numpy()  # noqa: SLF001
    rv = ind._rv(ctx, 1440).to_numpy()  # noqa: SLF001
    dh90 = ctx.d("dist_high_90d").to_numpy()
    rv_rank = ind._rv(ctx, 1440).rank(axis=1, pct=True).to_numpy()  # noqa: SLF001
    out = []
    specs = []
    for rk, oi, stop in itertools.product((0.95, 0.98, 0.99, 0.995), (None, 0.0, 0.1), (None, 0.1, 0.2)):
        specs.append(("short_top_gainer", -1, rk, oi, stop))
    for q, stop in itertools.product((0.05, 0.1, 0.2), (None, 0.1)):
        specs.append(("long_calm_beaten", 1, q, None, stop))
    for name, side, a, b, stop in specs:
        trades = []
        last = {}
        for i in rows:
            if side < 0:
                m = liquid[i] & (rank[i] >= a)
                if b is not None:
                    m &= oi24[i] > b
            else:
                m = liquid[i] & (rv_rank[i] <= a) & (dh90[i] <= -0.4)
            for j in np.flatnonzero(m):
                if i - last.get(j, -10 ** 9) < H:
                    continue
                last[j] = i
                e = c[i, j]
                path_h, path_l = hi[i + 1:i + H + 1, j], lo[i + 1:i + H + 1, j]
                px = c[i + H, j]
                if stop:
                    lvl = e * (1 + stop) if side < 0 else e * (1 - stop)
                    hit = np.flatnonzero(path_h >= lvl) if side < 0 else np.flatnonzero(path_l <= lvl)
                    if len(hit):
                        px = lvl
                if not (np.isfinite(e) and np.isfinite(px)):
                    continue
                trades.append((i, side * (px / e - 1) - COST))
        if len(trades) < 10:
            continue
        t = np.array(trades)
        half = rows[len(rows) // 2]
        row = {"rule": f"{name}|{a}|oi>{b}|stop{stop}", "n": len(t)}
        for part, m in (("all", np.ones(len(t), bool)), ("h1", t[:, 0] < half), ("h2", t[:, 0] >= half)):
            x = t[m, 1]
            if len(x):
                row[part] = {"n": int(len(x)), "mean": round(float(x.mean()), 4), "median": round(float(np.median(x)), 4),
                             "win10": round(float((x >= 0.10).mean()), 3), "lose10": round(float((x <= -0.10).mean()), 3),
                             "worst": round(float(x.min()), 3)}
        out.append(row)
    out.sort(key=lambda r: -min(r.get("h1", {}).get("mean", -9), r.get("h2", {}).get("mean", -9)))
    Path(PROJECT_ROOT / "data" / "reports" / "retro").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data" / "reports" / "retro" / "pump_fade_24h.json").write_text(json.dumps(out))
    for r in out[:14]:
        print(r["rule"], r["n"], "| h1", r.get("h1"), "| h2", r.get("h2"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
