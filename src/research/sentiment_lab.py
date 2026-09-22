"""Sentiment lab: measures how much futures sentiment moves prices, forms and
answers its own research questions, and decides how much weight sentiment
gets in live entry decisions.

Pipeline (run by scripts/sentiment_research.py every 6h):

1. load an hourly panel (price, funding, long/short ratio, open interest)
2. build point-in-time features (only data known at hour t)
3. generate the question grid: feature x horizon x target
4. answer each question with a cross-sectional rank-IC test on
   non-overlapping timestamps, Benjamini-Hochberg across the whole run
5. for every supported finding, generate follow-up questions (does it hold
   in high/low volatility regimes, in liquid/illiquid names?) and answer them
6. turn supported findings into target weights, move the live weights toward
   them by at most MAX_WEIGHT_SHIFT per run
7. validate the composite out of sample (fit on first 70%, test on last 30%)
   -- the live gate only enforces when this passes

Everything here is pure pandas/numpy on DataFrames, so the tests can feed
synthetic panels without a database.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

HOUR = 3600

HORIZONS = (4, 24, 72)
TARGETS = ("raw", "residual")
MIN_CROSS_SECTION = 20       # symbols needed at a timestamp for an IC
MIN_PERIODS = 20             # non-overlapping IC observations for a verdict
FDR_Q = 0.10                 # Benjamini-Hochberg false discovery rate
MAX_WEIGHT_SHIFT = 0.10      # max change of any weight per research run
PRIMARY_HORIZON = 24         # composite is validated on this horizon
OOS_FRACTION = 0.30
OOS_MIN_T = 2.0
OOS_MIN_PERIODS = 15

FEATURE_TEXT = {
    "funding": "the funding rate level",
    "funding_z": "funding relative to its own 30-day history (z-score)",
    "ls_z": "the global long/short account ratio vs its 7-day history (z-score)",
    "ls_chg24": "the 24h change in the long/short ratio",
    "oi_chg4": "the 4h change in open interest",
    "oi_chg24": "the 24h change in open interest",
    "oi_price_confirm": "open interest change signed by the past-24h price move (OI confirming the move)",
}
FEATURES = tuple(FEATURE_TEXT)


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------

def _rolling_z(df: pd.DataFrame, window: int, min_periods: int) -> pd.DataFrame:
    mean = df.rolling(window, min_periods=min_periods).mean()
    std = df.rolling(window, min_periods=min_periods).std()
    return (df - mean) / std.replace(0.0, np.nan)


def build_features(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Point-in-time features. ``panel`` holds wide hourly frames
    (index = hour timestamp, columns = symbols): close, funding, ls, oi.
    Every value at row t uses rows <= t only."""
    close = panel["close"]
    funding = panel["funding"].reindex_like(close).ffill(limit=16)
    ls = panel["ls"].reindex_like(close)
    oi = panel["oi"].reindex_like(close)

    log_ls = np.log(ls.where(ls > 0))
    log_oi = np.log(oi.where(oi > 0))
    past_ret24 = np.log(close / close.shift(24))
    oi_chg24 = log_oi - log_oi.shift(24)

    return {
        "funding": funding,
        "funding_z": _rolling_z(funding, 720, 240),
        "ls_z": _rolling_z(log_ls, 168, 72),
        "ls_chg24": log_ls - log_ls.shift(24),
        "oi_chg4": log_oi - log_oi.shift(4),
        "oi_chg24": oi_chg24,
        "oi_price_confirm": oi_chg24 * np.sign(past_ret24),
    }


def forward_returns(close: pd.DataFrame, horizon: int, target: str) -> pd.DataFrame:
    fwd = np.log(close.shift(-horizon) / close)
    if target == "residual":
        fwd = fwd.sub(fwd.mean(axis=1), axis=0)
    return fwd


def xs_zscore(row: pd.Series) -> pd.Series:
    row = row.replace([np.inf, -np.inf], np.nan).dropna()
    if len(row) < 3:
        return row * np.nan
    std = row.std()
    if not std or not np.isfinite(std):
        return row * 0.0
    return ((row - row.mean()) / std).clip(-4, 4)


def xs_zscore_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Row-wise (cross-sectional) z-score of a whole frame, vectorised."""
    df = df.replace([np.inf, -np.inf], np.nan)
    count = df.notna().sum(axis=1)
    std = df.std(axis=1).replace(0.0, np.nan)
    z = df.sub(df.mean(axis=1), axis=0).div(std, axis=0).clip(-4, 4)
    return z.where(count >= 3, np.nan)


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def _p_two_sided(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def _spearman(a: pd.Series, b: pd.Series) -> float:
    return float(a.rank().corr(b.rank()))


def bh_qvalues(pvals: list[float]) -> list[float]:
    n = len(pvals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvals[i])
    q = [1.0] * n
    running = 1.0
    for rank in range(n, 0, -1):
        i = order[rank - 1]
        running = min(running, pvals[i] * n / rank)
        q[i] = min(1.0, running)
    return q


@dataclass
class Question:
    qid: str
    text: str
    feature: str
    horizon: int
    target: str
    subset: str = "all"
    parent: str | None = None
    # answers
    n_periods: int = 0
    mean_ic: float | None = None
    t_stat: float | None = None
    p_value: float | None = None
    q_value: float | None = None
    halves_agree: bool | None = None
    spread_bps: float | None = None
    bps_per_sd: float | None = None
    verdict: str = "untested"
    answer: str = ""


def test_question(
    feature: pd.DataFrame,
    fwd: pd.DataFrame,
    horizon: int,
    mask: pd.DataFrame | None = None,
    min_cross_section: int = MIN_CROSS_SECTION,
) -> dict[str, Any]:
    """Cross-sectional rank IC sampled every ``horizon`` hours (non-overlapping
    forward windows, so the IC series is roughly independent)."""
    idx = feature.index.intersection(fwd.index)
    idx = idx[:: max(1, horizon)]
    ics: list[float] = []
    spreads: list[float] = []
    slopes: list[float] = []
    for ts in idx:
        f = feature.loc[ts]
        r = fwd.loc[ts]
        if mask is not None and ts in mask.index:
            keep = mask.loc[ts].fillna(False).astype(bool)
            f = f[keep.reindex(f.index, fill_value=False)]
        both = pd.concat([f, r], axis=1, keys=["f", "r"]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(both) < min_cross_section:
            continue
        ic = _spearman(both["f"], both["r"])
        if not np.isfinite(ic):
            continue
        ics.append(ic)
        q = both["f"].rank(pct=True)
        top, bot = both["r"][q >= 0.8], both["r"][q <= 0.2]
        if len(top) and len(bot):
            spreads.append(float(top.mean() - bot.mean()))
        z = xs_zscore(both["f"])
        denom = float((z * z).sum())
        if denom > 0:
            slopes.append(float((z * both["r"]).sum() / denom))
    n = len(ics)
    out: dict[str, Any] = {"n_periods": n}
    if n < 3:
        return out
    arr = np.asarray(ics)
    mean_ic = float(arr.mean())
    sd = float(arr.std(ddof=1))
    t = mean_ic / sd * math.sqrt(n) if sd > 0 else 0.0
    half = n // 2
    first, second = arr[:half].mean(), arr[half:].mean()
    out.update(
        mean_ic=mean_ic,
        t_stat=t,
        p_value=_p_two_sided(t),
        halves_agree=bool(np.sign(first) == np.sign(second) and first != 0),
        spread_bps=float(np.mean(spreads) * 1e4) if spreads else None,
        bps_per_sd=float(np.mean(slopes) * 1e4) if slopes else None,
    )
    return out


def _describe(q: Question) -> str:
    if q.verdict == "insufficient":
        return f"Not enough data yet ({q.n_periods} independent periods, need {MIN_PERIODS})."
    direction = "higher" if (q.mean_ic or 0) > 0 else "lower"
    size = f"{q.bps_per_sd:+.1f} bps per 1 sd" if q.bps_per_sd is not None else "n/a"
    base = (
        f"IC {q.mean_ic:+.3f} (t={q.t_stat:.2f}, q={q.q_value:.3f}, n={q.n_periods}); "
        f"higher values precede {direction} {q.horizon}h {q.target} returns; effect {size}"
    )
    if q.spread_bps is not None:
        base += f"; top-minus-bottom quintile {q.spread_bps:+.1f} bps"
    if q.verdict == "supported":
        return "Yes. " + base + "."
    if not q.halves_agree:
        return "No. Sign flips between the first and second half of the sample. " + base + "."
    return "No. Not significant after multiple-testing correction. " + base + "."


def _finalize(questions: list[Question]) -> None:
    tested = [q for q in questions if q.p_value is not None and q.n_periods >= MIN_PERIODS]
    qvals = bh_qvalues([q.p_value for q in tested])
    for q, qv in zip(tested, qvals):
        q.q_value = qv
        q.verdict = "supported" if (qv < FDR_Q and q.halves_agree) else "not_supported"
    for q in questions:
        if q.verdict == "untested":
            q.verdict = "insufficient"
        q.answer = _describe(q)


# --------------------------------------------------------------------------
# research run
# --------------------------------------------------------------------------

def generate_questions() -> list[Question]:
    out = []
    for feat in FEATURES:
        for h in HORIZONS:
            for tgt in TARGETS:
                kind = "market-relative" if tgt == "residual" else "raw"
                out.append(Question(
                    qid=f"{feat}|{h}h|{tgt}",
                    text=f"Does {FEATURE_TEXT[feat]} predict {kind} returns over the next {h}h?",
                    feature=feat, horizon=h, target=tgt,
                ))
    return out


def regime_masks(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Subsets used by follow-up questions. Each mask is a boolean frame
    shaped like the panel."""
    close = panel["close"]
    ret1 = np.log(close / close.shift(1))
    mkt_vol = ret1.mean(axis=1).rolling(168, min_periods=48).std()
    med = mkt_vol.expanding(min_periods=168).median()
    high_vol = (mkt_vol > med).reindex(close.index)
    ones = pd.DataFrame(True, index=close.index, columns=close.columns)
    masks = {
        "high_vol": ones.mul(high_vol.fillna(False), axis=0).astype(bool),
        "low_vol": ones.mul((~high_vol.fillna(True)).astype(bool), axis=0).astype(bool),
    }
    if "dollar_volume" in panel:
        dv = panel["dollar_volume"].reindex_like(close).rolling(168, min_periods=24).mean()
        rank = dv.rank(axis=1, pct=True)
        masks["liquid"] = rank >= 0.5
        masks["illiquid"] = rank < 0.5
    return masks


FOLLOW_UP_TEXT = {
    "high_vol": "Does it still hold when market volatility is above its median?",
    "low_vol": "Does it still hold in calm markets (volatility below median)?",
    "liquid": "Does it hold in the more liquid half of the universe?",
    "illiquid": "Does it hold in the less liquid half of the universe?",
}


def run_research(
    panel: dict[str, pd.DataFrame],
    prev_weights: dict[str, float] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    started = time.time()
    feats = build_features(panel)
    close = panel["close"]
    fwd_cache: dict[tuple[int, str], pd.DataFrame] = {}

    def fwd(h: int, t: str) -> pd.DataFrame:
        if (h, t) not in fwd_cache:
            fwd_cache[(h, t)] = forward_returns(close, h, t)
        return fwd_cache[(h, t)]

    questions = generate_questions()
    for q in questions:
        res = test_question(feats[q.feature], fwd(q.horizon, q.target), q.horizon)
        for k, v in res.items():
            setattr(q, k, v)
        if q.p_value is None:
            q.p_value = None
    _finalize(questions)
    log(f"grid: {sum(q.verdict == 'supported' for q in questions)}/{len(questions)} supported")

    # follow-ups on supported findings
    masks = regime_masks(panel)
    follow: list[Question] = []
    for q in questions:
        if q.verdict != "supported":
            continue
        for name, mask in masks.items():
            fq = Question(
                qid=f"{q.qid}|{name}", text=f"{q.text} {FOLLOW_UP_TEXT[name]}",
                feature=q.feature, horizon=q.horizon, target=q.target,
                subset=name, parent=q.qid,
            )
            res = test_question(feats[q.feature], fwd(q.horizon, q.target), q.horizon,
                                mask=mask, min_cross_section=max(10, MIN_CROSS_SECTION // 2))
            for k, v in res.items():
                setattr(fq, k, v)
            follow.append(fq)
    _finalize(follow)

    target = target_weights(questions)
    weights = shift_weights(prev_weights or {}, target)
    oos = validate_composite(feats, close, questions)
    return {
        "run_at": int(time.time()),
        "elapsed_s": round(time.time() - started, 1),
        "panel": {
            "hours": int(len(close.index)),
            "symbols": int(close.shape[1]),
            "from": int(close.index.min()) if len(close.index) else None,
            "to": int(close.index.max()) if len(close.index) else None,
        },
        "questions": [asdict(q) for q in questions],
        "follow_ups": [asdict(q) for q in follow],
        "target_weights": target,
        "weights": weights,
        "oos": oos,
        "enforce_ok": bool(oos.get("passed")),
    }


# --------------------------------------------------------------------------
# weights
# --------------------------------------------------------------------------

def target_weights(questions: list[Question]) -> dict[str, dict[str, Any]]:
    """One weight per feature from its strongest supported question on a
    market-relative target (the gate ranks names against each other).
    Raw weight = signed t-stat capped at 3; then normalised to sum|w| = 1."""
    best: dict[str, Question] = {}
    for q in questions:
        if q.verdict != "supported" or q.target != "residual" or q.subset != "all":
            continue
        cur = best.get(q.feature)
        if cur is None or abs(q.t_stat or 0) > abs(cur.t_stat or 0):
            best[q.feature] = q
    raw = {f: float(np.sign(q.t_stat) * min(abs(q.t_stat), 3.0)) for f, q in best.items()}
    total = sum(abs(v) for v in raw.values())
    return {
        f: {"weight": (raw[f] / total if total else 0.0), "horizon": best[f].horizon,
            "t_stat": best[f].t_stat, "qid": best[f].qid}
        for f in raw
    }


def shift_weights(prev: dict[str, float], target: dict[str, dict[str, Any]],
                  max_shift: float = MAX_WEIGHT_SHIFT) -> dict[str, float]:
    """Move each weight toward its target by at most ``max_shift``. Features
    with no supported evidence decay toward 0 at the same rate, so one noisy
    run can never swing the strategy."""
    out: dict[str, float] = {}
    for f in set(prev) | set(target):
        old = float(prev.get(f, 0.0))
        tgt = float(target.get(f, {}).get("weight", 0.0))
        step = max(-max_shift, min(max_shift, tgt - old))
        new = round(old + step, 4)
        if abs(new) >= 1e-4:
            out[f] = new
    return out


def composite_frame(feats: dict[str, pd.DataFrame], weights: dict[str, float]) -> pd.DataFrame | None:
    parts = []
    for f, w in weights.items():
        if not w or f not in feats:
            continue
        parts.append(xs_zscore_frame(feats[f]).fillna(0.0) * w)
    if not parts:
        return None
    total = parts[0]
    for p in parts[1:]:
        total = total.add(p, fill_value=0.0)
    return total


def validate_composite(feats: dict[str, pd.DataFrame], close: pd.DataFrame,
                       questions: list[Question]) -> dict[str, Any]:
    """Fit weights on the first 70% of hours, test the composite on the
    last 30%. The live gate only enforces when this out-of-sample test passes."""
    idx = close.index
    if len(idx) < 200:
        return {"passed": False, "reason": "panel too short"}
    cut = idx[int(len(idx) * (1 - OOS_FRACTION))]
    train_close = close.loc[:cut]
    train_feats = {k: v.loc[:cut] for k, v in feats.items()}
    train_qs = []
    for q in generate_questions():
        if q.target != "residual":
            continue
        res = test_question(train_feats[q.feature], forward_returns(train_close, q.horizon, q.target), q.horizon)
        for k, v in res.items():
            setattr(q, k, v)
        train_qs.append(q)
    _finalize(train_qs)
    w = {f: d["weight"] for f, d in target_weights(train_qs).items()}
    if not w:
        return {"passed": False, "reason": "no feature supported in the training window", "train_weights": {}}
    comp = composite_frame({k: v.loc[cut:] for k, v in feats.items()}, w)
    res = test_question(comp, forward_returns(close.loc[cut:], PRIMARY_HORIZON, "residual"), PRIMARY_HORIZON)
    n, t, ic = res.get("n_periods", 0), res.get("t_stat"), res.get("mean_ic")
    passed = bool(n >= OOS_MIN_PERIODS and t is not None and t >= OOS_MIN_T and (ic or 0) > 0)
    reason = "passed" if passed else (
        f"only {n} out-of-sample periods (need {OOS_MIN_PERIODS})" if n < OOS_MIN_PERIODS
        else f"out-of-sample t={t:.2f} (need >= {OOS_MIN_T})" if t is not None else "no out-of-sample IC")
    return {"passed": passed, "reason": reason, "train_weights": w, "oos_ic": ic,
            "oos_t": t, "oos_periods": n, "oos_bps_per_sd": res.get("bps_per_sd"),
            "split_ts": int(cut)}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def render_markdown(result: dict[str, Any]) -> str:
    from datetime import datetime, timezone

    def ts(x):
        return datetime.fromtimestamp(x, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if x else "?"

    p = result["panel"]
    lines = [
        "# Sentiment research",
        "",
        f"Run {ts(result['run_at'])} · panel {p['symbols']} symbols × {p['hours']} hours "
        f"({ts(p['from'])} → {ts(p['to'])}) · {result['elapsed_s']}s",
        "",
        "## Live weights",
        "",
    ]
    oos = result["oos"]
    lines.append(f"Gate status: **{'ENFORCE' if result['enforce_ok'] else 'SHADOW'}** "
                 f"({oos.get('reason')})")
    lines.append("")
    if result["weights"]:
        lines += ["| feature | weight | target |", "|---|---:|---:|"]
        for f, w in sorted(result["weights"].items(), key=lambda kv: -abs(kv[1])):
            tgt = result["target_weights"].get(f, {}).get("weight", 0.0)
            lines.append(f"| {f} | {w:+.3f} | {tgt:+.3f} |")
    else:
        lines.append("No weights yet: no sentiment feature has survived testing.")
    lines += ["", "## Questions answered", ""]
    for section, key in (("Grid", "questions"), ("Follow-ups", "follow_ups")):
        rows = result[key]
        if not rows:
            continue
        lines += [f"### {section}", ""]
        for q in sorted(rows, key=lambda r: (r["verdict"] != "supported", r.get("p_value") or 1)):
            lines.append(f"- **{q['text']}** {q['answer']}")
        lines.append("")
    return "\n".join(lines)


def to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=float)
