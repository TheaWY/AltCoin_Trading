"""Market-neutral event study — currently scoped to rel_strength_95_vs_btc.

Same event definition as the raw event study (unchanged, shared function
from candle_signals.py). Different MEASURED RETURN: symbol return minus
beta x BTC return over the horizon, where beta is a point-in-time trailing
OLS estimate (src.engine.indicators.correlation_and_beta, the same function
evaluate_symbol() already uses for its live correlation/beta metrics).

Why this exists (2026-07-13 decision): the raw event study found +1.46%/
+2.88% effects at 24h/72h on 6 symbols. Re-run on 50 symbols it shrank to
+0.45%/+0.84% -- and effective breadth on raw daily returns came out to
1.89 (avg pairwise correlation 0.519). A long-only alt basket at that
correlation is one leveraged BTC-beta bet wearing 50 costumes, not 50
independent trades. This script asks the sharper question: strip out each
symbol's own beta exposure and see what's left. If the effect survives,
it's evidence of real idiosyncratic rotation, not just "alts went up because
BTC went up." If it vanishes, the original finding was beta in disguise.

Beta is recomputed weekly (not per-hour) using the preceding 720h (30d) of
returns -- a deliberate efficiency simplification, documented, not hidden:
a fresh 720-point OLS regression at every single hourly timestamp for every
symbol (for both the sparse events and the full baseline pool) would be
~50 symbols x ~17,500 hours = ~875,000 regressions. Beta does not move
enough hour-to-hour for that precision to matter; it does matter not to
leak future data into it, which the weekly-boundary construction still
guarantees (each week's beta uses only strictly-prior hours).

    python scripts/event_study_market_neutral.py --days 730
"""

from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.storage import get_storage  # noqa: E402
from src.engine.indicators import correlation_and_beta  # noqa: E402
from src.research.candle_signals import rel_strength_95_vs_btc  # noqa: E402
from src.research.event_study import (  # noqa: E402
    DECLUSTER_SECONDS,
    EFFECT_FLOOR,
    HORIZONS_H,
    MIN_EVENTS,
    _bootstrap_ci_and_pvalue,
    _btc_regime,
    _decluster,
    _forward_return,
    _price_index,
)
from src.research.robust_stats import correlation_matrix, effective_breadth  # noqa: E402

WEEK_S = 7 * 86400
BETA_LOOKBACK_H = 720
BETA_MIN_POINTS = 24


def _weekly_betas(
    sym_prices: list[dict[str, Any]],
    btc_rows_by_ts: dict[int, dict[str, Any]],
) -> dict[int, float]:
    """week_start_ts -> beta, using only the preceding BETA_LOOKBACK_H hours."""
    sym_rows_by_ts = {int(p["timestamp"]): p for p in sym_prices}
    if not sym_prices:
        return {}
    start_ts = int(sym_prices[0]["timestamp"])
    end_ts = int(sym_prices[-1]["timestamp"])
    betas: dict[int, float] = {}
    week_start = start_ts - (start_ts % WEEK_S)
    while week_start <= end_ts:
        lookback_start = week_start - BETA_LOOKBACK_H * 3600
        sym_window = [
            sym_rows_by_ts[t] for t in range(lookback_start, week_start, 3600) if t in sym_rows_by_ts
        ]
        btc_window = [
            btc_rows_by_ts[t] for t in range(lookback_start, week_start, 3600) if t in btc_rows_by_ts
        ]
        result = correlation_and_beta(sym_window, btc_window, min_points=BETA_MIN_POINTS)
        if result["beta"] is not None:
            betas[week_start] = result["beta"]
        week_start += WEEK_S
    return betas


def _beta_at(betas: dict[int, float], ts: int) -> float | None:
    return betas.get(ts - (ts % WEEK_S))


def run(symbols: list[str], days: int) -> dict[str, Any]:
    storage = get_storage()
    symbols = [s for s in symbols if s != "BTC/USDT"]
    since = int(time.time()) - days * 86400
    pad = since - 8 * 86400 - BETA_LOOKBACK_H * 3600  # room for beta lookback + regime lookback

    btc_prices = storage.get_prices("BTC/USDT", limit=1_000_000, since=pad, timeframe="1h")
    btc_idx = _price_index(btc_prices)
    btc_rows_by_ts = {int(p["timestamp"]): p for p in btc_prices}

    baseline_neutral: dict[int, list[float]] = {h: [] for h in HORIZONS_H}
    event_rows: list[dict[str, Any]] = []
    per_symbol_24h_events: dict[str, dict[str, float]] = {}

    for sym in symbols:
        sym_prices = storage.get_prices(sym, limit=1_000_000, since=pad, timeframe="1h")
        if len(sym_prices) < BETA_LOOKBACK_H + 200:
            continue
        sym_idx = _price_index(sym_prices)
        betas = _weekly_betas(sym_prices, btc_rows_by_ts)
        if not betas:
            continue

        events = set(_decluster(rel_strength_95_vs_btc(sym, since)))

        for p in sym_prices:
            ts = int(p["timestamp"])
            if ts < since:
                continue
            beta = _beta_at(betas, ts)
            if beta is None:
                continue
            for h in HORIZONS_H:
                sym_ret = _forward_return(sym_idx, ts, h)
                btc_ret = _forward_return(btc_idx, ts, h)
                if sym_ret is None or btc_ret is None:
                    continue
                neutral_ret = sym_ret - beta * btc_ret
                baseline_neutral[h].append(neutral_ret)
                if ts in events:
                    event_rows.append(
                        {"symbol": sym, "ts": ts, "horizon_h": h, "neutral_ret": neutral_ret, "beta": beta}
                    )
                    if h == 24:
                        day = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")
                        per_symbol_24h_events.setdefault(sym, {})[day] = neutral_ret

    results = []
    for h in HORIZONS_H:
        base_pool = baseline_neutral[h]
        base_mean = sum(base_pool) / len(base_pool) if base_pool else 0.0
        ev_h = [r for r in event_rows if r["horizon_h"] == h]

        by_regime: dict[str, list[float]] = {"all": []}
        for r in ev_h:
            regime = _btc_regime(btc_idx, r["ts"])
            by_regime["all"].append(r["neutral_ret"])
            by_regime.setdefault(regime, []).append(r["neutral_ret"])

        regime_signs = {
            reg: (sum(v) / len(v)) - base_mean
            for reg, v in by_regime.items()
            if reg not in ("all", "unknown") and len(v) >= 30
        }

        for regime, rets in sorted(by_regime.items()):
            if not rets and regime != "all":
                continue
            mean = sum(rets) / len(rets) if rets else 0.0
            effect = mean - base_mean
            diffs = [r - base_mean for r in rets]
            if len(diffs) >= 30:
                ci_low, ci_high, p_value = _bootstrap_ci_and_pvalue(diffs)
            else:
                ci_low, ci_high, p_value = None, None, None

            verdict = "insufficient_n"
            if regime == "all" and len(by_regime["all"]) >= MIN_EVENTS:
                ci_excludes_zero = ci_low is not None and (ci_low > 0 or ci_high < 0)
                consistent = len(regime_signs) >= 2 and len({e > 0 for e in regime_signs.values()}) == 1
                verdict = "PASS" if (abs(effect) > EFFECT_FLOOR and ci_excludes_zero and consistent) else "fail"
            elif regime != "all":
                verdict = "context"

            results.append({
                "horizon_h": h, "regime": regime, "n": len(rets),
                "mean": round(mean, 6), "baseline": round(base_mean, 6),
                "effect": round(effect, 6),
                "ci_low": round(ci_low, 6) if ci_low is not None else None,
                "ci_high": round(ci_high, 6) if ci_high is not None else None,
                "p_value": round(p_value, 6) if p_value is not None else None,
                "verdict": verdict,
            })

    corr = correlation_matrix(per_symbol_24h_events)
    eb = effective_breadth(corr)

    return {
        "symbols": len(symbols),
        "since_days": days,
        "results": results,
        "effective_breadth_24h_events": eb,
        "symbols_with_events": len(per_symbol_24h_events),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=None, help="Comma-separated; default = all symbols with >=2y of 1h history")
    parser.add_argument("--days", type=int, default=730)
    args = parser.parse_args()

    storage = get_storage()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        now = int(time.time())
        with storage._connect() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT symbol, MIN(timestamp) AS mn FROM prices WHERE timeframe='1h' GROUP BY symbol"
            ).fetchall()
        symbols = [
            dict(r)["symbol"] for r in rows if now - dict(r)["mn"] >= 2 * 365 * 86400
        ]

    out = run(symbols, args.days)
    print(f"symbols: {out['symbols']}  since: {out['since_days']}d ago")
    print(f"{'h':>4}{'regime':>8}{'n':>7}{'effect':>10}{'p':>9}{'CI':>22}{'verdict':>16}")
    for r in out["results"]:
        ci = f"[{r['ci_low']:+.4f},{r['ci_high']:+.4f}]" if r["ci_low"] is not None else "-"
        p = f"{r['p_value']:.4f}" if r["p_value"] is not None else "-"
        print(f"{r['horizon_h']:>4}{r['regime']:>8}{r['n']:>7}{r['effect']:>+10.4f}{p:>9}{ci:>22}{r['verdict']:>16}")
    print()
    print("symbols with neutralized 24h events:", out["symbols_with_events"])
    print("effective_breadth (neutralized 24h event returns):", out["effective_breadth_24h_events"])


if __name__ == "__main__":
    main()
