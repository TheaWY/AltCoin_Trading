"""Forward paper test F15 (registered 2026-10-05 after B51 H1 PASS): Upbit BTC/ETH trend allocation, long only.

Rule exactly as prereg v2 H1 (research/prereg_v2_longonly.md): per coin w = mean over L in {20,50,100,200} of
1[close > SMA_L] on completed Upbit KRW daily candles (UTC 00:00 = 09:00 KST); w decided at a day's close is held from the
next day's open. Portfolio = 50/50 BTC/ETH sleeves, rest KRW cash. Costs 0.05% fee + slippage tier per unit of turnover.
Benchmark = 50/50 buy-and-hold. Forward sample = trade days >= 2026-10-06. Stateless: each run recomputes the series from
public candles and upserts rows into f15_trend_paper. Never places orders; LIVE_TRADING is not read.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from f2b_shadow_paper import q  # noqa: E402
from src.data.storage import get_storage  # noqa: E402

COINS, LOOKS, FEE = ("KRW-BTC", "KRW-ETH"), (20, 50, 100, 200), 0.0005
START = pd.Timestamp("2026-10-06")
SCHEMA = """CREATE TABLE IF NOT EXISTS f15_trend_paper (
  coin TEXT NOT NULL, day DATE NOT NULL, w DOUBLE PRECISION, open_px DOUBLE PRECISION, ret_coin DOUBLE PRECISION,
  ret_net DOUBLE PRECISION, status TEXT, updated BIGINT, PRIMARY KEY (coin, day))"""


def daily(m):
    r = requests.get("https://api.upbit.com/v1/candles/days", params={"market": m, "count": 260}, timeout=15)
    r.raise_for_status()
    d = pd.DataFrame(r.json())
    d["day"] = pd.to_datetime(d["candle_date_time_utc"]).dt.normalize()
    d = d.set_index("day").sort_index()
    return d.rename(columns={"opening_price": "o", "trade_price": "c", "candle_acc_trade_price": "v"})[["o", "c", "v"]]


def slip(medv):
    return 0.0002 if medv >= 1e11 else 0.0005 if medv >= 1e10 else 0.0010 if medv >= 1e9 else 0.0020


def main():
    st = get_storage(); q(st, SCHEMA)
    today = pd.Timestamp.now('UTC').tz_localize(None).normalize()
    now = int(time.time())
    for m in COINS:
        d = daily(m)
        done = d[d.index < today]                        # completed candles only
        c = done["c"]
        w = sum((c > c.rolling(L, min_periods=L).mean()).astype(float) for L in LOOKS) / len(LOOKS)
        # trade day t holds w decided at close of t-1; opens of t and t+1 give the day's return
        for t in d.index[d.index >= START]:
            prev = t - pd.Timedelta(days=1)
            if prev not in w.index:
                continue
            wt, wp = float(w[prev]), float(w.get(prev - pd.Timedelta(days=1), w[prev]))
            nxt = t + pd.Timedelta(days=1)
            medv = float(done["v"].loc[:prev].iloc[-30:].median())
            rc = rn = None; status = "open"
            if nxt in d.index:
                rc = float(d.at[nxt, "o"] / d.at[t, "o"] - 1)
                rn = wt * rc - abs(wt - wp) * (FEE + slip(medv)); status = "closed"
            q(st, "INSERT INTO f15_trend_paper (coin, day, w, open_px, ret_coin, ret_net, status, updated) VALUES (?,?,?,?,?,?,?,?) "
                  "ON CONFLICT (coin, day) DO UPDATE SET w=EXCLUDED.w, open_px=EXCLUDED.open_px, ret_coin=EXCLUDED.ret_coin, "
                  "ret_net=EXCLUDED.ret_net, status=EXCLUDED.status, updated=EXCLUDED.updated",
              (m, t.date(), wt, float(d.at[t, "o"]), rc, rn, status, now))
        print(time.strftime("%F %T"), m, "w today", float(w.iloc[-1]) if len(w) else None, flush=True)
    s = q(st, "SELECT day, avg(ret_net) AS s, avg(ret_coin) AS b, count(*) AS n FROM f15_trend_paper WHERE status='closed' GROUP BY day HAVING count(*)=2")
    if s is not None and len(s):
        print("days", len(s), "cum strat", float((1 + s.s).prod() - 1), "cum bh", float((1 + s.b).prod() - 1), flush=True)


if __name__ == "__main__":
    main()
