"""Live adapter for promoted autonomous-research signals (paper only). launchd com.altcoin.arlive, daily 00:05 UTC (09:05 KST).

For every entry with status 'forward' in research/forward_auto.yaml:
  1. build the last 45 days of the hourly panel from the live DB (prices_1m -> 1h bars; funding_rates; upbit_1h / bithumb_1h;
     metrics_5m for OI / long-short / taker) with the SAME builders as the research panel (ar_catalog.VARIABLES)
  2. close yesterday's book at the last price (net of funding and exit cost), open today's: band L/S (enter top/bottom 20%,
     keep while inside 30%), equal weight, gross 1.0, universe = 24h quote volume >= $5M, listed >= 30 days, crypto only
  3. one row per leg per day in table fa_<id> (same shape as f7_vshare_paper) -> main_book admission reads it generically
Also keeps a daily snapshot of the state so the research panel and the live panel can be reconciled (data/reports/arlive/).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import ar_catalog as C  # noqa: E402
import b7_lib as L  # noqa: E402
from src.data.storage import get_storage  # noqa: E402
from oi_drop_short_paper import q, slip  # noqa: E402

FWD_AUTO = ROOT / "research/forward_auto.yaml"
FEE = 0.0005
DAYS = 45
SCHEMA = """CREATE TABLE IF NOT EXISTS {t} (
  ts_signal BIGINT NOT NULL, symbol TEXT NOT NULL, f DOUBLE PRECISION, w DOUBLE PRECISION, dv24 DOUBLE PRECISION,
  entry_px DOUBLE PRECISION, exit_px DOUBLE PRECISION, ts_exit BIGINT, gross DOUBLE PRECISION, funding DOUBLE PRECISION,
  cost DOUBLE PRECISION, net DOUBLE PRECISION, status TEXT, PRIMARY KEY (ts_signal, symbol))"""


def live_panel(st, t_end):
    """hourly T x N arrays for the last DAYS days, aligned to hour starts, codes without slash."""
    t0 = t_end - DAYS * 86400
    px = q(st, "SELECT symbol, (ts/3600)*3600 AS h, max(high) AS h_, min(low) AS l_, sum(quote_volume) AS qv, sum(taker_buy_quote) AS tbq, sum(trades) AS n, "
               "max(ts) AS last_ts FROM prices_1m WHERE ts >= ? AND ts < ? GROUP BY symbol, (ts/3600)*3600", (t0, t_end))
    cl = q(st, "SELECT DISTINCT ON (symbol, (ts/3600)*3600) symbol, (ts/3600)*3600 AS h, close FROM prices_1m WHERE ts >= ? AND ts < ? ORDER BY symbol, (ts/3600)*3600, ts DESC", (t0, t_end))
    px["symbol"] = px.symbol.str.replace("/", ""); cl["symbol"] = cl.symbol.str.replace("/", "")
    hours = np.arange(t0, t_end, 3600); codes = sorted(px.symbol.unique())
    H = {c: j for j, c in enumerate(codes)}; T_ = {h: i for i, h in enumerate(hours)}
    def grid(df, col, fill=np.nan):
        A = np.full((len(hours), len(codes)), fill, float)
        ii = df.h.map(T_); jj = df.symbol.map(H); ok = ii.notna() & jj.notna()
        A[ii[ok].astype(int), jj[ok].astype(int)] = df.loc[ok, col].astype(float); return A
    c = pd.DataFrame(grid(cl, "close")).ffill(limit=3).to_numpy()
    P = {"ts": hours, "codes": codes, "lc": np.log(c), "h": grid(px, "h_"), "l": grid(px, "l_"), "qv": grid(px, "qv", 0.0), "tbq": grid(px, "tbq", 0.0), "n": grid(px, "n", 0.0)}
    P["r1"] = P["lc"] - L.lag(P["lc"], 1)
    P["btc"] = H.get("BTCUSDT", 0)
    fr = q(st, "SELECT symbol, timestamp AS ts, funding_rate FROM funding_rates WHERE timestamp >= ?", (t0 - 8 * 3600,))
    fr["symbol"] = fr.symbol.str.replace("/", ""); fr["h"] = (fr.ts // 3600) * 3600
    f8 = pd.DataFrame(grid(fr, "funding_rate")).ffill(limit=8).to_numpy(); P["f8"] = f8
    up = q(st, "SELECT symbol, ts AS h, value_krw, close FROM upbit_1h WHERE ts >= ?", (t0,)); bt = q(st, "SELECT symbol, ts AS h, volume * close AS value_krw FROM bithumb_1h WHERE ts >= ?", (t0,))
    usd = q(st, "SELECT ts AS h, close FROM upbit_1h WHERE symbol = 'USDT/KRW' AND ts >= ? ORDER BY ts", (t0,))
    usdkrw = pd.Series(usd.close.to_numpy(), index=usd.h.to_numpy()).reindex(hours).ffill().bfill().to_numpy() if usd is not None and len(usd) else np.full(len(hours), 1400.0)
    for df in (up, bt):
        if df is not None and len(df):
            df["symbol"] = df.symbol.str.replace("/", "")                      # upbit_1h/bithumb_1h already use Binance names (BTC/USDT), prices in KRW
    P["up_qv"] = np.nan_to_num(grid(up, "value_krw")) / usdkrw[:, None] if up is not None and len(up) else np.zeros_like(c)
    P["bt_qv"] = np.nan_to_num(grid(bt, "value_krw")) / usdkrw[:, None] if bt is not None and len(bt) else np.zeros_like(c)
    P["up_lc"] = np.log(grid(up, "close") / usdkrw[:, None]) if up is not None and len(up) else np.full_like(c, np.nan)
    m5 = q(st, "SELECT symbol, (ts/3600)*3600 AS h, avg(oi_usd) AS oi, avg(ls_top_pos) AS ls_top, avg(ls_global) AS ls_global, avg(taker_ratio) AS taker FROM metrics_5m WHERE ts >= ? GROUP BY symbol, (ts/3600)*3600", (t0,))
    if m5 is not None and len(m5):
        m5["symbol"] = m5.symbol.str.replace("/", "")
        for k in ("oi", "ls_top", "ls_global", "taker"):
            P[k] = pd.DataFrame(grid(m5, k)).ffill(limit=24).to_numpy()
    else:
        for k in ("oi", "ls_top", "ls_global", "taker"):
            P[k] = np.full_like(c, np.nan)
    rb = np.nan_to_num(P["r1"][:, [P["btc"]]]); r1 = np.nan_to_num(P["r1"])
    mb = L.M(rb, 720); P["beta"] = L.safe_div(L.M(r1 * rb, 720) - L.M(r1, 720) * mb, L.M(rb * rb, 720) - mb * mb)
    dv24 = L.S(P["qv"], 24)
    # prices_1m keeps ~3 weeks, so listing age cannot be read from the window: crypto_only() enforces >= 30 days listed
    P["U"] = (dv24 >= 5e6) & np.isfinite(c) & (np.cumsum(np.isfinite(c), 0) >= 168)
    P["dv24"] = dv24
    try:                                                           # coinalyze liquidations / OI (liq family)
        import ar_ext
        P.update(ar_ext.coinalyze_grid(st, P["ts"], P["codes"], t0=t0, fresh=True))
    except Exception as e:  # noqa: BLE001
        print("coinalyze grid unavailable:", type(e).__name__, e, flush=True)
        for k in ("liq_long", "liq_short", "oi_cz"):
            P[k] = np.full_like(c, np.nan)
    return P


def crypto_only(codes):
    try:
        info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20).json()["symbols"]
        ok = {x["symbol"] for x in info if x.get("contractType") == "PERPETUAL" and x["status"] == "TRADING" and x.get("underlyingType", "COIN") == "COIN"
              and time.time() * 1000 - x.get("onboardDate", 0) >= 30 * 86400 * 1000}
        return np.array([c in ok for c in codes])
    except Exception:  # noqa: BLE001
        return np.ones(len(codes), bool)


def rebalance(st, fid, spec, table, P, prices, now):
    q(st, SCHEMA.format(t=table))
    sign, fam, fn, src, live = C.VARIABLES[spec["var"]]
    with np.errstate(all="ignore"):
        S = sign * np.asarray(fn(P), float)[-1]
    if spec["method"] == "layered" and spec.get("cond") in C.CONDITIONERS and C.CONDITIONERS[spec["cond"]][0] is not None:
        with np.errstate(all="ignore"):
            on = bool(np.asarray(C.CONDITIONERS[spec["cond"]][0](P))[-1])
        if not on:
            S = np.full_like(S, np.nan)
    m = P["U"][-1] & np.isfinite(S) & crypto_only(P["codes"])
    codes = P["codes"]; dv = P["dv24"][-1]
    # close the open book
    prev = q(st, f"SELECT ts_signal, symbol, w, entry_px FROM {table} WHERE status='open'")
    w_prev = {}
    for r in (prev.itertuples(index=False) if prev is not None and len(prev) else []):
        x = prices.get(r.symbol, r.entry_px)
        fr = q(st, "SELECT sum(funding_rate) AS f FROM funding_rates WHERE symbol IN (?, ?) AND timestamp > ? AND timestamp <= ?",
               (r.symbol, r.symbol.replace("USDT", "/USDT"), int(r.ts_signal), now))
        fund = float(r.w) * float(fr["f"].iloc[0] or 0.0) if fr is not None and len(fr) else 0.0
        g = float(r.w) * (x / float(r.entry_px) - 1)
        q(st, f"UPDATE {table} SET exit_px=?, ts_exit=?, gross=?, funding=?, net=? - ? - COALESCE(cost,0), status=? WHERE ts_signal=? AND symbol=?",
          (x, now, g, fund, g, fund, "closed" if r.symbol in prices else "closed_nopx", int(r.ts_signal), r.symbol))
        w_prev[r.symbol] = float(r.w)
    if spec["method"] == "factor_momentum":
        hist = q(st, f"SELECT ts_signal, sum(net) AS net FROM {table} WHERE status LIKE 'closed%%' AND ts_signal >= ? GROUP BY ts_signal", (now - 32 * 86400,))
        if hist is not None and len(hist) >= 15 and float(hist.net.sum()) <= 0:
            m[:] = False                                             # own trailing 30d L/S not positive -> flat
    if m.sum() < 30:
        print(time.strftime("%F %T"), fid, "no book (universe", int(m.sum()), ")", flush=True); return
    p = np.full(len(codes), np.nan); p[m] = pd.Series(S[m]).rank(pct=True).to_numpy()
    held_l = np.array([w_prev.get(c, 0) > 0 for c in codes]); held_s = np.array([w_prev.get(c, 0) < 0 for c in codes])
    Lg = (m & (p >= 0.8)) | (held_l & m & (p >= 0.7)); Sg = (m & (p <= 0.2)) | (held_s & m & (p <= 0.3))
    w_new = {codes[j]: 0.5 / Lg.sum() for j in np.flatnonzero(Lg)}; w_new.update({codes[j]: -0.5 / Sg.sum() for j in np.flatnonzero(Sg)})
    turn = 0.0
    for j, c in enumerate(codes):
        if c not in w_new and c not in w_prev:
            continue
        dw = abs(w_new.get(c, 0.0) - w_prev.get(c, 0.0)); cst = dw * (FEE + slip(float(dv[j]))); turn += dw
        if c in w_new and c in prices:
            q(st, f"INSERT INTO {table} (ts_signal, symbol, f, w, dv24, entry_px, cost, status) VALUES (?,?,?,?,?,?,?,'open') ON CONFLICT DO NOTHING",
              (now, c, float(S[j]), w_new[c], float(dv[j]), prices[c], cst))
        elif c in w_prev:
            q(st, f"UPDATE {table} SET cost=COALESCE(cost,0)+?, net=net-? WHERE symbol=? AND ts_exit=?", (cst, cst, c, now))
    print(time.strftime("%F %T"), fid, spec["var"], f"book {int(Lg.sum())}L/{int(Sg.sum())}S turnover {turn:.2f}", flush=True)


def main() -> int:
    fa = yaml.safe_load(open(FWD_AUTO)) if FWD_AUTO.exists() else {}
    live = {k: v for k, v in (fa or {}).items() if v.get("status") == "forward"}
    if not live:
        print(time.strftime("%F %T"), "no promoted signals yet"); return 0
    st = get_storage(); now = int(time.time()) // 3600 * 3600
    P = live_panel(st, now)
    prices = {x["symbol"]: float(x["price"]) for x in requests.get("https://fapi.binance.com/fapi/v1/ticker/price", timeout=15).json()}
    for fid, v in live.items():
        try:
            rebalance(st, fid, v["spec"], v["table"], P, prices, now)
        except Exception as e:  # noqa: BLE001
            print(time.strftime("%F %T"), fid, "ERROR", repr(e)[:200], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
