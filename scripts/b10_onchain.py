"""B10 on-chain exchange flows (research/batch_B9_B13.yaml) from the free BigQuery public Ethereum dataset.
  .venv/bin/python -W ignore scripts/b10_onchain.py tokens    # map Binance perps -> ERC-20 contracts (+ decimals)
  .venv/bin/python -W ignore scripts/b10_onchain.py pull      # monthly hourly CEX in/outflows, dry-run budget guard
Cost guard: every month is dry-run first; the job stops if the cumulative scan would exceed BUDGET_GB (default 700 of the
free 1 TB/month). Output: data/cache/onchain/tokens.parquet, flows_YYYY-MM.parquet (token, hour, inflow, outflow, n_in, n_out)."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/cache/onchain"
PROJECT = "asdf-509914"
BUDGET_GB = float(os.environ.get("B10_BUDGET_GB", "700"))
LABELS = ROOT / "data/cache/cex_labels/spellbook/dbt_subprojects/hourly_spellbook/models/_sector/cex/addresses/chains/cex_evms_addresses.sql"


def cex_addresses():
    return sorted({a.lower() for a in re.findall(r"0x[0-9a-fA-F]{40}", LABELS.read_text())})


def tokens():
    OUT.mkdir(parents=True, exist_ok=True)
    from dotenv import load_dotenv
    import psycopg
    load_dotenv(ROOT / ".env")
    con = psycopg.connect(os.environ["DATABASE_URL"])
    cg = dict(con.execute("SELECT DISTINCT ON (symbol) symbol, cg_id FROM cg_daily WHERE cg_id IS NOT NULL ORDER BY symbol, ts DESC").fetchall())
    lst = requests.get("https://api.coingecko.com/api/v3/coins/list", params={"include_platform": "true"}, timeout=60).json()
    plat = {x["id"]: (x.get("platforms") or {}).get("ethereum") for x in lst}
    codes = json.load(open(ROOT / "data/cache/b7/codes.json"))
    rows = []
    for code in codes:
        base = code[:-4]
        for pre in ("1000000", "1000"):
            if base.startswith(pre) and len(base) > len(pre):
                base = base[len(pre):]
        cid = cg.get(f"{base}/USDT") or cg.get(base)
        addr = plat.get(cid) if cid else None
        if addr:
            rows.append(dict(code=code, base=base, cg_id=cid, address=addr.lower()))
    T = pd.DataFrame(rows).drop_duplicates("address")
    from google.cloud import bigquery
    c = bigquery.Client(project=PROJECT)
    q = "SELECT LOWER(address) AS address, SAFE_CAST(decimals AS INT64) AS decimals FROM `bigquery-public-data.crypto_ethereum.tokens` WHERE LOWER(address) IN UNNEST(@a)"
    cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter("a", "STRING", T["address"].tolist())])
    dry = c.query(q, job_config=bigquery.QueryJobConfig(dry_run=True, query_parameters=cfg.query_parameters))
    print("tokens dry-run GB", round(dry.total_bytes_processed / 1e9, 2))
    dec = c.query(q, job_config=cfg).to_dataframe().drop_duplicates("address")
    T = T.merge(dec, on="address", how="left")
    T.to_parquet(OUT / "tokens.parquet", index=False)
    print("mapped", len(T), "of", len(codes), "with decimals", int(T["decimals"].notna().sum()))


def pull():
    from google.cloud import bigquery
    c = bigquery.Client(project=PROJECT)
    T = pd.read_parquet(OUT / "tokens.parquet").dropna(subset=["decimals"])
    cex = cex_addresses()
    q = """
    SELECT token_address, TIMESTAMP_TRUNC(block_timestamp, HOUR) AS hour,
      SUM(IF(to_address IN UNNEST(@cex) AND from_address NOT IN UNNEST(@cex), SAFE_CAST(value AS FLOAT64), 0)) AS inflow_raw,
      SUM(IF(from_address IN UNNEST(@cex) AND to_address NOT IN UNNEST(@cex), SAFE_CAST(value AS FLOAT64), 0)) AS outflow_raw,
      COUNTIF(to_address IN UNNEST(@cex) AND from_address NOT IN UNNEST(@cex)) AS n_in,
      COUNTIF(from_address IN UNNEST(@cex) AND to_address NOT IN UNNEST(@cex)) AS n_out
    FROM `bigquery-public-data.crypto_ethereum.token_transfers`
    WHERE block_timestamp >= @t0 AND block_timestamp < @t1
      AND token_address IN UNNEST(@tok)
      AND (to_address IN UNNEST(@cex) OR from_address IN UNNEST(@cex))
    GROUP BY 1, 2"""
    months = pd.period_range("2024-03", "2026-09", freq="M")
    spent = float((OUT / "spent_gb.txt").read_text()) if (OUT / "spent_gb.txt").exists() else 0.0
    for m in months:
        p = OUT / f"flows_{m}.parquet"
        if p.exists():
            continue
        params = [bigquery.ArrayQueryParameter("cex", "STRING", cex),
                  bigquery.ArrayQueryParameter("tok", "STRING", T["address"].tolist()),
                  bigquery.ScalarQueryParameter("t0", "TIMESTAMP", m.start_time.tz_localize("UTC")),
                  bigquery.ScalarQueryParameter("t1", "TIMESTAMP", (m + 1).start_time.tz_localize("UTC"))]
        dry = c.query(q, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=params))
        gb = dry.total_bytes_processed / 1e9
        if spent + gb > BUDGET_GB:
            print(f"STOP: {m} would bring the scan to {spent + gb:.0f} GB > budget {BUDGET_GB} GB", flush=True)
            break
        df = c.query(q, job_config=bigquery.QueryJobConfig(query_parameters=params)).to_dataframe()
        df = df.merge(T[["address", "code", "decimals"]], left_on="token_address", right_on="address", how="left")
        scale = 10.0 ** df["decimals"].astype(float)
        df["inflow"] = df["inflow_raw"] / scale
        df["outflow"] = df["outflow_raw"] / scale
        df["ts"] = (pd.to_datetime(df["hour"], utc=True) - pd.Timestamp(0, tz="UTC")) // pd.Timedelta("1s") + 3600   # hour close
        df[["code", "ts", "inflow", "outflow", "n_in", "n_out"]].to_parquet(p, index=False)
        spent += gb
        (OUT / "spent_gb.txt").write_text(f"{spent:.2f}")
        print(f"{m} rows {len(df)} scanned {gb:.1f} GB total {spent:.1f} GB", flush=True)
    print("done, total scanned GB", round(spent, 1))


if __name__ == "__main__":
    {"tokens": tokens, "pull": pull}[sys.argv[1]]()
