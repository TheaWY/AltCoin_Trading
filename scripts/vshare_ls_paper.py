"""F7 forward paper test (registered 2026-10-01): weekly Upbit-volume-share long/short.

Why: B21_EDGE best book. vshare_up@168h stayed positive under 1h delay, 2x costs, last-price delisting, $1M sizing and both
BTC regimes, beat a 100-permutation placebo, but its 2026 CI touched 0 [-0.03%, +1.39%] per week. Only a clean forward test
can settle it. Rules are frozen copies of b18_disc2.book / b21_edge.book:

  universe  Binance USDT perps TRADING, listed >= 30 days, 24h quote volume >= $5M, also listed on Upbit KRW
  signal    F = -(Upbit KRW value of the last completed hour, in USD) / (Binance perp quote volume of the same hour)
  book      long F >= 80th pct, short F <= 20th pct; keep held longs while F >= 70th pct and held shorts while <= 30th pct;
            equal weight, +0.5 / -0.5 gross; needs >= 30 coins
  hold      168h, rebalanced weekly, Thursday 00:05 UTC (09:05 KST)
  P&L       w x (exit/entry - 1) - w x funding paid - |dw| x (0.05% fee + slippage by dv24)   (b7_lib.slip)
Paper only. Never places orders. Table f7_vshare_paper: one row per leg per week.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

FAPI = "https://fapi.binance.com"
UP = "https://api.upbit.com/v1"
FEE = 0.0005
SCHEMA = """CREATE TABLE IF NOT EXISTS f7_vshare_paper (
  ts_signal BIGINT NOT NULL, symbol TEXT NOT NULL, f DOUBLE PRECISION, w DOUBLE PRECISION, dv24 DOUBLE PRECISION,
  entry_px DOUBLE PRECISION, exit_px DOUBLE PRECISION, ts_exit BIGINT, gross DOUBLE PRECISION, funding DOUBLE PRECISION,
  cost DOUBLE PRECISION, net DOUBLE PRECISION, status TEXT, PRIMARY KEY (ts_signal, symbol))"""


def db(sql, params=()):
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        if not cur.description:
            return None
        # storage uses dict_row on Postgres; unpacking a dict yields its KEYS, so normalise to tuples
        return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]


def slip(dv):
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def get(s, url, **p):
    for k in range(5):
        r = s.get(url, params=p, timeout=15)
        if r.status_code == 429:
            time.sleep(2 ** k)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"429 x5 {url}")


def snapshot(s):
    now = time.time()
    info = get(s, f"{FAPI}/fapi/v1/exchangeInfo")
    perp = {x["symbol"]: x for x in info["symbols"] if x.get("contractType") == "PERPETUAL" and x["status"] == "TRADING"
            and x["quoteAsset"] == "USDT" and now * 1000 - x.get("onboardDate", 0) >= 30 * 86400 * 1000}
    t24 = {x["symbol"]: float(x["quoteVolume"]) for x in get(s, f"{FAPI}/fapi/v1/ticker/24hr")}
    px = {x["symbol"]: float(x["price"]) for x in get(s, f"{FAPI}/fapi/v1/ticker/price")}
    krw = {m["market"][4:] for m in get(s, f"{UP}/market/all") if m["market"].startswith("KRW-")}
    usdkrw = get(s, f"{UP}/ticker", markets="KRW-USDT")[0]["trade_price"]
    rows = []
    for sym in perp:
        dv = t24.get(sym, 0.0)
        if dv < 5e6:
            continue
        base = sym[:-4]
        for pre in ("1000000", "1000", ""):
            if base.startswith(pre) and pre:
                base = base[len(pre):]
                break
        if base not in krw:
            continue
        kl = get(s, f"{FAPI}/fapi/v1/klines", symbol=sym, interval="1h", limit=2)
        uc = get(s, f"{UP}/candles/minutes/60", market=f"KRW-{base}", count=2)
        time.sleep(0.12)
        if len(kl) < 2 or len(uc) < 2:
            continue
        pq = float(kl[0][7])                                             # last completed perp hour
        uq = float(uc[1]["candle_acc_trade_price"]) / usdkrw               # Upbit returns newest first: [1] = last completed
        if pq <= 0 or uq <= 0:
            continue
        rows.append((sym, -uq / pq, dv, px[sym]))
    return rows


def funding_paid(s, sym, t0, t1):
    fr = get(s, f"{FAPI}/fapi/v1/fundingRate", symbol=sym, startTime=int(t0 * 1000), endTime=int(t1 * 1000), limit=1000)
    return float(sum(float(x["fundingRate"]) for x in fr))


def main() -> int:
    db(SCHEMA)
    s = requests.Session()
    now = int(time.time()) // 3600 * 3600
    if db("SELECT 1 FROM f7_vshare_paper WHERE ts_signal=? LIMIT 1", (now,)):
        print("already ran for", now); return 0
    snap = snapshot(s)
    print(time.strftime("%F %T"), "universe", len(snap), flush=True)
    px = {sym: p for sym, _, _, p in snap}
    allpx = {x["symbol"]: float(x["price"]) for x in get(s, f"{FAPI}/fapi/v1/ticker/price")}
    # close the open book
    prev = db("SELECT ts_signal, symbol, w, entry_px FROM f7_vshare_paper WHERE status='open'") or []
    w_prev = {}
    for t0, sym, w, e in prev:
        x = allpx.get(sym, px.get(sym))
        if x is None:                                                      # delisted: settle at entry (flag it)
            x, st = e, "closed_nopx"
        else:
            st = "closed"
        fund = w * funding_paid(s, sym, t0, now)
        g = w * (x / e - 1)
        db("UPDATE f7_vshare_paper SET exit_px=?, ts_exit=?, gross=?, funding=?, net=? - ? - COALESCE(cost,0), status=? "
           "WHERE ts_signal=? AND symbol=?", (x, now, g, fund, g, fund, st, t0, sym))
        w_prev[sym] = w
    # new book
    if len(snap) < 30:
        print("fewer than 30 coins, no book this week", flush=True)
        return 0
    f = np.array([r[1] for r in snap])
    q20, q30, q70, q80 = np.quantile(f, [0.2, 0.3, 0.7, 0.8])
    Lg = [r for r in snap if r[1] >= q80 or (w_prev.get(r[0], 0) > 0 and r[1] >= q70)]
    Sg = [r for r in snap if r[1] <= q20 or (w_prev.get(r[0], 0) < 0 and r[1] <= q30)]
    w_new = {r[0]: 0.5 / len(Lg) for r in Lg}
    w_new.update({r[0]: -0.5 / len(Sg) for r in Sg})
    info = {r[0]: r for r in snap}
    turn = 0.0
    for sym in set(w_new) | set(w_prev):
        dw = abs(w_new.get(sym, 0.0) - w_prev.get(sym, 0.0))
        dv = info[sym][2] if sym in info else 1e6
        c = dw * (FEE + slip(dv))
        turn += dw
        if sym in w_new:
            r = info[sym]
            db("INSERT INTO f7_vshare_paper (ts_signal, symbol, f, w, dv24, entry_px, cost, status) VALUES (?,?,?,?,?,?,?,'open')",
               (now, sym, r[1], w_new[sym], r[2], r[3], c))
        else:                                                              # exit-only cost charged to the closed leg
            db("UPDATE f7_vshare_paper SET cost=COALESCE(cost,0)+?, net=net-? WHERE symbol=? AND ts_exit=?", (c, c, sym, now))
    print(time.strftime("%F %T"), f"book {len(Lg)}L/{len(Sg)}S closed {len(prev)} turnover {turn:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
