"""Forward paper test F4 (research/forward.yaml): short spot-led +10% pumps.

Runs hourly at minute 4 (launchd com.altcoin.spotled). For the hour that just closed (T):
  trigger = perp close(T) / close(T-1h) - 1 >= 10%, 24h quote volume >= $2M, >= 72h of 1m history, 24h cooldown per coin
  S1      = spot share of (spot + perp) quote volume over the last 6h minus the same share over the last 168h
            (Binance public REST 1h klines, only for triggered coins)
  side    = -1 (paper SHORT) if S1 >= 0.01999 (frozen discovery threshold), else 0 (control, hypothetical short still settled)
  entry   = open of the minute T+60s, exit = open of the minute T+60s+24h; net = short return - fees/slippage + funding received
Paper only; never places orders. Table spot_led_paper.
"""
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
THRESH = 0.01999
SCHEMA = """CREATE TABLE IF NOT EXISTS spot_led_paper (
  symbol TEXT NOT NULL, ts_signal BIGINT NOT NULL, ret_1h DOUBLE PRECISION, dv24 DOUBLE PRECISION, s1 DOUBLE PRECISION,
  side INTEGER, entry_px DOUBLE PRECISION, exit_px DOUBLE PRECISION, gross DOUBLE PRECISION, funding DOUBLE PRECISION,
  net DOUBLE PRECISION, cost DOUBLE PRECISION, status TEXT, created BIGINT, PRIMARY KEY (symbol, ts_signal))"""


def slip(dv: float) -> float:
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def q(st, sql, params=()):
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        if cur.description is None:
            return None
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def klines(url, sym, end_ms):
    r = requests.get(url, params={"symbol": sym, "interval": "1h", "limit": 170, "endTime": end_ms - 1}, timeout=15)
    if r.status_code != 200:
        return None
    d = pd.DataFrame(r.json())
    if d.empty:
        return None
    return pd.DataFrame({"t": d[0].astype("int64") // 1000, "qv": d[7].astype(float)}).set_index("t")["qv"]


def spot_share(symbol: str, T: int):
    base = symbol.split("/")[0]
    perp_sym = base + "USDT"
    spot_base = base[4:] if base.startswith("1000") and not base.startswith("1000000") else base
    perp = klines("https://fapi.binance.com/fapi/v1/klines", perp_sym, T * 1000)
    spot = klines("https://api.binance.com/api/v3/klines", spot_base + "USDT", T * 1000)
    if perp is None or spot is None or len(perp) < 100:
        return None
    df = pd.concat([perp.rename("p"), spot.rename("s")], axis=1).fillna(0.0).sort_index()
    df = df[df.index < T]
    last6, last168 = df.iloc[-6:], df.iloc[-168:]
    sh6 = last6["s"].sum() / max(last6["s"].sum() + last6["p"].sum(), 1e-9)
    sh168 = last168["s"].sum() / max(last168["s"].sum() + last168["p"].sum(), 1e-9)
    return sh6 - sh168


def settle(st) -> int:
    op = q(st, "SELECT symbol, ts_signal, cost, status FROM spot_led_paper WHERE status IN ('open','control')")
    n, now = 0, time.time()
    for r in op.itertuples(index=False):
        e0, e1 = int(r.ts_signal) + 60, int(r.ts_signal) + 60 + D_
        if now < e1 + 120:
            continue
        px = q(st, "SELECT ts, open FROM prices_1m WHERE symbol=? AND ts IN (?, ?)", (r.symbol, e0, e1))
        if px is None or len(px) < 2:
            if now > e1 + 3 * H_:
                q(st, "UPDATE spot_led_paper SET status=? WHERE symbol=? AND ts_signal=?",
                  ("no_data" if r.status == "open" else "control_no_data", r.symbol, r.ts_signal))
            continue
        p0 = float(px.loc[px["ts"] == e0, "open"].iloc[0])
        p1 = float(px.loc[px["ts"] == e1, "open"].iloc[0])
        fr = q(st, "SELECT COALESCE(SUM(funding_rate),0) AS f FROM funding_rates WHERE symbol=? AND timestamp > ? AND timestamp <= ?",
               (r.symbol, e0, e1))
        fund = float(fr["f"].iloc[0]) if fr is not None else 0.0
        g = -(p1 / p0 - 1)                                   # short
        q(st, "UPDATE spot_led_paper SET entry_px=?, exit_px=?, gross=?, funding=?, net=?, status=? WHERE symbol=? AND ts_signal=?",
          (p0, p1, g, fund, g - r.cost + fund, "closed" if r.status == "open" else "control_closed", r.symbol, r.ts_signal))
        n += 1
    return n


def main() -> int:
    st = get_storage()
    q(st, SCHEMA)
    T = int(time.time()) // H_ * H_
    last, prev = T - 60, T - 3660
    px = q(st, "SELECT symbol, ts, close FROM prices_1m WHERE ts IN (?, ?)", (last, prev))
    vol = q(st, "SELECT symbol, SUM(quote_volume) AS dv24 FROM prices_1m WHERE ts >= ? AND ts < ? GROUP BY symbol", (T - D_, T))
    first = q(st, "SELECT symbol, MIN(ts) AS t0 FROM prices_1m WHERE ts >= ? GROUP BY symbol", (T - 4 * D_,))
    dv = dict(zip(vol["symbol"], vol["dv24"].astype(float)))
    t0 = dict(zip(first["symbol"], first["t0"].astype(int)))
    made = 0
    for sym, g in px.groupby("symbol"):
        g = g.set_index("ts")["close"].astype(float)
        if last not in g.index or prev not in g.index:
            continue
        r1 = g[last] / g[prev] - 1
        if r1 < 0.10 or dv.get(sym, 0) < 2e6 or t0.get(sym, T) > T - 72 * H_ + 300:
            continue
        recent = q(st, "SELECT 1 FROM spot_led_paper WHERE symbol=? AND ts_signal > ? LIMIT 1", (sym, T - D_))
        if recent is not None and len(recent):
            continue
        try:
            s1 = spot_share(sym, T)
        except Exception:
            s1 = None
        side = -1 if (s1 is not None and s1 >= THRESH) else 0
        q(st, "INSERT INTO spot_led_paper (symbol, ts_signal, ret_1h, dv24, s1, side, cost, status, created) "
              "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
          (sym, T, r1, dv[sym], s1, side, 2 * (FEE + slip(dv[sym])), "open" if side else "control", int(time.time())))
        made += 1
    n = settle(st)
    print(time.strftime("%Y-%m-%d %H:%M"), f"T={T} pumps={made} settled={n}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
