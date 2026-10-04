#!/usr/bin/env python3
"""Liquidation-cascade → alt-spike precursor validation. SELF-GATING: the
liquidation stream (liquidation_agg_1h) only started collecting ~2026-07, far too
short to backtest across regimes. This job runs weekly and does nothing but log
"waiting" until enough liquidation-covered history + events accumulate; then it
runs the event study automatically and records the verdict.

Thesis (user, 2026-07-20): a SHORT-liquidation spike = shorts force-covered =
forced buying = upward pressure → precedes sudden alt spikes (short squeeze). We
test whether a short-liq spike raises P(forward +N% move) vs base — honest lift,
NOT a strategy (a strategy would still need the gate; cf. pump-riding-dead).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402

HOUR = 3600
MIN_WEEKS = 10          # need at least this much liquidation-covered history
MIN_EVENTS = 300        # and this many short-liq-spike events
SPIKE_MULT = 5.0        # short_liq_notional > SPIKE_MULT x its trailing median = a spike
MOON = 0.30             # a "spike-up" = forward-72h max run-up >= +30%
FWD = 72


def _log(msg: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{stamp}] liq-moonshot: {msg}", flush=True)


def main() -> int:
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        row = c.execute(
            "SELECT MIN(timestamp) mn, MAX(timestamp) mx, COUNT(*) n, "
            "COUNT(DISTINCT symbol) s FROM liquidation_agg_1h"
        ).fetchone()
    d = dict(row) if row else {}
    if not d or not d.get("mn"):
        _log("no liquidation_agg_1h data yet — waiting.")
        return 0
    span_weeks = (int(d["mx"]) - int(d["mn"])) / (7 * 24 * HOUR)
    _log(f"data span = {span_weeks:.1f} weeks, {d['n']} rows, {d['s']} symbols "
         f"(need >= {MIN_WEEKS} weeks + {MIN_EVENTS} spike events).")
    if span_weeks < MIN_WEEKS:
        _log(f"INSUFFICIENT history ({span_weeks:.1f}/{MIN_WEEKS} weeks) — waiting, "
             f"re-checks weekly.")
        return 0

    # --- enough data: run the event study ---
    lo = int(d["mn"])
    hi = int(d["mx"])
    with st._connect() as c:  # noqa: SLF001
        syms = [dict(r)["symbol"] for r in c.execute(
            "SELECT DISTINCT symbol FROM liquidation_agg_1h").fetchall()]

    fwd_up, base_fwd = [], []
    n_spike = 0
    for s in syms:
        with st._connect() as c:  # noqa: SLF001
            liq = c.execute(
                "SELECT timestamp, short_liq_notional FROM liquidation_agg_1h "
                "WHERE symbol=%s ORDER BY timestamp", (s,)).fetchall()
        liq = [(int(dict(r)["timestamp"]) // HOUR * HOUR, float(dict(r)["short_liq_notional"] or 0)) for r in liq]
        if len(liq) < 200:
            continue
        pr = st.get_prices(s, limit=200000, timeframe="1h")
        px = {int(r["timestamp"]) // HOUR * HOUR: float(r["close"]) for r in pr if float(r["close"]) > 0}
        sl = np.array([x[1] for x in liq])
        for k in range(168, len(liq) - FWD):
            t = liq[k][0]
            trail = np.median(sl[k - 168:k])
            base = px.get(t)
            fmax_ts = [px.get(t + h * HOUR) for h in range(FWD)]
            fvals = [v for v in fmax_ts if v]
            if not base or len(fvals) < FWD * 0.6:
                continue
            fmax = max(fvals) / base - 1.0
            base_fwd.append(1 if fmax >= MOON else 0)
            if trail > 0 and sl[k] > SPIKE_MULT * trail:      # a short-liq SPIKE
                n_spike += 1
                fwd_up.append(1 if fmax >= MOON else 0)

    if n_spike < MIN_EVENTS:
        _log(f"data long enough but only {n_spike}/{MIN_EVENTS} spike events — waiting for more.")
        return 0

    base_rate = float(np.mean(base_fwd)) if base_fwd else 0.0
    spike_rate = float(np.mean(fwd_up)) if fwd_up else 0.0
    lift = (spike_rate / base_rate) if base_rate > 0 else 0.0
    verdict = (f"RESULT: short-liq-spike -> P(+{int(MOON * 100)}% in {FWD}h) = "
               f"{spike_rate * 100:.2f}% vs base {base_rate * 100:.2f}%  LIFT={lift:.2f}x  "
               f"(n_spike={n_spike})")
    _log(verdict)
    if lift >= 1.5:
        _log("LIFT >= 1.5x — worth building into a gated strategy (still needs the honest gate).")
    else:
        _log("LIFT < 1.5x — short-liq spikes do NOT meaningfully precede alt spikes; drop it.")
    try:
        from src.research import decisions
        decisions.log("research", "liquidation_moonshot_validated", "short_liq_spike",
                      detail={"spike_rate": spike_rate, "base_rate": base_rate,
                              "lift": lift, "n_spike": n_spike, "span_weeks": span_weeks})
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
