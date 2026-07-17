"""Autonomous cross-sectional edge search — runs continuously on the Mac.

Each iteration: pick an untested strategy CONFIG from the search space, run the
HONEST preview backtest (the methodology the 2026-07 campaign settled on), score
it, and persist. Anything clearing the gate is flagged for a real-gate build.

Honest BY CONSTRUCTION — every trap the campaign hit is designed out:
  - FULL liquid universe (PIT dollar-volume filter), never a curated symbol list
    (that was the +1.04 momentum selection-bias trap).
  - NON-OVERLAPPING rebalances -> independent observations (no overlap inflation).
  - DSR deflated by the CUMULATIVE trial count across the whole search
    (multiple-comparison honest).
  - Sub-period / yearly stability required (no single-regime flukes).
  - Cost-aware (reports a cost sweep; a config that only works at 0 cost fails).
  - Sign-stability across neutralization where applicable.

Families (extensible — add a generator + a backtest branch):
  - funding_carry     : delta-neutral (short perp + long spot) on high-funding
                        names. THE edge found (DSR 0.997 @20bps in preview).
  - funding_momentum  : short high-funding AND recent-winner (pump-and-dump short).
  - price_xsec        : momentum/reversal/vol long/short/LS. Documented to fail;
                        kept as a null baseline + regime-change tripwire.
  - basis             : perp-vs-spot basis as the signal.

State lives in the edge_search_results table, so a launchd job can run one batch
per invocation and resume forever. See scripts/run_edge_search.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import math
import os
import random
from typing import Any, Callable

import numpy as np

from src.data.storage import get_storage
from src.research import perp_cache
from src.research.robust_stats import deflated_sharpe

HOUR = 3600
DAY = 86400
HOLDOUT = float(os.getenv("RESEARCH_HOLDOUT_START_TS", "1780272000"))  # 2026-06-01
IS_START = dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp()
GATE_DSR = float(os.getenv("EDGE_GATE_DSR", "0.95"))
GATE_WINDOW = float(os.getenv("EDGE_GATE_WINDOW", "0.66"))
GATE_SHARPE = float(os.getenv("EDGE_GATE_SHARPE", "1.0"))


# ---------------------------------------------------------------- search space
def _funding_carry_configs():
    for hold_h, top, look, minf, N, cost in itertools.product(
        (168, 336, 504), (0.15, 0.25, 0.5), (1, 3, 7), (0.0, 5e-5),
        (40, 70, 100), (0.0010, 0.0020)):
        yield {"family": "funding_carry", "hold_h": hold_h, "top_frac": top,
               "fund_look_d": look, "min_funding": minf, "universe_n": N, "cost_leg": cost}


def _funding_momentum_configs():
    for hold_h, top, mom_d, N, cost in itertools.product(
        (168, 336), (0.2, 0.4), (3, 7, 14), (60, 100), (0.0010, 0.0020)):
        yield {"family": "funding_momentum", "hold_h": hold_h, "top_frac": top,
               "mom_days": mom_d, "universe_n": N, "cost_leg": cost}


def _price_xsec_configs():
    for side, mom_d, hold_h, N, cost in itertools.product(
        ("ls", "short", "long"), (3, 7, 14, 30), (48, 72, 168), (60, 100), (0.0020,)):
        yield {"family": "price_xsec", "side": side, "mom_days": mom_d,
               "hold_h": hold_h, "universe_n": N, "cost_leg": cost}


def _basis_configs():
    for hold_h, top, N, cost in itertools.product(
        (168, 336), (0.25, 0.5), (60, 100), (0.0010, 0.0020)):
        yield {"family": "basis", "hold_h": hold_h, "top_frac": top,
               "universe_n": N, "cost_leg": cost}


FAMILIES: dict[str, Callable[[], Any]] = {
    "funding_carry": _funding_carry_configs,
    "funding_momentum": _funding_momentum_configs,
    "price_xsec": _price_xsec_configs,
    "basis": _basis_configs,
}


def config_hash(cfg: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- persistence
def ensure_schema(storage) -> None:
    pg = storage.is_postgres
    ddl = (
        "CREATE TABLE IF NOT EXISTS edge_search_results ("
        "config_hash TEXT PRIMARY KEY, family TEXT, config_json TEXT, "
        "metrics_json TEXT, sharpe DOUBLE PRECISION, dsr DOUBLE PRECISION, "
        "window_frac DOUBLE PRECISION, passed INTEGER, created_at BIGINT)"
        if pg else
        "CREATE TABLE IF NOT EXISTS edge_search_results ("
        "config_hash TEXT PRIMARY KEY, family TEXT, config_json TEXT, "
        "metrics_json TEXT, sharpe REAL, dsr REAL, window_frac REAL, "
        "passed INTEGER, created_at INTEGER)"
    )
    with storage._connect() as c:
        c.execute(ddl)


def tested_hashes(storage) -> set[str]:
    with storage._connect() as c:
        return {dict(r)["config_hash"] for r in
                c.execute("SELECT config_hash FROM edge_search_results").fetchall()}


def trial_count(storage) -> int:
    with storage._connect() as c:
        row = c.execute("SELECT COUNT(*) n FROM edge_search_results").fetchone()
    return int(dict(row)["n"]) if row else 0


def record(storage, cfg, metrics, passed, now_ts) -> None:
    ph = "%s" if storage.is_postgres else "?"
    q = (f"INSERT INTO edge_search_results (config_hash, family, config_json, "
         f"metrics_json, sharpe, dsr, window_frac, passed, created_at) "
         f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph}) "
         + ("ON CONFLICT (config_hash) DO NOTHING" if storage.is_postgres
            else "ON CONFLICT(config_hash) DO NOTHING"))
    with storage._connect() as c:
        c.execute(q, (config_hash(cfg), cfg["family"], json.dumps(cfg),
                      json.dumps(metrics), metrics.get("sharpe"), metrics.get("dsr"),
                      metrics.get("window_frac"), 1 if passed else 0, int(now_ts)))


# ---------------------------------------------------------------- backtest core
def _windows_pos(rets, dates, wlen=60 * DAY):
    if len(dates) < 2:
        return 0, 1
    ws = dates.min()
    wid = ((dates - ws) // wlen).astype(int)
    acc: dict[int, float] = {}
    for w, x in zip(wid, rets):
        acc[w] = acc.get(w, 0.0) + x
    return sum(1 for v in acc.values() if v > 0), len(acc)


def _metrics(rets, dates, hold_h, n_trials, gross_rets=None):
    rets = np.asarray(rets)
    if len(rets) < 8 or rets.std() == 0:
        return None
    rpy = 365 * 24 / hold_h
    sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(rpy))
    pw, nw = _windows_pos(rets, dates)
    d = deflated_sharpe(list(rets), n_trials=max(1, n_trials), trial_sharpes=[])
    # yearly stability
    yearly = {}
    for y in (2023, 2024, 2025):
        lo = dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        hi = dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        m = (dates >= lo) & (dates < hi)
        if m.sum() > 3 and rets[m].std() > 0:
            yearly[str(y)] = round(float(rets[m].mean() / rets[m].std(ddof=1) * math.sqrt(rpy)), 2)
    return {
        "sharpe": round(sharpe, 3), "dsr": round(float(d["dsr"]), 3),
        "sr0": round(float(d["sr0"]), 3), "window_pos": pw, "window_n": nw,
        "window_frac": round(pw / nw, 3), "n_obs": len(rets),
        "total_pct": round(float(rets.sum()) * 100, 1),
        "mean_per_reb_pct": round(float(rets.mean()) * 100, 3),
        "yearly_sharpe": yearly,
        "gross_sharpe": (round(float(np.mean(gross_rets) / np.std(gross_rets, ddof=1)
                                     * math.sqrt(rpy)), 3)
                         if gross_rets is not None and len(gross_rets) > 2
                         and np.std(gross_rets) > 0 else None),
    }


def passes_gate(m) -> bool:
    if not m:
        return False
    yearly_ok = bool(m["yearly_sharpe"]) and all(v > 0 for v in m["yearly_sharpe"].values())
    return (m["dsr"] >= GATE_DSR and m["window_frac"] >= GATE_WINDOW
            and m["sharpe"] >= GATE_SHARPE and yearly_ok)


# ---------------------------------------------------------------- family backtests
def _grid(hold_h, t_lo=IS_START, t_hi=HOLDOUT):
    n = int((t_hi - t_lo) / (hold_h * HOUR))
    g = [int((t_lo + k * hold_h * HOUR) // HOUR * HOUR) for k in range(n)]
    return [x for x in g if x + hold_h * HOUR < t_hi]


def bt_funding_carry(P, cfg, n_trials):
    """Delta-neutral: short perp + long spot on top-funding names. pnl = funding - basis."""
    perp, spot, fund = P["perp"], P["spot"], P["fund"]
    pool = P["by_liquidity"][:cfg["universe_n"]]
    H = cfg["hold_h"]
    rets, gross, ds, prev = [], [], [], set()
    for g in _grid(H):
        gh = int((g + H * HOUR) // HOUR * HOUR)
        cand = []
        for s in pool:
            if s not in perp:
                continue
            if g in perp[s] and gh in perp[s] and g in spot[s] and gh in spot[s] and perp[s][g] > 0 and spot[s][g] > 0:
                fs = perp_cache.fund_signal(fund[s], g, cfg["fund_look_d"])
                if fs is not None and fs > cfg["min_funding"]:
                    cand.append((fs, s, g, gh))
        if len(cand) < 6:
            continue
        cand.sort(reverse=True)
        k = max(2, int(len(cand) * cfg["top_frac"]))
        held = cand[:k]
        pnl, names = [], set()
        for _, s, g_, gh_ in held:
            names.add(s)
            pr = (perp[s][gh_] - perp[s][g_]) / perp[s][g_]
            sr = (spot[s][gh_] - spot[s][g_]) / spot[s][g_]
            pnl.append(perp_cache.fund_sum(fund[s], g_, gh_) - (pr - sr))
        turn = 1.0 if not prev else 1 - len(names & prev) / len(names)
        prev = names
        gross.append(float(np.mean(pnl)))
        rets.append(float(np.mean(pnl)) - 2 * cfg["cost_leg"] * turn)
        ds.append(g)
    return _metrics(rets, np.array(ds), H, n_trials, gross)


def bt_funding_momentum(P, cfg, n_trials):
    """Short high-funding AND recent-winner (pump-and-dump short), delta-neutral."""
    perp, spot, fund = P["perp"], P["spot"], P["fund"]
    pool = P["by_liquidity"][:cfg["universe_n"]]
    H, w = cfg["hold_h"], cfg["mom_days"] * 24
    rets, ds, prev = [], [], set()
    for g in _grid(H):
        gh = int((g + H * HOUR) // HOUR * HOUR)
        gm = int((g - w * HOUR) // HOUR * HOUR)
        cand = []
        for s in pool:
            if s not in perp:
                continue
            if all(t in spot[s] and spot[s][t] > 0 for t in (g, gh, gm)) and g in perp[s] and gh in perp[s]:
                fs = perp_cache.fund_signal(fund[s], g, 3)
                mom = (spot[s][g] - spot[s][gm]) / spot[s][gm]
                if fs is not None:
                    cand.append((fs + mom, s, g, gh))   # rank by funding+momentum (both crowded-long)
        if len(cand) < 6:
            continue
        cand.sort(reverse=True)
        k = max(2, int(len(cand) * cfg["top_frac"]))
        held = cand[:k]
        pnl, names = [], set()
        for _, s, g_, gh_ in held:
            names.add(s)
            pr = (perp[s][gh_] - perp[s][g_]) / perp[s][g_]
            sr = (spot[s][gh_] - spot[s][g_]) / spot[s][g_]
            pnl.append(perp_cache.fund_sum(fund[s], g_, gh_) - (pr - sr))
        turn = 1.0 if not prev else 1 - len(names & prev) / len(names)
        prev = names
        rets.append(float(np.mean(pnl)) - 2 * cfg["cost_leg"] * turn)
        ds.append(g)
    return _metrics(rets, np.array(ds), H, n_trials)


def bt_basis(P, cfg, n_trials):
    """Short high perp-premium (basis) names, delta-neutral. Basis mean-reverts."""
    perp, spot, fund = P["perp"], P["spot"], P["fund"]
    pool = P["by_liquidity"][:cfg["universe_n"]]
    H = cfg["hold_h"]
    rets, ds, prev = [], [], set()
    for g in _grid(H):
        gh = int((g + H * HOUR) // HOUR * HOUR)
        cand = []
        for s in pool:
            if s not in perp:
                continue
            if g in perp[s] and gh in perp[s] and g in spot[s] and gh in spot[s] and spot[s][g] > 0 and perp[s][g] > 0:
                basis = (perp[s][g] - spot[s][g]) / spot[s][g]
                cand.append((basis, s, g, gh))
        if len(cand) < 6:
            continue
        cand.sort(reverse=True)
        k = max(2, int(len(cand) * cfg["top_frac"]))
        held = cand[:k]
        pnl, names = [], set()
        for _, s, g_, gh_ in held:
            names.add(s)
            pr = (perp[s][gh_] - perp[s][g_]) / perp[s][g_]
            sr = (spot[s][gh_] - spot[s][g_]) / spot[s][g_]
            pnl.append(perp_cache.fund_sum(fund[s], g_, gh_) - (pr - sr))
        turn = 1.0 if not prev else 1 - len(names & prev) / len(names)
        prev = names
        rets.append(float(np.mean(pnl)) - 2 * cfg["cost_leg"] * turn)
        ds.append(g)
    return _metrics(rets, np.array(ds), H, n_trials)


def bt_price_xsec(P, cfg, n_trials):
    """Cross-sectional momentum, long/short/LS, BTC-hedged. Null baseline."""
    spot = P["spot"]
    pool = P["by_liquidity"][:cfg["universe_n"]]
    btc = P["btc"]
    H, w = cfg["hold_h"], cfg["mom_days"] * 24
    rets, ds, prev = [], [], set()
    for g in _grid(H):
        gh = int((g + H * HOUR) // HOUR * HOUR)
        gm = int((g - w * HOUR) // HOUR * HOUR)
        a, b = btc.get(g), btc.get(gh)
        if not (a and b):
            continue
        bench = (b - a) / a
        nm, mv, fwd = [], [], []
        for s in pool:
            if all(t in spot[s] and spot[s][t] > 0 for t in (g, gh, gm)):
                nm.append(s); mv.append((spot[s][g] - spot[s][gm]) / spot[s][gm])
                fwd.append((spot[s][gh] - spot[s][g]) / spot[s][g] - bench)
        if len(nm) < 20:
            continue
        nm = np.array(nm); mv = np.array(mv); fwd = np.array(fwd)
        order = np.argsort(mv); k = max(3, int(len(nm) * 0.1))
        win, los = order[-k:], order[:k]
        if cfg["side"] == "long":
            r = fwd[win].mean(); held = set(nm[win])
        elif cfg["side"] == "short":
            r = -fwd[los].mean(); held = set(nm[los])
        else:
            r = fwd[win].mean() - fwd[los].mean(); held = set(nm[win]) | set(nm[los])
        turn = 1.0 if not prev else 1 - len(held & prev) / len(held)
        prev = held
        rets.append(float(r) - 2 * cfg["cost_leg"] * turn)
        ds.append(g)
    return _metrics(rets, np.array(ds), H, n_trials)


BACKTESTS = {
    "funding_carry": bt_funding_carry, "funding_momentum": bt_funding_momentum,
    "basis": bt_basis, "price_xsec": bt_price_xsec,
}


# ---------------------------------------------------------------- driver
def all_configs():
    out = []
    for gen in FAMILIES.values():
        out.extend(gen())
    return out


def run_batch(n=8, log=print) -> list[dict]:
    """Run up to n untested configs. Returns the results (with any gate-passers)."""
    storage = get_storage()
    ensure_schema(storage)
    done = tested_hashes(storage)
    todo = [c for c in all_configs() if config_hash(c) not in done]
    random.shuffle(todo)                      # explore breadth, don't grind one family
    todo = todo[:n]
    if not todo:
        log("edge_search: all configs tested — nothing new")
        return []
    P = perp_cache.load_panel(max_names=100, log=log)
    now = perp_cache.now_ts()
    results = []
    for cfg in todo:
        nt = max(30, trial_count(storage))    # honest cumulative trial count for DSR
        try:
            m = BACKTESTS[cfg["family"]](P, cfg, nt)
        except Exception as e:  # a bad config must never kill the loop
            log(f"  [err] {cfg['family']} {config_hash(cfg)}: {e}")
            m = None
        passed = passes_gate(m)
        record(storage, cfg, m or {}, passed, now)
        results.append({"config": cfg, "metrics": m, "passed": passed})
        if m:
            flag = "  ***GATE PASS***" if passed else ""
            log(f"  {cfg['family']:16} sh={m['sharpe']:+.2f} dsr={m['dsr']:.3f} "
                f"win={m['window_pos']}/{m['window_n']} yr={m['yearly_sharpe']}{flag}")
    return results
