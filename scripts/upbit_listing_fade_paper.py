"""F9 forward paper test (registered 2026-10-01): post-Upbit-listing fade on the Binance perp.

Origin: B31 D2 + B31b. Coins already on Binance >= 30 days that get a new Upbit KRW market fall after the listing day:
market-adjusted CAR days 1..10 = -3.2% (clean in-sample, 27 events, 70% negative) and -6.7% (holdout, 56 events, 71%
negative, t -2.35); short-perp net of funding and 12bp round trip +1.8% / +2.6% per event. Day 0 (the pump) is excluded.

Rules (frozen):
  event   a 'krw_listing' notice recorded by the F1 watcher (upbit_notice_paper, status not 'stale'), coin is a Binance
          USDT perp listed >= 30 days
  entry   first run at >= 00:05 UTC on the UTC day AFTER the notice day (day 1), short at the Binance last price
  exit    10 days later at the 00:05 UTC run (last price)
  P&L     -(exit/entry - 1) + funding received (sum of funding rates over the hold) - 2 x (0.05% + slippage by dv24)
  pass    >= 30 closed events and mean net > 0 with a bootstrap 95% CI above 0; kill at 30 events with mean net <= 0
Runs daily 09:05 KST (launchd com.altcoin.listingfade). Paper only. Table f9_listing_fade_paper.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

FAPI = "https://fapi.binance.com"
FEE = 0.0005
SCHEMA = """CREATE TABLE IF NOT EXISTS f9_listing_fade_paper (
  symbol TEXT NOT NULL, notice_id BIGINT NOT NULL, notice_ts DOUBLE PRECISION, entry_ts BIGINT, entry_px DOUBLE PRECISION,
  exit_ts BIGINT, exit_px DOUBLE PRECISION, dv24 DOUBLE PRECISION, funding DOUBLE PRECISION, cost DOUBLE PRECISION,
  gross DOUBLE PRECISION, net DOUBLE PRECISION, status TEXT, PRIMARY KEY (symbol, notice_id))"""


def db(sql, params=()):
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor(); cur.execute(sql.replace("?", "%s"), params)
        return cur.fetchall() if cur.description else None


def slip(dv):
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def main() -> int:
    db(SCHEMA)
    s = requests.Session(); now = time.time(); today = int(now // 86400)
    px = {x["symbol"]: float(x["price"]) for x in s.get(f"{FAPI}/fapi/v1/ticker/price", timeout=15).json()}
    # close
    for sym, nid, ets, epx, dv in db("SELECT symbol, notice_id, entry_ts, entry_px, dv24 FROM f9_listing_fade_paper WHERE status='open'") or []:
        if now - ets < 10 * 86400 - 3600:
            continue
        fr = s.get(f"{FAPI}/fapi/v1/fundingRate", params={"symbol": sym, "startTime": int(ets * 1000), "endTime": int(now * 1000), "limit": 1000}, timeout=15).json()
        fund = sum(float(x["fundingRate"]) for x in fr)
        x = px.get(sym, epx); g = -(x / epx - 1); cost = 2 * (FEE + slip(dv))
        db("UPDATE f9_listing_fade_paper SET exit_ts=?, exit_px=?, funding=?, cost=?, gross=?, net=?, status=? WHERE symbol=? AND notice_id=?",
           (int(now), x, fund, cost, g, g + fund - cost, "closed" if sym in px else "closed_nopx", sym, nid))
        print(time.strftime("%F %T"), "close", sym, f"net {g + fund - cost:+.4f}", flush=True)
    # open: notices from the previous UTC day(s) that are not stale and not yet entered
    info = {x["symbol"]: x for x in s.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=20).json()["symbols"]}
    t24 = {x["symbol"]: float(x["quoteVolume"]) for x in s.get(f"{FAPI}/fapi/v1/ticker/24hr", timeout=15).json()}
    rows = db("SELECT notice_id, symbol, detect_ts FROM upbit_notice_paper WHERE kind='krw_listing' AND status <> 'stale' "
              "AND detect_ts >= ? AND detect_ts < ?", (today * 86400 - 3 * 86400, today * 86400)) or []
    for nid, sym, dts in rows:
        if db("SELECT 1 FROM f9_listing_fade_paper WHERE symbol=? AND notice_id=?", (sym, nid)):
            continue
        x = info.get(sym)
        if not x or x["status"] != "TRADING" or now * 1000 - x.get("onboardDate", now * 1000) < 30 * 86400 * 1000 or sym not in px:
            continue
        db("INSERT INTO f9_listing_fade_paper (symbol, notice_id, notice_ts, entry_ts, entry_px, dv24, status) VALUES (?,?,?,?,?,?,'open')",
           (sym, nid, dts, int(now), px[sym], t24.get(sym, 0.0)))
        print(time.strftime("%F %T"), "open short", sym, px[sym], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
