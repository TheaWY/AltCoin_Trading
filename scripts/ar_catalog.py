"""Autonomous research catalogue: variables, conditioners (regime states) and methods, each with an A-PRIORI sign and a
source, so the engine can pre-register hypotheses without looking at the data. Signals are hourly T x N arrays built from
a Panel (research cache or live DB); conditioners are hourly T vectors. See research/lit/*.md for the sources.

sign: +1 = higher value -> higher next-day return (long high / short low); -1 = the reverse.
live: True = computable from the live DB (prices_1m, funding_rates, upbit_1h, bithumb_1h, metrics_5m) -> promotable to a
      forward paper test; False = research cache only (on-chain, depth): survivors are logged, not traded.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import b7_lib as L


def _roll(a, w, fn="mean", mp=None):
    return getattr(pd.DataFrame(a).rolling(w, min_periods=mp or max(2, w // 2)), fn)().to_numpy()


def _share(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > 0, a / b, np.nan)


def _onup(P, w, val):
    """NaN for coins without Korean volume in the window (not listed there), as in the B29-B33 construction."""
    return np.where(L.S(P["up_qv"], w) > 0, val, np.nan)


def _rank(A):
    return pd.DataFrame(A).rank(axis=1, pct=True).to_numpy() - 0.5


def _z(a, w=168):
    df = pd.DataFrame(a); m = df.shift(1).rolling(w, min_periods=w // 2).mean(); s = df.shift(1).rolling(w, min_periods=w // 2).std()
    return ((df - m) / s.replace(0, np.nan)).to_numpy()


# ----------------------------------------------------------------------------------------------- variables
# name: (sign, family, builder(P) -> hourly T x N, source, live)
# P keys: lc (log close), qv, tbq, n, h, l, f8, up_qv, bt_qv, up_lc, oi, ls_top, ls_global, taker, btc (index of BTC), beta
VARIABLES = {
    # price / momentum / reversal
    "rev_1d": (-1, "price", lambda P: P["lc"] - L.lag(P["lc"], 24), "Jegadeesh 1990; Liu-Tsyvinski-Wu 2022", True),
    "rev_3d": (-1, "price", lambda P: P["lc"] - L.lag(P["lc"], 72), "short-term reversal", True),
    "mom_3w": (+1, "price", lambda P: P["lc"] - L.lag(P["lc"], 504), "LTW 2022 CMOM", True),
    "mom_4w_skip1w": (+1, "price", lambda P: L.lag(P["lc"], 168) - L.lag(P["lc"], 672), "Jegadeesh-Titman skip-week", True),
    "max_1h_7d": (-1, "price", lambda P: L.MX(P["r1"], 168), "Bali-Cakici-Whitelaw 2011 lottery", True),
    "dist_from_30d_high": (+1, "price", lambda P: P["lc"] - _roll(P["lc"], 720, "max"), "George-Hwang 2004 52-week high", True),
    "rangepos_24h": (-1, "price", lambda P: _share(np.exp(P["lc"]) - _roll(P["l"], 24, "min"), _roll(P["h"], 24, "max") - _roll(P["l"], 24, "min")), "intraday range position (reversal)", True),
    # volatility / higher moments
    "rv_7d": (-1, "vol", lambda P: L.SD(P["r1"], 168), "Ang-Hodrick-Xing-Zhang 2006", True),
    "ivol_7d": (-1, "vol", lambda P: L.SD(P["r1"] - np.nan_to_num(P["beta"]) * P["r1"][:, [P["btc"]]], 168), "idiosyncratic vol", True),
    "rsj_7d": (-1, "vol", lambda P: _share(L.S(np.maximum(P["r1"], 0) ** 2, 168) - L.S(np.minimum(P["r1"], 0) ** 2, 168), L.S(P["r1"] ** 2, 168)), "Zhang-Zhao realised signed jump; Bollerslev-Li-Zhao 2020", True),
    "jump_share_7d": (-1, "vol", lambda P: _share(L.S(np.where(np.abs(P["r1"]) > 4 * L.SD(P["r1"], 168), P["r1"] ** 2, 0), 168), L.S(P["r1"] ** 2, 168)), "Lee-Wang 2025 JFQA jump variation (negative)", True),
    "skew_7d": (-1, "vol", lambda P: _roll(P["r1"], 168, "skew"), "Amaya et al. 2015", True),
    "vol_of_vol_7d": (-1, "vol", lambda P: _roll(L.SD(P["r1"], 24), 168, "std"), "Baltussen-van Bekkum-van der Grient 2018", True),
    "session_asia_var_share": (-1, "vol", lambda P: _share(L.S(np.where(((P["ts"] % 86400) // 3600 < 8)[:, None], P["r1"] ** 2, 0), 168), L.S(P["r1"] ** 2, 168)), "session variance decomposition (Asia-heavy = retail) [exploratory]", True),
    # volume / liquidity / attention
    "vol_surprise": (+1, "volume", lambda P: np.log(_share(L.S(P["qv"], 24) + 1, _roll(L.S(P["qv"], 24), 720) + 1)), "Gervais-Kaniel-Mingelgrin 2001", True),
    "amihud_7d": (+1, "volume", lambda P: _roll(_share(np.abs(P["r1"]), P["qv"]), 168), "Amihud 2002", True),
    "trades_per_dollar": (-1, "volume", lambda P: _share(L.S(P["n"], 24), L.S(P["qv"], 24)), "retail intensity [exploratory]", True),
    "avg_trade_size_z": (+1, "volume", lambda P: _z(_share(L.S(P["qv"], 24), L.S(P["n"], 24))), "informed-flow proxy (large trades) [exploratory]", True),
    "taker_share_24h": (+1, "flow", lambda P: _share(L.S(P["tbq"], 24), L.S(P["qv"], 24)) - 0.5, "Chordia-Subrahmanyam 2004 order imbalance", True),
    "taker_share_7d": (+1, "flow", lambda P: _share(L.S(P["tbq"], 168), L.S(P["qv"], 168)) - 0.5, "order-flow persistence", True),
    "taker_var_compression": (-1, "flow", lambda P: _share(_roll(_share(P["tbq"], P["qv"]) - 0.5, 24, "std"), _roll(_share(P["tbq"], P["qv"]) - 0.5, 168, "std")), "Garcia Seuma 2026 pre-cascade compression [weak]", True),
    # funding / open interest / positioning
    "funding_7d": (-1, "funding", lambda P: _roll(P["f8"], 168), "Schmeling-Schrimpf-Todorov 2023 carry crashes", True),
    "funding_dev": (-1, "funding", lambda P: P["f8"] - 0.0001, "deviation from the 0.01% anchor (BitMEX 2025)", True),
    "funding_z": (-1, "funding", lambda P: _z(P["f8"]), "funding surprise", True),
    "oi_chg_7d": (-1, "oi", lambda P: np.log(_share(P["oi"], L.lag(P["oi"], 168))), "leverage build-up", True),
    "oi_to_volume": (+1, "oi", lambda P: np.log(_share(P["oi"], L.S(P["qv"], 24) + 1)), "DISC1 survivor (predictive only)", True),
    "fund_x_oi": (-1, "oi", lambda P: _rank(_roll(P["f8"], 168)) + _rank(np.log(_share(P["oi"], L.lag(P["oi"], 168)))), "crowding interaction [exploratory]", True),
    "ls_top_minus_global": (+1, "positioning", lambda P: np.log(P["ls_top"]) - np.log(P["ls_global"]), "smart vs crowd positioning [exploratory]", True),
    "taker_ratio_24h": (+1, "positioning", lambda P: _roll(np.log(P["taker"]), 24), "taker buy/sell ratio", True),
    # Korea / venue
    "upbit_share_24h": (-1, "korea", lambda P: _onup(P, 24, _share(L.S(P["up_qv"], 24), L.S(P["qv"], 24))), "B29/B30/B31/B33 survivor (retail attention)", True),
    "upbit_share_7d": (-1, "korea", lambda P: _onup(P, 168, _share(L.S(P["up_qv"], 168), L.S(P["qv"], 168))), "same, slow", True),
    "upbit_share_chg": (-1, "korea", lambda P: _onup(P, 168, _share(L.S(P["up_qv"], 24), L.S(P["qv"], 24)) - _share(L.S(P["up_qv"], 168), L.S(P["qv"], 168))), "attention surge", True),
    "korea_share_24h": (-1, "korea", lambda P: np.where(L.S(P["up_qv"] + P["bt_qv"], 24) > 0, _share(L.S(P["up_qv"] + P["bt_qv"], 24), L.S(P["qv"], 24)), np.nan), "Upbit+Bithumb (B32 H4: holdout only)", True),
    "kimchi_prem_rel": (-1, "korea", lambda P: _roll(P["up_lc"] - P["lc"], 24) - np.nanmedian(_roll(P["up_lc"] - P["lc"], 24), 1, keepdims=True), "coin kimchi premium vs market median", True),
    "kimchi_prem_chg": (-1, "korea", lambda P: (P["up_lc"] - P["lc"]) - L.lag(P["up_lc"] - P["lc"], 168), "premium change [exploratory]", True),
    # cross-asset
    "gap_vs_btc_24h": (+1, "cross", lambda P: (P["lc"] - L.lag(P["lc"], 24)) - np.nan_to_num(P["beta"]) * (P["lc"] - L.lag(P["lc"], 24))[:, [P["btc"]]], "Hou 2007 lead-lag catch-up", True),
    "beta_30d": (-1, "cross", lambda P: P["beta"], "Frazzini-Pedersen 2014 BAB", True),
    "corr_btc_7d": (+1, "cross", lambda P: L.corr(P["r1"], P["r1"][:, [P["btc"]]], 168), "co-movement [exploratory]", True),
}

# ----------------------------------------------------------------------------------------------- conditioners (hourly T vectors)
# name: (builder(P) -> bool T vector 'state on', description/source). Layered hypothesis = signal effect stronger when state on.
CONDITIONERS = {
    "btc_30d_down": (lambda P: (P["lc"][:, P["btc"]] - L.lag(P["lc"][:, [P["btc"]]], 720)[:, 0]) < 0, "bear state"),
    "mkt_vol_high": (lambda P: (_roll(np.nanmean(np.where(P["U"], P["r1"] ** 2, np.nan), 1), 168) > _roll(np.nanmean(np.where(P["U"], P["r1"] ** 2, np.nan), 1), 720)).ravel(), "Nagel 2012 reversal in high vol"),
    "breadth_low": (lambda P: np.nanmean(np.where(P["U"], (P["lc"] - L.lag(P["lc"], 24)) > 0, np.nan), 1) < 0.4, "weak breadth"),
    "funding_crowded": (lambda P: (lambda f: f > np.nanquantile(f, 0.8))(_roll(np.nanmean(np.where(P["U"], P["f8"], np.nan), 1), 24).ravel()), "crowded longs: mean funding in its top quintile (STT 2023)"),
    "weekend": (lambda P: (((P["ts"] // 86400) + 3) % 7) >= 5, "weekend (thin liquidity)"),
    "korea_hot": (lambda P: _share(np.nansum(np.where(P["U"], L.S(P["up_qv"], 24), np.nan), 1), np.nansum(np.where(P["U"], L.S(P["qv"], 24), np.nan), 1)) > np.nanmedian(_share(np.nansum(np.where(P["U"], L.S(P["up_qv"], 24), np.nan), 1), np.nansum(np.where(P["U"], L.S(P["qv"], 24), np.nan), 1))), "Korean retail share above median (Stambaugh-Yu-Yuan sentiment transplant)"),
    "strategy_lost_7d": (None, "the base signal's own L/S lost over the last 7 days (factor momentum, Fieberg et al. 2023) - built per signal"),
}

# ----------------------------------------------------------------------------------------------- methods
METHODS = {
    "xs_sort": "quintile sort battery (B30.battery): NW sorts EW/VW, PT monotonicity, FM with controls, size double sort, LTW alpha, costs, 1h lag, weekly",
    "layered": "signal x state: FM slope in state on vs off; pre-registered claim = stronger when on",
    "factor_momentum": "hold the signal only after its own trailing 30-day L/S was positive (Fieberg-Liedtke-Metko-Zaremba 2023)",
    "ctrend_combo": "Lewellen/Fieberg combination: rolling 180d FM slopes x characteristics, no in-sample weights",
    "mfd_gate": "machine-forecast-disagreement gate (Chu-Shen-Zhu 2026): trade only coins where bootstrap ridge forecasts agree",
}

PASS_RULE = ("holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND "
             "in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; "
             "the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.")
