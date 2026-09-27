"""Fetch CoinGecko sector categories for mapped coins (for B15_4 'unrelated coin' test).
Out: data/cache/cg_categories.json {symbol: [categories]}. Free API, polite backoff, resumable."""
import json, time, pathlib, requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
IDS = json.load(open(ROOT / "data/cache/coingecko_ids.json"))
OUT = ROOT / "data/cache/cg_categories.json"
done = json.load(open(OUT)) if OUT.exists() else {}
s = requests.Session()
for i, (sym, cid) in enumerate(IDS.items()):
    if sym in done:
        continue
    for attempt in range(6):
        try:
            r = s.get(f"https://api.coingecko.com/api/v3/coins/{cid}",
                      params={"localization": "false", "tickers": "false", "market_data": "false",
                              "community_data": "false", "developer_data": "false"}, timeout=30)
            if r.status_code == 429:
                time.sleep(30 * (attempt + 1)); continue
            done[sym] = r.json().get("categories", []) if r.ok else []
            break
        except Exception:
            time.sleep(10)
    if i % 20 == 0:
        json.dump(done, open(OUT, "w")); print(i, len(done), flush=True)
    time.sleep(2.2)
json.dump(done, open(OUT, "w")); print("done", len(done))
