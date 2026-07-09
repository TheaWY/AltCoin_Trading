"""Statistical guards from quant research literature, retail-adapted.

Implements the anti-false-discovery layer the deep-research report identified
as the pipeline's biggest gap:

  deflated_sharpe    Bailey & López de Prado (2014). Corrects the Sharpe ratio
                     for selection bias across N trials and for non-normal
                     returns (skew/kurtosis). DSR >= 0.95 required to promote.
  min_backtest_len   Bailey, Borwein, López de Prado & Zhu (2014):
                     MinBTL < 2·ln(N) / E[max_N]^2. Used inversely here to cap
                     how many trials the queue may consume for a given history
                     length — the runner refuses to exceed it.
  pbo_lite           Cheap probability-of-backtest-overfitting proxy: across
                     done experiments, is the config that ranked best on the
                     FIRST half of walk-forward windows above median on the
                     SECOND half? Estimated over many random config subsets.
  effective_breadth  Buckle (2004) equal-correlation form of Grinold-Kahn:
                     BR_eff = N / (1 + (N-1)·ρ̄). With few symbols and
                     correlated setups this is the honest bet count.

All pure functions, stdlib only, deterministic.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist

_ND = NormalDist()
_EULER_GAMMA = 0.5772156649015329


def sharpe(returns: list[float]) -> float:
    """Non-annualized per-observation Sharpe. Caller keeps units consistent."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return mean / math.sqrt(var) if var > 0 else 0.0


def _moments(returns: list[float]) -> tuple[float, float]:
    """(skewness, kurtosis) — kurtosis is NON-excess (normal = 3)."""
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    if var == 0:
        return 0.0, 3.0
    std = math.sqrt(var)
    skew = sum(((r - mean) / std) ** 3 for r in returns) / n
    kurt = sum(((r - mean) / std) ** 4 for r in returns) / n
    return skew, kurt


def expected_max_sharpe(n_trials: int, var_trial_sr: float) -> float:
    """E[max SR under H0] across N trials — the hurdle luck alone produces."""
    if n_trials <= 1:
        return 0.0
    z1 = _ND.inv_cdf(1 - 1 / n_trials)
    z2 = _ND.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(max(var_trial_sr, 0.0)) * (
        (1 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2
    )


def deflated_sharpe(
    returns: list[float],
    n_trials: int,
    trial_sharpes: list[float] | None = None,
) -> dict[str, float]:
    """DSR per Bailey & López de Prado (2014).

    returns:       candidate's per-trade returns (pnl / capital)
    n_trials:      total experiments evaluated on this data (the N that
                   inflates the best-of Sharpe)
    trial_sharpes: per-trade Sharpes of the other trials, for Var{SR_n}.
                   With < 10 available, falls back to the H0 estimator
                   variance Var(SR̂) ≈ 1/T (Lo 2002): under the null, trial
                   Sharpes scatter by estimation noise alone.
    """
    sr = sharpe(returns)
    t = len(returns)
    if t < 10:
        return {"sr": sr, "sr0": 0.0, "dsr": 0.0, "note": 1.0}  # note=1: too few obs

    if trial_sharpes and len(trial_sharpes) >= 10:
        mean_s = sum(trial_sharpes) / len(trial_sharpes)
        var_sr = sum((s - mean_s) ** 2 for s in trial_sharpes) / (len(trial_sharpes) - 1)
        var_sr = max(var_sr, 1.0 / t)  # never below pure estimation noise
    else:
        var_sr = 1.0 / t

    sr0 = expected_max_sharpe(max(n_trials, 1), var_sr)
    skew, kurt = _moments(returns)
    denom = 1 - skew * sr + ((kurt - 1) / 4.0) * sr * sr
    if denom <= 0:
        return {"sr": sr, "sr0": sr0, "dsr": 0.0, "note": 2.0}  # pathological moments
    z = (sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom)
    return {"sr": round(sr, 4), "sr0": round(sr0, 4),
            "dsr": round(_ND.cdf(z), 4), "note": 0.0}


def max_trials_for_history(history_years: float, target_max_sr: float = 1.0) -> int:
    """Inverse MinBTL: how many independent configs may be tried on this much
    history before luck alone is expected to produce an annualized SR >=
    target_max_sr in-sample.

    Derivation: trial SR estimates over T years have std ~ 1/sqrt(T)
    (annualized); the expected max over N null trials is E[max_N]/sqrt(T).
    Setting that equal to target gives T = (E[max_N]/target)^2. Anchor check
    against Bailey et al. (2014): 5 years <-> ~45 configs. E[max_45] = 2.236,
    2.236^2 = 5.0 years. Matches.
    """
    n = 1
    while n < 100_000:
        e_max = expected_max_sharpe(n + 1, 1.0)
        min_years = (e_max / target_max_sr) ** 2
        if min_years > history_years:
            return max(n, 1)
        n += 1
    return n


def pbo_lite(
    per_config_window_pnls: dict[str, list[float]],
    n_subsets: int = 200,
    subset_size: int = 8,
    seed: int = 42,
) -> dict[str, float]:
    """Probability-of-backtest-overfitting proxy.

    For many random subsets of configs: pick the config with the best FIRST-half
    window PnL (in-sample winner), check whether it falls BELOW the subset
    median on SECOND-half windows. PBO = fraction of subsets where it does.
    PBO > 0.5 means in-sample selection is anti-predictive — the queue is
    overfitting. Needs >= subset_size configs with >= 4 windows each.
    """
    eligible = {k: v for k, v in per_config_window_pnls.items() if len(v) >= 4}
    if len(eligible) < subset_size:
        return {"pbo": -1.0, "configs": float(len(eligible))}
    rng = random.Random(seed)
    keys = list(eligible)
    below = 0
    for _ in range(n_subsets):
        subset = rng.sample(keys, subset_size)
        half = len(eligible[subset[0]]) // 2
        is_winner = max(subset, key=lambda k: sum(eligible[k][:half]))
        oos = {k: sum(eligible[k][half:]) for k in subset}
        ranked = sorted(oos.values())
        median = ranked[len(ranked) // 2]
        if oos[is_winner] < median:
            below += 1
    return {"pbo": round(below / n_subsets, 3), "configs": float(len(eligible))}


def correlation_matrix(series: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Pairwise Pearson corr of daily PnL streams keyed by name -> {date: pnl}.
    Missing dates are treated as 0 PnL (no trade that day) over the union of
    dates, which is the correct treatment for strategy PnL overlap."""
    names = sorted(series)
    all_dates = sorted({d for s in series.values() for d in s})
    vectors = {n: [series[n].get(d, 0.0) for d in all_dates] for n in names}

    def corr(a: list[float], b: list[float]) -> float:
        n = len(a)
        if n < 5:
            return 0.0
        ma, mb = sum(a) / n, sum(b) / n
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0

    return {
        a: {b: round(corr(vectors[a], vectors[b]), 3) for b in names} for a in names
    }


def effective_breadth(corr: dict[str, dict[str, float]]) -> dict[str, float]:
    """BR_eff = N / (1 + (N-1)·ρ̄) with ρ̄ = mean off-diagonal correlation."""
    names = list(corr)
    n = len(names)
    if n < 2:
        return {"n": float(n), "avg_corr": 0.0, "effective_breadth": float(n)}
    offdiag = [corr[a][b] for a in names for b in names if a < b]
    rho = max(sum(offdiag) / len(offdiag), 0.0)  # negative avg corr caps at N
    return {
        "n": float(n),
        "avg_corr": round(rho, 3),
        "effective_breadth": round(n / (1 + (n - 1) * rho), 2),
    }
