"""E1b: Upbit KRW listing notices vs Binance perp trades at second resolution (aggTrades).

For each E1 event: price path around the notice from Binance futures aggTrades; entry at the first trade at or
after notice + L seconds; exit at the last trade before entry + 60 minutes. Registered L = 2 s.
Out: data/reports/formal/events_E1b.md, events_E1b.csv; ledger row.
"""

from __future__ import annotations

import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from formal_test import FEE, slip  # noqa: E402
from event_edges import boot, MID, LEDGER, pct  # noqa: E402

OUT = ROOT / "data" / "reports" / "formal"
LAT = [0, 0.5, 1, 2, 3, 5, 10, 30, 60]
URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/{c}/{c}-aggTrades-{d}.zip"


def trades(code: str, t: int) -> pd.DataFrame | None:
    frames = []
    days = {pd.to_datetime(x, unit="s").strftime("%Y-%m-%d") for x in (t - 300, t + 3900)}
    for d in sorted(days):
        r = requests.get(URL.format(c=code, d=d), timeout=120)
        if r.status_code != 200:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = z.read(z.namelist()[0])
        df = pd.read_csv(io.BytesIO(raw), header=None, usecols=[1, 2, 5], names=["p", "q", "ms"],
                         dtype={1: float, 2: float, 5: "int64"}, skiprows=0, on_bad_lines="skip",
                         engine="c") if raw[:1].isdigit() else \
            pd.read_csv(io.BytesIO(raw), usecols=["price", "quantity", "transact_time"]).rename(
                columns={"price": "p", "quantity": "q", "transact_time": "ms"})
        if df["ms"].iloc[0] > 10 ** 14:                  # microseconds
            df["ms"] //= 1000
        frames.append(df[(df["ms"] >= (t - 300) * 1000) & (df["ms"] <= (t + 3900) * 1000)])
    if not frames:
        return None
    return pd.concat(frames).sort_values("ms").reset_index(drop=True)


def one(ev: dict) -> dict | None:
    try:
        tr = trades(ev["code"], int(ev["ts"]))
    except Exception as e:  # noqa: BLE001
        return dict(code=ev["code"], err=repr(e))
    if tr is None or tr.empty:
        return None
    t_ms = int(ev["ts"]) * 1000
    pre = tr[tr["ms"] < t_ms]
    if pre.empty:
        return None
    base = float(pre["p"].iloc[-1])
    ms, p = tr["ms"].to_numpy(), tr["p"].to_numpy()
    out = dict(code=ev["code"], sym=ev["sym"], ts=int(ev["ts"]), base=base, dv24=float(ev["dv24"]))
    up3 = np.flatnonzero((ms >= t_ms - 60_000) & (p >= base * 1.03))
    out["jump3_s"] = (ms[up3[0]] - t_ms) / 1000 if len(up3) else np.nan     # first +3% print vs notice time
    cost = 2 * (FEE + float(slip(ev["dv24"])))
    for L in LAT:
        i = np.searchsorted(ms, t_ms + L * 1000)
        if i >= len(ms):
            continue
        entry = p[i]
        j = np.searchsorted(ms, ms[i] + 3_600_000) - 1
        out[f"move_before_{L}"] = entry / base - 1
        out[f"net_{L}"] = p[j] / entry - 1 - cost
    return out


def main() -> int:
    ev = pd.read_csv(OUT / "events_E1.csv").to_dict("records")
    with ThreadPoolExecutor(6) as ex:
        res = [r for r in ex.map(one, ev) if r]
    R = pd.DataFrame(res)
    R.to_csv(OUT / "events_E1b.csv", index=False)
    ok = R[R.get("err").isna()] if "err" in R else R
    L = ["# E1b: Upbit KRW listing notice vs Binance perp trades (second resolution)", "",
         f"{len(ok)} of {len(ev)} events with aggTrades. Base = last trade before the notice timestamp "
         "(Upbit first_listed_at). Net = 60 minutes after entry, after fees and slippage.", "",
         f"- First +3% print relative to the notice time: median {ok['jump3_s'].median():.1f} s "
         f"(25%: {ok['jump3_s'].quantile(.25):.1f} s, 75%: {ok['jump3_s'].quantile(.75):.1f} s); "
         f"no +3% within the window: {ok['jump3_s'].isna().mean():.0%}", "",
         "| entry latency | already moved before entry (median) | mean net 60m | median | hit | 95% CI | 2025 | 2026 |",
         "|---|---|---|---|---|---|---|---|"]
    reg = None
    for Lat in LAT:
        if f"net_{Lat}" not in ok:
            continue
        x = ok[f"net_{Lat}"].astype(float)
        lo, hi = boot(x.to_numpy())
        a, b = x[ok["ts"] < MID], x[ok["ts"] >= MID]
        L.append(f"| {Lat}s{' (registered)' if Lat == 2 else ''} | {pct(ok[f'move_before_{Lat}'].median())} | "
                 f"{pct(x.mean())} | {pct(x.median())} | {(x > 0).mean():.0%} | [{pct(lo)}, {pct(hi)}] | "
                 f"{pct(a.mean())} (n={a.notna().sum()}) | {pct(b.mean())} (n={b.notna().sum()}) |")
        if Lat == 2:
            reg = dict(mean=x.mean(), n=int(x.notna().sum()))
    (OUT / "events_E1b.md").write_text("\n".join(L) + "\n")
    if reg:
        led = pd.read_csv(LEDGER)
        led = pd.concat([led, pd.DataFrame([dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S"), run_id=f"EVT-{int(time.time())}",
                                                 hypothesis="E1b_upbit_listing_2s", tier="confirmatory",
                                                 period="2025-03..2026-09", mean_daily_net=reg["mean"],
                                                 n_trades=reg["n"], note="per-trade mean")])], ignore_index=True)
        led.to_csv(LEDGER, index=False)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
