"""Forward paper test F3 (research/forward.yaml, rule in research/batch_C1.yaml): market-wide crash rebound.

Runs hourly at minute 3 (launchd com.altcoin.crashrebound). For the hour that just closed (T):
  crash   = close(T) / close(T-24h) - 1 <= -25%, 24h quote volume >= $2M, >= 72h of hourly history
  C1 arm  = BTC 24h <= -3% AND close(T) < min of the prior 720 hourly closes  -> paper long (side 1)
  control = crashes failing either filter -> logged with side 0; its hypothetical long outcome is settled too
  entry   = open of the minute starting T+60s, exit = open of the minute starting T+60s+24h, net of fees+slippage
            (funding not included; logged gross of funding)
Paper only; never places orders. Table crash_rebound_paper.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

FEE, H_, D_ = 0.0005, 3600, 86400
HOLD = 24 * H_
SCHEMA = """CREATE TABLE IF NOT EXISTS crash_rebound_paper (
  symbol TEXT NOT NULL, ts_signal BIGINT NOT NULL, ret_24h DOUBLE PRECISION, btc_24h DOUBLE PRECISION,
  dlo30 DOUBLE PRECISION, dv24 DOUBLE PRECISION, side INTEGER, entry_px DOUBLE PRECISION, exit_px DOUBLE PRECISION,
  gross DOUBLE PRECISION, net DOUBLE PRECISION, cost DOUBLE PRECISION, status TEXT, created BIGINT,
  PRIMARY KEY (symbol, ts_signal))"""


def slip(dv: float) -> float:
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def q(st, sql, params=()):
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        if cur.description is None:
            return None
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def settle(st) -> int:
    op = q(st, "SELECT symbol, ts_signal, cost, status FROM crash_rebound_paper WHERE status IN ('open','control')")
    n = 0
    now = time.time()
    for r in op.itertuples(index=False):
        e0, e1 = int(r.ts_signal) + 60, int(r.ts_signal) + 60 + HOLD
        if now < e1 + 120:
            continue
        px = q(st, "SELECT ts, open FROM prices_1m WHERE symbol=? AND ts IN (?, ?)", (r.symbol, e0, e1))
        if px is None or len(px) < 2:
            if now > e1 + 3 * 3600:
                q(st, "UPDATE crash_rebound_paper SET status=? WHERE symbol=? AND ts_signal=?",
                  ("no_data" if r.status == "open" else "control_no_data", r.symbol, r.ts_signal))
            continue
        p0 = float(px.loc[px["ts"] == e0, "open"].iloc[0])
        p1 = float(px.loc[px["ts"] == e1, "open"].iloc[0])
        g = p1 / p0 - 1
        q(st, "UPDATE crash_rebound_paper SET entry_px=?, exit_px=?, gross=?, net=?, status=? WHERE symbol=? AND ts_signal=?",
          (p0, p1, g, g - r.cost, "closed" if r.status == "open" else "control_closed", r.symbol, r.ts_signal))
        n += 1
    return n


def main() -> int:
    st = get_storage()
    q(st, SCHEMA)
    T = int(time.time()) // H_ * H_
    now1 = q(st, "SELECT symbol, close FROM prices_1m WHERE ts = ?", (T - 60,))
    ago = q(st, "SELECT symbol, close FROM prices_1m WHERE ts = ?", (T - D_ - 60,))
    vol = q(st, "SELECT symbol, SUM(quote_volume) AS dv24 FROM prices_1m WHERE ts >= ? AND ts < ? GROUP BY symbol", (T - D_, T))
    c_now, c_ago = dict(zip(now1["symbol"], now1["close"].astype(float))), dict(zip(ago["symbol"], ago["close"].astype(float)))
    dv = dict(zip(vol["symbol"], vol["dv24"].astype(float)))
    r24 = {s: c_now[s] / c_ago[s] - 1 for s in c_now if s in c_ago and c_ago[s] > 0}
    btc = r24.get("BTC/USDT")
    crashes = [s for s, r in r24.items() if r <= -0.25 and dv.get(s, 0) >= 2e6]
    made = 0
    for s in crashes:
        if btc is None:
            break
        recent = q(st, "SELECT 1 FROM crash_rebound_paper WHERE symbol=? AND ts_signal > ? LIMIT 1", (s, T - D_))
        if recent is not None and len(recent):
            continue
        h = q(st, "SELECT timestamp, close FROM prices WHERE symbol=? AND timeframe='1h_perp' AND timestamp >= ? AND timestamp <= ?",
              (s, T - 721 * H_, T - 2 * H_))
        if h is None or len(h) < 360:
            continue
        first = q(st, "SELECT MIN(timestamp) AS t0 FROM prices WHERE symbol=? AND timeframe='1h_perp'", (s,))
        if int(first["t0"].iloc[0]) > T - 72 * H_:
            continue
        dlo30 = c_now[s] / float(h["close"].astype(float).min()) - 1
        side = 1 if (btc <= -0.03 and dlo30 <= 0) else 0
        cost = 2 * (FEE + slip(dv[s]))
        q(st, "INSERT INTO crash_rebound_paper (symbol, ts_signal, ret_24h, btc_24h, dlo30, dv24, side, cost, status, created) "
              "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
          (s, T, r24[s], btc, dlo30, dv[s], side, cost, "open" if side else "control", int(time.time())))
        made += 1
    n = settle(st)
    print(time.strftime("%Y-%m-%d %H:%M"), f"T={T} btc24={btc if btc is None else round(btc, 4)} crashes={len(crashes)} "
          f"logged={made} settled={n}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
