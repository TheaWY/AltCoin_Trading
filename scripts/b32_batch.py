"""B32 (registered 2026-10-01, BEFORE running). Six new cross-sectional hypotheses, a-priori signs, B30 battery
(holdout 2025-07-01..2026-09-23 decides; BHY over M=6; 1-hour-lag and size-double-sort checks inside the battery).

H1 CROWD      -(rank OI growth 7d + rank funding 7d): coins where open interest AND funding both surged = crowded longs
              (Schmeling-Schrimpf-Todorov 2023 crowding -> crashes; perp analogue)
H2 SECTORMOM  +sector return over 7 days excluding the coin itself (Moskowitz & Grinblatt 1999 industry momentum)
H3 CATCHUP    +(sector 1-day return - own 1-day return): laggards in a moving sector catch up (Hou 2007 lead-lag)
H4 KOREA2     -(Upbit + Bithumb) / Binance perp volume (combined Korea retail share; robustness of the B29-B31 survivor)
H5 ATTENTION  -trade-count surprise (24h trades / 30-day average) (Barber & Odean 2008 attention-driven buying)
H6 REVLIQ     -ret_1d x 1{24h volume below its 30-day average}: reversal where liquidity is thin (Nagel 2012)
Sector labels: CoinGecko categories (data/cache/cg_categories.json; current labels - small look-ahead in membership,
noted as a caveat). Output data/reports/b32/batch.{csv,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import aphx as A  # noqa: E402
import b7_lib as L  # noqa: E402
import b30_rigorous as B30  # noqa: E402

OUT = ROOT / "data/reports/b32"
D = 86400


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    d = B30.build_xs()
    ts, codes, X = L.data()
    i0 = np.flatnonzero((ts % D == 0) & (ts >= B30.START) & (ts < B30.END)); i0 = i0[i0 + 25 < len(ts)]
    lc = X["lc"]; f8 = X["f8"]; dv = np.nan_to_num(X["dv24"]); n = np.nan_to_num(X["n"])
    oi = np.asarray(B30.P("m_oi_usd"))
    oig = np.log(np.where(oi > 0, oi, np.nan) / np.where(L.lag(oi, 168) > 0, L.lag(oi, 168), np.nan))[i0]
    fund7 = B30.roll(f8, 168, "mean")[i0]
    rk = lambda a: pd.DataFrame(np.where(d["mask"], a, np.nan)).rank(axis=1, pct=True).to_numpy()  # noqa: E731
    sig = {"H1_CROWD": -(rk(oig) + rk(fund7))}
    cats = json.load(open(ROOT / "data/cache/cg_categories.json"))
    r7 = (lc - L.lag(lc, 168))[i0]; r1 = (lc - L.lag(lc, 24))[i0]
    members = {}
    for j, c in enumerate(codes):
        for k in cats.get(c, []):
            members.setdefault(k, []).append(j)
    members = {k: v for k, v in members.items() if len(v) >= 5}
    T, N = r7.shape
    s7 = np.full((T, N), np.nan); s1 = np.full((T, N), np.nan)
    for j, c in enumerate(codes):
        ks = [k for k in cats.get(c, []) if k in members]
        if not ks:
            continue
        a7, a1 = [], []
        for k in ks:
            others = [m for m in members[k] if m != j]
            M = d["mask"][:, others]
            a7.append(np.nanmean(np.where(M, r7[:, others], np.nan), 1)); a1.append(np.nanmean(np.where(M, r1[:, others], np.nan), 1))
        s7[:, j] = np.nanmean(np.stack(a7), 0); s1[:, j] = np.nanmean(np.stack(a1), 0)
    sig["H2_SECTORMOM"] = s7
    sig["H3_CATCHUP"] = s1 - r1
    upq = np.nan_to_num(np.asarray(B30.P("up_qv"))); btq = np.nan_to_num(np.asarray(B30.P("bt_qv"))); qv = np.nan_to_num(X["qv"])
    kq = pd.DataFrame(upq + btq).rolling(24, min_periods=12).sum().to_numpy(); pq = pd.DataFrame(qv).rolling(24, min_periods=12).sum().to_numpy()
    k2 = -(kq / np.maximum(pq, 1))[i0]
    sig["H4_KOREA2"] = np.where(kq[i0] > 0, k2, np.nan)
    n24 = L.S(n, 24); n30 = pd.DataFrame(n24).rolling(720, min_periods=240).mean().to_numpy()
    sig["H5_ATTENTION"] = -np.log((n24 + 1) / (n30 + 1))[i0]
    adv30 = pd.DataFrame(dv).rolling(720, min_periods=240).mean().to_numpy()
    thin = (dv < adv30)[i0]
    sig["H6_REVLIQ"] = np.where(thin, -r1, np.nan)
    F = A.ltw_factors(d["R"], d["size"], d["mom21"], d["mask"], mkt_w=d["adv30"])
    hold = d["day"] >= B30.HOLD
    rows = []
    for name, S in sig.items():
        for tag, sel in (("insample", ~hold), ("holdout", hold)):
            r, _ = B30.battery(name, S, d, F, sel); r["set"] = tag; rows.append(r)
            print(name, tag, f"FM t {r['fm_t']:+.2f} HML t {r['t_ew']:+.2f} alpha t {r['alpha_t']:+.2f} lag t {r['lag1h_t']:+.2f} dsort t {r['dsort_t']:+.2f} band {r['band_net_bp']:+.1f}", flush=True)
    Rt = pd.DataFrame(rows)
    H = Rt[Rt.set == "holdout"].copy()
    p1 = 1 - stats.norm.cdf(H.fm_t.fillna(-9).to_numpy())
    H["p_one"] = p1; H["bhy"] = A.bhy(p1)
    ins = Rt[Rt.set == "insample"].set_index("signal")
    H["insample_fm_t"] = H.signal.map(ins.fm_t)
    H["verdict"] = np.where((H.fm_t >= 2) & H.bhy & (H.insample_fm_t > 0) & (H.lag1h_t > 1.5) & (H.dsort_t > 1.5), "CONFIRMED",
                            np.where(H.fm_t >= 2, "promising", "no"))
    Rt.to_csv(OUT / "batch_all.csv", index=False); H.to_csv(OUT / "batch.csv", index=False)
    cols = ["signal", "insample_fm_t", "fm_t", "t_ew", "t_vw", "mr_p", "dsort_t", "alpha_t", "lag1h_t", "weekly_t", "net_bp", "band_net_bp", "breakeven_bp", "p_one", "bhy", "verdict"]
    Lm = ["# B32: six new hypotheses (a-priori signs; holdout 2025-07-01..2026-09-23; BHY over 6)", "",
          "CONFIRMED = holdout FM t >= 2 AND BHY AND same sign in-sample AND 1h-lag t > 1.5 AND size double-sort t > 1.5", "",
          "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in H[cols].itertuples(index=False):
        Lm.append("| " + " | ".join(f"{v:+.2f}" if isinstance(v, float) else str(v) for v in r) + " |")
    (OUT / "batch.md").write_text("\n".join(Lm) + "\n")
    print("\n".join(Lm))


if __name__ == "__main__":
    main()
