"""F12 "fresh burst" paper test (registered 2026-10-03 00:30 KST, forward only).

From the SAND retrospective (research/b35_burst_retro.md): over 23 days / 646 volume bursts (30-min return >= +5%, 30-min
volume >= 5x the prior 2h, dv24 >= $5M) the average 4h follow-through is ~0 and the median -1.8%. The one tape feature
that separated follow-through from fade was whether the coin had ALREADY run up over the prior week: bursts in coins
with a 7-day return <= 0 continued (+3.7% mean 4h, CI [+0.7, +7.7], n=83, hit 57%), bursts in extended coins faded. That
split was found in-sample and is carried by a handful of trades, so it gets no back-test credit: this is the forward test.

Rule (fixed now): every 5 minutes, for every Binance USDT perp (crypto, listed >= 30d, dv24 >= $5M): if close / close 30
min ago - 1 >= +5% AND 30-min quote volume >= 5x the average 5-min quote volume of the prior 2h (x6) AND the 7-day return
(close vs close 168h ago) <= 0 -> paper LONG at the current 1m close; one position per coin per 4h; max 10 open.
Exit: -3% from entry (checked on 1m lows), or 4h. Cost: taker fee 5 bp + slippage by dv24, both sides; settled funding.
Pass: >= 60 closed events and mean net > 0 with bootstrap 95% CI above 0. Kill: 60 events with mean net <= 0.
Paper only. LIVE_TRADING is never read here."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from src.data.storage import get_storage  # noqa: E402
from oi_drop_short_paper import q  # noqa: E402
from pairs_divergence_paper import funding_leg, slip  # noqa: E402

TABLE = "f12_fresh_burst_paper"
R30, VOLX, R7D_MAX, DV_MIN, STOP, HOLD_S, MAX_OPEN, FEE = 0.05, 5.0, 0.0, 5e6, 0.03, 4 * 3600, 10, 0.0005
SCHEMA = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
  id SERIAL PRIMARY KEY, symbol TEXT, ts_signal BIGINT, r30 DOUBLE PRECISION, volx DOUBLE PRECISION, r7d DOUBLE PRECISION,
  dv24 DOUBLE PRECISION, entry_px DOUBLE PRECISION, ts_exit BIGINT, exit_px DOUBLE PRECISION, exit_reason TEXT,
  gross DOUBLE PRECISION, funding DOUBLE PRECISION, cost DOUBLE PRECISION, net DOUBLE PRECISION, status TEXT DEFAULT 'open')"""


def crypto_only(codes):
    try:
        import requests
        info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20).json()["symbols"]
        ok = {x["symbol"] for x in info if x.get("contractType") == "PERPETUAL" and x["status"] == "TRADING" and x.get("underlyingType", "COIN") == "COIN"
              and time.time() * 1000 - x.get("onboardDate", 0) >= 30 * 86400 * 1000}
        return {c for c in codes if c.replace("/", "") in ok}
    except Exception:  # noqa: BLE001
        return set(codes)


def main():
    st = get_storage(); now = int(time.time()); q(st, SCHEMA)
    # ---- manage open positions on 1m data since entry
    op = q(st, f"SELECT * FROM {TABLE} WHERE status = 'open'")
    n_open = 0
    for _, t in (op.iterrows() if op is not None and len(op) else []):
        px = q(st, "SELECT ts, low, close FROM prices_1m WHERE symbol = %s AND ts > %s ORDER BY ts", (t.symbol, int(t.ts_signal)))
        if px is None or not len(px):
            n_open += 1; continue
        stop_px = float(t.entry_px) * (1 - STOP)
        hit = px[px.low <= stop_px]
        reason, exit_px, t_exit = None, None, None
        if len(hit):
            reason, exit_px, t_exit = "stop", stop_px, int(hit.ts.iloc[0])
        elif now - int(t.ts_signal) >= HOLD_S:
            last = px[px.ts <= int(t.ts_signal) + HOLD_S]
            reason, exit_px, t_exit = "time_4h", float((last if len(last) else px).close.iloc[-1]), int((last if len(last) else px).ts.iloc[-1])
        if reason is None:
            n_open += 1; continue
        gross = exit_px / float(t.entry_px) - 1
        fund = funding_leg(st, t.symbol, int(t.ts_signal), t_exit, +1)
        cost = 2 * (FEE + slip(float(t.dv24))); net = gross + fund - cost
        q(st, f"UPDATE {TABLE} SET ts_exit=%s, exit_px=%s, exit_reason=%s, gross=%s, funding=%s, cost=%s, net=%s, status='closed' WHERE id=%s",
          (t_exit, exit_px, reason, gross, fund, cost, net, int(t.id)))
        print(time.strftime("%F %T"), f"F12 close {t.symbol} {reason} gross {gross:+.4f} net {net:+.4f}", flush=True)
    # ---- scan for new bursts (last 5-min bar vs 30 min ago; volume vs prior 2h)
    d = q(st, "SELECT symbol, ts, close, quote_volume FROM prices_1m WHERE ts >= %s", (now - 170 * 3600,))
    if d is None or not len(d):
        return 0
    d = d.sort_values(["symbol", "ts"])
    last = d[d.ts >= now - 300].groupby("symbol").agg(c=("close", "last"), ts=("ts", "last"))
    c30 = d[(d.ts >= now - 1860) & (d.ts < now - 1740)].groupby("symbol").close.last().rename("c30")
    v30 = d[d.ts >= now - 1800].groupby("symbol").quote_volume.sum().rename("v30")
    vbase = (d[(d.ts >= now - 1800 - 7200) & (d.ts < now - 1800)].groupby("symbol").quote_volume.sum() / 24).rename("vbase")
    dv24 = d[d.ts >= now - 86400].groupby("symbol").quote_volume.sum().rename("dv24")
    c7 = d[(d.ts >= now - 168 * 3600 - 1800) & (d.ts < now - 168 * 3600 + 1800)].groupby("symbol").close.last().rename("c7")
    f = pd.concat([last, c30, v30, vbase, dv24, c7], axis=1).dropna()
    f["r30"] = f.c / f.c30 - 1; f["volx"] = f.v30 / (6 * f.vbase.replace(0, np.nan)); f["r7d"] = f.c / f.c7 - 1
    cand = f[(f.r30 >= R30) & (f.volx >= VOLX) & (f.r7d <= R7D_MAX) & (f.dv24 >= DV_MIN)]
    if len(cand):
        okc = crypto_only(cand.index.tolist()); cand = cand[cand.index.isin(okc)]
    recent = q(st, f"SELECT symbol FROM {TABLE} WHERE ts_signal >= %s", (now - HOLD_S,))
    block = set(recent.symbol) if recent is not None and len(recent) else set()
    for sym, r in cand.sort_values("r30", ascending=False).iterrows():
        if n_open >= MAX_OPEN:
            break
        if sym in block:
            continue
        q(st, f"INSERT INTO {TABLE} (symbol, ts_signal, r30, volx, r7d, dv24, entry_px, status) VALUES (%s,%s,%s,%s,%s,%s,%s,'open')",
          (sym, int(r.ts), float(r.r30), float(r.volx), float(r.r7d), float(r.dv24), float(r.c)))
        n_open += 1
        print(time.strftime("%F %T"), f"F12 open {sym} r30 {r.r30:+.3f} volx {r.volx:.1f} r7d {r.r7d:+.3f} at {r.c}", flush=True)
    s = q(st, f"SELECT count(*) n, avg(net) m FROM {TABLE} WHERE status = 'closed'")
    print(time.strftime("%F %T"), f"F12 open {n_open} closed {int(s.n[0])} mean net {float(s.m[0]) if s.m[0] is not None else float('nan'):+.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
