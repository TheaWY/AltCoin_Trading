"""Rotating hypothesis engine.

Every run forms NEW null hypotheses, tests them, and remembers the answer, so
research never re-asks the same question and never stops:

    H0: <signal>/<transform> has no relation to the next <h> hours of
        <raw|market-relative> returns <in condition>.

The hypothesis space is signals x transforms x horizons x targets x
conditions (a few thousand H0s). Each run picks up to N_PER_RUN of them:

  1. follow-ups of promising results (p < 0.10): same signal at neighbouring
     horizons and in every condition
  2. re-tests of supported results older than RETEST_DAYS (does it decay?)
  3. fresh, never-tested hypotheses, drawn at random (seeded by run time)

All p-values ever recorded go through one Benjamini-Hochberg pass, so the
bar rises as more hypotheses are tested -- running forever cannot manufacture
a discovery. A hypothesis is "supported" at q < FDR_Q with a consistent sign
in both halves of the sample.

Supported, unconditional, market-relative results become live weights for the
sentiment gate (weight = signed t capped at 3, normalised, moved at most
MAX_WEIGHT_SHIFT per run).
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.research import sentiment_lab as lab

HOUR = 3600
N_PER_RUN = 100
RETEST_DAYS = 7
PROMISING_P = 0.10
FDR_Q = lab.FDR_Q

HORIZONS = (1, 4, 8, 24, 72, 168)
# Rank IC is invariant to subtracting the cross-sectional mean, so "raw" and
# "residual" targets give identical tests; keeping both double-counted every
# finding in the FDR pass. Market-relative only.
TARGETS = ("residual",)
CONDITIONS = ("all", "high_vol", "low_vol", "btc_up", "btc_down", "liquid", "illiquid")

SIGNAL_TEXT = {
    "funding": "funding rate",
    "ls": "global long/short account ratio (log)",
    "oi": "open interest (log)",
    "oi_price": "OI change signed by the price move",
    "ret": "past return",
    "rvol": "realised volatility",
    "dvol": "dollar volume (log)",
    "hi_dist": "distance below the 7-day high",
}
TRANSFORMS = {
    "level": "level",
    "chg4": "4h change",
    "chg24": "24h change",
    "z168": "z-score vs its own 7 days",
    "z720": "z-score vs its own 30 days",
    "decile": "top/bottom-decile membership",
}
CONDITION_TEXT = {
    "all": "",
    "high_vol": " when market volatility is above its median",
    "low_vol": " in calm markets",
    "btc_up": " when BTC is up over the past 7 days",
    "btc_down": " when BTC is down over the past 7 days",
    "liquid": " in the more liquid half of the universe",
    "illiquid": " in the less liquid half of the universe",
}


# --------------------------------------------------------------------------
# signals and transforms (all point-in-time: row t uses rows <= t)
# --------------------------------------------------------------------------

def _signal(panel: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    close = panel["close"]
    if name == "funding":
        return panel["funding"].reindex_like(close).ffill(limit=16)
    if name == "ls":
        ls = panel["ls"].reindex_like(close)
        return np.log(ls.where(ls > 0))
    if name == "oi":
        oi = panel["oi"].reindex_like(close)
        return np.log(oi.where(oi > 0))
    if name == "oi_price":
        oi = np.log(panel["oi"].reindex_like(close).where(lambda x: x > 0))
        return (oi - oi.shift(24)) * np.sign(np.log(close / close.shift(24)))
    if name == "ret":
        return np.log(close / close.shift(1))
    if name == "rvol":
        return np.log(close / close.shift(1)).rolling(24, min_periods=12).std()
    if name == "dvol":
        dv = panel.get("dollar_volume")
        if dv is None:
            return close * np.nan
        return np.log(dv.reindex_like(close).rolling(24, min_periods=6).sum().where(lambda x: x > 0))
    if name == "hi_dist":
        return np.log(close / close.rolling(168, min_periods=24).max())
    raise KeyError(name)


def _transform(s: pd.DataFrame, tf: str, signal: str) -> pd.DataFrame:
    if tf == "level":
        # a raw 1h return is noise; "level" of ret means the 24h return
        return s.rolling(24, min_periods=12).sum() if signal == "ret" else s
    if tf == "chg4":
        return s.rolling(4, min_periods=2).sum() if signal == "ret" else s - s.shift(4)
    if tf == "chg24":
        return s.rolling(24, min_periods=12).sum() if signal == "ret" else s - s.shift(24)
    if tf == "z168":
        return lab._rolling_z(s, 168, 72)
    if tf == "z720":
        return lab._rolling_z(s, 720, 240)
    if tf == "decile":
        pct = s.rank(axis=1, pct=True)
        return (pct >= 0.9).astype(float) - (pct <= 0.1).astype(float) + s * 0.0
    raise KeyError(tf)


class FeatureCache:
    def __init__(self, panel: dict[str, pd.DataFrame]):
        self.panel = panel
        self._sig: dict[str, pd.DataFrame] = {}
        self._feat: dict[str, pd.DataFrame] = {}

    def feature(self, key: str) -> pd.DataFrame:
        if key not in self._feat:
            sig, tf = key.split(":")
            if sig not in self._sig:
                self._sig[sig] = _signal(self.panel, sig)
            self._feat[key] = _transform(self._sig[sig], tf, sig)
        return self._feat[key]


def feature_available(panel: dict[str, pd.DataFrame], key: str) -> bool:
    sig = key.split(":")[0]
    need = {"funding": "funding", "ls": "ls", "oi": "oi", "oi_price": "oi", "dvol": "dollar_volume"}.get(sig)
    return need is None or (need in panel and not panel[need].empty)


def condition_masks(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame | None]:
    close = panel["close"]
    masks: dict[str, pd.DataFrame | None] = {"all": None}
    masks.update(lab.regime_masks(panel))
    btc = next((c for c in close.columns if c.startswith("BTC/")), None)
    if btc is not None:
        up = (np.log(close[btc] / close[btc].shift(168)) > 0)
        ones = pd.DataFrame(True, index=close.index, columns=close.columns)
        masks["btc_up"] = ones.mul(up, axis=0).astype(bool)
        masks["btc_down"] = ones.mul(~up & close[btc].shift(168).notna(), axis=0).astype(bool)
    return masks


# --------------------------------------------------------------------------
# hypotheses
# --------------------------------------------------------------------------

@dataclass
class Hypothesis:
    signal: str
    transform: str
    horizon: int
    target: str
    condition: str

    @property
    def key(self) -> str:
        return f"{self.signal}:{self.transform}"

    @property
    def hid(self) -> str:
        return f"{self.key}|{self.horizon}h|{self.target}|{self.condition}"

    @property
    def h0(self) -> str:
        kind = "market-relative" if self.target == "residual" else "raw"
        return (f"H0: the {TRANSFORMS[self.transform]} of {SIGNAL_TEXT[self.signal]} has no relation "
                f"to the next {self.horizon}h of {kind} returns{CONDITION_TEXT[self.condition]}.")

    @classmethod
    def parse(cls, hid: str) -> "Hypothesis":
        key, h, tgt, cond = hid.split("|")
        sig, tf = key.split(":")
        return cls(sig, tf, int(h.rstrip("h")), tgt, cond)


def hypothesis_space() -> list[Hypothesis]:
    return [Hypothesis(s, t, h, g, c) for s in SIGNAL_TEXT for t in TRANSFORMS
            for h in HORIZONS for g in TARGETS for c in CONDITIONS]


def choose(registry: dict[str, dict[str, Any]], now: int, n: int = N_PER_RUN,
           available: Callable[[str], bool] = lambda _k: True) -> list[tuple[Hypothesis, str]]:
    """(hypothesis, why) for this run: follow-ups, re-tests, then fresh draws."""
    picked: dict[str, tuple[Hypothesis, str]] = {}

    def add(h: Hypothesis, why: str) -> None:
        if len(picked) < n and h.hid not in picked and available(h.key):
            picked[h.hid] = (h, why)

    rows = sorted(registry.items(), key=lambda kv: kv[1].get("p") if kv[1].get("p") is not None else 1)
    # 1. follow-ups of promising results
    for hid, r in rows:
        if r.get("p") is None or r["p"] >= PROMISING_P:
            break
        base = Hypothesis.parse(hid)
        i = HORIZONS.index(base.horizon)
        neighbours = [HORIZONS[j] for j in (i - 1, i + 1) if 0 <= j < len(HORIZONS)]
        for h in neighbours:
            cand = Hypothesis(base.signal, base.transform, h, base.target, base.condition)
            if cand.hid not in registry:
                add(cand, f"follow-up: neighbouring horizon of {hid}")
        for c in CONDITIONS:
            cand = Hypothesis(base.signal, base.transform, base.horizon, base.target, c)
            if cand.hid not in registry:
                add(cand, f"follow-up: does {hid} hold{CONDITION_TEXT[c] or ' unconditionally'}?")
    # 2. re-test supported results that are getting old
    for hid, r in rows:
        if r.get("verdict") == "supported" and now - int(r.get("tested_at", 0)) > RETEST_DAYS * 86400:
            add(Hypothesis.parse(hid), "re-test: is the effect decaying?")
    # 3. fresh hypotheses
    fresh = [h for h in hypothesis_space() if h.hid not in registry]
    random.Random(now // HOUR).shuffle(fresh)
    for h in fresh:
        add(h, "new hypothesis")
    return list(picked.values())


def _test(cache: FeatureCache, masks: dict, fwd: Callable[[int, str], pd.DataFrame],
          h: Hypothesis) -> dict[str, Any]:
    mask = masks.get(h.condition)
    if h.condition != "all" and mask is None:
        return {"n_periods": 0}
    return lab.test_question(
        cache.feature(h.key), fwd(h.horizon, h.target), h.horizon, mask=mask,
        min_cross_section=lab.MIN_CROSS_SECTION if h.condition == "all" else max(10, lab.MIN_CROSS_SECTION // 2),
    )


def apply_fdr(registry: dict[str, dict[str, Any]]) -> None:
    """One BH pass over every p-value ever recorded."""
    tested = [(hid, r) for hid, r in registry.items()
              if r.get("p") is not None and r.get("n", 0) >= lab.MIN_PERIODS]
    qs = lab.bh_qvalues([r["p"] for _, r in tested])
    for (hid, r), q in zip(tested, qs):
        r["q"] = q
        r["verdict"] = "supported" if (q < FDR_Q and r.get("halves_agree")) else "not_supported"
    for r in registry.values():
        if r.get("p") is None or r.get("n", 0) < lab.MIN_PERIODS:
            r["verdict"] = "insufficient"
            r["q"] = None


def weights_from_registry(registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, tuple[str, dict[str, Any]]] = {}
    for hid, r in registry.items():
        if r.get("verdict") != "supported":
            continue
        h = Hypothesis.parse(hid)
        if h.target != "residual" or h.condition != "all":
            continue
        if h.key not in best or abs(r["t"]) > abs(best[h.key][1]["t"]):
            best[h.key] = (hid, r)
    raw = {k: float(np.sign(r["t"]) * min(abs(r["t"]), 3.0)) for k, (_, r) in best.items()}
    total = sum(abs(v) for v in raw.values())
    return {k: {"weight": raw[k] / total if total else 0.0, "hid": best[k][0], "t_stat": best[k][1]["t"]}
            for k in raw}


def validate(cache: FeatureCache, close: pd.DataFrame, weights: dict[str, float]) -> dict[str, Any]:
    """Out-of-sample check of the composite: signs and relative sizes are
    re-fit on the first 70% of hours, then the composite is scored on the
    last 30% it never saw."""
    if not weights:
        return {"passed": False, "reason": "no supported unconditional market-relative signal yet"}
    idx = close.index
    if len(idx) < 400:
        return {"passed": False, "reason": "panel too short"}
    cut = idx[int(len(idx) * (1 - lab.OOS_FRACTION))]
    train_fwd = lab.forward_returns(close.loc[:cut], lab.PRIMARY_HORIZON, "residual")
    fit: dict[str, float] = {}
    for key in weights:
        res = lab.test_question(cache.feature(key).loc[:cut], train_fwd, lab.PRIMARY_HORIZON)
        t = res.get("t_stat")
        if t is not None and abs(t) >= 1.0:
            fit[key] = float(np.sign(t) * min(abs(t), 3.0))
    if not fit:
        return {"passed": False, "reason": "signals not present in the training window"}
    comp = lab.composite_frame({k: cache.feature(k).loc[cut:] for k in fit}, fit)
    res = lab.test_question(comp, lab.forward_returns(close.loc[cut:], lab.PRIMARY_HORIZON, "residual"),
                            lab.PRIMARY_HORIZON)
    n, t, ic = res.get("n_periods", 0), res.get("t_stat"), res.get("mean_ic")
    passed = bool(n >= lab.OOS_MIN_PERIODS and t is not None and t >= lab.OOS_MIN_T and (ic or 0) > 0)
    reason = ("passed" if passed else f"only {n} out-of-sample periods (need {lab.OOS_MIN_PERIODS})"
              if n < lab.OOS_MIN_PERIODS else f"out-of-sample t={t:.2f} (need >= {lab.OOS_MIN_T})"
              if t is not None else "no out-of-sample IC")
    return {"passed": passed, "reason": reason, "oos_ic": ic, "oos_t": t, "oos_periods": n,
            "oos_bps_per_sd": res.get("bps_per_sd"), "train_weights": fit, "split_ts": int(cut)}


def run(panel: dict[str, pd.DataFrame], registry: dict[str, dict[str, Any]],
        prev_weights: dict[str, float] | None = None, now: int | None = None,
        n: int = N_PER_RUN, log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Test a fresh batch of hypotheses. Mutates and returns ``registry``."""
    now = int(now or time.time())
    started = time.time()
    close = panel["close"]
    cache = FeatureCache(panel)
    masks = condition_masks(panel)
    fwd_cache: dict[tuple[int, str], pd.DataFrame] = {}

    def fwd(h: int, t: str) -> pd.DataFrame:
        if (h, t) not in fwd_cache:
            fwd_cache[(h, t)] = lab.forward_returns(close, h, t)
        return fwd_cache[(h, t)]

    batch = choose(registry, now, n, available=lambda k: feature_available(panel, k))
    tested = []
    for h, why in batch:
        res = _test(cache, masks, fwd, h)
        registry[h.hid] = {
            "h0": h.h0, "why": why, "tested_at": now, "n": res.get("n_periods", 0),
            "ic": res.get("mean_ic"), "t": res.get("t_stat"), "p": res.get("p_value"),
            "halves_agree": res.get("halves_agree"), "bps_per_sd": res.get("bps_per_sd"),
            "spread_bps": res.get("spread_bps"),
        }
        tested.append(h.hid)
    apply_fdr(registry)
    target = weights_from_registry(registry)
    weights = lab.shift_weights(prev_weights or {}, target)
    oos = validate(cache, close, {k: w for k, w in weights.items() if w})
    log(f"tested {len(tested)} hypotheses; registry {len(registry)}; "
        f"supported {sum(r['verdict'] == 'supported' for r in registry.values())}")
    return {
        "run_at": now,
        "elapsed_s": round(time.time() - started, 1),
        "panel": {"hours": int(len(close.index)), "symbols": int(close.shape[1]),
                  "from": int(close.index.min()) if len(close.index) else None,
                  "to": int(close.index.max()) if len(close.index) else None},
        "tested": [{"hid": hid, **registry[hid]} for hid in tested],
        "registry_size": len(registry),
        "space_size": len(hypothesis_space()),
        "supported": {hid: r for hid, r in registry.items() if r["verdict"] == "supported"},
        "target_weights": target,
        "weights": weights,
        "oos": oos,
        "enforce_ok": bool(oos.get("passed")),
    }


# --------------------------------------------------------------------------
# persistence + report
# --------------------------------------------------------------------------

REGISTRY_KEY = "hypothesis_registry"


def load_registry(storage: Any) -> dict[str, dict[str, Any]]:
    row = storage.get_system_status(REGISTRY_KEY)
    if not row or not row.get("value"):
        return {}
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return {}


def save_registry(storage: Any, registry: dict[str, dict[str, Any]]) -> None:
    storage.set_system_status(REGISTRY_KEY, json.dumps(registry, default=float))


def render_markdown(result: dict[str, Any]) -> str:
    from datetime import datetime, timezone

    def ts(x):
        return datetime.fromtimestamp(x, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if x else "?"

    p = result["panel"]
    oos = result["oos"]
    out = [
        "# Hypothesis research", "",
        f"Run {ts(result['run_at'])} · {p['symbols']} symbols × {p['hours']} hours · "
        f"{len(result['tested'])} hypotheses tested this run · "
        f"{result['registry_size']}/{result['space_size']} of the space explored", "",
        f"Sentiment gate: **{'ENFORCE' if result['enforce_ok'] else 'SHADOW'}** ({oos.get('reason')})", "",
        "## Live weights", "",
    ]
    if result["weights"]:
        out += ["| signal | weight | target |", "|---|---:|---:|"]
        for k, w in sorted(result["weights"].items(), key=lambda kv: -abs(kv[1])):
            out.append(f"| {k} | {w:+.3f} | {result['target_weights'].get(k, {}).get('weight', 0):+.3f} |")
    else:
        out.append("None yet: no unconditional market-relative signal has survived the cumulative FDR.")
    out += ["", "## Supported so far", ""]
    if result["supported"]:
        for hid, r in sorted(result["supported"].items(), key=lambda kv: kv[1]["q"]):
            out.append(f"- `{hid}` rejected {r['h0']} IC {r['ic']:+.3f}, t={r['t']:.2f}, "
                       f"q={r['q']:.3f}, {r['bps_per_sd']:+.1f} bps per sd")
    else:
        out.append("Nothing yet.")
    out += ["", "## Tested this run", ""]
    for r in sorted(result["tested"], key=lambda r: r["p"] if r["p"] is not None else 1):
        if r["p"] is None:
            res = f"insufficient data (n={r['n']})"
        else:
            q = f"{r['q']:.3f}" if r.get("q") is not None else "n/a"
            res = f"{r['verdict']}: IC {r['ic']:+.3f}, t={r['t']:+.2f}, p={r['p']:.3f}, q={q}, n={r['n']}"
        out.append(f"- {r['h0']} ({r['why']}) → {res}")
    return "\n".join(out)
