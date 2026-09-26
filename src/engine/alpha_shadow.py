"""Forward test of the two alpha-lab candidates (no money), exactly as backtested:

  breakout8      every day at 00:00 UTC take the 10 most volatile coins (24h
                 realised vol, >= $1M daily volume); enter whichever side first
                 moves 8% from the 00:00 price, stop back at the 00:00 price,
                 exit at the 24h close; a minute bar touching both sides = loss
  leverage_long  every day at 00:00 UTC long the top 5% by open interest /
                 market cap (CoinGecko market cap, else supply x price), 24h

Runs hourly (scripts/alpha_shadow.py, launchd com.altcoin.alphashadow): makes
the day's picks once, and settles picks whose 24h window has closed using
1-minute bars. Net = gross - 0.3% round trip. Table alpha_shadow.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
DAY = 86400
COST = 0.003
X = 0.08
SCHEMA = """CREATE TABLE IF NOT EXISTS alpha_shadow (
    strategy TEXT NOT NULL, day BIGINT NOT NULL, symbol TEXT NOT NULL, ref_price DOUBLE PRECISION,
    score DOUBLE PRECISION, status TEXT NOT NULL, side INTEGER, entry DOUBLE PRECISION, exit DOUBLE PRECISION,
    gross DOUBLE PRECISION, net DOUBLE PRECISION, note TEXT, PRIMARY KEY (strategy, day, symbol))"""


def _q(storage: Any, sql: str, params: tuple = ()) -> list[dict]:
    with storage._connect() as c:  # noqa: SLF001
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def make_picks(storage: Any, day: int) -> int:
    from src.research import pump_study as ps

    p = ps.load_panel(storage, day - 1500 * 60)
    c = p["close"].loc[:day - 60]
    if len(c) < 1200:
        return 0
    ref = c.iloc[-1]                                     # close of 23:59 = the 00:00 price
    hourly = c.iloc[::60]
    rv = np.log(hourly / hourly.shift(1)).iloc[-24:].std()
    dv = p["quote_volume"].loc[:day - 60].iloc[-1440:].sum()
    ok = (dv >= 1e6) & ref.notna() & rv.notna()
    rows = []
    for sym in rv[ok].sort_values().index[-10:]:
        rows.append(("breakout8", day, sym, float(ref[sym]), float(rv[sym])))
    oi = {r["symbol"]: r["oi_usd"] for r in _q(storage, "SELECT DISTINCT ON (symbol) symbol, oi_usd FROM perp_5m "
                                                        "WHERE oi_usd IS NOT NULL AND ts > ? ORDER BY symbol, ts DESC",
                                                        (day - 3 * 3600,))}
    mc = {r["symbol"]: r["mcap"] for r in _q(storage, "SELECT DISTINCT ON (symbol) symbol, mcap FROM cg_daily "
                                                      "WHERE mcap > 0 AND ts >= ? ORDER BY symbol, ts DESC", (day - 3 * DAY,))}
    sup = {r["symbol"]: r["supply"] for r in _q(storage, "SELECT DISTINCT ON (symbol) symbol, supply FROM perp_5m "
                                                       "WHERE supply > 0 ORDER BY symbol, ts DESC")}
    lev = {}
    for sym in ref[ok].index:
        m = mc.get(sym) or (sup.get(sym) * float(ref[sym]) if sup.get(sym) else None)
        if oi.get(sym) and m:
            lev[sym] = oi[sym] / m
    if len(lev) >= 20:
        s = pd.Series(lev)
        for sym in s[s >= s.quantile(0.95)].index:
            rows.append(("leverage_long", day, sym, float(ref[sym]), float(s[sym])))
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(SCHEMA)
        for r in rows:
            conn.execute("INSERT INTO alpha_shadow (strategy, day, symbol, ref_price, score, status) "
                         "VALUES (?,?,?,?,?,'open') ON CONFLICT DO NOTHING", r)
    return len(rows)


def settle(storage: Any, now: int) -> int:
    from src.research import pump_study as ps

    open_ = _q(storage, "SELECT * FROM alpha_shadow WHERE status='open' AND day + ? <= ?", (DAY + 120, now))
    if not open_:
        return 0
    n = 0
    for day in sorted({r["day"] for r in open_}):
        rows = [r for r in open_ if r["day"] == day]
        p = ps.load_panel(storage, day, sorted({r["symbol"] for r in rows}))
        hi, lo, cl = (p[k].loc[day:day + DAY - 60] for k in ("high", "low", "close"))
        for r in rows:
            sym, o = r["symbol"], float(r["ref_price"])
            if sym not in cl or cl[sym].dropna().empty:
                upd = ("void", None, None, None, None, None, "no bars")
            elif r["strategy"] == "leverage_long":
                ex = float(cl[sym].dropna().iloc[-1])
                g = ex / o - 1
                upd = ("closed", 1, o, ex, g, g - COST, "24h")
            else:
                side, entry, ex, note = 0, None, None, "no trigger"
                for h, l_, c_ in zip(hi[sym].to_numpy(), lo[sym].to_numpy(), cl[sym].to_numpy()):
                    if not np.isfinite(h):
                        continue
                    if side == 0:
                        up, dn = h >= o * (1 + X), l_ <= o * (1 - X)
                        if up and dn:
                            side, entry, ex, note = 1, o * (1 + X), o * (1 - X), "both sides in one bar"
                            break
                        if up:
                            side, entry = 1, o * (1 + X)
                        elif dn:
                            side, entry = -1, o * (1 - X)
                        continue
                    if (side > 0 and l_ <= o) or (side < 0 and h >= o):
                        ex, note = o, "stopped at 00:00 price"
                        break
                if side and ex is None:
                    ex, note = float(cl[sym].dropna().iloc[-1]), "24h close"
                if side:
                    g = side * (ex / entry - 1)
                    upd = ("closed", side, entry, ex, g, g - COST, note)
                else:
                    upd = ("closed", 0, None, None, 0.0, 0.0, note)
            with storage._connect() as conn:  # noqa: SLF001
                conn.execute("UPDATE alpha_shadow SET status=?, side=?, entry=?, exit=?, gross=?, net=?, note=? "
                             "WHERE strategy=? AND day=? AND symbol=?", (*upd, r["strategy"], r["day"], sym))
            n += 1
    return n


def run(storage: Any, now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute(SCHEMA)
    today = now // DAY * DAY
    made = 0
    if not _q(storage, "SELECT 1 FROM alpha_shadow WHERE day = ? LIMIT 1", (today,)) and now - today >= 120:
        made = make_picks(storage, today)
    return {"picked": made, "settled": settle(storage, now)}
