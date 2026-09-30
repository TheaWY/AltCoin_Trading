"""Main paper book executor: trades the F2 (pump CNN) and F6 (oi-drop short) signals in the main $1,000 paper account.  PAPER ONLY.
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
STRAT = {"F2": "f2_pump_cnn", "F6": "f6_oi_short"}
ENABLED = {"F2"}   # F6 removed 2026-09-30 (유리): its source was invalidated by the 5-min OI timestamp fix; open F6 trades still exit normally


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
    return [s for s in out if s[4] <= now and s[0] in ENABLED]


def open_new(st, now):
    seen = q(st, "SELECT source, symbol, key_ts FROM main_book_map")
    done = set(map(tuple, seen[["source", "symbol", "key_ts"]].itertuples(index=False))) if seen is not None and len(seen) else set()
    n = 0
    for src, sym, key, side, t_plan, dv in sorted(signals(st, now), key=lambda s: s[4]):
        if (src, sym, key) in done:
            continue
        def log(status, note, tid=None, t_in=None, stop=None):
            q(st, "INSERT INTO main_book_map (source, symbol, key_ts, trade_id, status, t_entry, t_exit_plan, stop_px, note, created) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
              (src, sym, key, tid, status, t_in, (t_in + HOLD) if t_in else None, stop, note, now))
        if now - t_plan > STALE:
            log("skip_stale", f"seen {now - t_plan}s after planned entry"); continue
        mine = q(st, "SELECT count(*) AS n FROM main_book_map WHERE status='open'")
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
        print(time.strftime("%Y-%m-%d %H:%M"), f"opened={o} closed={c} equity={equity(st):.2f}",
              dict(zip(m["status"], m["n"])) if m is not None and len(m) else {}, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
