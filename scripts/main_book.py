"""Main paper book executor (2026-10-01: all forward tests F2-F9 are candidates, admitted only by their own registered rule;
F3/F4/F5/F9/F7 are mirrored from their forward tables). Originally: trades the F2 (pump CNN) and F6 (oi-drop short) signals in the main $1,000 paper account.  PAPER ONLY.
Chosen by 유리 2026-09-28 after the portfolio reset: main book = F2 + F6 only (signal_xs, core hold and pump rider disabled).
Runs every minute (launchd com.altcoin.mainbook). Never places orders; LIVE_TRADING is not read or changed here.

Signals (read from the forward-test tables, which keep running independently as the clean evidence):
  F2  pump_cnn_paper      rows with side != 0, status 'open', ts_signal >= BOOK_START; planned entry T+60s, exit entry+4h, no stop
  F6  oi_drop_short_paper rows with status 'open', ts_entry >= BOOK_START; SHORT, exit entry+4h or 5% stop on 1-minute highs
Entry at the latest 1-minute close (prices_1m) when first seen; skipped if more than 15 min after the planned entry (stale).
Sizing (B17_F10): notional = 10% of equity (cash + marked open positions), max 3 open main-book positions; extra signals are logged 'skip_cap'.
Costs: 0.05%/side fee + liquidity slippage on entry (in fees) and exit fee via signal_book._close; stops fill at max(bar open, stop) + 0.2%.
State: table main_book_map (source, symbol, key_ts, trade_id, status, t_entry, t_exit_plan, stop_px, note)."""
from __future__ import annotations

import sys
import time

import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from src.data.storage import get_storage  # noqa: E402
from src.engine.signal_book import _close, _fee  # noqa: E402
from oi_drop_short_paper import q, slip  # noqa: E402

BOOK_START = 1790606683            # portfolio reset (portfolio_state.benchmark_started_at)
NOTIONAL_PCT, MAX_OPEN, HOLD, STOP, STOP_SLIP, STALE = 0.10, 3, 4 * 3600, 0.05, 0.002, 15 * 60
SCHEMA = """CREATE TABLE IF NOT EXISTS main_book_map (
  source TEXT NOT NULL, symbol TEXT NOT NULL, key_ts BIGINT NOT NULL, trade_id BIGINT, status TEXT, t_entry BIGINT,
  t_exit_plan BIGINT, stop_px DOUBLE PRECISION, note TEXT, created BIGINT, PRIMARY KEY (source, symbol, key_ts))"""
STRAT = {"F2": "f2_pump_cnn", "F6": "f6_oi_short", "F3": "f3_crash_rebound", "F4": "f4_spot_led", "F5": "f5_unlock_short",
         "F7": "f7_vshare_ls", "F9": "f9_listing_fade"}
# mirror sources: the main-book position closes when the forward row it copies is no longer 'open'
MIRROR = {"F3": ("crash_rebound_paper", "ts_signal"), "F4": ("spot_led_paper", "ts_signal"), "F5": ("unlock_short_paper", "ts_open"),
          "F9": ("f9_listing_fade_paper", "notice_id"), "F7": ("f7_vshare_paper", "ts_signal"), "F11": ("f11_notice_mom_paper", "notice_id"), "F12": ("f12_fresh_burst_paper", "ts_signal")}
# Admission is rule-based, not discretionary (2026-10-01, after the -8.07 USDT start):
#  - a strategy trades in the main book only once its OWN forward test has passed its registered decision rule
#    (research/forward.yaml: >= 300 closed paper trades, mean net > 0 with a day-clustered 95% CI above 0);
#    until then it keeps running in its forward table only. When it passes it is admitted automatically at full size
#    (no cap on the upside).
#  - the only brake is on the downside: if equity falls 10% below its running peak, new entries pause for 7 days.
# F6 stays out permanently (source invalidated by the 5-min OI timestamp fix).
# 2026-10-01 (B31 follow-up): every forward test is a candidate, each judged by ITS registered rule in research/forward.yaml.
# unit: 'trade' = one net per row, CI clustered by day; 'book' = rows summed per ts (weekly L/S book); 'event' = per row, iid bootstrap.
FORWARD = {
    "F2": ("pump_cnn_paper", "ts_signal", "net", 300, "trade"),
    "F3": ("crash_rebound_paper", "ts_signal", "net", 30, "trade"),
    "F4": ("spot_led_paper", "ts_signal", "net", 100, "trade"),
    "F5": ("unlock_short_paper", "ts_open", "net", 60, "trade"),
    "F7": ("f7_vshare_paper", "ts_signal", "net", 12, "book"),
    "F9": ("f9_listing_fade_paper", "entry_ts", "net", 30, "event"),
    "F11": ("f11_notice_mom_paper", "confirm_ts", "net", 30, "trade"),       # Upbit notice momentum (registered 2026-10-02)
    "F12": ("f12_fresh_burst_paper", "ts_signal", "net", 30, "trade"),        # fresh burst (registered 2026-10-03)
}
# 2026-10-03: event strategies (F11/F12/F13) fire a few times a day at most, so the 60-event floor meant months to a verdict.
# Floor lowered to 30 for them; in exchange, any candidate judged on fewer than 60 events must clear a 99% bootstrap CI
# (0.5th percentile > 0) instead of 95%. Same rule in qualified().
SMALL_N, SMALL_N_PCT, PCT = 60, 0.5, 2.5
# Autonomous-research promotions (research/forward_auto.yaml, status 'forward') are candidates too: same rule as F7 (book unit).
try:
    import yaml as _yaml
    _fa = _yaml.safe_load(open(ROOT / "research/forward_auto.yaml")) or {}
    for _k, _v in _fa.items():
        if _v.get("status") == "forward":
            FORWARD[_k] = (_v["table"], "ts_signal", "net", 60, "book")
except Exception:  # noqa: BLE001
    pass
# F8 (late-session basket) is judged too but has no execution adapter yet; it is reported, not traded.
for _k, _v in list(FORWARD.items()):
    if _k.startswith("AR"):
        MIRROR[_k] = (_v[0], "ts_signal"); STRAT[_k] = _k.lower()
FORWARD_REPORT_ONLY = {"F8": ("f8_latesession_paper", "day", "net", 120, "event"),
                       # F13: Upbit-executed notice longs (KRW spot, live Upbit book). The main book is a Binance-USDT perp
                       # book and has no Upbit adapter, so F13 is judged and reported here; execution venue is 유리's call.
                       "F13": ("f13_upbit_live_paper", "entry_ts", "net", 30, "trade")}
F7_GROSS = 0.5                     # gross notional of the mirrored F7 book as a share of equity (legs exempt from MAX_OPEN)
DD_PAUSE, PAUSE_S = 0.10, 7 * 86400
STATE = "CREATE TABLE IF NOT EXISTS main_book_state (k TEXT PRIMARY KEY, v DOUBLE PRECISION, note TEXT, updated BIGINT)"


def qualified(st, src):
    tab, tcol, ncol, need, unit = {**FORWARD, **FORWARD_REPORT_ONLY}[src]
    try:
        d = q(st, f"SELECT {tcol} AS t, {ncol} AS net FROM {tab} WHERE status LIKE 'closed%%' AND {ncol} IS NOT NULL")
    except Exception as e:  # noqa: BLE001
        return False, f"table not ready ({type(e).__name__})"
    if d is None or not len(d):
        return False, f"0/{need}"
    if unit == "book":
        d = d.groupby("t", as_index=False)["net"].sum()
    if len(d) < need:
        return False, f"{len(d)}/{need}"
    rng = np.random.default_rng(3)
    if unit == "trade":
        days = [g.to_numpy() for _, g in d.groupby(d["t"] // 86400)["net"]]
        boot = [np.concatenate([days[i] for i in rng.integers(0, len(days), len(days))]).mean() for _ in range(2000)]
    else:
        x = d["net"].to_numpy(); boot = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(2000)]
    pct = SMALL_N_PCT if len(d) < SMALL_N else PCT
    lo = float(np.percentile(boot, pct))
    return lo > 0, f"n={len(d)} mean={d['net'].mean():+.4f} ci_lo={lo:+.4f} ({100 - 2 * pct:.0f}% CI)"


def enabled(st, now):
    q(st, STATE)
    eq = equity(st)
    r = q(st, "SELECT k, v FROM main_book_state")
    kv = dict(zip(r["k"], r["v"])) if r is not None and len(r) else {}
    peak = max(kv.get("peak", eq), eq)
    q(st, "INSERT INTO main_book_state (k, v, updated) VALUES ('peak', ?, ?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v, updated=EXCLUDED.updated", (peak, now))
    if eq < peak * (1 - DD_PAUSE) and now >= kv.get("pause_until", 0):
        q(st, "INSERT INTO main_book_state (k, v, note, updated) VALUES ('pause_until', ?, ?, ?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v, note=EXCLUDED.note, updated=EXCLUDED.updated",
          (now + PAUSE_S, f"equity {eq:.2f} < 90% of peak {peak:.2f}", now))
        kv["pause_until"] = now + PAUSE_S
    if now < kv.get("pause_until", 0):
        return set(), "paused (drawdown brake)"
    ok, why = set(), []
    for src in list(FORWARD) + list(FORWARD_REPORT_ONLY):
        good, msg = qualified(st, src)
        why.append(f"{src}: {'admitted' if good else 'not yet'} ({msg})")
        if good and src in FORWARD:
            ok.add(src)
    return ok, "; ".join(why)


def last_px(st, sym):
    r = q(st, "SELECT ts, close FROM prices_1m WHERE symbol=? ORDER BY ts DESC LIMIT 1", (sym,))
    return (int(r["ts"].iloc[0]), float(r["close"].iloc[0])) if r is not None and len(r) else (None, None)


def equity(st):
    cash = float(st.get_portfolio_state()["cash"])
    val = 0.0
    for t in st.get_open_trades():
        _, px = last_px(st, t["symbol"]); px = px or float(t["entry_price"])
        qty, e = float(t["quantity"]), float(t["entry_price"])
        val += qty * px if t["direction"] == "LONG" else qty * e + (e - px) * qty
    return cash + val


def signals(st, now):
    out = []
    a = q(st, "SELECT symbol, ts_signal, side, dv24 FROM pump_cnn_paper WHERE status='open' AND side <> 0 AND ts_signal >= ?", (BOOK_START - 3600,))
    for r in (a.itertuples(index=False) if a is not None else []):
        out.append(("F2", r.symbol, int(r.ts_signal), "LONG" if r.side > 0 else "SHORT", int(r.ts_signal) + 60, float(r.dv24 or 0)))
    b = q(st, "SELECT symbol, ts_onset, ts_entry, dv24 FROM oi_drop_short_paper WHERE status='open' AND ts_entry >= ?", (BOOK_START,))
    for r in (b.itertuples(index=False) if b is not None else []):
        out.append(("F6", r.symbol, int(r.ts_onset), "SHORT", int(r.ts_entry), float(r.dv24 or 0)))
    # mirror adapters (2026-10-01): single-coin forward rows; the main-book position closes when the forward row closes
    for src, sql, side in (("F3", "SELECT symbol, ts_signal AS k, ts_signal AS t, side, dv24 FROM crash_rebound_paper WHERE status='open' AND side <> 0", None),
                           ("F4", "SELECT symbol, ts_signal AS k, ts_signal AS t, side, dv24 FROM spot_led_paper WHERE status='open' AND side <> 0", None),
                           ("F5", "SELECT symbol, ts_open AS k, ts_open AS t, -1 AS side, 0 AS dv24 FROM unlock_short_paper WHERE status='open'", None),
                           ("F9", "SELECT replace(symbol, 'USDT', '/USDT') AS symbol, notice_id AS k, entry_ts AS t, -1 AS side, dv24 FROM f9_listing_fade_paper WHERE status='open'", None),
                           ("F11", "SELECT replace(symbol, 'USDT', '/USDT') AS symbol, notice_id AS k, confirm_ts AS t, side, dv24 FROM f11_notice_mom_paper WHERE status='open'", None),
                           ("F12", "SELECT symbol, ts_signal AS k, ts_signal AS t, 1 AS side, dv24 FROM f12_fresh_burst_paper WHERE status='open'", None)):
        try:
            r_ = q(st, sql)
        except Exception:  # noqa: BLE001
            continue
        for r in (r_.itertuples(index=False) if r_ is not None else []):
            out.append((src, r.symbol, int(r.k), "LONG" if r.side > 0 else "SHORT", int(r.t), float(r.dv24 or 0)))
    return [s for s in out if s[4] <= now]


def f7_legs(st, table="f7_vshare_paper"):
    """open legs of a book-type forward table (symbol with slash, ts_signal, weight)."""
    try:
        r = q(st, f"SELECT replace(symbol, 'USDT', '/USDT') AS symbol, ts_signal, w, dv24 FROM {table} WHERE status='open'")
    except Exception:  # noqa: BLE001
        return []
    return [] if r is None else list(r.itertuples(index=False))


def open_new(st, now):
    seen = q(st, "SELECT source, symbol, key_ts FROM main_book_map")
    done = set(map(tuple, seen[["source", "symbol", "key_ts"]].itertuples(index=False))) if seen is not None and len(seen) else set()
    n = 0
    ok, _ = enabled(st, now)
    for src, sym, key, side, t_plan, dv in sorted(signals(st, now), key=lambda s: s[4]):
        if (src, sym, key) in done or src not in ok:
            continue
        def log(status, note, tid=None, t_in=None, stop=None):
            q(st, "INSERT INTO main_book_map (source, symbol, key_ts, trade_id, status, t_entry, t_exit_plan, stop_px, note, created) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
              (src, sym, key, tid, status, t_in, (t_in + HOLD) if t_in else None, stop, note, now))
        if now - t_plan > STALE:
            log("skip_stale", f"seen {now - t_plan}s after planned entry"); continue
        mine = q(st, "SELECT count(*) AS n FROM main_book_map WHERE status='open' AND source <> 'F7' AND source NOT LIKE 'AR%%'")
        if int(mine["n"].iloc[0]) >= MAX_OPEN:
            log("skip_cap", f"{MAX_OPEN} positions already open"); continue
        ts_px, px = last_px(st, sym)
        if px is None or now - ts_px > 180:
            log("skip_noprice", "no fresh 1m price"); continue
        eq = equity(st); notional = NOTIONAL_PCT * eq; fee = notional * (_fee() + slip(dv))
        cash = float(st.get_portfolio_state()["cash"])
        if notional + fee > cash:
            log("skip_cash", f"cash {cash:.2f} < {notional + fee:.2f}"); continue
        big = 1e18
        tid = st.insert_paper_trade({
            "signal_id": None, "exit_price": None, "symbol": sym, "direction": side, "entry_price": px, "quantity": notional / px,
            "stop_loss": big if side == "SHORT" else 0.0,          # sentinel (like signal_xs); the F6 stop lives in main_book_map.stop_px
            "take_profit": 0.0 if side == "SHORT" else big, "status": "open", "pnl": None, "opened_at": now, "closed_at": None,
            "strategy": STRAT[src], "style": STRAT[src], "atr_pct": None, "trail_price": px, "exit_reason": None, "fees": fee})
        st.update_portfolio_cash(cash - notional - fee)
        log("open", f"{side} {notional:.2f} USDT at {px:g} (equity {eq:.2f})", tid, now, px * (1 + STOP) if src == "F6" else None)
        print(time.strftime("%H:%M"), "OPEN", src, sym, side, round(notional, 2), px, flush=True); n += 1
    for bsrc in [k for k in ok if k == "F7" or k.startswith("AR")]:
        eq = equity(st)
        n_books = len([k for k in ok if k == "F7" or k.startswith("AR")])
        for leg in f7_legs(st, FORWARD[bsrc][0]):
            if (bsrc, leg.symbol, int(leg.ts_signal)) in done:
                continue
            ts_px, px = last_px(st, leg.symbol)
            if px is None or now - ts_px > 180:
                continue
            side = "LONG" if leg.w > 0 else "SHORT"; notional = abs(float(leg.w)) * F7_GROSS * eq / n_books
            fee = notional * (_fee() + slip(float(leg.dv24 or 0))); cash = float(st.get_portfolio_state()["cash"])
            if notional + fee > cash:
                continue
            big = 1e18
            tid = st.insert_paper_trade({
                "signal_id": None, "exit_price": None, "symbol": leg.symbol, "direction": side, "entry_price": px, "quantity": notional / px,
                "stop_loss": big if side == "SHORT" else 0.0, "take_profit": 0.0 if side == "SHORT" else big, "status": "open", "pnl": None,
                "opened_at": now, "closed_at": None, "strategy": STRAT[bsrc], "style": STRAT[bsrc], "atr_pct": None, "trail_price": px,
                "exit_reason": None, "fees": fee})
            st.update_portfolio_cash(cash - notional - fee)
            q(st, "INSERT INTO main_book_map (source, symbol, key_ts, trade_id, status, t_entry, note, created) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
              (bsrc, leg.symbol, int(leg.ts_signal), tid, "open", now, f"{side} {notional:.2f} USDT leg", now))
            n += 1
    return n


def manage(st, now):
    op = q(st, "SELECT source, symbol, key_ts, trade_id, t_entry, t_exit_plan, stop_px FROM main_book_map WHERE status='open'")
    trades = {int(t["id"]): t for t in st.get_open_trades()}
    n = 0
    for r in (op.itertuples(index=False) if op is not None else []):
        t = trades.get(int(r.trade_id))
        if t is None:                                    # closed elsewhere (should not happen)
            q(st, "UPDATE main_book_map SET status='gone' WHERE source=? AND symbol=? AND key_ts=?", (r.source, r.symbol, r.key_ts)); continue
        px_exit, reason, t_close = None, None, now
        if r.source in MIRROR:
            tab, kcol = MIRROR[r.source]
            sym_src = r.symbol.replace("/", "") if r.source in ("F9", "F7") or r.source.startswith("AR") else r.symbol
            stt = q(st, f"SELECT status FROM {tab} WHERE symbol=? AND {kcol}=?", (sym_src, int(r.key_ts)))
            if stt is not None and len(stt) and stt["status"].iloc[0] == "open":
                continue
            ts_px, px = last_px(st, r.symbol)
            if px is None:
                continue
            _close(st, t, px, now, f"{STRAT[r.source]}:mirror")
            q(st, "UPDATE main_book_map SET status='closed', note=COALESCE(note,'') || ? WHERE source=? AND symbol=? AND key_ts=?",
              (f" | closed mirror at {px:g}", r.source, r.symbol, r.key_ts))
            n += 1
            continue
        if r.stop_px is not None and r.stop_px == r.stop_px:     # F6 stop on 1-minute highs since entry
            b = q(st, "SELECT ts, open, high FROM prices_1m WHERE symbol=? AND ts > ? AND ts <= ? ORDER BY ts", (r.symbol, int(r.t_entry) - 60, min(now, int(r.t_exit_plan))))
            if b is not None and len(b):
                hit = b[b["high"] >= float(r.stop_px)]
                if len(hit):
                    px_exit = max(float(hit["open"].iloc[0]), float(r.stop_px)) * (1 + STOP_SLIP); reason = "stop5"; t_close = int(hit["ts"].iloc[0]) + 60
        if px_exit is None and now >= int(r.t_exit_plan):
            ts_px, px = last_px(st, r.symbol)
            if px is None or now - ts_px > 600:
                continue                                  # wait for a fresh price
            px_exit, reason = px, "time_4h"
        if px_exit is None:
            continue
        _close(st, t, px_exit, t_close, f"{STRAT[r.source]}:{reason}")
        q(st, "UPDATE main_book_map SET status='closed', note=COALESCE(note,'') || ? WHERE source=? AND symbol=? AND key_ts=?",
          (f" | closed {reason} at {px_exit:g}", r.source, r.symbol, r.key_ts))
        print(time.strftime("%H:%M"), "CLOSE", r.source, r.symbol, reason, px_exit, flush=True); n += 1
    return n


def main() -> int:
    st = get_storage(); q(st, SCHEMA); now = int(time.time())
    c = manage(st, now); o = open_new(st, now)
    if o or c or time.gmtime(now).tm_min % 30 == 0:
        m = q(st, "SELECT status, count(*) AS n FROM main_book_map GROUP BY status")
        print(time.strftime("%Y-%m-%d %H:%M"), f"opened={o} closed={c} equity={equity(st):.2f}", enabled(st, now)[1],
              dict(zip(m["status"], m["n"])) if m is not None and len(m) else {}, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
