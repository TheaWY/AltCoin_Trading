"""F14 "Korea-led burst fade" paper test (registered 2026-10-03 11:40 KST, forward only).

Source: B35 (research/b35_burst_retro.md) found that volume bursts whose 30-min volume was >= 20% Upbit faded on Binance
(mean 4h -1.5%, top quartile of Upbit share -2.8%, n=135, in-sample, 23 days). B37 (research/b37_upbit_only.md) found the
same on Upbit itself out of sample: hourly bursts fade -2.2% in 4h on the last-40% holdout (n=1058, CI [-2.7,-1.7]), worst
for coins without a Binance perp. Upbit spot cannot be shorted, so the tradable form is the Binance perp of a cross-listed
coin whose burst is Korea-led. In-sample finding -> no back-test credit -> forward test.

Rule (fixed now): every 5 minutes, for every Binance USDT perp (crypto, listed >= 30d, dv24 >= $5M) that also trades on
Upbit KRW: if close / close 30 min ago - 1 >= +5% AND 30-min quote volume >= 5x the average 5-min quote volume of the prior
2h (x6) AND Upbit's share of the combined 30-min turnover (Upbit KRW value / USDKRW vs Binance quote volume) >= 20% ->
paper SHORT at the current 1m close; one position per coin per 4h; max 10 open.
Exit: +3% from entry against us (checked on 1m highs), or 4h. Cost: taker fee 5 bp + slippage by dv24, both sides; settled funding.
Pass: >= 30 closed events and mean net > 0 with bootstrap 99% CI above 0 (95% once n >= 60). Kill: 30 events with mean net <= 0.
Paper only. LIVE_TRADING is never read here."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from src.data.storage import get_storage  # noqa: E402
from oi_drop_short_paper import q  # noqa: E402
from pairs_divergence_paper import funding_leg, slip  # noqa: E402
from fresh_burst_paper import crypto_only  # noqa: E402

TABLE = "f14_korea_led_fade_paper"
R30, VOLX, KR_SHARE, DV_MIN, STOP, HOLD_S, MAX_OPEN, FEE = 0.05, 5.0, 0.20, 5e6, 0.03, 4 * 3600, 10, 0.0005
SCHEMA = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
  id SERIAL PRIMARY KEY, symbol TEXT, ts_signal BIGINT, r30 DOUBLE PRECISION, volx DOUBLE PRECISION, kr_share DOUBLE PRECISION,
  dv24 DOUBLE PRECISION, entry_px DOUBLE PRECISION, ts_exit BIGINT, exit_px DOUBLE PRECISION, exit_reason TEXT,
  gross DOUBLE PRECISION, funding DOUBLE PRECISION, cost DOUBLE PRECISION, net DOUBLE PRECISION, status TEXT DEFAULT 'open')"""


def usdkrw():
    try:
        return float(requests.get("https://api.upbit.com/v1/ticker", params={"markets": "KRW-USDT"}, timeout=5).json()[0]["trade_price"])
    except Exception:  # noqa: BLE001
        return 1400.0


def main():
    st = get_storage(); now = int(time.time()); q(st, SCHEMA)
    op = q(st, f"SELECT * FROM {TABLE} WHERE status = 'open'")
    n_open = 0
    for _, t in (op.iterrows() if op is not None and len(op) else []):
        px = q(st, "SELECT ts, high, close FROM prices_1m WHERE symbol = %s AND ts > %s ORDER BY ts", (t.symbol, int(t.ts_signal)))
        if px is None or not len(px):
            n_open += 1; continue
        stop_px = float(t.entry_px) * (1 + STOP)
        hit = px[px.high >= stop_px]
        reason, exit_px, t_exit = None, None, None
        if len(hit):
            reason, exit_px, t_exit = "stop", stop_px, int(hit.ts.iloc[0])
        elif now - int(t.ts_signal) >= HOLD_S:
            last = px[px.ts <= int(t.ts_signal) + HOLD_S]
            reason, exit_px, t_exit = "time_4h", float((last if len(last) else px).close.iloc[-1]), int((last if len(last) else px).ts.iloc[-1])
        if reason is None:
            n_open += 1; continue
        gross = -(exit_px / float(t.entry_px) - 1)
        fund = funding_leg(st, t.symbol, int(t.ts_signal), t_exit, -1)
        cost = 2 * (FEE + slip(float(t.dv24))); net = gross + fund - cost
        q(st, f"UPDATE {TABLE} SET ts_exit=%s, exit_px=%s, exit_reason=%s, gross=%s, funding=%s, cost=%s, net=%s, status='closed' WHERE id=%s",
          (t_exit, exit_px, reason, gross, fund, cost, net, int(t.id)))
        print(time.strftime("%F %T"), f"F14 close {t.symbol} {reason} gross {gross:+.4f} net {net:+.4f}", flush=True)
    d = q(st, "SELECT symbol, ts, close, quote_volume FROM prices_1m WHERE ts >= %s", (now - 4 * 3600,))
    if d is None or not len(d):
        return 0
    d = d.sort_values(["symbol", "ts"])
    last = d[d.ts >= now - 300].groupby("symbol").agg(c=("close", "last"), ts=("ts", "last"))
    c30 = d[(d.ts >= now - 1860) & (d.ts < now - 1740)].groupby("symbol").close.last().rename("c30")
    v30 = d[d.ts >= now - 1800].groupby("symbol").quote_volume.sum().rename("v30")
    vbase = (d[(d.ts >= now - 1800 - 7200) & (d.ts < now - 1800)].groupby("symbol").quote_volume.sum() / 24).rename("vbase")
    dv24 = q(st, "SELECT symbol, sum(quote_volume) dv24 FROM prices_1m WHERE ts >= %s GROUP BY symbol", (now - 86400,))
    dv24 = dv24.set_index("symbol").dv24 if dv24 is not None and len(dv24) else pd.Series(dtype=float, name="dv24")
    kr = q(st, "SELECT symbol, sum(value_krw) krw FROM kr_1m WHERE exchange='upbit' AND ts >= %s GROUP BY symbol", (now - 1800,))
    fx = usdkrw()
    kr_usd = (kr.set_index("symbol").krw / fx) if kr is not None and len(kr) else pd.Series(dtype=float)
    f = pd.concat([last, c30, v30, vbase, dv24.rename("dv24")], axis=1).dropna()
    f["r30"] = f.c / f.c30 - 1; f["volx"] = f.v30 / (6 * f.vbase.replace(0, np.nan))
    f["kr_usd"] = [kr_usd.get(s.split("/")[0], 0.0) for s in f.index]
    f["kr_share"] = f.kr_usd / (f.kr_usd + f.v30)
    cand = f[(f.r30 >= R30) & (f.volx >= VOLX) & (f.dv24 >= DV_MIN) & (f.kr_share >= KR_SHARE)]
    if len(cand):
        okc = crypto_only(cand.index.tolist()); cand = cand[cand.index.isin(okc)]
    recent = q(st, f"SELECT symbol FROM {TABLE} WHERE ts_signal >= %s", (now - HOLD_S,))
    block = set(recent.symbol) if recent is not None and len(recent) else set()
    for sym, r in cand.sort_values("kr_share", ascending=False).iterrows():
        if n_open >= MAX_OPEN:
            break
        if sym in block:
            continue
        q(st, f"INSERT INTO {TABLE} (symbol, ts_signal, r30, volx, kr_share, dv24, entry_px, status) VALUES (%s,%s,%s,%s,%s,%s,%s,'open')",
          (sym, int(r.ts), float(r.r30), float(r.volx), float(r.kr_share), float(r.dv24), float(r.c)))
        n_open += 1
        print(time.strftime("%F %T"), f"F14 open SHORT {sym} r30 {r.r30:+.3f} volx {r.volx:.1f} kr_share {r.kr_share:.2f} at {r.c}", flush=True)
    s = q(st, f"SELECT count(*) n, avg(net) m FROM {TABLE} WHERE status = 'closed'")
    print(time.strftime("%F %T"), f"F14 open {n_open} closed {int(s.n[0])} mean net {float(s.m[0]) if s.m[0] is not None else float('nan'):+.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
