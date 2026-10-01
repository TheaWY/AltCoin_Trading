"""aphx: academic test battery for the AltCoin_Trading research (2026-10-01).

Implements the procedures used in empirical asset pricing / return-predictability papers so every hypothesis is judged
the same way a referee would judge it. References in research/lit/test_methodology.md.

Cross-section    sorts (EW/VW, NW t), Patton-Timmermann monotonicity, dependent double sorts, Fama-MacBeth (NW),
                 LTW-style factors (CMKT, CSMB, CMOM) + alpha regressions + GRS, turnover / net / break-even
                 (Novy-Marx & Velikov), buy-hold band
Forecasting      Campbell-Thompson R2_OS (+ Gu-Kelly-Xiu non-demeaned), Clark-West, Diebold-Mariano (HLN),
                 Pesaran-Timmermann, Rapach-Strauss-Zhou mean combination
Data snooping    BHY / Holm / Bonferroni, Harvey-Liu-Zhu |t|>=3, Hansen SPA and Romano-Wolf StepM (arch),
                 Deflated Sharpe Ratio, PBO via CSCV, Politis-White block length
Everything takes plain numpy / pandas objects; nothing here touches the database.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd
from scipy import stats

# ------------------------------------------------------------------ basics


def nw_lag(T: int) -> int:
    """Newey & West (1994) rule of thumb floor(4 (T/100)^(2/9))."""
    return int(np.floor(4 * (T / 100.0) ** (2.0 / 9.0)))


def nw_t(x, lag: int | None = None):
    """mean, Newey-West t-stat (Bartlett kernel) of a series."""
    x = np.asarray(pd.Series(x).dropna(), float)
    T = len(x)
    if T < 5:
        return float("nan"), float("nan")
    L = nw_lag(T) if lag is None else lag
    e = x - x.mean()
    s = e @ e / T
    for k in range(1, L + 1):
        s += 2 * (1 - k / (L + 1)) * (e[k:] @ e[:-k]) / T
    se = math.sqrt(max(s, 1e-300) / T)
    return float(x.mean()), float(x.mean() / se)


def nw_ols(y, X, lag: int | None = None):
    """OLS with Newey-West covariance. X without constant; returns (coef incl. const, t-stats, r2)."""
    y = np.asarray(y, float); X = np.column_stack([np.ones(len(y)), np.asarray(X, float)])
    ok = np.isfinite(y) & np.isfinite(X).all(1); y, X = y[ok], X[ok]
    T, k = X.shape
    L = nw_lag(T) if lag is None else lag
    b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b
    XtXi = np.linalg.pinv(X.T @ X)
    Xe = X * e[:, None]
    S = Xe.T @ Xe
    for l in range(1, L + 1):
        G = Xe[l:].T @ Xe[:-l]; S += (1 - l / (L + 1)) * (G + G.T)
    V = XtXi @ S @ XtXi
    t = b / np.sqrt(np.maximum(np.diag(V), 1e-300))
    r2 = 1 - (e @ e) / (((y - y.mean()) ** 2).sum() + 1e-300)
    return b, t, float(r2)


def winsor_rows(A, q=0.01):
    """winsorise each row (period) at q / 1-q."""
    lo = np.nanquantile(A, q, axis=1, keepdims=True); hi = np.nanquantile(A, 1 - q, axis=1, keepdims=True)
    return np.clip(A, lo, hi)


def block_length(x) -> float:
    try:
        from arch.bootstrap import optimal_block_length
        v = float(optimal_block_length(np.asarray(pd.Series(x).dropna(), float))["stationary"].iloc[0])
        return max(1.0, v) if np.isfinite(v) else 5.0
    except Exception:  # noqa: BLE001
        return 5.0

# ------------------------------------------------------------------ cross-sectional sorts


def sort_portfolios(S, R, W=None, n=5, mask=None):
    """S signal (T x N, known at t), R next-period return (T x N), W weights for VW (T x N, e.g. dollar volume).
    Returns dict: EW and VW quantile returns (T x n DataFrames), assignment array, counts."""
    T, N = S.shape
    m = np.isfinite(S) & np.isfinite(R) if mask is None else (mask & np.isfinite(S) & np.isfinite(R))
    q = np.full((T, N), -1, int)
    ew = np.full((T, n), np.nan); vw = np.full((T, n), np.nan); cnt = np.zeros((T, n))
    for t in range(T):
        mt = m[t]
        if mt.sum() < 3 * n:
            continue
        s = S[t, mt]
        r = pd.Series(s).rank(method="first").to_numpy()
        g = np.minimum((r - 1) * n // mt.sum(), n - 1).astype(int)
        q[t, np.flatnonzero(mt)] = g
        rr = R[t, mt]; ww = None if W is None else np.nan_to_num(W[t, mt])
        for k in range(n):
            sel = g == k
            ew[t, k] = rr[sel].mean(); cnt[t, k] = sel.sum()
            if ww is not None and ww[sel].sum() > 0:
                vw[t, k] = (rr[sel] * ww[sel]).sum() / ww[sel].sum()
    return {"EW": pd.DataFrame(ew), "VW": pd.DataFrame(vw), "q": q, "count": cnt}


def mr_test(P: pd.DataFrame, B=1000, seed=0):
    """Patton & Timmermann (2010) monotonic-relation test, increasing pattern Q1<Q2<..<Qn.
    Returns p-value of H0 (not monotonic increasing); stationary bootstrap, Politis-White block length."""
    X = P.dropna().to_numpy()
    D = np.diff(X, axis=1)
    J = D.mean(0).min()
    T = len(D)
    bl = block_length(X[:, -1] - X[:, 0])
    rng = np.random.default_rng(seed); p_geo = 1.0 / bl
    cnt = 0
    for _ in range(B):
        idx = np.empty(T, int); i = rng.integers(T)
        for t in range(T):
            if t > 0 and rng.random() < p_geo:
                i = rng.integers(T)
            idx[t] = i; i = (i + 1) % T
        Db = D[idx]
        Jb = (Db.mean(0) - D.mean(0)).min()
        cnt += Jb >= J
    return float(J), float(cnt / B)


def long_short(P: pd.DataFrame):
    return P.iloc[:, -1] - P.iloc[:, 0]


def turnover_cost(q, W_assign_top, W_assign_bot, cost, R=None):
    """Equal-weight top/bottom legs from assignment q (T x N ints); per-period one-way turnover and cost.
    cost: T x N one-way cost per unit traded (fee + slippage). Returns turnover series, cost series."""
    T, N = q.shape
    w_prev = np.zeros(N); to = np.zeros(T); cs = np.zeros(T)
    for t in range(T):
        top, bot = q[t] == W_assign_top, q[t] == W_assign_bot
        w = np.zeros(N)
        if top.sum() and bot.sum():
            w[top] = 0.5 / top.sum(); w[bot] = -0.5 / bot.sum()
        # drift weights by realised returns before rebalancing (Novy-Marx & Velikov use post-return weights)
        if R is not None and t > 0:
            g = np.nan_to_num(R[t - 1]); wp = w_prev * (1 + g); s = np.abs(wp).sum()
            w_prev = wp / s if s > 0 else wp
        dw = np.abs(w - w_prev)
        to[t] = dw.sum() / 2; cs[t] = (dw * np.nan_to_num(cost[t], nan=0.002)).sum()
        w_prev = w
    return pd.Series(to), pd.Series(cs)


def band_long_short(S, R, cost, mask, enter=0.2, exit_=0.4):
    """Buy/hold band (Novy-Marx & Velikov): enter top/bottom `enter`, keep until outside top/bottom `exit_`."""
    T, N = S.shape
    w_prev = np.zeros(N); gross = np.full(T, np.nan); net = np.full(T, np.nan); to = np.zeros(T)
    for t in range(T):
        m = mask[t] & np.isfinite(S[t]) & np.isfinite(R[t])
        if m.sum() < 15:
            continue
        p = np.full(N, np.nan); p[m] = pd.Series(S[t, m]).rank(pct=True).to_numpy()
        L = (m & (p >= 1 - enter)) | ((w_prev > 0) & m & (p >= 1 - exit_))
        Sh = (m & (p <= enter)) | ((w_prev < 0) & m & (p <= exit_))
        w = np.zeros(N)
        if L.sum() and Sh.sum():
            w[L] = 0.5 / L.sum(); w[Sh] = -0.5 / Sh.sum()
        dw = np.abs(w - w_prev); to[t] = dw.sum() / 2
        gross[t] = (w * np.nan_to_num(R[t])).sum(); net[t] = gross[t] - (dw * np.nan_to_num(cost[t], nan=0.002)).sum()
        w_prev = w
    return pd.Series(gross), pd.Series(net), pd.Series(to)

# ------------------------------------------------------------------ Fama-MacBeth


def fama_macbeth(R, chars: dict, mask=None, rank=True):
    """Per-period cross-sectional OLS of R on characteristics (ranked to [-0.5, 0.5] by default).
    Returns DataFrame of slopes (T x K) and summary {name: (mean, NW t)}."""
    names = list(chars)
    T, N = R.shape
    out = np.full((T, len(names)), np.nan)
    for t in range(T):
        cols = []
        for k in names:
            x = chars[k][t].astype(float)
            cols.append(x)
        Xt = np.column_stack(cols); y = R[t]
        m = np.isfinite(y) & np.isfinite(Xt).all(1)
        if mask is not None:
            m &= mask[t]
        if m.sum() < len(names) + 10:
            continue
        Xm = Xt[m]
        if rank:
            Xm = np.column_stack([pd.Series(c).rank(pct=True).to_numpy() - 0.5 for c in Xm.T])
        Xm = np.column_stack([np.ones(m.sum()), Xm])
        out[t] = np.linalg.lstsq(Xm, y[m], rcond=None)[0][1:]
    G = pd.DataFrame(out, columns=names)
    return G, {k: nw_t(G[k]) for k in names}

# ------------------------------------------------------------------ factors, alphas, GRS


def ltw_factors(R, size, mom, mask, mkt_w=None):
    """Liu-Tsyvinski-Wu style factors on the given panel (T x N):
    CMKT = value-weighted (by `mkt_w`, default size) market return; CSMB = small minus big (30/40/30 on size);
    CMOM = within-size-group 30/40/30 momentum sort, high minus low (LTW: 3-week momentum)."""
    T, N = R.shape
    W = size if mkt_w is None else mkt_w
    out = np.full((T, 3), np.nan)
    for t in range(T):
        m = mask[t] & np.isfinite(R[t]) & np.isfinite(size[t]) & np.isfinite(mom[t])
        if m.sum() < 30:
            continue
        r, s, mo, w = R[t, m], size[t, m], mom[t, m], np.nan_to_num(W[t, m])
        out[t, 0] = (r * w).sum() / w.sum() if w.sum() > 0 else r.mean()
        s30, s70 = np.quantile(s, [0.3, 0.7])
        out[t, 1] = r[s <= s30].mean() - r[s >= s70].mean()
        big = s >= np.median(s)
        legs = []
        for g in (big, ~big):
            if g.sum() < 10:
                continue
            m30, m70 = np.quantile(mo[g], [0.3, 0.7])
            legs.append(r[g][mo[g] >= m70].mean() - r[g][mo[g] <= m30].mean())
        out[t, 2] = np.mean(legs) if legs else np.nan
    return pd.DataFrame(out, columns=["CMKT", "CSMB", "CMOM"])


def alpha(y, F: pd.DataFrame, lag=None):
    b, t, r2 = nw_ols(np.asarray(y, float), F.to_numpy(), lag)
    return {"alpha": float(b[0]), "t_alpha": float(t[0]), "betas": dict(zip(F.columns, b[1:].round(4).tolist())), "r2": r2}


def grs(Rp: pd.DataFrame, F: pd.DataFrame):
    """Gibbons-Ross-Shanken test that all N portfolio alphas are zero given L factors."""
    D = pd.concat([Rp, F], axis=1).dropna()
    Y, X = D[Rp.columns].to_numpy(), D[F.columns].to_numpy()
    T, N = Y.shape; Lf = X.shape[1]
    Xc = np.column_stack([np.ones(T), X])
    B = np.linalg.lstsq(Xc, Y, rcond=None)[0]; E = Y - Xc @ B; a = B[0]
    Sig = E.T @ E / (T - Lf - 1)
    mu = X.mean(0); Om = np.cov(X.T, ddof=1).reshape(Lf, Lf)
    stat = (T / N) * ((T - N - Lf) / (T - Lf - 1)) * (a @ np.linalg.pinv(Sig) @ a) / (1 + mu @ np.linalg.pinv(Om) @ mu)
    return float(stat), float(1 - stats.f.cdf(stat, N, T - N - Lf))

# ------------------------------------------------------------------ forecasting tests


def r2_os(y, f, bench):
    y, f, bench = map(lambda a: np.asarray(a, float), (y, f, bench))
    ok = np.isfinite(y) & np.isfinite(f) & np.isfinite(bench)
    return float(1 - ((y[ok] - f[ok]) ** 2).sum() / ((y[ok] - bench[ok]) ** 2).sum())


def r2_gkx(y, f):
    y, f = np.asarray(y, float), np.asarray(f, float); ok = np.isfinite(y) & np.isfinite(f)
    return float(1 - ((y[ok] - f[ok]) ** 2).sum() / (y[ok] ** 2).sum())


def clark_west(y, f_small, f_big, lag=None):
    """one-sided test that the bigger (nested) model has lower MSPE. Returns (stat, p)."""
    y, a, b = map(lambda v: np.asarray(v, float), (y, f_small, f_big))
    ok = np.isfinite(y) & np.isfinite(a) & np.isfinite(b)
    fx = (y[ok] - a[ok]) ** 2 - ((y[ok] - b[ok]) ** 2 - (a[ok] - b[ok]) ** 2)
    _, t = nw_t(fx, lag)
    return float(t), float(1 - stats.norm.cdf(t))


def diebold_mariano(e1, e2, h=1, loss="mse"):
    """DM with Harvey-Leybourne-Newbold correction; positive stat = model 2 better. Returns (stat, two-sided p)."""
    e1, e2 = np.asarray(e1, float), np.asarray(e2, float)
    d = (e1 ** 2 - e2 ** 2) if loss == "mse" else (np.abs(e1) - np.abs(e2))
    d = d[np.isfinite(d)]; T = len(d)
    m, t = nw_t(d, h - 1 if h > 1 else 0)
    corr = math.sqrt((T + 1 - 2 * h + h * (h - 1) / T) / T)
    s = t * corr
    return float(s), float(2 * (1 - stats.t.cdf(abs(s), T - 1)))


def pesaran_timmermann(y, f):
    """directional accuracy test (H0: forecast sign independent of outcome sign). Returns (hit, S, p one-sided)."""
    y, f = np.asarray(y, float), np.asarray(f, float); ok = np.isfinite(y) & np.isfinite(f) & (y != 0)
    a, b = (y[ok] > 0).astype(float), (f[ok] > 0).astype(float); n = len(a)
    P = (a == b).mean(); py, pf = a.mean(), b.mean()
    Ps = py * pf + (1 - py) * (1 - pf)
    vP = Ps * (1 - Ps) / n
    vPs = ((2 * py - 1) ** 2 * pf * (1 - pf) / n + (2 * pf - 1) ** 2 * py * (1 - py) / n + 4 * py * pf * (1 - py) * (1 - pf) / n ** 2)
    S = (P - Ps) / math.sqrt(max(vP - vPs, 1e-12))
    return float(P), float(S), float(1 - stats.norm.cdf(S))


def recursive_ols_forecast(y: pd.Series, x: pd.Series, start: int, min_obs=60):
    """Goyal-Welch recursive forecast of y_t from x_{t} (x already lagged by caller). Returns model and
    historical-mean forecasts aligned with y."""
    f = pd.Series(np.nan, index=y.index); hm = pd.Series(np.nan, index=y.index)
    yv, xv = y.to_numpy(float), x.to_numpy(float)
    for i in range(start, len(y)):
        ok = np.isfinite(yv[:i]) & np.isfinite(xv[:i])
        if ok.sum() < min_obs or not np.isfinite(xv[i]):
            continue
        b = np.polyfit(xv[:i][ok], yv[:i][ok], 1)
        f.iloc[i] = b[0] * xv[i] + b[1]; hm.iloc[i] = yv[:i][ok].mean()
    return f, hm

# ------------------------------------------------------------------ data snooping


def bhy(pvals, alpha=0.05):
    p = np.asarray(pvals, float); M = len(p); c = (1.0 / np.arange(1, M + 1)).sum()
    o = np.argsort(p); thr = (np.arange(1, M + 1) * alpha) / (M * c)
    passed = p[o] <= thr; k = np.max(np.flatnonzero(passed)) + 1 if passed.any() else 0
    rej = np.zeros(M, bool); rej[o[:k]] = True
    return rej


def holm(pvals, alpha=0.05):
    p = np.asarray(pvals, float); M = len(p); o = np.argsort(p); rej = np.zeros(M, bool)
    for i, j in enumerate(o):
        if p[j] <= alpha / (M - i):
            rej[j] = True
        else:
            break
    return rej


def spa_stepm(losses_bench, losses_models: pd.DataFrame, reps=1000, seed=0):
    """Hansen SPA (consistent p) and Romano-Wolf StepM superior set. Losses: lower is better (use -returns)."""
    from arch.bootstrap import SPA, StepM
    bl = block_length(-np.asarray(losses_bench, float) + losses_models.mean(1).to_numpy())
    spa = SPA(np.asarray(losses_bench, float), losses_models.to_numpy(), reps=reps, block_size=int(round(bl)), seed=seed)
    spa.compute()
    sm = StepM(np.asarray(losses_bench, float), losses_models, reps=reps, block_size=int(round(bl)), seed=seed)
    sm.compute()
    return {"spa_p_consistent": float(spa.pvalues["consistent"]), "spa_p_lower": float(spa.pvalues["lower"]),
            "spa_p_upper": float(spa.pvalues["upper"]), "stepm_superior": [str(c) for c in sm.superior_models], "block": bl}


def deflated_sharpe(r, n_trials, sr_var_trials):
    """Bailey & Lopez de Prado (2014). r: per-period returns of the selected strategy; n_trials: N; sr_var_trials:
    variance of the per-period Sharpe ratios across all trials. Returns (DSR probability, SR0)."""
    r = np.asarray(pd.Series(r).dropna(), float); T = len(r)
    sr = r.mean() / r.std(ddof=1)
    g3 = stats.skew(r); g4 = stats.kurtosis(r, fisher=False)
    em = 0.5772156649
    N = max(int(n_trials), 2)
    sr0 = math.sqrt(max(sr_var_trials, 1e-12)) * ((1 - em) * stats.norm.ppf(1 - 1 / N) + em * stats.norm.ppf(1 - 1 / (N * math.e)))
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(max(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2, 1e-12))
    return float(stats.norm.cdf(z)), float(sr0)


def pbo_cscv(M: pd.DataFrame, S=16, max_splits=3000, seed=0):
    """Probability of backtest overfitting (Bailey, Borwein, Lopez de Prado, Zhu 2017). M: T x N strategy returns."""
    X = M.dropna(how="all").fillna(0).to_numpy(); T, N = X.shape
    blocks = np.array_split(np.arange(T), S)
    combos = list(itertools.combinations(range(S), S // 2))
    rng = np.random.default_rng(seed)
    if len(combos) > max_splits:
        combos = [combos[i] for i in rng.choice(len(combos), max_splits, replace=False)]
    lam = []
    for c in combos:
        tr = np.concatenate([blocks[i] for i in c]); te = np.concatenate([blocks[i] for i in range(S) if i not in c])
        sr_tr = X[tr].mean(0) / (X[tr].std(0) + 1e-12); sr_te = X[te].mean(0) / (X[te].std(0) + 1e-12)
        best = int(np.argmax(sr_tr))
        w = (stats.rankdata(sr_te)[best]) / (N + 1)
        lam.append(math.log(w / (1 - w)))
    lam = np.array(lam)
    return float((lam <= 0).mean()), float(np.median(lam))


def sharpe(x, per_year):
    x = pd.Series(x).dropna()
    return float(x.mean() / x.std() * math.sqrt(per_year)) if x.std() > 0 else 0.0


def max_dd(x):
    eq = (1 + pd.Series(x).fillna(0)).cumprod()
    return float((eq / eq.cummax() - 1).min())
