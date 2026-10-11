"""Descriptive profile of the one edge that passed twice (E1b 2025-26 and B3 2024): Upbit KRW listing notice ->
Binance perp. Pools all events 2024-03..2026-09 and tabulates net return by entry latency and holding time, by year,
plus how fast Upbit's announcement API answers from this machine (for the watcher design). Not a new test."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from listing_latency import trades  # noqa: E402

LAT = [1, 2, 3, 5, 10]
HOLD = [5, 15, 60, 240]
COST = 0.0025                      # fees + a thin-book slippage allowance per round trip


def one(ev):
    try:
        tr = trades(ev["code"], int(ev["ts"]))
    except Exception:  # noqa: BLE001
        return None
    if tr is None or tr.empty:
        return None
    t = int(ev["ts"]) * 1000
    ms, p = tr["ms"].to_numpy(), tr["p"].to_numpy()
    if not (ms < t).any():
        return None
    out = dict(code=ev["code"], ts=int(ev["ts"]))
    for L in LAT:
        i = np.searchsorted(ms, t + L * 1000)
        if i >= len(ms):
            continue
        for H in HOLD:
            j = np.searchsorted(ms, ms[i] + H * 60_000) - 1
            out[f"{L}s_{H}m"] = p[j] / p[i] - 1 - COST
    return out


def main():
    a = pd.read_csv(ROOT / "data/reports/formal/events_E1.csv")[["code", "ts"]]
    b = pd.read_csv(ROOT / "data/reports/b3/notices_2024.csv")
    b = b[b["hid"].str.contains("E1b")][["code", "ts"]]
    ev = pd.concat([b, a]).drop_duplicates()
    with ThreadPoolExecutor(6) as ex:
        R = pd.DataFrame([r for r in ex.map(one, ev.to_dict("records")) if r])
    R["year"] = pd.to_datetime(R["ts"], unit="s").dt.year
    R.to_csv(ROOT / "data/reports/b2/upbit_listing_profile.csv", index=False)
    L_ = [f"# Upbit KRW listing -> Binance perp, all events 2024-03..2026-09 (n={len(R)})", "",
          f"Net of {COST*100:.2f}% round trip. Cells: mean / median.", "",
          "| entry | " + " | ".join(f"hold {h}m" for h in HOLD) + " |", "|---|" + "---|" * len(HOLD)]
    for Lt in LAT:
        L_.append(f"| +{Lt}s | " + " | ".join(f"{R[f'{Lt}s_{h}m'].mean()*100:+.1f}% / {R[f'{Lt}s_{h}m'].median()*100:+.1f}%"
                                             for h in HOLD) + " |")
    L_ += ["", "By year, entry +2s, hold 15m and 60m (mean / median / n):", ""]
    for y, g in R.groupby("year"):
        L_.append(f"- {y}: 15m {g['2s_15m'].mean()*100:+.1f}% / {g['2s_15m'].median()*100:+.1f}%, "
                  f"60m {g['2s_60m'].mean()*100:+.1f}% / {g['2s_60m'].median()*100:+.1f}% (n={len(g)})")
    # API latency from this machine
    s = requests.Session()
    lat = []
    for _ in range(10):
        t0 = time.perf_counter()
        r = s.get("https://api-manager.upbit.com/api/v1/announcements",
                  params={"os": "web", "page": 1, "per_page": 5, "category": "trade"}, timeout=10)
        lat.append((time.perf_counter() - t0) * 1000)
        time.sleep(1.1)
    L_ += ["", f"Upbit announcement API round trip from the Mac mini (10 calls, 1.1 s apart): median {np.median(lat):.0f} ms, "
               f"max {max(lat):.0f} ms, last status {r.status_code}"]
    (ROOT / "data/reports/b2/upbit_listing_profile.md").write_text("\n".join(L_) + "\n")
    print("\n".join(L_))


if __name__ == "__main__":
    main()
