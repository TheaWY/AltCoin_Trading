"""Forward paper test F15 (registered 2026-10-05 after B51 H1 PASS): Upbit BTC/ETH trend allocation, long only.

Rule exactly as prereg v2 H1 (research/prereg_v2_longonly.md): per coin w = mean over L in {20,50,100,200} of
1[close > SMA_L] on completed Upbit KRW daily candles (UTC 00:00 = 09:00 KST); w decided at a day's close is held from the
next day's open. Portfolio = 50/50 BTC/ETH sleeves, rest KRW cash. Costs 0.05% fee + slippage tier per unit of turnover.
Benchmark = 50/50 buy-and-hold.
F17 (registered 2026-10-05 after B52 P PASS) is recorded in the same rows: w_p = loss-protection overlay
(prereg v3 P: crash brake + volatility scaling, b52_ride_protect.p_weight) and ret_p its net return. Forward sample = trade days >= 2026-10-06. Stateless: each run recomputes the series from
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
from b52_ride_protect import p_weight  # noqa: E402

COINS, LOOKS, FEE = ("KRW-BTC", "KRW-ETH"), (20, 50, 100, 200), 0.0005
START = pd.Timestamp("2026-10-06")
SCHEMA = """CREATE TABLE IF NOT EXISTS f15_trend_paper (
  coin TEXT NOT NULL, day DATE NOT NULL, w DOUBLE PRECISION, open_px DOUBLE PRECISION, ret_coin DOUBLE PRECISION,
  ret_net DOUBLE PRECISION, status TEXT, updated BIGINT, PRIMARY KEY (coin, day))"""
ALTER = "ALTER TABLE f15_trend_paper ADD COLUMN IF NOT EXISTS w_p DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS ret_p DOUBLE PRECISION"


def daily(m, pages=3):
    rows, to = [], None
    for _ in range(pages):                           # 3 x 200 days: enough for SMA200 and the 365d vol median
        prm = {"market": m, "count": 200} | ({"to": to} if to else {})
        r = requests.get("https://api.upbit.com/v1/candles/days", params=prm, timeout=15)
        r.raise_for_status(); js = r.json()
        if not js:
            break
        rows += js; to = js[-1]["candle_date_time_utc"] + "Z"; time.sleep(0.15)
    d = pd.DataFrame(rows).drop_duplicates("candle_date_time_utc")
    d["day"] = pd.to_datetime(d["candle_date_time_utc"]).dt.normalize()
    d = d.set_index("day").sort_index()
    return d.rename(columns={"opening_price": "o", "trade_price": "c", "candle_acc_trade_price": "v"})[["o", "c", "v"]]


def slip(medv):
    return 0.0002 if medv >= 1e11 else 0.0005 if medv >= 1e10 else 0.0010 if medv >= 1e9 else 0.0020


def main():
    st = get_storage(); q(st, SCHEMA); q(st, ALTER)
    today = pd.Timestamp.now('UTC').tz_localize(None).normalize()
    now = int(time.time())
    for m in COINS:
        d = daily(m)
        done = d[d.index < today]                        # completed candles only
        c = done["c"]
        w = sum((c > c.rolling(L, min_periods=L).mean()).astype(float) for L in LOOKS) / len(LOOKS)
        wpp = p_weight(c)
        # trade day t holds w decided at close of t-1; opens of t and t+1 give the day's return
        for t in d.index[d.index >= START]:
            prev = t - pd.Timedelta(days=1)
            if prev not in w.index:
                continue
            wt, wp = float(w[prev]), float(w.get(prev - pd.Timedelta(days=1), w[prev]))
            nxt = t + pd.Timedelta(days=1)
            medv = float(done["v"].loc[:prev].iloc[-30:].median())
            pt, pp = float(wpp.get(prev, float("nan"))), float(wpp.get(prev - pd.Timedelta(days=1), wpp.get(prev, float("nan"))))
            rc = rn = rp = None; status = "open"
            if nxt in d.index:
                rc = float(d.at[nxt, "o"] / d.at[t, "o"] - 1)
                rn = wt * rc - abs(wt - wp) * (FEE + slip(medv)); status = "closed"
                rp = pt * rc - abs(pt - pp) * (FEE + slip(medv))
            q(st, "INSERT INTO f15_trend_paper (coin, day, w, open_px, ret_coin, ret_net, status, updated, w_p, ret_p) VALUES (?,?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT (coin, day) DO UPDATE SET w=EXCLUDED.w, open_px=EXCLUDED.open_px, ret_coin=EXCLUDED.ret_coin, "
                  "ret_net=EXCLUDED.ret_net, status=EXCLUDED.status, updated=EXCLUDED.updated, w_p=EXCLUDED.w_p, ret_p=EXCLUDED.ret_p",
              (m, t.date(), wt, float(d.at[t, "o"]), rc, rn, status, now, pt, rp))
        print(time.strftime("%F %T"), m, "w today", float(w.iloc[-1]) if len(w) else None, "P today", round(float(wpp.iloc[-1]), 3), flush=True)
    s = q(st, "SELECT day, avg(ret_net) AS s, avg(ret_coin) AS b, count(*) AS n FROM f15_trend_paper WHERE status='closed' GROUP BY day HAVING count(*)=2")
    if s is not None and len(s):
        print("days", len(s), "cum strat", float((1 + s.s).prod() - 1), "cum bh", float((1 + s.b).prod() - 1), flush=True)


if __name__ == "__main__":
    main()
