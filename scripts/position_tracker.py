"""Position tracker + stop-loss (손절) advisor for 유리's own positions. Advisory only: it NEVER places or cancels orders.

Reads data/positions.yaml:
  positions:
    - symbol: SOL/USDT        # Binance USDT perp or spot symbol as in prices_1m
      side: long              # long | short
      entry_price: 142.5
      size_usd: 500
      opened: 2026-09-28 10:00   # KST
      policy: auto            # auto (= best policy that passed B16, else default) | fixed | atr | trail | none
      stop_pct: 0.05          # used by 'fixed'
      atr_k: 2.0              # used by 'atr' / 'trail' (multiple of 24h realised vol)
      note: ""
Every run (launchd com.altcoin.positions, every 5 min): price from prices_1m, unrealised P&L, running high/low since entry,
the stop level for the chosen policy, and a status: OK / NEAR_STOP (within 25% of the stop distance) / STOP_HIT / TIME_REVIEW.
Writes table position_status (latest per position) and appends position_alerts when the status changes.
Default policy until B16 finds a passing rule: trailing stop at 2.0 x 24h realised vol from the running high (long) / low (short).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

KST = timezone(timedelta(hours=9))
POS = ROOT / "data/positions.yaml"
B16 = ROOT / "data/reports/b16/b16.json"
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS position_status (
      pid TEXT PRIMARY KEY, symbol TEXT, side TEXT, entry_price DOUBLE PRECISION, size_usd DOUBLE PRECISION, opened BIGINT,
      last_price DOUBLE PRECISION, pnl_pct DOUBLE PRECISION, pnl_usd DOUBLE PRECISION, run_extreme DOUBLE PRECISION,
      policy TEXT, stop_price DOUBLE PRECISION, dist_to_stop_pct DOUBLE PRECISION, status TEXT, updated BIGINT)""",
    """CREATE TABLE IF NOT EXISTS position_alerts (
      ts BIGINT, pid TEXT, symbol TEXT, old_status TEXT, new_status TEXT, last_price DOUBLE PRECISION, stop_price DOUBLE PRECISION,
      pnl_pct DOUBLE PRECISION, message TEXT)"""]


def q(st, sql, params=()):
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        if cur.description is None:
            return None
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def best_policy():
    """If B16 found a passing exit family, use it; otherwise the default trailing ATR stop."""
    try:
        v = json.load(open(B16))["verdict"]
        ok = [f for f, r in v.items() if r.get("pass_")]
        if ok:
            return ok[0]
    except Exception:
        pass
    return "trail"


def main() -> int:
    if not POS.exists():
        POS.write_text("positions: []\n# add positions like:\n# - {symbol: SOL/USDT, side: long, entry_price: 142.5, size_usd: 500, opened: '2026-09-28 10:00', policy: auto}\n")
        print("created empty", POS)
        return 0
    cfg = yaml.safe_load(POS.read_text()) or {}
    st = get_storage()
    for s in SCHEMA:
        q(st, s)
    now = int(time.time())
    auto = best_policy()
    for n, p in enumerate(cfg.get("positions") or []):
        sym, side = p["symbol"], p.get("side", "long").lower()
        sgn = 1 if side == "long" else -1
        opened = int(datetime.strptime(str(p["opened"]), "%Y-%m-%d %H:%M").replace(tzinfo=KST).timestamp())
        pid = f"{sym}|{opened}|{side}"
        px = q(st, "SELECT ts, high, low, close FROM prices_1m WHERE symbol=? AND ts >= ? ORDER BY ts", (sym, opened - 60))
        if px is None or px.empty:
            continue
        last = float(px["close"].iloc[-1])
        ext = float(px["high"].max()) if sgn == 1 else float(px["low"].min())
        hist = q(st, "SELECT close FROM prices_1m WHERE symbol=? AND ts >= ? ORDER BY ts", (sym, now - 86400))
        r = np.log(hist["close"].astype(float)).diff().dropna() if hist is not None and len(hist) > 60 else pd.Series([0.02 / 38])
        vol24 = float(r.std() * np.sqrt(1440))
        pol = p.get("policy", "auto")
        pol = auto if pol == "auto" else pol
        e = float(p["entry_price"])
        if pol == "fixed":
            stop = e * (1 - sgn * float(p.get("stop_pct", 0.05)))
        elif pol == "atr":
            stop = e * (1 - sgn * float(p.get("atr_k", 2.0)) * vol24)
        elif pol in ("trail", "learned", "combo", "stop", "time"):
            stop = ext * (1 - sgn * float(p.get("atr_k", 2.0)) * vol24)
        else:
            stop = np.nan
        pnl = sgn * (last / e - 1)
        dist = sgn * (last / stop - 1) if np.isfinite(stop) else np.nan
        full = sgn * (e / stop - 1) if np.isfinite(stop) else np.nan
        if np.isfinite(stop) and sgn * (last - stop) <= 0:
            status = "STOP_HIT"
        elif np.isfinite(dist) and full > 0 and dist <= 0.25 * abs(full):
            status = "NEAR_STOP"
        elif now - opened > 7 * 86400 and pnl < 0:
            status = "TIME_REVIEW"
        else:
            status = "OK"
        old = q(st, "SELECT status FROM position_status WHERE pid=?", (pid,))
        old_s = old["status"].iloc[0] if old is not None and len(old) else None
        q(st, "INSERT INTO position_status VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (pid) DO UPDATE SET last_price=EXCLUDED.last_price, "
              "pnl_pct=EXCLUDED.pnl_pct, pnl_usd=EXCLUDED.pnl_usd, run_extreme=EXCLUDED.run_extreme, policy=EXCLUDED.policy, "
              "stop_price=EXCLUDED.stop_price, dist_to_stop_pct=EXCLUDED.dist_to_stop_pct, status=EXCLUDED.status, updated=EXCLUDED.updated",
          (pid, sym, side, e, float(p.get("size_usd", 0)), opened, last, pnl, pnl * float(p.get("size_usd", 0)), ext, pol,
           float(stop) if np.isfinite(stop) else None, float(dist) if np.isfinite(dist) else None, status, now))
        if status != old_s:
            msg = f"{sym} {side} {status}: price {last:.6g}, stop {stop:.6g}, P&L {pnl * 100:+.1f}%"
            q(st, "INSERT INTO position_alerts VALUES (?,?,?,?,?,?,?,?,?)",
              (now, pid, sym, old_s, status, last, float(stop) if np.isfinite(stop) else None, pnl, msg))
            print(msg, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
