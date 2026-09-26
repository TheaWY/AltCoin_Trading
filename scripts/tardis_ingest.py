#!/usr/bin/env python3
"""Tardis.dev trial ingest: stream the raw tick files and keep only compact
1-minute features, so ~250 GB of downloads end up as ~1-2 GB on disk.

Nothing raw is written except liquidations (small). Every file is streamed
from the HTTPS response through gzip into pandas in chunks.

Per exchange (binance-futures, bybit, okex-swap, bitget-futures, hyperliquid)
and day of the trial window:
  liquidations  PERPETUALS file -> data/tardis/liq/<ex>/<day>.parquet (raw rows, USD notional)
  derivative_ticker PERPETUALS -> 1m last open interest (USD), funding, predicted funding,
                                mark, index, last  -> data/tardis/deriv/<ex>/<day>.parquet
  trades PERPETUALS           -> 1m buy/sell notional, trade count, large-trade (>=25k, >=100k USD)
                                buy/sell notional, max trade, vwap -> data/tardis/trades/<ex>/<day>.parquet
  book_snapshot_25 (binance-futures and bybit only, the ~120 most traded coins of
                    that day plus the most volatile)  -> 1m spread, depth within
                    0.5 / 1 / 2% each side, imbalance, top-5 imbalance mean, microprice
                    offset -> data/tardis/book/<ex>/<day>/<symbol>.parquet

Resumable: an existing output file is skipped. OKX amounts are contracts and
are converted with each instrument's contract value (OKX public API).
Key: TARDIS_API_KEY in .env (never printed).
"""

from __future__ import annotations

import argparse
import gzip
import io
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "tardis"
BASE = "https://datasets.tardis.dev/v1"
EXCHANGES = ("binance-futures", "bybit", "okex-swap", "bitget-futures", "hyperliquid")
BOOK_EXCHANGES = ("binance-futures", "bybit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("tardis")
CHUNK = 2_000_000


def key() -> str:
    k = os.getenv("TARDIS_API_KEY")
    if not k:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("TARDIS_API_KEY="):
                k = line.split("=", 1)[1].strip()
    return k


def stream_csv(url: str, usecols=None, dtype=None):
    """Yield DataFrame chunks of a gzipped CSV straight from HTTP (nothing on disk)."""
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {key()}"}, stream=True, timeout=120)
            if r.status_code == 404:
                return
            r.raise_for_status()
            r.raw.decode_content = False
            gz = gzip.GzipFile(fileobj=r.raw)
            yield from pd.read_csv(io.TextIOWrapper(gz), usecols=usecols, dtype=dtype, chunksize=CHUNK)
            return
        except (requests.RequestException, EOFError, OSError) as e:
            log.warning("retry %s (%r)", url, e)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"failed {url}")


_CTVAL: dict[str, float] = {}


def okx_ctval() -> dict[str, float]:
    if not _CTVAL:
        d = requests.get("https://www.okx.com/api/v5/public/instruments", params={"instType": "SWAP"}, timeout=30).json()
        for i in d.get("data", []):
            if i.get("ctValCcy") and i["settleCcy"] == "USDT":
                _CTVAL[i["instId"]] = float(i["ctVal"])
    return _CTVAL


def base_of(ex: str, sym: str) -> str:
    s = sym.upper()
    if ex == "okex-swap":
        return s.split("-")[0]
    if ex == "hyperliquid":
        return s
    for q in ("USDT", "USDC", "USD", "PERP"):
        if s.endswith(q):
            return s[: -len(q)]
    return s


def _scale(ex: str, df: pd.DataFrame, col: str) -> pd.Series:
    if ex != "okex-swap":
        return df[col].astype(float)
    cv = okx_ctval()
    return df[col].astype(float) * df["symbol"].map(cv)


def job_liq(ex: str, day: str) -> str:
    out = OUT / "liq" / ex / f"{day}.parquet"
    if out.exists():
        return "skip"
    y, m, d = day.split("-")
    parts = []
    for ch in stream_csv(f"{BASE}/{ex}/liquidations/{y}/{m}/{d}/PERPETUALS.csv.gz"):
        ch["qty"] = _scale(ex, ch, "amount")
        ch["usd"] = ch["qty"] * ch["price"].astype(float)
        ch["base"] = [base_of(ex, s) for s in ch["symbol"]]
        parts.append(ch[["symbol", "base", "timestamp", "side", "price", "qty", "usd"]])
    out.parent.mkdir(parents=True, exist_ok=True)
    df = pd.concat(parts) if parts else pd.DataFrame(columns=["symbol", "base", "timestamp", "side", "price", "qty", "usd"])
    df.to_parquet(out, index=False)
    return f"{len(df)} rows"


def job_deriv(ex: str, day: str) -> str:
    out = OUT / "deriv" / ex / f"{day}.parquet"
    if out.exists():
        return "skip"
    y, m, d = day.split("-")
    cols = ["symbol", "timestamp", "funding_rate", "predicted_funding_rate", "open_interest", "last_price",
            "index_price", "mark_price"]
    agg = []
    for ch in stream_csv(f"{BASE}/{ex}/derivative_ticker/{y}/{m}/{d}/PERPETUALS.csv.gz", usecols=lambda c: c in cols):
        ch["minute"] = (ch["timestamp"] // 60_000_000) * 60
        ch["oi_qty"] = _scale(ex, ch, "open_interest") if "open_interest" in ch else np.nan
        g = ch.sort_values("timestamp").groupby(["symbol", "minute"]).last()
        agg.append(g.drop(columns=["timestamp", "open_interest"], errors="ignore"))
    if not agg:
        return "none"
    df = pd.concat(agg).reset_index()
    df = df.sort_values(["symbol", "minute"]).groupby(["symbol", "minute"]).last().reset_index()
    df["oi_usd"] = df["oi_qty"] * df["mark_price"]
    df["base"] = [base_of(ex, s) for s in df["symbol"]]
    out.parent.mkdir(parents=True, exist_ok=True)
    df.astype({c: "float32" for c in df.columns if df[c].dtype == "float64"}).to_parquet(out, index=False)
    return f"{df['symbol'].nunique()} symbols"


def job_trades(ex: str, day: str) -> str:
    out = OUT / "trades" / ex / f"{day}.parquet"
    if out.exists():
        return "skip"
    y, m, d = day.split("-")
    acc: list[pd.DataFrame] = []
    for ch in stream_csv(f"{BASE}/{ex}/trades/{y}/{m}/{d}/PERPETUALS.csv.gz",
                         usecols=["symbol", "timestamp", "side", "price", "amount"]):
        q = _scale(ex, ch, "amount")
        usd = q * ch["price"].astype(float)
        buy = ch["side"].astype(str).str.lower().eq("buy")
        f = pd.DataFrame({"symbol": ch["symbol"].to_numpy(), "minute": (ch["timestamp"].to_numpy() // 60_000_000) * 60,
                          "usd": usd.to_numpy(), "buy_usd": np.where(buy, usd, 0.0), "n": 1,
                          "big25_buy": np.where(buy & (usd >= 25_000), usd, 0.0),
                          "big25_sell": np.where(~buy & (usd >= 25_000), usd, 0.0),
                          "big100_buy": np.where(buy & (usd >= 100_000), usd, 0.0),
                          "big100_sell": np.where(~buy & (usd >= 100_000), usd, 0.0),
                          "pq": (ch["price"].astype(float) * usd).to_numpy(), "max_usd": usd.to_numpy()})
        g = f.groupby(["symbol", "minute"])
        a = g[["usd", "buy_usd", "n", "big25_buy", "big25_sell", "big100_buy", "big100_sell", "pq"]].sum()
        a["max_usd"] = g["max_usd"].max()
        acc.append(a)
    if not acc:
        return "none"
    df = pd.concat(acc).groupby(level=[0, 1]).agg({"usd": "sum", "buy_usd": "sum", "n": "sum", "big25_buy": "sum",
                                                   "big25_sell": "sum", "big100_buy": "sum", "big100_sell": "sum",
                                                   "pq": "sum", "max_usd": "max"}).reset_index()
    df["vwap"] = df["pq"] / df["usd"].replace(0, np.nan)
    df = df.drop(columns="pq")
    df["base"] = [base_of(ex, s) for s in df["symbol"]]
    out.parent.mkdir(parents=True, exist_ok=True)
    df.astype({c: "float32" for c in df.columns if df[c].dtype == "float64"}).to_parquet(out, index=False)
    return f"{df['symbol'].nunique()} symbols"


def book_symbols(ex: str, day: str, n: int = 120) -> list[str]:
    """Most traded symbols of the day (from our own trades aggregate) plus the most volatile."""
    tf = OUT / "trades" / ex / f"{day}.parquet"
    if not tf.exists():
        return []
    t = pd.read_parquet(tf, columns=["symbol", "minute", "usd", "vwap"])
    vol = t.groupby("symbol")["usd"].sum().sort_values(ascending=False)
    top = list(vol.index[:n])
    rng = t.groupby("symbol")["vwap"].agg(lambda x: x.max() / x.min() - 1 if x.min() > 0 else 0)
    rng = rng[vol.reindex(rng.index) > 2e6].sort_values(ascending=False)
    return list(dict.fromkeys(top + list(rng.index[:40])))


def job_book(ex: str, day: str, sym: str) -> str:
    out = OUT / "book" / ex / day / f"{sym}.parquet"
    if out.exists():
        return "skip"
    y, m, d = day.split("-")
    L = 25
    rows = []
    for ch in stream_csv(f"{BASE}/{ex}/book_snapshot_25/{y}/{m}/{d}/{sym}.csv.gz"):
        ap = ch[[f"asks[{i}].price" for i in range(L)]].to_numpy(float)
        aq = ch[[f"asks[{i}].amount" for i in range(L)]].to_numpy(float)
        bp = ch[[f"bids[{i}].price" for i in range(L)]].to_numpy(float)
        bq = ch[[f"bids[{i}].amount" for i in range(L)]].to_numpy(float)
        mid = (ap[:, 0] + bp[:, 0]) / 2
        f = {"minute": (ch["timestamp"].to_numpy() // 60_000_000) * 60, "spread_bps": (ap[:, 0] - bp[:, 0]) / mid * 1e4}
        for band, lab in ((0.005, "05"), (0.01, "1"), (0.02, "2")):
            f[f"bid_{lab}"] = np.nansum(np.where(bp >= mid[:, None] * (1 - band), bp * bq, 0), axis=1)
            f[f"ask_{lab}"] = np.nansum(np.where(ap <= mid[:, None] * (1 + band), ap * aq, 0), axis=1)
        b5, a5 = np.nansum(bp[:, :5] * bq[:, :5], axis=1), np.nansum(ap[:, :5] * aq[:, :5], axis=1)
        f["imb5"] = (b5 - a5) / (b5 + a5)
        f["micro_bps"] = ((ap[:, 0] * bq[:, 0] + bp[:, 0] * aq[:, 0]) / (bq[:, 0] + aq[:, 0]) / mid - 1) * 1e4
        f["reach_bps"] = np.minimum(ap[:, -1] / mid - 1, 1 - bp[:, -1] / mid) * 1e4   # how far 25 levels reach
        rows.append(pd.DataFrame(f).groupby("minute").agg(
            spread_bps=("spread_bps", "mean"), bid_05=("bid_05", "mean"), ask_05=("ask_05", "mean"),
            bid_1=("bid_1", "mean"), ask_1=("ask_1", "mean"), bid_2=("bid_2", "mean"), ask_2=("ask_2", "mean"),
            imb5=("imb5", "mean"), imb5_last=("imb5", "last"), micro_bps=("micro_bps", "mean"),
            reach_bps=("reach_bps", "median"), n_snap=("imb5", "size")))
    if not rows:
        return "none"
    df = pd.concat(rows)
    df = df.groupby(level=0).agg({c: ("sum" if c == "n_snap" else "mean") for c in df.columns}).reset_index()
    df.insert(0, "symbol", sym)
    df.insert(1, "base", base_of(ex, sym))
    out.parent.mkdir(parents=True, exist_ok=True)
    df.astype({c: "float32" for c in df.columns if df[c].dtype == "float64"}).to_parquet(out, index=False)
    return f"{len(df)} minutes"


def run(jobs, workers: int) -> None:
    with ProcessPoolExecutor(workers) as pool:
        futs = {pool.submit(fn, *args): (fn.__name__, args) for fn, args in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            name, args = futs[f]
            try:
                res = f.result()
            except Exception as e:  # noqa: BLE001
                res = f"ERROR {e!r}"
            log.info("%d/%d %s %s -> %s", i, len(futs), name, " ".join(args), res)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", default="2026-05-19")
    ap.add_argument("--to", dest="d1", default="2026-05-28")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--book-coins", type=int, default=120)
    ap.add_argument("--skip-books", action="store_true")
    a = ap.parse_args()
    d0, d1 = date.fromisoformat(a.d0), date.fromisoformat(a.d1)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    if not key():
        log.error("TARDIS_API_KEY missing")
        return 1
    okx_ctval()
    log.info("phase 1: liquidations + derivative ticker (%d days x %d exchanges)", len(days), len(EXCHANGES))
    run([(job_liq, (ex, d)) for ex in EXCHANGES if ex != "hyperliquid" for d in days]
        + [(job_deriv, (ex, d)) for ex in EXCHANGES for d in days], a.workers)
    log.info("phase 2: trades")
    run([(job_trades, (ex, d)) for d in days for ex in EXCHANGES], min(a.workers, 4))
    if not a.skip_books:
        log.info("phase 3: order books")
        jobs = [(job_book, (ex, d, s)) for d in days for ex in BOOK_EXCHANGES for s in book_symbols(ex, d, a.book_coins)]
        log.info("%d book files", len(jobs))
        run(jobs, a.workers)
    log.info("tardis ingest done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
