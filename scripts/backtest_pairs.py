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
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.data.storage import get_storage
from src.engine import pairs
from src.engine.pairs_trader import _cap_pairs
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
                    qv = r.get("quote_volume")
                    dv[i] = float(qv) if qv not in (None, 0, 0.0) else cl * v
        if np.isfinite(lc).sum() >= 1500:
            logp[s] = lc
            dvol[s] = dv
    return allt, logp, dvol


def _trade_pair(spec: pairs.PairSpec, logp: dict, w0: int, trade_hours: int,
                z_stop_delta: float | None = None,
                dollar_stop_pct: float | None = None) -> list[float]:
    return [r for r, _ in _trade_pair_ex(
        spec, logp, w0, trade_hours, z_stop_delta, dollar_stop_pct
    )]


def _trade_pair_ex(spec: pairs.PairSpec, logp: dict, w0: int, trade_hours: int,
                   z_stop_delta: float | None = None,
                   dollar_stop_pct: float | None = None) -> list[tuple[float, str]]:
    """Trade ONE pair forward. Returns (spread-return, reason) net of cost."""
    zwin = pairs.ZWIN_HOURS
    fa = logp[spec.a][w0 - zwin:w0 + trade_hours]
    fb = logp[spec.b][w0 - zwin:w0 + trade_hours]
    sp = pairs.spread(fa, fb, spec.beta)
    n = len(sp)
    out: list[tuple[float, str]] = []
    pos = 0
    entry_i = 0
    entry_z: float | None = None
    last_fin = None
    for i in range(zwin, n):
        if not np.isfinite(sp[i]):
            if pos != 0 and (i + 24 >= n or not np.isfinite(sp[i:]).any()):
                ret = pos * (last_fin - sp[entry_i]) - pairs.round_trip_cost(i - entry_i)
                out.append((ret, "delist"))
                pos = 0
                entry_z = None
            continue
        last_fin = sp[i]
        z = pairs.zscore(sp[i - zwin:i], sp[i])
        if pos == 0 and pairs.should_open(z):
            pos = pairs.entry_side(z)
            entry_i = i
            entry_z = z
        elif pos != 0:
            pnl = pos * (sp[i] - sp[entry_i])
            cost = pairs.round_trip_cost(i - entry_i)
            if z_stop_delta and pairs.should_stop(z, entry_z, z_stop_delta):
                out.append((pnl - cost, "z_stop"))
                pos = 0
                entry_z = None
            elif dollar_stop_pct and pairs.should_dollar_stop(pnl, dollar_stop_pct):
                out.append((pnl - cost, "stop_loss"))
                pos = 0
                entry_z = None
            elif pairs.should_close(z):
                out.append((pnl - cost, "z_revert"))
                pos = 0
                entry_z = None
    return out


def _scorecard(trips: list[tuple[float, str]]) -> dict:
    pnls = [r for r, _ in trips]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw, gl = sum(wins), abs(sum(losses))
    pf = (gw / gl) if gl else (float("inf") if gw else None)
    reasons: dict[str, int] = {}
    for _, reason in trips:
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "n": len(pnls),
        "win_rate": (len(wins) / len(pnls)) if pnls else None,
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "expectancy": (sum(pnls) / len(pnls)) if pnls else None,
        "profit_factor": pf,
        "reasons": reasons,
    }


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


def compare_stops(lo_ts: int, hi_ts: int, k: int = 60, max_windows: int = 6,
                  deltas: list[float | None] | None = None) -> dict:
    """Live-like (K pairs) stop scorecards on historical panels.

    Always includes the old live book (z-revert + 15% dollar stop) as the
    before-case, then a z-stop sweep. `deltas=None` means z-revert only.
    """
    if deltas is None:
        deltas = [None, 0.5, 0.75, 1.0, 1.25, 1.5]
    allt, logp, dvol = _load_panel(lo_ts, hi_ts)
    T = len(allt)
    sel_h, trade_h = pairs.SEL_HOURS, pairs.TRADE_HOURS
    starts = list(range(sel_h, T - trade_h, trade_h))[-max_windows:]
    books: dict[str, list[tuple[float, str]]] = {}
    specs_plan: list[tuple[str, float | None, float | None]] = [
        ("BEFORE dollar 15% (old live)", None, 0.15),
    ]
    for d in deltas:
        label = "z_revert only" if d is None else f"z_stop +{d:g}"
        specs_plan.append((label, d, None))
    specs_plan.append(("AFTER z_stop +1.0 + dollar 15% (new live)", 1.0, 0.15))
    for label, _, _ in specs_plan:
        books[label] = []
    print(f"compare-stops windows={len(starts)} universe={len(logp)} K={k} "
          f"min_daily_dvol={pairs.MIN_DVOL:g} listing_days={pairs.MIN_LISTING_DAYS:g} "
          f"exclude={sorted(pairs.EXCLUDE)}", flush=True)
    for w0 in starts:
        sel = slice(w0 - sel_h, w0)
        live = pairs.liquid_universe(logp, dvol, sel)
        if len(live) < 5:
            continue
        specs = _cap_pairs(pairs.select_pairs(live, logp, sel), k, 2)
        wdate = dt.datetime.fromtimestamp(int(allt[w0]), tz=dt.timezone.utc).date()
        print(f"  window {wdate} live={len(live)} pairs={len(specs)}", flush=True)
        for spec in specs:
            for label, z_d, dollar in specs_plan:
                books[label].extend(
                    _trade_pair_ex(spec, logp, w0, trade_h, z_d, dollar)
                )

    def _fmt(s):
        pf = s["profit_factor"]
        pf_s = "inf" if pf == float("inf") else (f"{pf:.3f}" if pf is not None else "n/a")
        wr = s["win_rate"]
        aw, al, e = s["avg_win"], s["avg_loss"], s["expectancy"]
        ok = (aw is not None and al is not None and abs(al) <= aw)
        return (f"n={s['n']} wr={wr and wr*100:.1f}% "
                f"avgW={aw and aw*100:+.3f}% "
                f"avgL={al and al*100:+.3f}% "
                f"E={e and e*100:+.3f}% PF={pf_s} "
                f"|L|<=W={'yes' if ok else 'no'} reasons={s['reasons']}")

    print("\n=== STOP COMPARE (spread-return units, live-like K) ===", flush=True)
    out: dict = {}
    for label, _, _ in specs_plan:
        card = _scorecard(books[label])
        print(f"  {label:42s} {_fmt(card)}", flush=True)
        out[label] = card
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2021-06")
    ap.add_argument("--to", dest="to", default="2026-07-16")
    ap.add_argument("--trials", type=int, default=150)
    ap.add_argument("--compare-stops", action="store_true")
    ap.add_argument("--windows", type=int, default=6)
    a = ap.parse_args()

    def _p(s: str) -> int:
        parts = [int(x) for x in s.split("-")]
        while len(parts) < 3:
            parts.append(1)
        return int(dt.datetime(*parts, tzinfo=dt.timezone.utc).timestamp())

    if a.compare_stops:
        compare_stops(_p(a.frm), _p(a.to), max_windows=a.windows)
    else:
        run(_p(a.frm), _p(a.to), n_trials=a.trials)
