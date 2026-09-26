#!/usr/bin/env python3
"""Offload AltCoin_Trading data to Backblaze B2 with rclone (weekly launchd job
com.altcoin.offload, or by hand).

Credentials come from .env and are passed to rclone as environment variables,
so no config file with secrets is written:
  B2_KEY_ID, B2_APP_KEY   an application key restricted to one bucket
  B2_BUCKET               bucket name
  B2_PREFIX               folder inside the bucket (default altcoin)

What it does:
  1. copies data/cache (1m klines, order book depth, Korea 1m, funding),
     data/tardis and data/reports to B2 (local copies stay; they are inputs of
     the research jobs and cheap to re-derive anyway)
  2. moves data/backups older than --keep-backups days to B2: upload, verify
     with `rclone check`, then delete locally
  3. with --prune-db: rows older than --db-days in the big Postgres tables
     (prices, funding_rates, market_categories) are exported month by month to
     parquet, uploaded, verified by row count, and only then deleted.
     --vacuum-full rewrites those tables afterwards so the disk space actually
     returns to macOS (locks each table for a few minutes)
Restore: `rclone copy b2:<bucket>/<prefix>/db/<table>/ ./restore/` and read the parquet.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("offload")
DB_TABLES = {"prices": "timestamp", "funding_rates": "timestamp", "market_categories": "timestamp"}


def env() -> dict[str, str]:
    vals = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    need = ("B2_KEY_ID", "B2_APP_KEY", "B2_BUCKET")
    missing = [k for k in need if not (os.getenv(k) or vals.get(k))]
    if missing:
        raise SystemExit(f"missing in .env: {', '.join(missing)}")
    e = dict(os.environ)
    e["RCLONE_CONFIG_B2_TYPE"] = "b2"
    e["RCLONE_CONFIG_B2_ACCOUNT"] = os.getenv("B2_KEY_ID") or vals["B2_KEY_ID"]
    e["RCLONE_CONFIG_B2_KEY"] = os.getenv("B2_APP_KEY") or vals["B2_APP_KEY"]
    e["RCLONE_CONFIG_B2_HARD_DELETE"] = "false"
    e["_BUCKET"] = os.getenv("B2_BUCKET") or vals["B2_BUCKET"]
    e["_PREFIX"] = os.getenv("B2_PREFIX") or vals.get("B2_PREFIX", "altcoin")
    return e


def rclone(e: dict, *args: str) -> None:
    exe = shutil.which("rclone") or "/opt/homebrew/bin/rclone"
    cmd = [exe, *args, "--transfers", "8", "--fast-list", "--stats-one-line", "--stats", "60s"]
    log.info("rclone %s", " ".join(args))
    subprocess.run(cmd, env=e, check=True)


def dest(e: dict, sub: str) -> str:
    return f"b2:{e['_BUCKET']}/{e['_PREFIX']}/{sub}"


def copy_dirs(e: dict) -> None:
    for sub in ("cache", "tardis", "reports", "models"):
        p = ROOT / "data" / sub
        if p.exists():
            rclone(e, "copy", str(p), dest(e, sub))


def move_backups(e: dict, keep_days: int) -> None:
    src = ROOT / "data" / "backups"
    if not src.exists():
        return
    cutoff = time.time() - keep_days * 86400
    old = [f for f in src.iterdir() if f.is_file() and f.stat().st_mtime < cutoff]
    if not old:
        return
    with tempfile.NamedTemporaryFile("w", delete=False) as fl:
        fl.write("\n".join(f.name for f in old))
    rclone(e, "copy", str(src), dest(e, "backups"), "--files-from", fl.name)
    rclone(e, "check", str(src), dest(e, "backups"), "--files-from", fl.name, "--one-way")
    freed = sum(f.stat().st_size for f in old)
    for f in old:
        f.unlink()
    log.info("backups: moved %d files, freed %.1f GB", len(old), freed / 1e9)


def prune_db(e: dict, days: int, vacuum_full: bool) -> None:
    from src.data.storage import get_storage
    st = get_storage()
    cutoff = int(time.time()) - days * 86400
    for table, col in DB_TABLES.items():
        with st._connect() as c:  # noqa: SLF001
            lo = c.execute(f"SELECT MIN({col}) AS a FROM {table}").fetchone()["a"]
        if lo is None or lo >= cutoff:
            continue
        months = pd.date_range(pd.Timestamp(lo, unit="s").to_period("M").to_timestamp(),
                               pd.Timestamp(cutoff, unit="s"), freq="MS")
        tmp = Path(tempfile.mkdtemp(prefix=f"offload_{table}_"))
        total = 0
        for m0 in months:
            a = int(m0.timestamp())
            b = min(int((m0 + pd.offsets.MonthBegin(1)).timestamp()), cutoff)
            if b <= a:
                continue
            with st._connect() as c:  # noqa: SLF001
                cur = c.raw.cursor() if hasattr(c, "raw") else c.cursor()
                cur.execute(f"SELECT * FROM {table} WHERE {col} >= %s AND {col} < %s", (a, b))
                cols = [x[0] for x in cur.description]
                df = pd.DataFrame.from_records(cur.fetchall(), columns=cols)
            if df.empty:
                continue
            df.to_parquet(tmp / f"{table}_{m0:%Y-%m}.parquet", index=False, compression="zstd")
            total += len(df)
        if total == 0:
            shutil.rmtree(tmp)
            continue
        rclone(e, "copy", str(tmp), dest(e, f"db/{table}"))
        rclone(e, "check", str(tmp), dest(e, f"db/{table}"), "--one-way")
        # verify row counts from the uploaded files before deleting anything
        n_files = sum(len(pd.read_parquet(f, columns=[col])) for f in tmp.glob("*.parquet"))
        if n_files != total:
            raise SystemExit(f"{table}: row count mismatch {n_files} != {total}, nothing deleted")
        with st._connect() as c:  # noqa: SLF001
            c.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff,))
        shutil.rmtree(tmp)
        log.info("%s: archived and deleted %d rows older than %d days", table, total, days)
    import psycopg
    dsn = os.getenv("DATABASE_URL") or [line.split("=", 1)[1].strip() for line in (ROOT / ".env").read_text().splitlines()
                                        if line.startswith("DATABASE_URL=")][0]
    with psycopg.connect(dsn, autocommit=True) as conn:
        for table in DB_TABLES:
            conn.execute(f"VACUUM {'FULL ' if vacuum_full else ''}ANALYZE {table}")
            log.info("vacuum%s %s done", " full" if vacuum_full else "", table)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-backups", type=int, default=3)
    ap.add_argument("--prune-db", action="store_true")
    ap.add_argument("--db-days", type=int, default=800)
    ap.add_argument("--vacuum-full", action="store_true")
    a = ap.parse_args()
    e = env()
    before = shutil.disk_usage(str(Path.home())).free
    copy_dirs(e)
    move_backups(e, a.keep_backups)
    if a.prune_db:
        prune_db(e, a.db_days, a.vacuum_full)
    after = shutil.disk_usage(str(Path.home())).free
    log.info("offload done: free space %.1f GB -> %.1f GB", before / 1e9, after / 1e9)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
