#!/usr/bin/env python3
"""BTC feature panel from free, key-less public sources (launchd: com.altcoin.btcfeatures, hourly).
Long table btc_features(source, metric, ts, value). Research only: nothing here trades.

sources (min resolution / history):
  coinmetrics   Coin Metrics community: 31 daily on-chain metrics (2009-), ReferenceRateUSD hourly
  mempool       mempool.space: recommended fees + mempool state (live), hashrate/difficulty (daily, 3y)
  deribit       DVOL hourly (2021-), realized vol hourly, option-book snapshot -> ATM IV term structure, 25d skew, put/call OI
  cftc          CME bitcoin futures COT by trader category (weekly, 2018-)
  defillama     stablecoin total circulating USD (daily)
  blockchain    blockchain.com charts: transactions, miners revenue, unique addresses (daily)
  okx           long/short account ratio, taker buy/sell volume (5m, OKX keeps only a few days -> we accumulate)
  bybit         BTCUSDT open interest (5m)
  binance       premium index (basis) 1m, spot BTCUSDT 1m close
  premium       Coinbase BTC-USD 1m, Upbit KRW-BTC + KRW-USDT 1m -> coinbase_premium_pct, kimchi_premium_pct
  fng           alternative.me fear & greed (daily, 2018-)
  polymarket    BTC-related market probabilities (live only)

    .venv/bin/python scripts/collect_btc_features.py            # incremental (last ~3 days / live snapshots)
    .venv/bin/python scripts/collect_btc_features.py --backfill  # full history where the source allows
    .venv/bin/python scripts/collect_btc_features.py --only deribit,okx
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.data.storage import get_storage  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("btc_features")
S = requests.Session()
S.headers["User-Agent"] = "AltCoin_Trading research collector (contact: eurysohn@gmail.com)"
NOW = int(time.time())
DAY = 86400
Row = tuple[str, str, int, float]


def get(url: str, params: dict | None = None, tries: int = 3, timeout: int = 30, **kw):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=timeout, **kw)
            if r.status_code == 429:
                time.sleep(5 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            log.warning("retry %s: %s", url[:80], e); time.sleep(2 * (i + 1))
    return None


def ensure(storage) -> None:
    with storage._connect() as c:  # noqa: SLF001
        c.execute("""CREATE TABLE IF NOT EXISTS btc_features (
            source TEXT NOT NULL, metric TEXT NOT NULL, ts BIGINT NOT NULL, value DOUBLE PRECISION,
            PRIMARY KEY (source, metric, ts))""")
        c.execute("CREATE INDEX IF NOT EXISTS btc_features_ts ON btc_features (ts)")


def save(storage, rows: list[Row]) -> int:
    rows = [(s, m, int(t), float(v)) for s, m, t, v in rows if v is not None and v == v]
    if not rows:
        return 0
    with storage._connect() as c:  # noqa: SLF001
        for i in range(0, len(rows), 5000):
            c.executemany("INSERT INTO btc_features (source, metric, ts, value) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT (source, metric, ts) DO UPDATE SET value = EXCLUDED.value", rows[i:i + 5000])
    return len(rows)


def last_ts(storage, source: str, metric: str | None = None) -> int | None:
    with storage._connect() as c:  # noqa: SLF001
        if metric:
            r = c.execute("SELECT MAX(ts) AS mx FROM btc_features WHERE source = ? AND metric = ?", (source, metric)).fetchone()
        else:
            r = c.execute("SELECT MAX(ts) AS mx FROM btc_features WHERE source = ?", (source,)).fetchone()
    r = dict(r) if r else {}
    return int(r["mx"]) if r.get("mx") is not None else None


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pts(ts_str: str) -> int:
    return int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())


# ------------------------------------------------------------------ sources
CM = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
CM_DAILY = ["AdrActCnt", "AdrBalCnt", "BlkCnt", "CapMVRVCur", "CapMrktCurUSD", "FeeTotNtv", "FlowInExUSD", "FlowOutExUSD",
            "HashRate", "IssTotUSD", "PriceUSD", "ROI30d", "SplyCur", "SplyExUSD", "SplyExNtv", "TxCnt", "TxTfrCnt",
            "volume_reported_spot_usd_1d"]


def src_coinmetrics(storage, backfill: bool) -> list[Row]:
    out: list[Row] = []
    for metrics, freq, name in ((CM_DAILY, "1d", "cm"), (["ReferenceRateUSD"], "1h", "cm_ref")):
        lt = last_ts(storage, "coinmetrics", metrics[0] if freq == "1d" else "ReferenceRateUSD_1h")
        start = "2009-01-03" if backfill or lt is None else iso(lt - 3 * DAY)
        url, params = CM, {"assets": "btc", "metrics": ",".join(metrics), "frequency": freq, "start_time": start, "page_size": 10000}
        while url:
            d = get(url, params)
            for r in d.get("data", []):
                t = pts(r["time"])
                for m in metrics:
                    if r.get(m) not in (None, ""):
                        out.append(("coinmetrics", m if freq == "1d" else m + "_1h", t, float(r[m])))
            url, params = d.get("next_page_url"), None
            time.sleep(0.5)
    return out


def src_mempool(storage, backfill: bool) -> list[Row]:
    out: list[Row] = []
    t = NOW - NOW % 60
    f = get("https://mempool.space/api/v1/fees/recommended")
    out += [("mempool", f"fee_{k}", t, v) for k, v in f.items()]
    m = get("https://mempool.space/api/mempool")
    out += [("mempool", "mempool_count", t, m["count"]), ("mempool", "mempool_vsize", t, m["vsize"]), ("mempool", "mempool_total_fee", t, m["total_fee"])]
    h = get("https://mempool.space/api/v1/mining/hashrate/" + ("3y" if backfill else "1m"))
    out += [("mempool", "hashrate", r["timestamp"], r["avgHashrate"]) for r in h.get("hashrates", [])]
    out += [("mempool", "difficulty", r["time"], r["difficulty"]) for r in h.get("difficulty", [])]
    return out


DERIBIT = "https://www.deribit.com/api/v2/public/"


def src_deribit(storage, backfill: bool) -> list[Row]:
    out: list[Row] = []
    lt = last_ts(storage, "deribit", "dvol")
    start = (pts("2021-03-24T00:00:00Z") if backfill or lt is None else lt - DAY) * 1000
    while start < NOW * 1000:
        end = min(start + 1000 * 3600 * 1000, NOW * 1000)
        d = get(DERIBIT + "get_volatility_index_data", {"currency": "BTC", "resolution": 3600, "start_timestamp": start, "end_timestamp": end})
        data = d.get("result", {}).get("data", [])
        out += [("deribit", "dvol", r[0] // 1000, r[4]) for r in data]
        start = end + 1; time.sleep(0.3)
    hv = get(DERIBIT + "get_historical_volatility", {"currency": "BTC"})
    out += [("deribit", "realized_vol", r[0] // 1000, r[1]) for r in hv.get("result", [])]
    # option book snapshot -> term structure + skew (no history from the source; we accumulate hourly)
    bk = get(DERIBIT + "get_book_summary_by_currency", {"currency": "BTC", "kind": "option"})["result"]
    t = NOW - NOW % 3600
    by_exp: dict[str, list] = {}
    for o in bk:
        try:
            _, exp, k, cp = o["instrument_name"].split("-")
            by_exp.setdefault(exp, []).append((float(k), cp, o.get("mark_iv"), o.get("open_interest") or 0.0, o.get("underlying_price")))
        except ValueError:
            continue
    put_oi = sum(x[3] for v in by_exp.values() for x in v if x[1] == "P"); call_oi = sum(x[3] for v in by_exp.values() for x in v if x[1] == "C")
    out += [("deribit", "put_oi", t, put_oi), ("deribit", "call_oi", t, call_oi), ("deribit", "put_call_oi", t, put_oi / call_oi if call_oi else None)]
    exps = []
    for exp, v in by_exp.items():
        try:
            dte = (datetime.strptime(exp, "%d%b%y").replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds() / DAY
        except ValueError:
            continue
        exps.append((dte, exp, v))
    for target, name in ((7, "7d"), (30, "30d"), (90, "90d"), (180, "180d")):
        cand = [e for e in exps if e[0] >= target * 0.6]
        if not cand:
            continue
        dte, exp, v = min(cand, key=lambda e: abs(e[0] - target))
        und = next((x[4] for x in v if x[4]), None)
        if not und:
            continue
        def iv_at(mult, cp):
            c = [x for x in v if x[1] == cp and x[2]]
            return min(c, key=lambda x: abs(x[0] - und * mult))[2] if c else None
        atm = iv_at(1.0, "C"); p25 = iv_at(0.9, "P"); c25 = iv_at(1.1, "C")
        out += [("deribit", f"atm_iv_{name}", t, atm), ("deribit", f"skew_{name}", t, (p25 - c25) if p25 and c25 else None),
                ("deribit", f"dte_{name}", t, dte)]
    return out


def src_cftc(storage, backfill: bool) -> list[Row]:
    # Traders in Financial Futures, futures only. Bitcoin contract at CME.
    d = get("https://publicreporting.cftc.gov/resource/gpe5-46if.json",
            {"market_and_exchange_names": "BITCOIN - CHICAGO MERCANTILE EXCHANGE", "$limit": 5000 if backfill else 8,
             "$order": "report_date_as_yyyy_mm_dd DESC"})
    out: list[Row] = []
    for r in d:
        t = pts(r["report_date_as_yyyy_mm_dd"].replace(".000", "Z"))
        for k in ("open_interest_all", "dealer_positions_long_all", "dealer_positions_short_all", "asset_mgr_positions_long_all",
                  "asset_mgr_positions_short_all", "lev_money_positions_long_all", "lev_money_positions_short_all",
                  "other_rept_positions_long_all", "other_rept_positions_short_all", "nonrept_positions_long_all", "nonrept_positions_short_all"):
            if r.get(k) is not None:
                out.append(("cftc", k.replace("_positions", "").replace("_all", ""), t, float(r[k])))
        for g in ("dealer", "asset_mgr", "lev_money", "other_rept", "nonrept"):
            lo, sh = r.get(f"{g}_positions_long_all"), r.get(f"{g}_positions_short_all")
            if lo is not None and sh is not None:
                out.append(("cftc", f"{g}_net", t, float(lo) - float(sh)))
    return out


def src_defillama(storage, backfill: bool) -> list[Row]:
    d = get("https://stablecoins.llama.fi/stablecoincharts/all")
    rows = d if backfill else d[-10:]
    out = [("defillama", "stable_total_usd", int(r["date"]), r["totalCirculatingUSD"]["peggedUSD"]) for r in rows if r.get("totalCirculatingUSD")]
    u = get("https://stablecoins.llama.fi/stablecoincharts/all", {"stablecoin": 1})
    out += [("defillama", "usdt_usd", int(r["date"]), r["totalCirculatingUSD"]["peggedUSD"]) for r in (u if backfill else u[-10:]) if r.get("totalCirculatingUSD")]
    return out


def src_blockchain(storage, backfill: bool) -> list[Row]:
    out: list[Row] = []
    for chart in ("n-transactions", "miners-revenue", "n-unique-addresses", "mempool-size", "utxo-count"):
        try:
            d = get(f"https://api.blockchain.info/charts/{chart}", {"timespan": "all" if backfill else "30days", "format": "json"})
            out += [("blockchain", chart.replace("-", "_"), r["x"], r["y"]) for r in d.get("values", [])]
        except Exception as e:  # noqa: BLE001
            log.warning("blockchain %s: %s", chart, e)
        time.sleep(0.5)
    return out


def src_okx(storage, backfill: bool) -> list[Row]:
    out: list[Row] = []
    d = get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": "BTC", "period": "5m"})
    out += [("okx", "ls_account_ratio", int(r[0]) // 1000, float(r[1])) for r in d.get("data", [])]
    d = get("https://www.okx.com/api/v5/rubik/stat/taker-volume", {"ccy": "BTC", "instType": "CONTRACTS", "period": "5m"})
    for r in d.get("data", []):
        t, sell, buy = int(r[0]) // 1000, float(r[1]), float(r[2])
        out += [("okx", "taker_sell", t, sell), ("okx", "taker_buy", t, buy), ("okx", "taker_buy_ratio", t, buy / (buy + sell) if buy + sell else None)]
    d = get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume", {"ccy": "BTC", "period": "5m"})
    out += [("okx", "oi_usd", int(r[0]) // 1000, float(r[1])) for r in d.get("data", [])]
    return out


def src_bybit(storage, backfill: bool) -> list[Row]:
    out: list[Row] = []
    cursor, laps = None, (60 if backfill else 2)
    for _ in range(laps):
        p = {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "5min", "limit": 200}
        if cursor:
            p["cursor"] = cursor
        d = get("https://api.bybit.com/v5/market/open-interest", p)
        res = d.get("result", {})
        out += [("bybit", "oi_btc", int(r["timestamp"]) // 1000, float(r["openInterest"])) for r in res.get("list", [])]
        cursor = res.get("nextPageCursor")
        if not cursor:
            break
        time.sleep(0.2)
    return out


def klines(url: str, symbol: str, since: int, until: int, limit: int, pause: float = 0.15) -> list[list]:
    rows, t = [], since * 1000
    while t < until * 1000:
        d = get(url, {"symbol": symbol, "interval": "1m", "startTime": t, "limit": limit})
        if not d:
            break
        rows += d
        t = int(d[-1][0]) + 60000
        if len(d) < limit:
            break
        time.sleep(pause)
    return rows


def src_binance(storage, backfill: bool) -> list[Row]:
    out: list[Row] = []
    lt = last_ts(storage, "binance", "spot_close")
    since = NOW - (30 * DAY if backfill or lt is None else 3 * 3600)
    for r in klines("https://fapi.binance.com/fapi/v1/premiumIndexKlines", "BTCUSDT", since, NOW, 1500):
        out.append(("binance", "premium_index", int(r[0]) // 1000, float(r[4])))
    for r in klines("https://api.binance.com/api/v3/klines", "BTCUSDT", since, NOW, 1000):
        out.append(("binance", "spot_close", int(r[0]) // 1000, float(r[4])))
    return out


def src_premium(storage, backfill: bool) -> list[Row]:
    """Coinbase BTC-USD, Upbit KRW-BTC and KRW-USDT at 1m; premiums vs Binance spot computed at report time (API)."""
    out: list[Row] = []
    lt = last_ts(storage, "premium", "coinbase_close")
    since = NOW - (30 * DAY if backfill or lt is None else 3 * 3600)
    t = since
    while t < NOW:                                              # Coinbase: 300 candles per call
        d = get("https://api.exchange.coinbase.com/products/BTC-USD/candles", {"granularity": 60, "start": iso(t), "end": iso(min(t + 300 * 60, NOW))})
        out += [("premium", "coinbase_close", int(r[0]), float(r[4])) for r in d]
        t += 300 * 60; time.sleep(0.25)
    for mkt, name in (("KRW-BTC", "upbit_btc_krw"), ("KRW-USDT", "upbit_usdt_krw")):
        to = NOW
        while to > since:                                       # Upbit: 200 candles per call, backwards
            d = get("https://api.upbit.com/v1/candles/minutes/1", {"market": mkt, "count": 200, "to": iso(to)})
            if not d:
                break
            out += [("premium", name, int(r["timestamp"]) // 1000 // 60 * 60, float(r["trade_price"])) for r in d]
            to = min(int(r["timestamp"]) // 1000 for r in d) - 60; time.sleep(0.15)
    return out


def src_fng(storage, backfill: bool) -> list[Row]:
    d = get("https://api.alternative.me/fng/", {"limit": 0 if backfill else 5, "format": "json"})
    return [("fng", "fear_greed", int(r["timestamp"]), float(r["value"])) for r in d.get("data", [])]


def src_polymarket(storage, backfill: bool) -> list[Row]:
    out: list[Row] = []
    t = NOW - NOW % 3600
    for q in ("bitcoin", "btc"):
        d = get("https://gamma-api.polymarket.com/markets", {"limit": 50, "active": "true", "closed": "false", "order": "volume24hr", "ascending": "false"})
        for m in d:
            if q not in (m.get("question") or "").lower():
                continue
            try:
                p = m.get("outcomePrices"); p = __import__("json").loads(p) if isinstance(p, str) else p
                out.append(("polymarket", m["slug"][:80], t, float(p[0])))
            except Exception:  # noqa: BLE001
                continue
    return out


SOURCES = {"coinmetrics": src_coinmetrics, "mempool": src_mempool, "deribit": src_deribit, "cftc": src_cftc, "defillama": src_defillama,
           "blockchain": src_blockchain, "okx": src_okx, "bybit": src_bybit, "binance": src_binance, "premium": src_premium,
           "fng": src_fng, "polymarket": src_polymarket}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    storage = get_storage(); ensure(storage)
    names = [n for n in a.only.split(",") if n] or list(SOURCES)
    for n in names:
        t0 = time.time()
        try:
            k = save(storage, SOURCES[n](storage, a.backfill))
            log.info("%s: %d rows in %.0fs", n, k, time.time() - t0)
        except Exception:  # noqa: BLE001
            log.exception("%s failed", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
