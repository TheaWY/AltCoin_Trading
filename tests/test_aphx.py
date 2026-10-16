"""Sanity tests for scripts/aphx.py on synthetic data with known answers."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import aphx as A  # noqa: E402

rng = np.random.default_rng(1)


def panel(T=400, N=120, beta=0.0):
    S = rng.normal(size=(T, N))
    R = beta * S + rng.normal(scale=1.0, size=(T, N))
    return S, R


def test_nw_t_null_and_signal():
    _, t0 = A.nw_t(rng.normal(size=2000))
    assert abs(t0) < 3.5
    _, t1 = A.nw_t(rng.normal(0.2, 1, size=2000))
    assert t1 > 5


def test_sorts_and_mr():
    S, R = panel(beta=0.05)
    P = A.sort_portfolios(S, R, W=np.abs(rng.normal(size=S.shape)) + 1)
    m, t = A.nw_t(A.long_short(P["EW"]))
    assert t > 4
    J, p = A.mr_test(P["EW"], B=300)
    assert p < 0.10
    S0, R0 = panel(beta=0.0)
    P0 = A.sort_portfolios(S0, R0)
    _, p0 = A.mr_test(P0["EW"], B=300)
    assert p0 > 0.05


def test_fama_macbeth():
    S, R = panel(beta=0.05)
    G, summ = A.fama_macbeth(R, {"s": S, "noise": rng.normal(size=S.shape)})
    assert summ["s"][1] > 4 and abs(summ["noise"][1]) < 3.5


def test_grs_null():
    T = 500
    F = pd.DataFrame(rng.normal(size=(T, 2)), columns=["a", "b"])
    Rp = pd.DataFrame(F.to_numpy() @ rng.normal(size=(2, 5)) + rng.normal(size=(T, 5)))
    stat, p = A.grs(Rp, F)
    assert p > 0.01
    Rp2 = Rp + 0.3
    assert A.grs(Rp2, F)[1] < 0.001


def test_forecast_tests():
    T = 1500
    x = rng.normal(size=T); y = 0.15 * x + rng.normal(size=T)
    f, hm = A.recursive_ols_forecast(pd.Series(y), pd.Series(x), start=200)
    assert A.r2_os(y, f, hm) > 0
    assert A.clark_west(y, hm, f)[1] < 0.05
    hit, S, p = A.pesaran_timmermann(y[200:], f[200:])
    assert p < 0.05
    z = rng.normal(size=T)
    f2, hm2 = A.recursive_ols_forecast(pd.Series(y), pd.Series(z), start=200)
    assert A.clark_west(y, hm2, f2)[1] > 0.01


def test_multiple_testing_and_dsr_pbo():
    p = np.r_[np.full(5, 1e-6), rng.uniform(size=95)]
    rej = A.bhy(p)
    assert rej[:5].all() and rej[5:].sum() <= 3
    M = pd.DataFrame(rng.normal(0, 0.01, size=(800, 50)))
    pbo, _ = A.pbo_cscv(M, S=10)
    assert 0.25 < pbo < 0.75
    srs = (M.mean() / M.std())
    best = M[srs.idxmax()]
    dsr, sr0 = A.deflated_sharpe(best, 50, float(srs.var()))
    assert dsr < 0.95


def test_spa():
    T = 600
    bench = rng.normal(0, 1, T)
    models = pd.DataFrame(rng.normal(0, 1, (T, 10)))
    out = A.spa_stepm(bench, models, reps=300)
    assert out["spa_p_consistent"] > 0.05
    models[0] = bench - 0.4
    out = A.spa_stepm(bench, models, reps=300)
    assert out["spa_p_consistent"] < 0.05 and "0" in out["stepm_superior"]
