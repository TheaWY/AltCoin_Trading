"""Pairs stat-arb backtest driver — the REAL-CODE-PATH gate.

Reproduces the validated result (DSR ~0.99, positive every year 2021-2026,
survivorship-controlled) by driving the SHARED core in src/engine/pairs.py —
the exact functions the live pairs runner calls. If this reproduces the gate,
the shared core is validated in production code, not just a scratchpad.

Usage:
    .venv/bin/python scripts/backtest_pairs.py [--from 2021-06] [--to 2026-07-16]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, "/Users/pc/Projects/AltCoin_Trading")
from src.data.storage import get_storage
from src.engine import pairs
from src.research.robust_stats import deflated_sharpe

HOUR = 3600


def _load_panel(lo_ts: int, hi_ts: int) -> tuple[np.ndarray, dict, dict]:
    """Grid-aligned log-close and dollar-volume for every symbol with data,
    incl. short-lived / delisted names (survivorship control). NaN where absent."""
    st = get_storage()
    with st._connect() as c:
        syms = [
            dict(r)["symbol"]
            for r in c.execute(
                "SELECT symbol FROM prices WHERE timeframe='1h' "
                "GROUP BY symbol HAVING COUNT(*)>=2000"
            ).fetchall()
        ]
    syms = [s for s in syms if s != "BTC/USDT"]
    allt = np.arange(lo_ts // HOUR * HOUR, hi_ts // HOUR * HOUR, HOUR)
    T = len(allt)
    tidx = {int(t): i for i, t in enumerate(allt)}
    logp, dvol = {}, {}
    for s in syms:
        rows = st.get_prices(s, limit=300000, timeframe="1h")
        if len(rows) < 2000:
            continue
        lc = np.full(T, np.nan)
        dv = np.full(T, np.nan)
        for r in rows:
            i = tidx.get(int(r["timestamp"]) // HOUR * HOUR)
            if i is not None:
                cl = float(r["close"])
                v = float(r.get("volume") or 0)
                if cl > 0:
                    lc[i] = np.log(cl)
                    dv[i] = cl * v
        if np.isfinite(lc).sum() >= 1500:
            logp[s] = lc
            dvol[s] = dv
    return allt, logp, dvol


def _trade_pair(spec: pairs.PairSpec, logp: dict, w0: int, trade_hours: int) -> list[float]:
    """Trade ONE pair forward over the trade window using the shared core's
    spread math + entry/exit rule. Force-close on delisting (leg data ends).
    Returns per-round-trip spread returns, net of realistic cost + funding."""
    zwin = pairs.ZWIN_HOURS
    fa = logp[spec.a][w0 - zwin:w0 + trade_hours]
    fb = logp[spec.b][w0 - zwin:w0 + trade_hours]
    sp = pairs.spread(fa, fb, spec.beta)
    n = len(sp)
    out: list[float] = []
    pos = 0
    entry_i = 0
    last_fin = None
    for i in range(zwin, n):
        if not np.isfinite(sp[i]):
            # delisting: leg data ended -> force-close at last finite spread
            if pos != 0 and (i + 24 >= n or not np.isfinite(sp[i:]).any()):
                ret = pos * (last_fin - sp[entry_i]) - pairs.round_trip_cost(i - entry_i)
                out.append(ret)
                pos = 0
            continue
        last_fin = sp[i]
        z = pairs.zscore(sp[i - zwin:i], sp[i])
        if pos == 0 and pairs.should_open(z):
            pos = pairs.entry_side(z)
            entry_i = i
        elif pos != 0 and pairs.should_close(z):
            ret = pos * (sp[i] - sp[entry_i]) - pairs.round_trip_cost(i - entry_i)
            out.append(ret)
            pos = 0
    return out


def run(lo_ts: int, hi_ts: int, n_trials: int = 150) -> dict:
    allt, logp, dvol = _load_panel(lo_ts, hi_ts)
    T = len(allt)
    print(f"universe incl. delisted: {len(logp)}  (survivorship-controlled)", flush=True)
    sel_h, trade_h = pairs.SEL_HOURS, pairs.TRADE_HOURS
    book, dates = [], []
    delist = 0
    for w0 in range(sel_h, T - trade_h, trade_h):
        sel = slice(w0 - sel_h, w0)
        live = pairs.liquid_universe(logp, dvol, sel)
        if len(live) < 5:
            continue
        specs = pairs.select_pairs(live, logp, sel)
        win_ret: list[float] = []
        for spec in specs:
            win_ret.extend(_trade_pair(spec, logp, w0, trade_h))
        if win_ret:
            book.append(float(np.mean(win_ret)))
            dates.append(int(allt[w0]))
    book = np.array(book)
    dates = np.array(dates)
    if len(book) < 8:
        print("too few windows"); return {}
    rpy = 365 / (trade_h / 24)
    sh = book.mean() / book.std(ddof=1) * np.sqrt(rpy)
    pw = int((book > 0).sum())
    nw = len(book)
    d = deflated_sharpe(list(book), n_trials=n_trials)
    print(f"\n=== PAIRS STAT-ARB via src/engine/pairs.py (real code path) ===", flush=True)
    print(f"  Sharpe(ann)={sh:+.2f}  windows={pw}/{nw}({pw / nw * 100:.0f}%)  "
          f"DSR={d['dsr']:.3f}  meanWin={book.mean() * 100:+.2f}%", flush=True)
    print("  yearly:", flush=True)
    for y in range(2021, 2027):
        loy = dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        hiy = dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        mask = (dates >= loy) & (dates < hiy)
        if mask.sum():
            rr = book[mask]
            print(f"    {y}: mean={rr.mean() * 100:+.2f}% pos={int((rr > 0).mean() * 100)}% n={int(mask.sum())}", flush=True)
    gate = d["dsr"] >= 0.95 and pw / nw >= 0.66
    print(f"\n  GATE {'PASS' if gate else 'FAIL'}: DSR {d['dsr']:.3f} (>=0.95) & windows {pw / nw * 100:.0f}% (>=66%)", flush=True)
    return {"sharpe": sh, "windows": pw / nw, "dsr": d["dsr"], "gate": gate}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2021-06")
    ap.add_argument("--to", dest="to", default="2026-07-16")
    ap.add_argument("--trials", type=int, default=150)
    a = ap.parse_args()

    def _p(s: str) -> int:
        parts = [int(x) for x in s.split("-")]
        while len(parts) < 3:
            parts.append(1)
        return int(dt.datetime(*parts, tzinfo=dt.timezone.utc).timestamp())

    run(_p(a.frm), _p(a.to), n_trials=a.trials)
