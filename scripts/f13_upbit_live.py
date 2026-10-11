"""F13 (registered 2026-10-03): Upbit-executed notice strategies, paper only, priced off the LIVE Upbit KRW order book.

Why: SAND (+42%) and POD (+82% first hour) on 2026-10-02 both happened on Upbit spot. POD had no Binance perp at all, and
M-series showed Korea never leads Binance at the minute level, so the Binance-mirrored tests (F1/F11) structurally miss the
move. This track asks the only question that matters for those events: what would a long on Upbit itself have made?

Rules (long only; Upbit spot cannot short):
  krw_listing     -> poll the KRW-<SYM> order book from detection until it first shows a live ask (trading open, can be
                     hours after the notice: POD notice 13:37, open 16:00); buy at the first book, hold 60 min, stop -5%.
                     B35: 153 past Upbit listings, 1h mean +7.8%, median +0.1% -> the tail pays, so the stop must be wide.
  warning_lifted / warning / other on an existing KRW market -> wait for +3% on mid within 10 min of detection, buy at the
                     touch, stop -3%, hold 4h (same confirmation gate as F11, executed where the move actually is).
  delisting       -> skipped (no short on spot).
Fill model: VWAP of walking the live book for NOTIONAL_KRW (default 1,000,000 KRW), Upbit KRW fee 0.05% per side.
Denominator is honest: no_confirm / never_opened rows stay in the table. Never places orders.
Table f13_upbit_live_paper. Called from upbit_listing_watcher for every fresh notice (perp-listed or not).
"""
from __future__ import annotations

import threading
import time

import requests

UPBIT = "https://api.upbit.com/v1"
FEE = 0.0005
NOTIONAL_KRW = 1_000_000
CONFIRM, WINDOW_S = 0.03, 600
LIST_HOLD_S, LIST_STOP, LIST_WAIT_S = 3600, 0.05, 24 * 3600
MOM_HOLD_S, MOM_STOP = 4 * 3600, 0.03
POLL_S = 2.0

SCHEMA = """CREATE TABLE IF NOT EXISTS f13_upbit_live_paper (
  notice_id BIGINT NOT NULL, market TEXT NOT NULL, kind TEXT, title TEXT, first_listed_ts DOUBLE PRECISION,
  detect_ts DOUBLE PRECISION, open_ts DOUBLE PRECISION, mid0 DOUBLE PRECISION, confirm_ts DOUBLE PRECISION,
  confirm_move DOUBLE PRECISION, entry_ts DOUBLE PRECISION, entry_px DOUBLE PRECISION, entry_vwap DOUBLE PRECISION,
  exit_ts DOUBLE PRECISION, exit_px DOUBLE PRECISION, exit_vwap DOUBLE PRECISION, exit_reason TEXT,
  gross DOUBLE PRECISION, cost DOUBLE PRECISION, net DOUBLE PRECISION, status TEXT, PRIMARY KEY (notice_id, market))"""


def orderbook(s: requests.Session, market: str):
    """Live Upbit L5 book -> (bid1, ask1, units) or None when the market does not exist / is not trading yet."""
    r = s.get(f"{UPBIT}/orderbook", params={"markets": market}, timeout=5)
    if r.status_code != 200:
        return None
    d = r.json()
    if not d or not d[0].get("orderbook_units"):
        return None
    u = d[0]["orderbook_units"]
    if not u[0]["ask_price"] or not u[0]["bid_price"] or u[0]["ask_size"] <= 0:
        return None
    return float(u[0]["bid_price"]), float(u[0]["ask_price"]), u


def vwap(units, side: str, notional_krw: float) -> float:
    """Walk the book: average price paid to buy (side='ask') or received to sell (side='bid') notional_krw."""
    left, cost, qty = notional_krw, 0.0, 0.0
    for u in units:
        px, sz = float(u[f"{side}_price"]), float(u[f"{side}_size"])
        take = min(sz, left / px)
        cost += take * px; qty += take; left -= take * px
        if left <= 0:
            break
    if qty == 0:
        return float(units[0][f"{side}_price"])
    if left > 0:  # book thinner than the order: pay the last level for the remainder (worst case)
        px = float(units[-1][f"{side}_price"]); qty += left / px; cost += left
    return cost / qty


def upbit_live(db, nid: int, kind: str, sym: str, title: str, first: float):
    market = f"KRW-{sym}"
    if kind == "delisting":
        return

    def run():
        s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"
        t_det = time.time()
        try:
            db("INSERT INTO f13_upbit_live_paper (notice_id, market, kind, title, first_listed_ts, detect_ts, status) "
               "VALUES (?,?,?,?,?,?,'watching') ON CONFLICT DO NOTHING", (nid, market, kind, title[:200], first, t_det))
            if kind == "krw_listing":
                # existing market already trading (e.g. BTC->KRW market add)? then it is a momentum case, not an open
                ob = orderbook(s, market)
                pre_exists = ob is not None
                if not pre_exists:
                    while time.time() - t_det < LIST_WAIT_S:
                        time.sleep(POLL_S); ob = orderbook(s, market)
                        if ob is not None:
                            break
                    if ob is None:
                        db("UPDATE f13_upbit_live_paper SET status='never_opened' WHERE notice_id=? AND market=?", (nid, market)); return
                bid, ask, units = ob
                t_in = time.time(); entry, entry_vwap = ask, vwap(units, "ask", NOTIONAL_KRW)
                mid0 = (bid + ask) / 2
                db("UPDATE f13_upbit_live_paper SET open_ts=?, mid0=?, entry_ts=?, entry_px=?, entry_vwap=?, status='open' "
                   "WHERE notice_id=? AND market=?", (t_in, mid0, t_in, entry, entry_vwap, nid, market))
                hold, stop = (MOM_HOLD_S, MOM_STOP) if pre_exists else (LIST_HOLD_S, LIST_STOP)
                print(time.strftime("%F %T"), f"F13 open {market} listing at {entry} (vwap {entry_vwap:.6g}) {t_in - t_det:.0f}s after notice", flush=True)
            else:
                ob = orderbook(s, market)
                if ob is None:
                    db("UPDATE f13_upbit_live_paper SET status='no_market' WHERE notice_id=? AND market=?", (nid, market)); return
                bid, ask, units = ob; mid0 = (bid + ask) / 2
                db("UPDATE f13_upbit_live_paper SET mid0=? WHERE notice_id=? AND market=?", (mid0, nid, market))
                entry = None
                while time.time() - t_det < WINDOW_S:
                    time.sleep(POLL_S); ob = orderbook(s, market)
                    if ob is None:
                        continue
                    bid, ask, units = ob; mv = (bid + ask) / 2 / mid0 - 1
                    if mv >= CONFIRM:
                        entry, entry_vwap, t_in = ask, vwap(units, "ask", NOTIONAL_KRW), time.time(); break
                    if mv <= -CONFIRM:
                        db("UPDATE f13_upbit_live_paper SET confirm_ts=?, confirm_move=?, status='no_confirm_down' WHERE notice_id=? AND market=?",
                           (time.time(), mv, nid, market)); return
                if entry is None:
                    db("UPDATE f13_upbit_live_paper SET status='no_confirm' WHERE notice_id=? AND market=?", (nid, market)); return
                db("UPDATE f13_upbit_live_paper SET confirm_ts=?, confirm_move=?, entry_ts=?, entry_px=?, entry_vwap=?, status='open' "
                   "WHERE notice_id=? AND market=?", (t_in, entry / mid0 - 1, t_in, entry, entry_vwap, nid, market))
                hold, stop = MOM_HOLD_S, MOM_STOP
                print(time.strftime("%F %T"), f"F13 open {market} {kind} at {entry} (vwap {entry_vwap:.6g}) after {t_in - t_det:.0f}s", flush=True)
            reason, px, exit_vwap = None, entry, entry_vwap
            while reason is None:
                time.sleep(POLL_S * 2); ob = orderbook(s, market)
                if ob is None:
                    continue
                bid, ask, units = ob; px = bid
                if px / entry_vwap - 1 <= -stop:
                    reason = "stop"
                elif time.time() - t_in >= hold:
                    reason = f"time_{hold // 60}m"
                if reason:
                    exit_vwap = vwap(units, "bid", NOTIONAL_KRW)
            gross = exit_vwap / entry_vwap - 1; cost = 2 * FEE; net = gross - cost
            db("UPDATE f13_upbit_live_paper SET exit_ts=?, exit_px=?, exit_vwap=?, exit_reason=?, gross=?, cost=?, net=?, status='closed' "
               "WHERE notice_id=? AND market=?", (time.time(), px, exit_vwap, reason, gross, cost, net, nid, market))
            print(time.strftime("%F %T"), f"F13 close {market} {reason} gross {gross:+.4f} net {net:+.4f}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(time.strftime("%F %T"), "F13 error", market, repr(e)[:120], flush=True)
            db("UPDATE f13_upbit_live_paper SET status='error' WHERE notice_id=? AND market=? AND status IN ('watching','open')", (nid, market))
    threading.Thread(target=run, daemon=True).start()
