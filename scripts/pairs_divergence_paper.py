"""F10 pairs DIVERGENCE paper test (registered 2026-10-02, pre-registered BEFORE any live data).

Hypothesis (from B34): the retired pairs_statarb book lost systematically (31 trades, -85 USDT; the opposite side would have
made +84 with a bootstrap CI excluding 0). Claim: when a selected pair's spread reaches |z| >= Z_IN, the spread CONTINUES
to diverge over the next 1-2 days (momentum in the spread), i.e. the exact opposite of the mean-reversion entry.

Construction (same signal core as the retired book, src/engine/pairs.py and pairs_trader.refresh_selection / _pair_z, so
the ENTRY moments are identical; only the side and the exits are mirrored):
  entry   z >= +Z_IN -> LONG A / SHORT beta*B (ride the rich spread richer); z <= -Z_IN -> SHORT A / LONG beta*B
  take    |z| >= |entry_z| + Z_STOP_DELTA (the old book's z-stop level is this book's target)
  stop    |z| <= Z_OUT (the old book's take-profit level is this book's stop), or spread P&L <= -STOP_PCT
  time    MAX_HOLD 48h (old book: median hold 12h, mean 24h)
  one open position per pair; cap 20 open pairs; cost = taker fee + slippage per leg, both sides; funding per leg.
Pass: >= 60 closed events AND mean net > 0 with bootstrap 95% CI above 0. Kill: 60 events with mean net <= 0.
Paper only. LIVE_TRADING is never read here."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from src.data.storage import get_storage  # noqa: E402
from src.engine import pairs, pairs_trader  # noqa: E402
from oi_drop_short_paper import q  # noqa: E402

TABLE = "f10_pairs_div_paper"
MAX_OPEN, MAX_HOLD_H, FEE = 20, 48.0, 0.0005
SCHEMA = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
  id SERIAL PRIMARY KEY, a TEXT, b TEXT, beta DOUBLE PRECISION, entry_z DOUBLE PRECISION, dir_a INTEGER,
  ts_open BIGINT, px_a DOUBLE PRECISION, px_b DOUBLE PRECISION, dv_a DOUBLE PRECISION, dv_b DOUBLE PRECISION,
  ts_exit BIGINT, exit_px_a DOUBLE PRECISION, exit_px_b DOUBLE PRECISION, exit_z DOUBLE PRECISION,
  gross DOUBLE PRECISION, funding DOUBLE PRECISION, cost DOUBLE PRECISION, net DOUBLE PRECISION,
  exit_reason TEXT, status TEXT DEFAULT 'open')"""


def slip(dv):
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def last_px(st, sym):
    d = q(st, "SELECT close, quote_volume FROM prices_1m WHERE symbol = %s AND ts >= %s ORDER BY ts DESC LIMIT 1", (sym, int(time.time()) - 7200))
    return (float(d.close[0]) if d is not None and len(d) else None)


def dv24(st, sym):
    d = q(st, "SELECT sum(quote_volume) v FROM prices_1m WHERE symbol = %s AND ts >= %s", (sym, int(time.time()) - 86400))
    return float(d.v[0] or 0) if d is not None and len(d) else 0.0


def funding_leg(st, sym, t0, t1, direction):
    """funding received (+) / paid (-) by a position of `direction` (+1 long / -1 short) over (t0, t1], settled rates from the
    Binance public endpoint (read-only, no keys; the local funding_rates table is 5-minute predicted samples, not settlements)."""
    try:
        import requests
        fr = requests.get("https://fapi.binance.com/fapi/v1/fundingRate", params={"symbol": sym.replace("/", ""), "startTime": int(t0 * 1000), "endTime": int(t1 * 1000), "limit": 1000}, timeout=20).json()
        return float(-direction * sum(float(x["fundingRate"]) for x in fr))
    except Exception:  # noqa: BLE001
        return 0.0


def main():
    st = get_storage(); now = int(time.time())
    q(st, SCHEMA)
    # ---- manage open positions
    opened = q(st, f"SELECT * FROM {TABLE} WHERE status = 'open'")
    n_open = 0
    for _, t in (opened.iterrows() if opened is not None and len(opened) else []):
        z = pairs_trader._pair_z(st, t.a, t.b, float(t.beta), now)  # noqa: SLF001
        pa, pb = last_px(st, t.a), last_px(st, t.b)
        if pa is None or pb is None:
            n_open += 1; continue
        da = int(t.dir_a); db = -da
        gross = da * (pa / t.px_a - 1) + db * float(t.beta) * (pb / t.px_b - 1)      # return on A-notional
        hold_h = (now - int(t.ts_open)) / 3600
        reason = None
        if z is not None and abs(z) >= abs(float(t.entry_z)) + pairs.Z_STOP_DELTA:
            reason = "z_target"
        elif z is not None and abs(z) <= pairs.Z_OUT:
            reason = "z_stop"
        elif gross <= -pairs.STOP_PCT:
            reason = "stop_loss"
        elif hold_h >= MAX_HOLD_H:
            reason = "max_hold"
        if reason is None:
            n_open += 1; continue
        fund = funding_leg(st, t.a, t.ts_open, now, da) + float(t.beta) * funding_leg(st, t.b, t.ts_open, now, db)
        cost = 2 * (FEE + slip(float(t.dv_a))) + 2 * float(t.beta) * (FEE + slip(float(t.dv_b)))
        net = gross + fund - cost
        q(st, f"UPDATE {TABLE} SET ts_exit=%s, exit_px_a=%s, exit_px_b=%s, exit_z=%s, gross=%s, funding=%s, cost=%s, net=%s, exit_reason=%s, status='closed' WHERE id=%s",
          (now, pa, pb, z, gross, fund, cost, net, reason, int(t.id)))
        print(time.strftime("%F %T"), f"close {t.a}/{t.b} {reason} z {z if z is None else round(z, 2)} gross {gross:+.4f} net {net:+.4f}", flush=True)
    # ---- entries: same selection and z as the retired book, opposite side
    sel = pairs_trader.refresh_selection(st, now)
    open_keys = set()
    cur = q(st, f"SELECT a, b FROM {TABLE} WHERE status = 'open'")
    if cur is not None and len(cur):
        open_keys = set(zip(cur.a, cur.b))
    for p in sel:
        if n_open >= MAX_OPEN:
            break
        if (p["a"], p["b"]) in open_keys:
            continue
        z = pairs_trader._pair_z(st, p["a"], p["b"], float(p["beta"]), now)  # noqa: SLF001
        if not pairs.should_open(z):
            continue
        pa, pb = last_px(st, p["a"]), last_px(st, p["b"])
        if pa is None or pb is None:
            continue
        dir_a = 1 if z > 0 else -1                                            # rich spread -> long A (continuation)
        q(st, f"INSERT INTO {TABLE} (a, b, beta, entry_z, dir_a, ts_open, px_a, px_b, dv_a, dv_b, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open')",
          (p["a"], p["b"], float(p["beta"]), float(z), dir_a, now, pa, pb, dv24(st, p["a"]), dv24(st, p["b"])))
        n_open += 1
        print(time.strftime("%F %T"), f"open {p['a']}/{p['b']} z {z:+.2f} dir_a {dir_a:+d} beta {p['beta']:.2f}", flush=True)
    d = q(st, f"SELECT count(*) n, avg(net) m FROM {TABLE} WHERE status = 'closed'")
    print(time.strftime("%F %T"), f"F10 open {n_open} closed {int(d.n[0])} mean net {float(d.m[0]) if d.m[0] is not None else float('nan'):+.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
