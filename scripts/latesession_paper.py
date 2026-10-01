"""F8 forward paper test (registered 2026-10-01): late-session alt continuation.

Origin: B27_INTRADAY found one cell out of 46: if the liquid-alt basket is up from 09:00 KST (00:00 UTC) to 01:00 KST
(16:00 UTC), it tended to keep rising until 09:00 KST (+24bp) and to keep falling after a down start (-31bp); AUC 0.574
(2026: 0.550). One cell among many, so it is only allowed a clean forward test. Rules are frozen here:

  basket   top 50 Binance USDT perps by 24h quote volume, crypto only (underlyingType COIN), listed >= 30 days, BTC excluded
  signal   at 16:00 UTC: mean over the basket of log(close 15:00-16:00 bar / open 00:00 bar)   (equal weight)
  trade    signal > 0 -> long the basket equal weight; signal <= 0 -> short it; entry at last price at 16:0x UTC,
           exit at last price at 00:0x UTC (8 hours)
  P&L      side x mean(exit/entry - 1) - 2 x mean(0.05% fee + slippage by dv24) ; funding ignored (one 00:00 UTC settlement
           falls at the exit and is not paid by an exit at 00:0x)
  pass     >= 120 trading days and day-level net bootstrap 95% CI above 0; kill at 120 days with mean net <= 0
Runs twice a day (launchd com.altcoin.latesession): `open` at 16:05 UTC (01:05 KST), `close` at 00:05 UTC (09:05 KST).
Paper only. Never places orders. Table f8_latesession_paper.
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
FEE = 0.0005
SCHEMA = """CREATE TABLE IF NOT EXISTS f8_latesession_paper (
  day BIGINT PRIMARY KEY, signal DOUBLE PRECISION, side INTEGER, n INTEGER, symbols TEXT, entry_ts BIGINT, exit_ts BIGINT,
  entry_px TEXT, exit_px TEXT, gross DOUBLE PRECISION, cost DOUBLE PRECISION, net DOUBLE PRECISION, status TEXT)"""


def db(sql, params=()):
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor(); cur.execute(sql.replace("?", "%s"), params)
        return cur.fetchall() if cur.description else None


def slip(dv):
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def get(s, path, **p):
    for k in range(5):
        r = s.get(FAPI + path, params=p, timeout=15)
        if r.status_code == 429:
            time.sleep(2 ** k); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError("429")


def prices(s):
    return {x["symbol"]: float(x["price"]) for x in get(s, "/fapi/v1/ticker/price")}


def open_trade(s):
    now = time.time(); day = int(now // 86400)
    if db("SELECT 1 FROM f8_latesession_paper WHERE day=?", (day,)):
        return
    info = get(s, "/fapi/v1/exchangeInfo")["symbols"]
    ok = {x["symbol"] for x in info if x.get("contractType") == "PERPETUAL" and x["status"] == "TRADING" and x["quoteAsset"] == "USDT"
          and x.get("underlyingType", "COIN") == "COIN" and now * 1000 - x.get("onboardDate", 0) >= 30 * 86400 * 1000} - {"BTCUSDT"}
    t24 = sorted(((x["symbol"], float(x["quoteVolume"])) for x in get(s, "/fapi/v1/ticker/24hr") if x["symbol"] in ok), key=lambda r: -r[1])[:50]
    t0 = day * 86400 * 1000
    sig, syms, dvs = [], [], []
    for sym, dv in t24:
        k = get(s, "/fapi/v1/klines", symbol=sym, interval="1h", startTime=t0, limit=16)
        if len(k) < 16:
            continue
        sig.append(np.log(float(k[15][4]) / float(k[0][1]))); syms.append(sym); dvs.append(dv)
        time.sleep(0.05)
    px = prices(s)
    side = 1 if np.mean(sig) > 0 else -1
    cost = 2 * float(np.mean([FEE + slip(d) for d in dvs]))
    db("INSERT INTO f8_latesession_paper (day, signal, side, n, symbols, entry_ts, entry_px, cost, status) VALUES (?,?,?,?,?,?,?,?,'open')",
       (day, float(np.mean(sig)), side, len(syms), ",".join(syms), int(now), ",".join(str(px[x]) for x in syms), cost))
    print(time.strftime("%F %T"), f"open day {day} signal {np.mean(sig):+.4f} side {side} n {len(syms)}", flush=True)


def close_trade(s):
    rows = db("SELECT day, side, symbols, entry_px, cost FROM f8_latesession_paper WHERE status='open'") or []
    px = prices(s)
    for day, side, symbols, entry, cost in rows:
        syms, e = symbols.split(","), [float(v) for v in entry.split(",")]
        r = [px[a] / b - 1 for a, b in zip(syms, e) if a in px]
        g = side * float(np.mean(r))
        db("UPDATE f8_latesession_paper SET exit_ts=?, exit_px=?, gross=?, net=?, status='closed' WHERE day=?",
           (int(time.time()), ",".join(str(px.get(a, "")) for a in syms), g, g - cost, day))
        print(time.strftime("%F %T"), f"close day {day} gross {g:+.4f} net {g - cost:+.4f}", flush=True)


def main() -> int:
    db(SCHEMA)
    s = requests.Session()
    mode = sys.argv[1] if len(sys.argv) > 1 else ("open" if time.gmtime().tm_hour == 16 else "close")
    (open_trade if mode == "open" else close_trade)(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
