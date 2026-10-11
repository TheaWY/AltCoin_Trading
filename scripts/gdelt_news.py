"""Daily news volume and tone per coin from GDELT (free, no key, 1 request / 5 s).

For each perp base we query '"<project name>" (crypto OR token OR cryptocurrency)'
for the panel period, daily article count and average tone.
Out: data/cache/gdelt/<BASE>.json   (resumable)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache" / "gdelt"
API = "https://api.gdeltproject.org/api/v2/doc/doc"
START, END = "20260325000000", "20260926000000"
GAP = 15.0
TRADFI = {"AAPL", "AMD", "AMZN", "COIN", "COPPER", "CRCL", "CRWD", "EWY", "GOOGL", "HOOD", "INTC", "KORU", "META",
          "MSFT", "MSTR", "NATGAS", "NFLX", "NVDA", "PAXG", "PLTR", "QQQ", "SOXL", "SPY", "TQQQ", "TSLA", "XAG", "XAU",
          "XAUT", "XPD", "XPT"}


def names() -> dict[str, str]:
    ids = json.loads((ROOT / "data" / "cache" / "coingecko_ids.json").read_text())
    cl = requests.get("https://api.coingecko.com/api/v3/coins/list", timeout=60).json()
    by_id = {c["id"]: c["name"] for c in cl}
    by_sym: dict[str, list[str]] = {}
    for c in cl:
        by_sym.setdefault(c["symbol"].upper(), []).append(c["name"])
    bases = pd.read_parquet(ROOT / "data" / "cache" / "lit_panel.parquet", columns=["symbol"])["symbol"].unique()
    out = {}
    for s in bases:
        b = s.split("/")[0]
        if b in TRADFI:
            continue
        cid = ids.get(s)
        if cid and cid in by_id:
            out[b] = by_id[cid]
            continue
        core = b.removeprefix("1000000").removeprefix("1000")
        cand = by_sym.get(core, [])
        if len(cand) == 1:
            out[b] = cand[0]
    return out


def get(q: str, mode: str) -> dict | None:
    url = f"{API}?query={quote(q)}&mode={mode}&format=json&startdatetime={START}&enddatetime={END}&timelinesmooth=0"
    for attempt in range(6):
        try:
            r = requests.get(url, timeout=60)
            time.sleep(GAP)
            if r.status_code == 200 and r.text.startswith("{"):
                return r.json()
            time.sleep(120 * (attempt + 1))      # 429: back off hard, GDELT extends the block otherwise
        except (requests.RequestException, ValueError):
            time.sleep(60 * (attempt + 1))
    return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    nm = names()
    (OUT / "_names.json").write_text(json.dumps(nm, ensure_ascii=False, indent=0))
    print(f"{len(nm)} coins with a name", flush=True)
    for i, (b, n) in enumerate(sorted(nm.items())):
        f = OUT / f"{b}.json"
        if f.exists():
            continue
        q = f'"{n}" (crypto OR token OR cryptocurrency)' if len(n) > 3 else f'"{n} token"'
        vol = get(q, "timelinevolraw")
        tone = get(q, "timelinetone")
        if vol is None:
            print("skip (rate limited)", b, flush=True)
            continue
        f.write_text(json.dumps({"base": b, "name": n, "query": q, "vol": vol, "tone": tone}))
        if i % 25 == 0:
            print(i, b, n, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
