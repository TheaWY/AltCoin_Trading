"""Live pairs stat-arb runner — cross-sectional, market-neutral execution.

Calls the SHARED signal core src/engine/pairs.py (the SAME functions the gated
backtest scripts/backtest_pairs.py uses) to select pairs and time entries/exits,
and realizes each pair as a paper trade: LONG leg A + SHORT beta*leg B, stored in
the primary+hedge columns of paper_trades. Backtest and live therefore cannot
diverge on the signal (project rule: one code path).

Cadence:
  - SELECTION refreshes every PAIRS_TRADE_HOURS (cached in system_status); it is
    O(N^2) so it runs rarely, off the hot path.
  - Each cycle: recompute the z-score of every SELECTED pair from trailing prices
    and OPEN (|z|>=Z_IN, not already open, capacity permitting) or CLOSE
    (|z|<=Z_OUT, or the pair fell out of selection) — cheap, only selected pairs.

Breadth note: the full-breadth backtest (~1400 pairs) clears DSR 0.99, but live
holds a per-symbol-capped, non-redundant subset (PAIRS_MAX_CONCURRENT /
PAIRS_MAX_PER_SYMBOL). The honest expected performance is the DSR AT THAT
BREADTH, measured by scratchpad/pairs_live_breadth.py — deploy only if the live
breadth clears the gate.

Paper only; does nothing unless SETUP_PAIRS_STATARB_ENABLED.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np

from src import config
from src.data.storage import get_storage
from src.engine import pairs

logger = logging.getLogger(__name__)

STRATEGY = "pairs_statarb"
_SELECTION_KEY = "pairs_selection"
HOUR = 3600

MAX_CONCURRENT = int(getattr(config, "PAIRS_MAX_CONCURRENT", 60))
MAX_PER_SYMBOL = int(getattr(config, "PAIRS_MAX_PER_SYMBOL", 2))
PAIR_NOTIONAL_PCT = float(getattr(config, "PAIRS_PAIR_NOTIONAL_PCT", 0.01))
MAX_GROSS_PCT = float(getattr(config, "PAIRS_MAX_GROSS_PCT", 0.80))
MARGIN_FRAC = float(getattr(config, "PAIRS_MARGIN_FRAC", 1.0))
STOP_PCT = float(getattr(config, "PAIRS_STOP_PCT", 0.15))
Z_STOP_DELTA = float(getattr(config, "PAIRS_Z_STOP_DELTA", 1.0))
NEG_EXPECTANCY_DEPLOY_PCT = float(getattr(config, "PAIRS_NEG_EXPECTANCY_DEPLOY_PCT", 0.40))
MIN_EXPECTANCY_TRADES = int(getattr(config, "PAIRS_MIN_EXPECTANCY_TRADES", 10))
MAX_HOLD_HOURS = float(getattr(config, "PAIRS_MAX_HOLD_HOURS", pairs.TRADE_HOURS))


def _pair_gross(t: dict[str, Any]) -> float:
    """Gross notional (both legs) of one pair at entry."""
    g = float(t["quantity"]) * float(t["entry_price"])
    if t.get("hedge_symbol"):
        g += float(t.get("hedge_quantity") or 0) * float(t.get("hedge_entry_price") or 0)
    return g


def _deployed_notional(storage) -> float:
    """Total GROSS notional across open pairs (primary + hedge legs)."""
    return sum(_pair_gross(t) for t in _open_pairs(storage))


def _pair_unrealized(storage, t: dict[str, Any]) -> float:
    """Mark-to-market P&L of both legs at current prices (no fees/funding)."""
    pa = storage.get_latest_price(t["symbol"])
    pb = storage.get_latest_price(t["hedge_symbol"]) if t.get("hedge_symbol") else None
    price_a = float(pa["close"]) if pa else float(t["entry_price"])
    qa, ea = float(t["quantity"]), float(t["entry_price"])
    pnl = (price_a - ea) * qa if t["direction"] == "LONG" else (ea - price_a) * qa
    if t.get("hedge_symbol"):
        price_b = float(pb["close"]) if pb else float(t["hedge_entry_price"])
        qb, eb = float(t.get("hedge_quantity") or 0), float(t.get("hedge_entry_price") or 0)
        pnl += (price_b - eb) * qb if t["hedge_direction"] == "LONG" else (eb - price_b) * qb
    return pnl


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def _panel(storage, symbols: list[str], lookback_hours: int, now_ts: int) -> tuple[np.ndarray, dict, dict]:
    """Grid-aligned trailing log-close + dollar-volume for `symbols`."""
    lo = (now_ts - lookback_hours * HOUR) // HOUR * HOUR
    hi = now_ts // HOUR * HOUR + HOUR
    grid = np.arange(lo, hi, HOUR)
    tidx = {int(t): i for i, t in enumerate(grid)}
    logp, dvol = {}, {}
    for s in symbols:
        rows = storage.get_prices(s, limit=lookback_hours + 64, timeframe="1h")
        if not rows:
            continue
        lc = np.full(len(grid), np.nan)
        dv = np.full(len(grid), np.nan)
        for r in rows:
            i = tidx.get(int(r["timestamp"]) // HOUR * HOUR)
            if i is not None:
                cl = float(r["close"])
                v = float(r.get("volume") or 0)
                if cl > 0:
                    lc[i] = np.log(cl)
                    qv = r.get("quote_volume")
                    dv[i] = float(qv) if qv not in (None, 0, 0.0) else cl * v
        if np.isfinite(lc).sum() >= lookback_hours * pairs.COVERAGE:
            logp[s] = lc
            dvol[s] = dv
    return grid, logp, dvol


def _all_symbols(storage) -> list[str]:
    min_span = int(getattr(config, "PAIRS_MIN_LISTING_DAYS", 180) * 86400)
    with storage._connect() as c:  # noqa: SLF001
        rows = c.execute(
            "SELECT symbol FROM prices WHERE timeframe='1h' "
            "GROUP BY symbol HAVING COUNT(*)>=2000 "
            "AND (MAX(timestamp) - MIN(timestamp)) >= ?",
            (min_span,),
        ).fetchall()
    return [
        dict(r)["symbol"] for r in rows
        if dict(r)["symbol"] != config.SYMBOL and not pairs.is_excluded(dict(r)["symbol"])
    ]


def tight_deploy_cap(n_closed: int, expectancy: float | None) -> float | None:
    """Extra margin/equity cap while live expectancy is negative or unproven.

    None = only PAIRS_MAX_GROSS_PCT applies. 40% is the user cap for the
    losing book so a burst of |z|>=2 signals cannot refill to ~99% deployed."""
    if n_closed < MIN_EXPECTANCY_TRADES:
        return NEG_EXPECTANCY_DEPLOY_PCT
    if expectancy is not None and expectancy < 0:
        return NEG_EXPECTANCY_DEPLOY_PCT
    return None


def _live_expectancy(storage) -> tuple[int, float | None]:
    state = storage.get_portfolio_state() if hasattr(storage, "get_portfolio_state") else None
    epoch = int(state.get("benchmark_started_at") or 0) if state else 0
    with storage._connect() as c:  # noqa: SLF001
        rows = c.execute(
            "SELECT pnl FROM paper_trades WHERE status='closed' AND strategy=? "
            "AND closed_at IS NOT NULL AND closed_at >= ?",
            (STRATEGY, epoch),
        ).fetchall()
    pnls = [float(dict(r)["pnl"] or 0) for r in rows]
    if not pnls:
        return 0, None
    return len(pnls), sum(pnls) / len(pnls)


def _cap_pairs(specs: list[pairs.PairSpec], k: int, m: int) -> list[pairs.PairSpec]:
    """Greedy non-redundant subset: keep fastest-reverting pairs first, but let
    no symbol appear in more than `m` pairs, up to `k` total. Cuts the heavy
    overlap that makes 1400 pairs only ~50 symbols' worth of real breadth."""
    cnt: dict[str, int] = defaultdict(int)
    out: list[pairs.PairSpec] = []
    for sp in specs:
        if cnt[sp.a] >= m or cnt[sp.b] >= m:
            continue
        out.append(sp)
        cnt[sp.a] += 1
        cnt[sp.b] += 1
        if len(out) >= k:
            break
    return out


# --------------------------------------------------------------------------- #
# selection (cached, refreshed every PAIRS_TRADE_HOURS)
# --------------------------------------------------------------------------- #
def refresh_selection(storage, now_ts: int, *, force: bool = False) -> list[dict[str, Any]]:
    cached = storage.get_system_status(_SELECTION_KEY)
    if cached and not force:
        try:
            info = json.loads(cached["value"])
            if now_ts - int(info["selected_at"]) < pairs.TRADE_HOURS * HOUR:
                return info["pairs"]
        except Exception:  # noqa: BLE001
            pass
    grid, logp, dvol = _panel(storage, _all_symbols(storage), pairs.SEL_HOURS, now_ts)
    if len(logp) < 5:
        logger.warning("pairs: universe too small (%s) — no selection", len(logp))
        return []
    sel = slice(0, len(grid) - 1)
    live = pairs.liquid_universe(logp, dvol, sel)
    # LIVE-only freshness: the backtest universe includes delisted coins (correct
    # for survivorship), but live can only trade names that are STILL trading and
    # whose recent window is dense enough to compute a z-score. Require a bar in
    # the last few hours + >=80% coverage over the trailing z-window.
    zwin = pairs.ZWIN_HOURS
    live = [
        s for s in live
        if np.isfinite(logp[s][-3:]).any()
        and np.isfinite(logp[s][-zwin:]).sum() >= zwin * 0.8
    ]
    specs = _cap_pairs(pairs.select_pairs(live, logp, sel), MAX_CONCURRENT, MAX_PER_SYMBOL)
    payload = [{"a": s.a, "b": s.b, "beta": s.beta, "half_life": s.half_life} for s in specs]
    storage.set_system_status(
        _SELECTION_KEY, json.dumps({"selected_at": now_ts, "pairs": payload})
    )
    logger.info("pairs: reselected %s pairs from %s live symbols", len(payload), len(live))
    return payload


# --------------------------------------------------------------------------- #
# z-score of a selected pair from trailing prices
# --------------------------------------------------------------------------- #
def _pair_z(storage, a: str, b: str, beta: float, now_ts: int) -> float | None:
    grid, logp, _ = _panel(storage, [a, b], pairs.ZWIN_HOURS, now_ts)
    if a not in logp or b not in logp:
        return None
    sp = pairs.spread(logp[a], logp[b], beta)
    if not np.isfinite(sp[-1]):
        return None
    return pairs.zscore(sp[:-1], sp[-1])


def _open_pairs(storage) -> list[dict[str, Any]]:
    return [t for t in storage.get_open_trades() if t.get("strategy") == STRATEGY]


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def _equity(storage) -> float:
    """Equity = cash + reserved margin + unrealized P&L across open pairs. With
    margin trading, cash already has only the MARGIN deducted, so equity =
    cash + Σ(margin_i) + Σ(unrealized_i) = baseline + realized + unrealized."""
    state = storage.get_portfolio_state() if hasattr(storage, "get_portfolio_state") else None
    cash = float(state["cash"]) if state else 0.0
    eq = cash
    for t in _open_pairs(storage):
        eq += _pair_gross(t) * MARGIN_FRAC + _pair_unrealized(storage, t)
    from src.engine.core_manager import core_value

    return eq + core_value(storage)


def _open_one(storage, a: str, b: str, beta: float, z: float, now_ts: int) -> bool:
    pa = storage.get_latest_price(a)
    pb = storage.get_latest_price(b)
    if not pa or not pb:
        return False
    price_a, price_b = float(pa["close"]), float(pb["close"])
    if price_a <= 0 or price_b <= 0:
        return False
    pos = pairs.entry_side(z)  # +1 long spread (long A/short B), -1 short spread
    state = storage.get_portfolio_state()
    cash = float(state["cash"])
    equity = _equity(storage)
    notional = max(0.0, equity * PAIR_NOTIONAL_PCT)
    hedge_notional = beta * notional
    margin = (notional + hedge_notional) * MARGIN_FRAC
    if notional <= 0 or cash < margin:
        return False
    # gross-deployment cap (= leverage): gross exposure ≤ MAX_GROSS_PCT × equity
    if _deployed_notional(storage) + notional + hedge_notional > MAX_GROSS_PCT * equity:
        return False
    n_closed, exp = _live_expectancy(storage)
    cap = tight_deploy_cap(n_closed, exp)
    if cap is not None:
        deployed_margin = _deployed_notional(storage) * MARGIN_FRAC
        if equity > 0 and (deployed_margin + margin) / equity > cap:
            return False
    dir_a = "LONG" if pos > 0 else "SHORT"
    dir_b = "SHORT" if pos > 0 else "LONG"
    # NON-TRIGGERING stop/TP sentinels (belt-and-suspenders with the pairs skip in
    # PaperTrader.check_open_trades_for_symbol). A 0 take_profit on a LONG fires
    # instantly (price>=0); a 0 stop_loss on a SHORT fires instantly (price>=0).
    # Set the un-hit side far away so evaluate_stop_exit returns None even if some
    # path reaches it — pairs exit ONLY via run_pairs_cycle's z-score rule.
    BIG = 1e18
    stop_loss = 0.0 if dir_a == "LONG" else BIG
    take_profit = BIG if dir_a == "LONG" else 0.0
    row = {
        "signal_id": None, "exit_price": None,
        "symbol": a, "direction": dir_a, "entry_price": price_a,
        "quantity": notional / price_a, "stop_loss": stop_loss, "take_profit": take_profit,
        "status": "open", "pnl": None, "opened_at": now_ts, "closed_at": None,
        "strategy": STRATEGY, "style": STRATEGY, "atr_pct": float(z),
        "trail_price": price_a, "exit_reason": None, "fees": None,
        "hedge_symbol": b, "hedge_direction": dir_b, "hedge_entry_price": price_b,
        "hedge_quantity": hedge_notional / price_b, "hedge_beta": beta,
    }
    tid = storage.insert_paper_trade(row)
    storage.update_portfolio_cash(cash - margin)
    logger.info("pairs OPEN id=%s %s %s / %s %s beta=%.3f z=%.2f notional=%.2f",
                tid, dir_a, a, dir_b, b, beta, z, notional)
    return True


def _close_one(storage, trade: dict[str, Any], now_ts: int, reason: str) -> None:
    a, b = trade["symbol"], trade["hedge_symbol"]
    pa = storage.get_latest_price(a)
    pb = storage.get_latest_price(b)
    price_a = float(pa["close"]) if pa else float(trade["entry_price"])
    price_b = float(pb["close"]) if pb else float(trade["hedge_entry_price"])
    qa, qb = float(trade["quantity"]), float(trade["hedge_quantity"])
    ea, eb = float(trade["entry_price"]), float(trade["hedge_entry_price"])
    long_a = trade["direction"] == "LONG"
    pnl_a = (price_a - ea) * qa if long_a else (ea - price_a) * qa
    pnl_b = (price_b - eb) * qb if trade["hedge_direction"] == "LONG" else (eb - price_b) * qb
    hold_h = (now_ts - int(trade["opened_at"])) / HOUR
    short_notional = (eb * qb) if trade["hedge_direction"] == "SHORT" else (ea * qa)
    funding = hold_h * pairs.FUND_HR * short_notional
    fees = 4 * pairs.COST_LEG * (ea * qa)  # 4 legs on the primary notional scale
    pnl = pnl_a + pnl_b - funding - fees
    storage.update_paper_trade(trade["id"], {
        "status": "closed", "exit_price": price_a, "closed_at": now_ts,
        "pnl": pnl, "exit_reason": reason,
    })
    state = storage.get_portfolio_state()
    released = (ea * qa + eb * qb) * MARGIN_FRAC   # return the margin (not full notional)
    storage.update_portfolio_cash(float(state["cash"]) + released + pnl)
    logger.info("pairs CLOSE id=%s %s/%s reason=%s pnl=%.2f", trade["id"], a, b, reason, pnl)


# --------------------------------------------------------------------------- #
# entry point (scheduler job)
# --------------------------------------------------------------------------- #
def run_pairs_cycle(storage=None) -> dict[str, Any]:
    if not getattr(config, "SETUP_PAIRS_STATARB_ENABLED", False):
        return {"enabled": False}
    storage = storage or get_storage()
    now_ts = int(datetime.now(timezone.utc).timestamp())
    selection = refresh_selection(storage, now_ts)
    sel_keys = {(p["a"], p["b"]) for p in selection}
    opened = closed = 0

    # 1) exits: z-revert, deselection, or max hold
    open_trades = _open_pairs(storage)
    open_keys = {(t["symbol"], t["hedge_symbol"]) for t in open_trades}
    for t in open_trades:
        key = (t["symbol"], t["hedge_symbol"])
        beta = float(t.get("hedge_beta") or 1.0)
        z = _pair_z(storage, t["symbol"], t["hedge_symbol"], beta, now_ts)
        hold_h = (now_ts - int(t["opened_at"])) / HOUR
        # CATASTROPHIC STOP: mean-reversion assumes the spread reverts, but a leg
        # that rugs/delists (e.g. DEXE -52% on 2026-07-22) never reverts — cut it
        # before it bleeds to max-hold. Loss measured vs the primary notional.
        primary_notional = float(t["quantity"]) * float(t["entry_price"])
        entry_z = t.get("atr_pct")
        entry_z = float(entry_z) if entry_z is not None else None
        if pairs.is_excluded(t["symbol"]) or pairs.is_excluded(t.get("hedge_symbol")):
            _close_one(storage, t, now_ts, "excluded"); closed += 1
        elif pairs.should_stop(z, entry_z, Z_STOP_DELTA):
            _close_one(storage, t, now_ts, "z_stop"); closed += 1
        elif primary_notional > 0 and _pair_unrealized(storage, t) < -STOP_PCT * primary_notional:
            _close_one(storage, t, now_ts, "stop_loss"); closed += 1
        elif pairs.should_close(z):
            _close_one(storage, t, now_ts, "z_revert"); closed += 1
        elif key not in sel_keys and hold_h >= MAX_HOLD_HOURS:
            _close_one(storage, t, now_ts, "deselected"); closed += 1
        elif hold_h >= MAX_HOLD_HOURS * 2:
            _close_one(storage, t, now_ts, "max_hold"); closed += 1

    # 2) entries: selected, not open, capacity permitting
    live_open = len(_open_pairs(storage))
    for p in selection:
        if live_open >= MAX_CONCURRENT:
            break
        key = (p["a"], p["b"])
        if key in open_keys:
            continue
        if pairs.is_excluded(p["a"]) or pairs.is_excluded(p["b"]):
            continue
        z = _pair_z(storage, p["a"], p["b"], p["beta"], now_ts)
        if pairs.should_open(z):
            # sentiment gate on the spread: +1 = long A / short B
            from src.engine.sentiment_gate import allow_pair

            long_leg, short_leg = (p["a"], p["b"]) if pairs.entry_side(z) > 0 else (p["b"], p["a"])
            if not allow_pair(storage, long_leg, short_leg)[0]:
                continue
        if pairs.should_open(z) and _open_one(storage, p["a"], p["b"], p["beta"], z, now_ts):
            opened += 1
            live_open += 1

    return {"enabled": True, "selected": len(selection), "opened": opened,
            "closed": closed, "open_now": live_open}
