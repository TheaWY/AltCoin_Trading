"""Forward paper test F6 (research/forward.yaml): short after a pump when open interest drops 3% from its post-onset high.
Source: B17_F04 oi_drop3|s5|t4h (validation +0.78%/trade, discovery +0.37%). Runs every 5 minutes (launchd com.altcoin.oidropshort).

Each run:
  1. onset scan: for each of the last 5 closed minutes T, perp close(T)/close(T-60m) - 1 >= 10%, dv24 >= $2M, >= 72h of 1m history,
     6h cooldown per coin -> row status 'watch' (ts_onset = T).
  2. OI poll: for every 'watch' row younger than 24h, fetch Binance fapi/v1/openInterest (REST, public) and append to oi_drop_short_oi;
     oi_high = max since onset. When oi <= 0.97 * oi_high -> status 'open', ts_entry = the next full minute after the poll.
  3. settle: for 'open' rows, once ts_entry + 4h + 2 min has passed: entry = open of minute ts_entry; walk 1m highs; if high >= entry*1.05 ->
     exit at entry*1.052 (stop + slippage) at that minute; else exit = open of minute ts_entry + 4h. net = -(exit/entry - 1) - cost + funding received.
Paper only; never places orders. Tables oi_drop_short_paper, oi_drop_short_oi."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

FEE, H_, D_ = 0.0005, 3600, 86400
STOP, HOLD, DROP = 0.05, 4 * H_, 0.03
SCHEMA = ["""CREATE TABLE IF NOT EXISTS oi_drop_short_paper (
  symbol TEXT NOT NULL, ts_onset BIGINT NOT NULL, ret_1h DOUBLE PRECISION, dv24 DOUBLE PRECISION, oi_high DOUBLE PRECISION,
  oi_entry DOUBLE PRECISION, ts_entry BIGINT, entry_px DOUBLE PRECISION, exit_px DOUBLE PRECISION, ts_exit BIGINT, stopped INTEGER,
  gross DOUBLE PRECISION, funding DOUBLE PRECISION, net DOUBLE PRECISION, cost DOUBLE PRECISION, status TEXT, created BIGINT,
  PRIMARY KEY (symbol, ts_onset))""",
          """CREATE TABLE IF NOT EXISTS oi_drop_short_oi (symbol TEXT NOT NULL, ts BIGINT NOT NULL, oi DOUBLE PRECISION, PRIMARY KEY (symbol, ts))"""]


def slip(dv: float) -> float:
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def q(st, sql, params=()):
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        if cur.description is None:
            return None
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def scan(st, now: int) -> int:
    T1 = now // 60 * 60 - 60
    mins = [T1 - 60 * k for k in range(5)]
    need = mins + [m - 3600 for m in mins]
    px = q(st, "SELECT symbol, ts, close FROM prices_1m WHERE ts = ANY(?)", (need,))
    if px is None or px.empty:
        return 0
    vol = q(st, "SELECT symbol, SUM(quote_volume) AS dv24 FROM prices_1m WHERE ts >= ? AND ts < ? GROUP BY symbol", (T1 - D_, T1))
    first = q(st, "SELECT symbol, MIN(ts) AS t0 FROM prices_1m WHERE ts >= ? GROUP BY symbol", (T1 - 4 * D_,))
    dv = dict(zip(vol["symbol"], vol["dv24"].astype(float))); t0 = dict(zip(first["symbol"], first["t0"].astype(int)))
    made = 0
    for sym, g in px.groupby("symbol"):
        g = g.set_index("ts")["close"].astype(float)
        if dv.get(sym, 0) < 2e6 or t0.get(sym, T1) > T1 - 72 * H_ + 300:
            continue
        for T in sorted(mins):
            if T in g.index and T - 3600 in g.index and g[T] / g[T - 3600] - 1 >= 0.10:
                recent = q(st, "SELECT 1 FROM oi_drop_short_paper WHERE symbol=? AND ts_onset > ? LIMIT 1", (sym, T - 6 * H_))
                if recent is not None and len(recent):
                    break
                q(st, "INSERT INTO oi_drop_short_paper (symbol, ts_onset, ret_1h, dv24, cost, status, created) VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                  (sym, T, float(g[T] / g[T - 3600] - 1), dv[sym], 2 * (FEE + slip(dv[sym])), "watch", now))
                made += 1
                break
    return made


def poll(st, now: int) -> int:
    w = q(st, "SELECT symbol, ts_onset, oi_high FROM oi_drop_short_paper WHERE status='watch'")
    n = 0
    for r in w.itertuples(index=False):
        if now > int(r.ts_onset) + D_:
            q(st, "UPDATE oi_drop_short_paper SET status='expired' WHERE symbol=? AND ts_onset=?", (r.symbol, r.ts_onset)); continue
        try:
            j = requests.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": r.symbol.replace("/", "")}, timeout=10).json()
            oi = float(j["openInterest"]); ts = int(j["time"]) // 1000
        except Exception:
            continue
        q(st, "INSERT INTO oi_drop_short_oi (symbol, ts, oi) VALUES (?,?,?) ON CONFLICT DO NOTHING", (r.symbol, ts, oi))
        hi = max(float(r.oi_high or 0), oi)
        if oi <= (1 - DROP) * hi and r.oi_high is not None:
            q(st, "UPDATE oi_drop_short_paper SET oi_high=?, oi_entry=?, ts_entry=?, status='open' WHERE symbol=? AND ts_onset=?",
              (hi, oi, ts // 60 * 60 + 60, r.symbol, r.ts_onset))
            n += 1
        else:
            q(st, "UPDATE oi_drop_short_paper SET oi_high=? WHERE symbol=? AND ts_onset=?", (hi, r.symbol, r.ts_onset))
    return n


def settle(st, now: int) -> int:
    op = q(st, "SELECT symbol, ts_onset, ts_entry, cost FROM oi_drop_short_paper WHERE status='open'")
    n = 0
    for r in op.itertuples(index=False):
        e0, e1 = int(r.ts_entry), int(r.ts_entry) + HOLD
        if now < e1 + 120:
            continue
        px = q(st, "SELECT ts, open, high FROM prices_1m WHERE symbol=? AND ts >= ? AND ts <= ? ORDER BY ts", (r.symbol, e0, e1))
        if px is None or len(px) < 200:
            if now > e1 + 3 * H_:
                q(st, "UPDATE oi_drop_short_paper SET status='no_data' WHERE symbol=? AND ts_onset=?", (r.symbol, r.ts_onset))
            continue
        p0 = float(px["open"].iloc[0]); hit = px[(px["ts"] > e0) & (px["high"] >= p0 * (1 + STOP))]
        if len(hit):
            lvl = p0 * (1 + STOP); fill = max(float(hit["open"].iloc[0]), lvl)          # gap-open above the stop fills at the open (matches b17_f08_exact)
            p1, t1, stopped = fill + 0.002 * p0, int(hit["ts"].iloc[0]), 1
        else:
            p1, t1, stopped = float(px["open"].iloc[-1]), int(px["ts"].iloc[-1]), 0
        fr = q(st, "SELECT COALESCE(SUM(funding_rate),0) AS f FROM funding_rates WHERE symbol=? AND timestamp > ? AND timestamp <= ?", (r.symbol, e0, t1))
        fund = float(fr["f"].iloc[0]) if fr is not None else 0.0
        g = -(p1 / p0 - 1)
        q(st, "UPDATE oi_drop_short_paper SET entry_px=?, exit_px=?, ts_exit=?, stopped=?, gross=?, funding=?, net=?, status='closed' WHERE symbol=? AND ts_onset=?",
          (p0, p1, t1, stopped, g, fund, g - r.cost + fund, r.symbol, r.ts_onset))
        n += 1
    return n


def main() -> int:
    st = get_storage()
    for s in SCHEMA:
        q(st, s)
    now = int(time.time())
    a, b, c = scan(st, now), poll(st, now), settle(st, now)
    print(time.strftime("%Y-%m-%d %H:%M"), f"onsets={a} entries={b} settled={c}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
