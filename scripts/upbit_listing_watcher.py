"""Forward paper test F1 (research/forward.yaml): Upbit notice watcher, paper only.

Polls Upbit's announcement API about once per second (backs off on 429). On a new notice:
  KRW listing  -> paper LONG the Binance USDT perp (E1b)
  delisting    -> paper SHORT (B2_N2), logged separately
It records how late we saw the notice (detect time - first_listed_at), the Binance best bid/ask at detection
(real fill reference), and settles at +15m and +60m from Binance best bid/ask. Never places orders.
Table upbit_notice_paper. launchd com.altcoin.upbitlisting (KeepAlive).
"""
from __future__ import annotations

import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402
from scripts.f13_upbit_live import SCHEMA as F13_SCHEMA, upbit_live  # noqa: E402

UPBIT = "https://api-manager.upbit.com/api/v1/announcements"
FAPI = "https://fapi.binance.com"
MAX_LAG_S = 600
FEE = 0.0005
SCHEMA = """CREATE TABLE IF NOT EXISTS upbit_notice_paper (
  notice_id BIGINT NOT NULL, symbol TEXT NOT NULL, kind TEXT, title TEXT, first_listed_ts DOUBLE PRECISION,
  detect_ts DOUBLE PRECISION, lag_s DOUBLE PRECISION, side INTEGER, bid0 DOUBLE PRECISION, ask0 DOUBLE PRECISION,
  bid15 DOUBLE PRECISION, ask15 DOUBLE PRECISION, bid60 DOUBLE PRECISION, ask60 DOUBLE PRECISION,
  net15 DOUBLE PRECISION, net60 DOUBLE PRECISION, status TEXT, PRIMARY KEY (notice_id, symbol))"""


def db(sql, params=()):
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur.fetchall() if cur.description else None


def perps(s: requests.Session) -> set[str]:
    info = s.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=20).json()
    return {x["symbol"] for x in info["symbols"] if x.get("contractType") == "PERPETUAL" and x["status"] == "TRADING"}


def book(s: requests.Session, sym: str) -> tuple[float, float]:
    r = s.get(f"{FAPI}/fapi/v1/ticker/bookTicker", params={"symbol": sym}, timeout=5).json()
    return float(r["bidPrice"]), float(r["askPrice"])


def classify(title: str) -> tuple[str, int] | None:
    if ("신규 거래지원" in title or "디지털 자산 추가" in title) and "KRW" in title and "취소" not in title:
        return "krw_listing", 1
    if "거래지원 종료" in title and "취소" not in title:
        return "delisting", -1
    return None


def settle_later(s, nid, sym, side, bid0, ask0):
    def run():
        vals = {}
        for mins in (15, 60):
            time.sleep(mins * 60 - (15 * 60 if mins == 60 else 0))
            try:
                vals[mins] = book(s, sym)
            except Exception:  # noqa: BLE001
                vals[mins] = (float("nan"), float("nan"))
        entry = ask0 if side > 0 else bid0
        res = {}
        for mins, (b, a) in vals.items():
            exitp = b if side > 0 else a
            r_ = side * (exitp / entry - 1) - 2 * FEE
            res[mins] = None if r_ != r_ else r_  # NaN -> NULL so averages stay valid
        st = "closed" if res[60] is not None else "no_book"
        vals = {k: tuple(None if x != x else x for x in v) for k, v in vals.items()}
        db("UPDATE upbit_notice_paper SET bid15=?, ask15=?, bid60=?, ask60=?, net15=?, net60=?, status=? "
           "WHERE notice_id=? AND symbol=?", (*vals[15], *vals[60], res[15], res[60], st, nid, sym))
    threading.Thread(target=run, daemon=True).start()


F11_SCHEMA = """CREATE TABLE IF NOT EXISTS f11_notice_mom_paper (
  notice_id BIGINT NOT NULL, symbol TEXT NOT NULL, kind TEXT, title TEXT, first_listed_ts DOUBLE PRECISION, detect_ts DOUBLE PRECISION,
  mid0 DOUBLE PRECISION, confirm_ts DOUBLE PRECISION, confirm_move DOUBLE PRECISION, side INTEGER, entry_px DOUBLE PRECISION,
  exit_ts DOUBLE PRECISION, exit_px DOUBLE PRECISION, exit_reason TEXT, dv24 DOUBLE PRECISION, gross DOUBLE PRECISION,
  cost DOUBLE PRECISION, net DOUBLE PRECISION, status TEXT, PRIMARY KEY (notice_id, symbol))"""
F11_CONFIRM, F11_WINDOW_S, F11_STOP, F11_HOLD_S, F11_FEE = 0.03, 600, 0.03, 4 * 3600, 0.0005


def slip(dv):
    return 0.0002 if dv > 1e8 else 0.0005 if dv > 2e7 else 0.0010 if dv > 5e6 else 0.0020


def notice_momentum(s, nid, kind, code, title, first):
    """F11 (registered 2026-10-02 23:55 KST): ANY fresh Upbit notice naming a perp-listed coin -> wait for confirmation
    (|mid move| >= 3% within 10 min of detection) -> paper position in the direction of the move at the touch (ask for a
    long, bid for a short); exit at -3% adverse (stop) or +4h (time); taker fee + slippage both sides. No confirmation
    within 10 min -> row kept with status 'no_confirm' so the denominator is honest. Never places orders."""
    def run():
        try:
            bid0, ask0 = book(s, code); mid0 = (bid0 + ask0) / 2; t_det = time.time()
            try:
                dv = float(s.get(f"{FAPI}/fapi/v1/ticker/24hr", params={"symbol": code}, timeout=5).json()["quoteVolume"])
            except Exception:  # noqa: BLE001
                dv = 0.0
            db("INSERT INTO f11_notice_mom_paper (notice_id, symbol, kind, title, first_listed_ts, detect_ts, mid0, dv24, status) VALUES (?,?,?,?,?,?,?,?,'watching') ON CONFLICT DO NOTHING",
               (nid, code, kind, title[:200], first, t_det, mid0, dv))
            side, entry, t_in = 0, None, None
            while time.time() - t_det < F11_WINDOW_S:
                time.sleep(10)
                bid, ask = book(s, code); mv = (bid + ask) / 2 / mid0 - 1
                if mv >= F11_CONFIRM:
                    side, entry, t_in = 1, ask, time.time(); break
                if mv <= -F11_CONFIRM:
                    side, entry, t_in = -1, bid, time.time(); break
            if not side:
                db("UPDATE f11_notice_mom_paper SET status='no_confirm' WHERE notice_id=? AND symbol=?", (nid, code)); return
            db("UPDATE f11_notice_mom_paper SET confirm_ts=?, confirm_move=?, side=?, entry_px=?, status='open' WHERE notice_id=? AND symbol=?",
               (t_in, entry / mid0 - 1, side, entry, nid, code))
            print(time.strftime("%F %T"), f"F11 open {code} {'LONG' if side > 0 else 'SHORT'} at {entry} after {t_in - t_det:.0f}s ({kind})", flush=True)
            reason = None
            while reason is None:
                time.sleep(10)
                bid, ask = book(s, code); px = bid if side > 0 else ask
                if side * (px / entry - 1) <= -F11_STOP:
                    reason = "stop"
                elif time.time() - t_in >= F11_HOLD_S:
                    reason = "time_4h"
            gross = side * (px / entry - 1); cost = 2 * (F11_FEE + slip(dv)); net = gross - cost
            db("UPDATE f11_notice_mom_paper SET exit_ts=?, exit_px=?, exit_reason=?, gross=?, cost=?, net=?, status='closed' WHERE notice_id=? AND symbol=?",
               (time.time(), px, reason, gross, cost, net, nid, code))
            print(time.strftime("%F %T"), f"F11 close {code} {reason} gross {gross:+.4f} net {net:+.4f}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(time.strftime("%F %T"), "F11 error", code, repr(e)[:120], flush=True)
            db("UPDATE f11_notice_mom_paper SET status='error' WHERE notice_id=? AND symbol=? AND status IN ('watching','open')", (nid, code))
    threading.Thread(target=run, daemon=True).start()


def main() -> int:
    db(SCHEMA); db(F11_SCHEMA); db(F13_SCHEMA)
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    live = perps(s)
    seen = set()
    for n in s.get(UPBIT, params={"os": "web", "page": 1, "per_page": 20, "category": "trade"}, timeout=10).json()["data"]["notices"]:
        seen.add(n["id"])
    print(time.strftime("%F %T"), f"watching; {len(seen)} existing notices marked seen; {len(live)} perps", flush=True)
    wait, last_perps = 1.0, time.time()
    while True:
        t_req = time.time()
        try:
            r = s.get(UPBIT, params={"os": "web", "page": 1, "per_page": 20, "category": "trade"}, timeout=5)
            if r.status_code == 429:
                wait = min(wait * 2, 30)
                print(time.strftime("%F %T"), "429, backing off", wait, flush=True)
                time.sleep(wait)
                continue
            wait = 1.0
            ns = r.json()["data"]["notices"]
        except Exception as e:  # noqa: BLE001
            print(time.strftime("%F %T"), "poll error", repr(e)[:120], flush=True)
            time.sleep(3)
            continue
        now = time.time()
        for n in ns:
            if n["id"] in seen:
                continue
            seen.add(n["id"])
            cl = classify(n["title"])
            first = datetime.fromisoformat(n.get("first_listed_at") or n["listed_at"]).timestamp()
            print(time.strftime("%F %T"), "NEW", n["id"], n["title"][:80], f"lag {now - first:.2f}s", flush=True)
            if now - first > MAX_LAG_S:  # old notice resurfacing (edit/repost), not a fresh signal
                print(time.strftime("%F %T"), "SKIP stale", n["id"], f"lag {now - first:.0f}s", flush=True)
                continue
            syms = [x for x in re.findall(r"[A-Z][A-Z0-9]{1,11}", n["title"]) if x not in ("KRW", "BTC", "USDT")]
            # F11: every fresh notice (listing, warning, warning lifted, delisting, other) on a perp-listed coin
            f11_kind = cl[0] if cl else ("warning_lifted" if "유의 종목 지정 해제" in n["title"] else "warning" if "유의 종목" in n["title"] else "other")
            for sym in dict.fromkeys(syms):
                upbit_live(db, n["id"], f11_kind, sym, n["title"], first)   # F13: executed on Upbit itself, perp or not
            for sym in dict.fromkeys(syms):
                code = next((c for c in (f"{sym}USDT", f"1000{sym}USDT") if c in live), None)
                if code:
                    notice_momentum(s, n["id"], f11_kind, code, n["title"], first)
            if not cl:
                continue
            kind, side = cl
            for sym in dict.fromkeys(syms):
                code = next((c for c in (f"{sym}USDT", f"1000{sym}USDT") if c in live), None)
                if not code:
                    continue
                try:
                    bid0, ask0 = book(s, code)
                except Exception:  # noqa: BLE001
                    continue
                t_book = time.time()
                db("INSERT INTO upbit_notice_paper (notice_id, symbol, kind, title, first_listed_ts, detect_ts, lag_s, side, "
                   "bid0, ask0, status) VALUES (?,?,?,?,?,?,?,?,?,?,'open') ON CONFLICT DO NOTHING",
                   (n["id"], code, kind, n["title"][:200], first, t_book, t_book - first, side, bid0, ask0))
                settle_later(s, n["id"], code, side, bid0, ask0)
        if time.time() - last_perps > 3600:
            try:
                live, last_perps = perps(s), time.time()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(max(0.0, 1.0 - (time.time() - t_req)))


if __name__ == "__main__":
    sys.exit(main())
