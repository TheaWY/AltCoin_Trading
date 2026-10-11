"""Sector tags per coin for B15_4 'unrelated coin' test.
CoinGecko per-coin endpoint is rate-limited to near zero on the free tier, so tags come from Binance's public product list
(one call). Non-sector tags (zones, launchpool, seed, monitoring, pos, mining) are dropped.
Out: data/cache/cg_categories.json {FUTURES_CODE: [tags]} (e.g. 1000PEPEUSDT -> PEPE tags)."""
import json, pathlib, re
import pandas as pd, requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
DROP = {"mining-zone", "innovation-zone", "Launchpool", "Launchpad", "Seed", "Monitoring", "pos", "pow", "storage-zone",
        "newListing", "Alpha", "Megadrop", "HODLer Airdrops", "RWA-zone"}
d = requests.get("https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products",
                 params={"includeEtf": "true"}, timeout=30).json()["data"]
base = {}
for x in d:
    base.setdefault(x["b"], set()).update(t for t in (x.get("tags") or []) if t not in DROP)
out = {}
for code in sorted(pd.read_parquet(ROOT / "data/cache/b15/pumps.parquet", columns=["code"])["code"].unique()):
    b = code[:-4] if code.endswith("USDT") else code
    out[code] = sorted(base.get(b) or base.get(re.sub(r"^(1000000|1000|1M)", "", b)) or [])
json.dump(out, open(ROOT / "data/cache/cg_categories.json", "w"))
print(len(out), sum(1 for v in out.values() if v), "with tags")
