#!/usr/bin/env python3
"""One-screen health check of every data source: is it arriving, how fresh,
how much. Prints a table and writes system_status data_audit (dashboard)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

# (label, table, time column, expected max age in seconds, extra where)
TABLES = [
    ("바이낸스 1분봉", "prices_1m", "ts", 300, ""),
    ("바이낸스 1시간봉 (선물)", "prices", "timestamp", 3 * 3600, "timeframe='1h_perp'"),
    ("바이낸스 1시간봉 (현물)", "prices", "timestamp", 3 * 3600, "timeframe='1h'"),
    ("바이낸스 OI·펀딩 1분", "perp_1m", "ts", 600, ""),
    ("바이낸스 OI·롱숏 5분", "perp_5m", "ts", 1800, ""),
    ("바이낸스 롱숏·OI 아카이브", "metrics_5m", "ts", 3 * 86400, ""),
    ("바이낸스 펀딩", "funding_rates", "timestamp", 3600, ""),
    ("바이낸스 청산 (시간 합계)", "liquidation_agg_1h", "timestamp", 3 * 3600, ""),
    ("바이낸스 호가 (전체, 5분)", "book_5m", "ts", 1800, ""),
    ("바이낸스 호가 (핫 30, 1분)", "book_1m", "ts", 300, ""),
    ("업비트·빗썸 1분봉", "kr_1m", "ts", 300, ""),
    ("업비트 1시간봉", "upbit_1h", "ts", 3 * 3600, ""),
    ("거래소 공지", "exchange_notices", "ts", 3 * 86400, ""),
    ("코인게코 시총 (일)", "cg_daily", "ts", 3 * 86400, ""),
    ("코인게코 트렌딩", "cg_trending", "ts", 3 * 3600, ""),
    ("코인베이스 BTC/ETH", "coinbase_1h", "ts", 3 * 3600, ""),
    ("빗썸 1시간봉", "bithumb_1h", "ts", 3 * 3600, ""),
    ("공포·탐욕 지수", "fng_daily", "ts", 3 * 86400, ""),
    ("Coinalyze 거래소 합산 (1시간)", "coinalyze_1h", "ts", 3 * 3600, ""),
    ("하이퍼리퀴드 OI·펀딩 (5분)", "hl_ctx", "ts", 900, ""),
    ("하이퍼리퀴드 청산 맵 (10분)", "hl_liqmap", "ts", 1800, ""),
    ("하이퍼리퀴드 지갑", "hl_users", "last_seen", 600, ""),
    ("급등 감시 사건", "pump_watch_events", "ts", 6 * 3600, ""),
    ("TOP 10 기록", "move_top10_log", "ts", 2 * 3600, "replay = 0"),
    ("지표 스냅샷", "coin_snapshot", "ts", 900, ""),
]
FILES = [
    ("1분봉 6개월 (vision)", "data/cache/k1m"),
    ("호가 깊이 6개월 (vision)", "data/cache/bookdepth"),
    ("펀딩 히스토리 (vision)", "data/cache/funding"),
    ("업비트·빗썸 1분 60일", "data/cache/kr1m_hist"),
    ("업비트·빗썸 1분 보관", "data/cache/kr1m"),
    ("Tardis 체험 (청산)", "data/tardis/liq"),
    ("Tardis 체험 (OI·펀딩)", "data/tardis/deriv"),
    ("Tardis 체험 (체결)", "data/tardis/trades"),
    ("Tardis 체험 (호가)", "data/tardis/book"),
]
JOBS = ["worker", "dashboard", "stream1m", "perpmetrics", "korea", "korea1m", "extra", "pumpwatch", "indicators",
        "alphashadow", "coinalyze", "hyperliquid", "offload", "vision", "swing", "grid", "indstudy", "watchdog"]


def main() -> int:
    st = get_storage()
    now = int(time.time())
    rows = []
    for label, table, col, max_age, where in TABLES:
        try:
            with st._connect() as c:  # noqa: SLF001
                r = c.execute(f"SELECT MAX({col}) AS b, COUNT(*) AS n FROM {table}" + (f" WHERE {where}" if where else "")).fetchone()
            b, n = r["b"], r["n"]
            age = now - int(b) if b else None
            ok = age is not None and age <= max_age
            rows.append({"label": label, "rows": int(n), "age_min": None if age is None else round(age / 60),
                         "ok": ok})
        except Exception as e:  # noqa: BLE001
            rows.append({"label": label, "rows": 0, "age_min": None, "ok": False, "error": str(e)[:80]})
    files = []
    for label, d in FILES:
        p = ROOT / d
        fs = list(p.rglob("*.parquet")) if p.exists() else []
        size = sum(f.stat().st_size for f in fs)
        newest = max((f.stat().st_mtime for f in fs), default=0)
        files.append({"label": label, "files": len(fs), "mb": round(size / 1e6), "age_h": round((now - newest) / 3600, 1) if newest else None})
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    jobs = {}
    for j in JOBS:
        line = next((l for l in out.splitlines() if l.endswith(f"com.altcoin.{j}")), None)
        if line is None:
            jobs[j] = "not loaded"
        else:
            pid, status, _ = line.split("\t")
            jobs[j] = "running" if pid != "-" else ("ok (scheduled)" if status == "0" else f"last exit {status}")
    for r in rows:
        print(f"{'OK ' if r['ok'] else 'OLD'}  {r['label']:<28} {r['rows']:>12,} rows   last {r['age_min']} min ago")
    for f in files:
        print(f"     {f['label']:<28} {f['files']:>6} files {f['mb']:>7} MB   newest {f['age_h']} h ago")
    for j, s in jobs.items():
        print(f"     job {j:<14} {s}")
    st.set_system_status("data_audit", json.dumps({"at": now, "tables": rows, "files": files, "jobs": jobs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
