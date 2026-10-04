"""Indicator snapshot API: every indicator for every coin (coin_snapshot, refreshed
every 5 minutes by scripts/snapshot_indicators.py) plus what the precursor
study says about each indicator."""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException

from src.data.storage import get_storage
from src.research import indicators as ind

router = APIRouter(prefix="/indicators", tags=["indicators"])
_cache: dict[str, Any] = {"at": 0.0}


def _load() -> dict[str, Any]:
    """All snapshots + per-indicator sorted value arrays (for percentiles), cached 60s."""
    if time.time() - _cache["at"] < 60 and "rows" in _cache:
        return _cache
    storage = get_storage()
    with storage._connect() as c:  # noqa: SLF001
        try:
            rows = [dict(r) for r in c.execute("SELECT symbol, ts, data FROM coin_snapshot").fetchall()]
        except Exception:  # noqa: BLE001
            rows = []
    snaps = {r["symbol"]: {"ts": int(r["ts"]), "v": json.loads(r["data"])} for r in rows}
    dist: dict[str, np.ndarray] = {}
    for i in ind.REGISTRY:
        vals = [s["v"][i.name] for s in snaps.values() if i.name in s["v"]]
        if vals:
            dist[i.name] = np.sort(np.asarray(vals, dtype=float))
    study: dict[str, dict[str, Any]] = {}
    row = storage.get_system_status("indicator_study")
    if row and row.get("value"):
        try:
            st = json.loads(row["value"])
            for side in ("pump", "dump"):
                for r in (st.get(side) or {}).get("holds", []):
                    study.setdefault(r["feature"], {})[side] = {"auc": r.get("test_auc"),
                                                                 "high_before": (r.get("test_auc") or 0.5) > 0.5}
        except ValueError:
            pass
    _cache.update(at=time.time(), rows=snaps, dist=dist, study=study)
    return _cache


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    return {"count": len(ind.REGISTRY), "indicators": ind.catalog()}


@router.get("/coin/{base}")
def coin(base: str) -> dict[str, Any]:
    data = _load()
    sym = base.upper() if "/" in base else f"{base.upper()}/USDT"
    snap = data["rows"].get(sym)
    if not snap:
        raise HTTPException(404, f"no snapshot for {sym}")
    groups: dict[str, list[dict[str, Any]]] = {}
    for i in ind.REGISTRY:
        v = snap["v"].get(i.name)
        d = data["dist"].get(i.name)
        pct = float(np.searchsorted(d, v, side="right") / len(d)) if (v is not None and d is not None and len(d)) else None
        groups.setdefault(i.cat, []).append({"name": i.name, "ko": i.ko, "value": v, "pct": pct,
                                             "study": data["study"].get(i.name)})
    return {"symbol": sym, "ts": snap["ts"], "count": sum(1 for i in ind.REGISTRY if i.name in snap["v"]),
            "total": len(ind.REGISTRY), "groups": groups}


@router.get("/coins")
def coins() -> dict[str, Any]:
    return {"symbols": sorted(_load()["rows"])}


@router.get("/screener")
def screener(sort: str = "vol_ratio_15", n: int = 20, desc: bool = True) -> dict[str, Any]:
    data = _load()
    if sort not in {i.name for i in ind.REGISTRY}:
        raise HTTPException(400, f"unknown indicator {sort}")
    cols = ["ret_60", "ret_1440", "vol_ratio_15", "taker_15", "oi_chg_60", "funding", "rsi_60", "dist_high_1440"]
    rows = [(s, r["v"]) for s, r in data["rows"].items() if sort in r["v"]]
    rows.sort(key=lambda sv: sv[1][sort], reverse=desc)
    return {"sort": sort, "columns": cols,
            "rows": [{"symbol": s, "value": v[sort], **{c: v.get(c) for c in cols}} for s, v in rows[:max(1, min(n, 100))]]}
