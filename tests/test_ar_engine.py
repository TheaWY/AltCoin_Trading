"""Smoke tests for the autonomous research engine (no DB, no heavy panel)."""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ar_catalog as C  # noqa: E402
import ar_engine as E  # noqa: E402


def fake_panel(T=900, N=40, seed=0):
    rng = np.random.default_rng(seed)
    ts = np.arange(T) * 3600 + 1_700_000_000
    lc = np.cumsum(rng.normal(0, 0.01, (T, N)), 0)
    P = {"ts": ts, "codes": [f"C{j}" for j in range(N)], "btc": 0, "lc": lc, "r1": np.vstack([np.zeros((1, N)), np.diff(lc, axis=0)]),
         "qv": rng.uniform(1e6, 1e8, (T, N)), "tbq": rng.uniform(0, 1, (T, N)), "n": rng.integers(100, 1000, (T, N)).astype(float),
         "h": lc + 0.01, "l": lc - 0.01, "f8": rng.normal(1e-4, 1e-4, (T, N)), "beta": np.ones((T, N)), "up_qv": rng.uniform(0, 1e6, (T, N)),
         "bt_qv": rng.uniform(0, 1e5, (T, N)), "up_lc": lc + 0.02, "oi": rng.uniform(1e6, 1e8, (T, N)), "ls_top": rng.uniform(0.5, 2, (T, N)),
         "ls_global": rng.uniform(0.5, 2, (T, N)), "taker": rng.uniform(0.5, 2, (T, N)), "U": np.ones((T, N), bool)}
    P["tbq"] = P["tbq"] * P["qv"]
    return P


def test_every_variable_builds():
    P = fake_panel()
    for name, (sign, fam, fn, src, live) in C.VARIABLES.items():
        with np.errstate(all="ignore"):
            S = np.asarray(fn(P), float)
        assert S.shape == P["lc"].shape, name
        assert np.isfinite(S[-1]).sum() > 0, name
        assert sign in (-1, 1)


def test_every_conditioner_builds():
    P = fake_panel()
    for name, (fn, desc) in C.CONDITIONERS.items():
        if fn is None:
            continue
        with np.errstate(all="ignore"):
            v = np.asarray(fn(P)).astype(bool)
        assert v.shape == (len(P["ts"]),), name


def test_ids_stable_and_verdict():
    a = E.hid({"method": "xs_sort", "var": "rev_1d", "cond": None}); b = E.hid({"cond": None, "var": "rev_1d", "method": "xs_sort"})
    assert a == b and a.startswith("AR")
    ok, p = E.verdict({"holdout_fm_t": 2.5, "insample_fm_t": 1.0, "holdout_lag1h_t": 2.0, "holdout_dsort_t": 2.0, "holdout_band_net_bp": 1.0, "method": "xs_sort"}, True)
    assert p
    ok, p = E.verdict({"holdout_fm_t": 2.5, "insample_fm_t": -1.0, "holdout_lag1h_t": 2.0, "holdout_dsort_t": 2.0, "holdout_band_net_bp": 1.0, "method": "xs_sort"}, True)
    assert not p and not ok["insample_sign"]
