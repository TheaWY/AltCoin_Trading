"""Research API — experimentation feed + long-term history views.

Read-only except /api/research/rollback (manual override, POST).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter

from src.data.storage import get_storage
from src.research import decisions
from src.research.promotion import _ensure_schema
from src.research.report import build_report
from src.research.robust_stats import correlation_matrix, effective_breadth, pbo_lite

router = APIRouter(prefix="/research", tags=["research"])


def _rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with get_storage()._connect() as conn:  # noqa: SLF001
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _table_exists(name: str) -> bool:
    try:
        _rows(f"SELECT 1 FROM {name} LIMIT 1")  # noqa: S608 — fixed internal names
        return True
    except Exception:
        return False


def _max_drawdown_from_pnls(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _closed_trade_stats(start_ts: int, end_ts: int | None) -> dict[str, Any]:
    storage = get_storage()
    clauses = ["status = 'closed'", "closed_at >= ?"]
    params: list[Any] = [start_ts]
    if end_ts is not None:
        clauses.append("closed_at < ?")
        params.append(end_ts)
    sql = (
        "SELECT pnl FROM paper_trades "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY closed_at"
    )
    with storage._connect() as conn:  # noqa: SLF001
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    pnls = [float(r.get("pnl") or 0.0) for r in rows]
    trade_count = len(pnls)
    net_pnl = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    max_dd = _max_drawdown_from_pnls(pnls)
    return {
        "trade_count": trade_count,
        "net_pnl": round(net_pnl, 4),
        "expectancy": round(net_pnl / trade_count, 6) if trade_count else 0.0,
        "win_rate_pct": round(wins / trade_count * 100.0, 2) if trade_count else None,
        "max_drawdown": round(max_dd, 4),
    }


def _parse_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _config_diff(current: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
    diff: list[dict[str, Any]] = []
    keys = sorted(set(current.keys()) | set(previous.keys()))
    for key in keys:
        before = previous.get(key)
        after = current.get(key)
        if before != after:
            diff.append({"key": key, "from": before, "to": after})
    return diff


def _week_start(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


@router.get("/summary")
def summary() -> dict[str, Any]:
    from src.research.promotion import OVERRIDES_PATH, _last_promotion, _read_json

    out: dict[str, Any] = {"generated_at": int(time.time())}
    if _table_exists("experiments"):
        counts = _rows("SELECT status, COUNT(*) AS n FROM experiments GROUP BY status")
        out["queue"] = {r["status"]: r["n"] for r in counts}
    else:
        out["queue"] = {}
    try:
        out["active_overrides"] = _read_json(OVERRIDES_PATH)
        out["last_promotion"] = _last_promotion()
    except Exception:
        out["active_overrides"], out["last_promotion"] = {}, None
    return out


@router.get("/report")
def report() -> dict[str, Any]:
    return build_report()


@router.get("/decisions")
def recent_decisions(limit: int = 100, since: int | None = None) -> list[dict[str, Any]]:
    return decisions.recent(limit=min(limit, 500), since=since)


@router.get("/experiments")
def experiments(limit: int = 100) -> list[dict[str, Any]]:
    if not _table_exists("experiments"):
        return []
    rows = _rows(
        "SELECT id, config_hash, config_json, status, priority, "
        "is_champion_baseline, created_at, finished_at, metrics_json "
        "FROM experiments ORDER BY COALESCE(finished_at, created_at) DESC LIMIT ?",
        (min(limit, 500),),
    )
    out = []
    for r in rows:
        item = {
            k: r[k]
            for k in (
                "id",
                "config_hash",
                "status",
                "priority",
                "is_champion_baseline",
                "created_at",
                "finished_at",
            )
        }
        item["config"] = json.loads(r["config_json"] or "{}")
        metrics = json.loads(r["metrics_json"] or "{}")
        agg = metrics.get("aggregate", {})
        item["aggregate"] = {
            k: agg.get(k)
            for k in (
                "trade_count",
                "total_pnl",
                "gross_pnl",
                "total_fees",
                "expectancy",
                "profit_factor",
                "positive_windows",
                "window_count",
            )
        }
        item["windows"] = [
            {k: w.get(k) for k in ("start", "end", "total_pnl", "trade_count")}
            for w in metrics.get("windows", [])
        ]
        out.append(item)
    return out


@router.get("/event-study")
def event_study(limit: int = 200) -> list[dict[str, Any]]:
    if not _table_exists("event_study_results"):
        return []
    latest = _rows("SELECT MAX(run_at) AS m FROM event_study_results")
    run_at = latest[0]["m"] if latest and latest[0]["m"] else 0
    return _rows(
        "SELECT * FROM event_study_results WHERE run_at = ? "
        "ORDER BY signal, horizon_h, regime LIMIT ?",
        (run_at, min(limit, 500)),
    )


@router.get("/promotions")
def promotions(limit: int = 50) -> list[dict[str, Any]]:
    if not _table_exists("promotions"):
        return []
    rows = _rows(
        "SELECT * FROM promotions ORDER BY created_at DESC LIMIT ?",
        (min(limit, 200),),
    )
    for r in rows:
        for key in ("overrides_json", "previous_overrides_json"):
            try:
                r[key.replace("_json", "")] = json.loads(r.pop(key) or "{}")
            except (TypeError, json.JSONDecodeError):
                r[key.replace("_json", "")] = {}
    return rows


@router.get("/correlation")
def correlation() -> dict[str, Any]:
    """Setup-level daily-PnL correlation + effective breadth."""
    rows = _rows(
        "SELECT strategy, closed_at, pnl FROM paper_trades "
        "WHERE status='closed' AND closed_at IS NOT NULL"
    )
    series: dict[str, dict[str, float]] = {}
    for r in rows:
        name = r["strategy"] or "unknown"
        day = time.strftime("%Y-%m-%d", time.gmtime(int(r["closed_at"])))
        series.setdefault(name, {})
        series[name][day] = series[name].get(day, 0.0) + float(r["pnl"] or 0.0)
    matrix = correlation_matrix(series) if len(series) >= 2 else {}
    return {
        "matrix": matrix,
        "breadth": effective_breadth(matrix)
        if matrix
        else {"n": float(len(series)), "avg_corr": 0.0, "effective_breadth": float(len(series))},
        "trade_days": {k: len(v) for k, v in series.items()},
    }


@router.get("/overfit")
def overfit() -> dict[str, Any]:
    """PBO-lite across done experiments + current trial budget usage."""
    result: dict[str, Any] = {}
    if _table_exists("experiments"):
        rows = _rows("SELECT config_hash, metrics_json FROM experiments WHERE status='done'")
        per_config: dict[str, list[float]] = {}
        for r in rows:
            windows = json.loads(r["metrics_json"] or "{}").get("windows", [])
            if windows:
                per_config[r["config_hash"]] = [w.get("total_pnl", 0.0) for w in windows]
        result["pbo"] = pbo_lite(per_config)
        result["trials_done"] = len(rows) if rows else 0
    return result


@router.get("/alpha")
def alpha() -> dict[str, Any]:
    """Alpha lab summary (scripts/alpha_lab.py)."""
    row = get_storage().get_system_status("alpha_lab")
    if not row or not row.get("value"):
        return {}
    try:
        return json.loads(row["value"])
    except ValueError:
        return {}


@router.get("/alpha_shadow")
def alpha_shadow() -> dict[str, Any]:
    """Forward test of the alpha-lab candidates (src/engine/alpha_shadow.py)."""
    storage = get_storage()
    try:
        with storage._connect() as c:  # noqa: SLF001
            rows = [dict(r) for r in c.execute(
                "SELECT strategy, day, symbol, status, side, net, note FROM alpha_shadow "
                "WHERE day >= ? ORDER BY day DESC, strategy, net DESC NULLS LAST",
                (int(time.time()) - 60 * 86400,)).fetchall()]
    except Exception:  # noqa: BLE001
        return {"strategies": {}, "today": []}
    out: dict[str, Any] = {}
    for s in ("breakout8", "leverage_long"):
        days: dict[int, list[float]] = {}
        for r in rows:
            if r["strategy"] == s and r["status"] == "closed" and r["net"] is not None and (s != "breakout8" or r["side"]):
                days.setdefault(int(r["day"]), []).append(float(r["net"]))
        daily = [sum(v) / len(v) for _, v in sorted(days.items())]
        eq = 1.0
        for d in daily:
            eq *= 1 + d
        out[s] = {"days": len(daily), "mean": (sum(daily) / len(daily)) if daily else None,
                  "win": (sum(d > 0 for d in daily) / len(daily)) if daily else None, "total": eq - 1 if daily else None,
                  "trades": sum(len(v) for v in days.values())}
    last = max((int(r["day"]) for r in rows), default=None)
    prev = max((int(r["day"]) for r in rows if r["status"] == "closed"), default=None)
    pick = lambda d: [{k: r[k] for k in ("strategy", "symbol", "status", "side", "net", "note")}  # noqa: E731
                      for r in rows if int(r["day"]) == d]
    return {"strategies": out, "today_day": last, "today": pick(last) if last else [],
            "last_closed_day": prev, "last_closed": pick(prev) if prev else []}


@router.get("/grid")
def grid() -> dict[str, Any]:
    """Strategy grid results (taker and maker cost) + live forward-test of its candidates."""
    storage = get_storage()
    out: dict[str, Any] = {}
    for key, label in (("strategy_grid", "taker"), ("strategy_grid_cost0.001", "maker")):
        row = storage.get_system_status(key)
        if row and row.get("value"):
            try:
                d = json.loads(row["value"])
                out[label] = {"run_at": d.get("run_at"), "tried": d.get("tried"), "families": d.get("families"),
                              "validated": len(d.get("validated") or []),
                              "picks": [{k: p.get(k) for k in ("rule", "family", "train_mean", "train_t", "test_mean",
                                                               "test_t", "train_trades", "test_trades", "win_rate_test")}
                                        for p in d.get("picks") or []]}
            except ValueError:
                pass
    fwd = []
    try:
        with storage._connect() as c:  # noqa: SLF001
            rows = c.execute("SELECT rule, COUNT(*) AS n, SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_n, "
                             "AVG(net_taker) AS taker, AVG(net_maker) AS maker, "
                             "AVG(CASE WHEN status='closed' THEN (CASE WHEN gross > 0 THEN 1.0 ELSE 0.0 END) END) AS win "
                             "FROM grid_signals GROUP BY rule ORDER BY COUNT(*) DESC").fetchall()
        for r in rows:
            r = dict(r)
            fwd.append({"rule": r["rule"], "signals": int(r["n"]), "open": int(r["open_n"] or 0),
                        "closed": int(r["n"]) - int(r["open_n"] or 0),
                        "net_taker": None if r["taker"] is None else float(r["taker"]),
                        "net_maker": None if r["maker"] is None else float(r["maker"]),
                        "win": None if r["win"] is None else float(r["win"])})
    except Exception:  # noqa: BLE001
        pass
    out["forward"] = fwd
    return out


@router.get("/swing")
def swing() -> dict[str, Any]:
    """Swing core (trend-following BTC/ETH legs) + latest swing research."""
    from pathlib import Path

    from src import config
    from src.engine import core_manager as cm

    storage = get_storage()
    ma = cm._trend_ma()  # noqa: SLF001
    legs = []
    for sym in cm._symbols():  # noqa: SLF001
        px = cm._price(storage, sym)  # noqa: SLF001
        trades = cm.core_trades(storage, sym)
        value = sum(float(t["quantity"]) * (px or float(t["entry_price"])) for t in trades)
        cost = sum(float(t["quantity"]) * float(t["entry_price"]) for t in trades)
        st = cm.trend_state(storage, sym, ma) if ma else {"up": True}
        legs.append({"symbol": sym, "price": px, "held": bool(trades), "value": round(value, 2),
                     "pnl": round(value - cost, 2), "up": st.get("up"), "close": st.get("close"),
                     "ma": st.get("ma"), "ma_days": ma,
                     "gap_pct": (st["close"] / st["ma"] - 1) if st.get("close") and st.get("ma") else None})
    out: dict[str, Any] = {"enabled": bool(getattr(config, "CORE_ENABLED", False)), "ma_days": ma, "legs": legs}
    path = Path(__file__).resolve().parents[3] / "data" / "reports" / "swing" / "latest.json"
    if path.exists():
        rep = json.loads(path.read_text())
        keep = ("btc_eth_trend_ma50", "btc_eth_trend_ma100", "btc_trend_ma50", "top3liq_trend_ma50",
                "xs_mom30_top10", "btc_hold")
        out["report_at"] = rep.get("run_at")
        out["test_from"] = rep.get("split_ts")
        out["rules_tested"] = rep.get("rules_tested")
        out["rules_validated"] = rep.get("validated")
        out["books"] = [{"name": k, "train": b["train"], "test": b["test"], "beats_btc_test": b["beats_btc_test"],
                         "beats_btc_train": b["beats_btc_train"]}
                        for k, b in (rep.get("books") or {}).items() if k in keep]
    return out


@router.get("/pumps")
def get_pumps() -> dict[str, Any]:
    """Pump rider: 1m pipeline health, precursor findings, rules, shadow and live results."""
    from pathlib import Path

    storage = get_storage()
    root = Path(__file__).resolve().parents[3]
    rep: dict[str, Any] = {}
    try:
        rep = json.loads((root / "data" / "reports" / "pumps" / "latest.json").read_text())
    except (OSError, ValueError):
        pass
    now = int(time.time())
    pipe = {"symbols_last_min": 0, "last_bar_age_s": None}
    shadow = {"open": 0, "closed": 0, "win_rate": None, "avg_net": None, "recent": []}
    try:
        with storage._connect() as c:  # noqa: SLF001
            r = dict(c.execute("SELECT MAX(ts) AS mx FROM prices_1m WHERE ts > ?", (now - 900,)).fetchone())
            if r.get("mx"):
                pipe["last_bar_age_s"] = now - int(r["mx"])
                pipe["symbols_last_min"] = int(dict(c.execute(
                    "SELECT COUNT(*) AS n FROM prices_1m WHERE ts = ?", (int(r["mx"]),)).fetchone())["n"])
            if _table_exists("pump_signals"):
                s = dict(c.execute("SELECT SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS o, "
                                   "SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS cl, "
                                   "AVG(CASE WHEN status='closed' THEN net END) AS avg_net, "
                                   "AVG(CASE WHEN status='closed' THEN (CASE WHEN net>0 THEN 1.0 ELSE 0.0 END) END) AS win "
                                   "FROM pump_signals").fetchone())
                shadow.update(open=int(s.get("o") or 0), closed=int(s.get("cl") or 0),
                              avg_net=None if s.get("avg_net") is None else float(s["avg_net"]),
                              win_rate=None if s.get("win") is None else float(s["win"]))
                shadow["recent"] = [dict(x) for x in c.execute(
                    "SELECT symbol, rule, opened_at, entry, peak, status, net, reason, target_pct "
                    "FROM pump_signals ORDER BY opened_at DESC LIMIT 12").fetchall()]
    except Exception:  # noqa: BLE001
        pass
    live = [dict(t) for t in storage.get_open_trades() if t.get("strategy") == "pump_rider"]
    closed = []
    try:
        with storage._connect() as c:  # noqa: SLF001
            closed = [dict(x) for x in c.execute(
                "SELECT symbol, entry_price, exit_price, pnl, exit_reason, opened_at, closed_at FROM paper_trades "
                "WHERE strategy='pump_rider' AND status='closed' ORDER BY closed_at DESC LIMIT 10").fetchall()]
    except Exception:  # noqa: BLE001
        pass
    study = rep.get("study") or {}
    pre = (rep.get("precursors") or {}).get("precursors") or []
    return {
        "pipeline": pipe,
        "report_at": rep.get("run_at"),
        "coins": study.get("symbols"), "minutes": study.get("minutes"),
        "rules_tested": study.get("rules_tested"),
        "validated": [{k: r.get(k) for k in ("rule", "events", "mfe_median", "minutes_to_peak_median",
                                             "test_mean_net", "test_t", "test_win_rate", "target_pct", "trail")}
                      for r in (study.get("validated") or [])[:8]],
        "top_rules": [{k: r.get(k) for k in ("rule", "events", "mfe_median", "test_mean_net", "test_t",
                                             "test_win_rate", "validated")} for r in (rep.get("top_rules") or [])[:8]],
        "onsets": (rep.get("precursors") or {}).get("onsets_total"),
        "precursors": [{k: r.get(k) for k in ("text", "train_auc", "test_auc", "test_lift_top10", "holds")}
                       for r in pre[:8]],
        "shadow": shadow,
        "live_open": [{k: t.get(k) for k in ("symbol", "entry_price", "take_profit", "trail_price", "opened_at", "quantity")}
                      for t in live],
        "live_closed": closed,
    }


@router.get("/hypotheses")
def get_hypotheses() -> dict[str, Any]:
    """Rotating hypothesis engine + signal lab forward test, for the dashboard."""
    from pathlib import Path

    from src.engine import sentiment_gate, signal_lab

    storage = get_storage()
    root = Path(__file__).resolve().parents[3]
    latest: dict[str, Any] = {}
    try:
        latest = json.loads((root / "data" / "reports" / "hypotheses" / "latest.json").read_text())
    except (OSError, ValueError):
        pass
    gstate = sentiment_gate.load_state(storage)
    lab_state = signal_lab.load_state(storage, int(time.time()))

    def price(sym: str) -> float | None:
        row = storage.get_latest_price(sym)
        return float(row["close"]) if row else None

    lab_eq = signal_lab.equity(lab_state, price) if lab_state.get("positions") or lab_state.get("rebalances") else None
    supported = sorted((latest.get("supported") or {}).items(), key=lambda kv: kv[1].get("q") or 1)
    return {
        "run_at": latest.get("run_at"),
        "explored": latest.get("registry_size"),
        "space": latest.get("space_size"),
        "tested_last_run": len(latest.get("tested") or []),
        "supported": [{"hid": h, "h0": r.get("h0"), "ic": r.get("ic"), "t": r.get("t"), "q": r.get("q"),
                       "bps_per_sd": r.get("bps_per_sd")} for h, r in supported[:12]],
        "weights": gstate.get("weights") or {},
        "gate_mode": sentiment_gate.effective_mode(gstate),
        "oos": gstate.get("oos") or {},
        "signal_lab": {
            "equity": lab_eq, "starting": lab_state.get("starting"),
            "rebalances": lab_state.get("rebalances", 0), "legs": len(lab_state.get("positions") or []),
            "fees_paid": lab_state.get("fees_paid", 0.0), "started_at": lab_state.get("started_at"),
        },
    }


@router.get("/history")
def get_history() -> dict[str, Any]:
    """Promotion timeline + weekly long-term research report cards."""
    _ensure_schema()
    storage = get_storage()
    now_ts = int(time.time())
    promotions_rows = _rows("SELECT * FROM promotions ORDER BY created_at DESC") if _table_exists("promotions") else []

    timeline: list[dict[str, Any]] = []
    chronological = sorted(promotions_rows, key=lambda p: int(p["created_at"]))
    for idx, row in enumerate(chronological):
        start_ts = int(row["created_at"])
        end_ts = int(chronological[idx + 1]["created_at"]) if idx + 1 < len(chronological) else now_ts
        current_overrides = _parse_json(row.get("overrides_json"))
        previous_overrides = _parse_json(row.get("previous_overrides_json"))
        timeline.append(
            {
                "id": row["id"],
                "created_at": start_ts,
                "action": row.get("action"),
                "label": "승격" if row.get("action") == "promote" else "롤백",
                "config_hash": row.get("config_hash"),
                "reason": row.get("reason") or "",
                "reign_start": start_ts,
                "reign_end": end_ts,
                "config_diff": _config_diff(current_overrides, previous_overrides),
                "reign_performance": _closed_trade_stats(start_ts, end_ts),
            }
        )
    timeline.sort(key=lambda item: int(item["created_at"]), reverse=True)

    now_dt = datetime.now(timezone.utc)
    week0 = _week_start(now_dt)
    week_starts = [week0 - timedelta(days=7 * i) for i in range(12)]
    weekly: list[dict[str, Any]] = []
    decisions_enabled = _table_exists("research_decisions")

    for ws in week_starts:
        we = ws + timedelta(days=7)
        ws_ts = int(ws.timestamp())
        we_ts = int(we.timestamp())
        done_failed = (
            _rows(
                "SELECT status, COUNT(*) AS n FROM experiments "
                "WHERE finished_at >= ? AND finished_at < ? "
                "AND status IN ('done', 'failed') GROUP BY status",
                (ws_ts, we_ts),
            )
            if _table_exists("experiments")
            else []
        )
        best_rows = (
            _rows(
                "SELECT config_hash, metrics_json FROM experiments "
                "WHERE status = 'done' AND finished_at >= ? AND finished_at < ?",
                (ws_ts, we_ts),
            )
            if _table_exists("experiments")
            else []
        )
        decision_counts: dict[str, int] = {}
        if decisions_enabled:
            for r in _rows(
                "SELECT action, COUNT(*) AS n FROM research_decisions "
                "WHERE ts >= ? AND ts < ? GROUP BY action",
                (ws_ts, we_ts),
            ):
                decision_counts[str(r["action"])] = int(r["n"])

        by_status = {str(r["status"]): int(r["n"]) for r in done_failed}
        best_candidate: dict[str, Any] | None = None
        for row in best_rows:
            metrics = _parse_json(row["metrics_json"])
            agg = metrics.get("aggregate", {}) if isinstance(metrics, dict) else {}
            exp = agg.get("expectancy")
            if exp is None:
                continue
            if best_candidate is None or float(exp) > float(best_candidate["expectancy"]):
                best_candidate = {"config_hash": row["config_hash"], "expectancy": float(exp)}

        weekly.append(
            {
                "week_start": ws_ts,
                "week_end": we_ts,
                "experiments": {"done": by_status.get("done", 0), "failed": by_status.get("failed", 0)},
                "decisions_by_action": decision_counts,
                "best_candidate": best_candidate,
                "champion_reign": _closed_trade_stats(ws_ts, we_ts),
            }
        )

    return {"timeline": timeline, "weekly": weekly, "generated_at": now_ts}


@router.post("/rollback")
def manual_rollback() -> dict[str, Any]:
    from src.research.promotion import rollback

    return rollback("manual rollback from dashboard")
