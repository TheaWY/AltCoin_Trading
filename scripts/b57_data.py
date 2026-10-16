"""B57 data builder (prereg v8): daily panel rows for BTC, ETH + top-60 alts, ~80 features, targets h=1/7/28.
Output data/upbit_db/b57/rows.parquet, seq_weekly.npy (+ seq_index.parquet). Paper research only."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b54_sweep100 as S  # noqa: E402

OUT = S.B.ROOT / "data/upbit_db/b57"
IDX, CF, O, V = S.IDX, S.CF, S.O, S.V
MAJ = ["KRW-BTC", "KRW-ETH"]
START = pd.Timestamp("2018-04-01")
HS = (1, 7, 28)
SEQ = 60


def universe():
    rk = S._medv[S._alts].where(S._age[S._alts] >= 180).rank(axis=1, ascending=False, method="first")
    U = (rk <= 60).reindex(columns=CF.columns, fill_value=False).fillna(False)
    for m in MAJ:
        U[m] = S._age[m] >= 180
    return U


def feats(U):
    lc = np.log(CF); lr = lc.diff(); F = {}
    for n in (1, 2, 3, 5, 7, 14, 21, 28, 56, 90, 180):
        F[f"r{n}"] = lc - lc.shift(n)
    F["skip28_7"] = lc.shift(7) - lc.shift(28); F["skip90_28"] = lc.shift(28) - lc.shift(90)
    for n in (7, 30, 90, 180):
        F[f"rv{n}"] = lr.rolling(n, min_periods=max(n // 2, 5)).std()
    F["rv7_90"] = F["rv7"] / F["rv90"]; F["rv30_180"] = F["rv30"] / F["rv180"]
    F["upvol30"] = lr.clip(lower=0).rolling(30, min_periods=15).std(); F["dnvol30"] = lr.clip(upper=0).rolling(30, min_periods=15).std()
    F["skew30"] = lr.rolling(30, min_periods=20).skew(); F["kurt30"] = lr.rolling(30, min_periods=20).kurt()
    for n in (7, 30):
        F[f"max{n}"] = lr.rolling(n, min_periods=n // 2).max(); F[f"min{n}"] = lr.rolling(n, min_periods=n // 2).min()
    for n in (20, 50, 100, 200):
        F[f"dsma{n}"] = CF / CF.rolling(n, min_periods=n // 2).mean() - 1
    F["dhi365"] = CF / CF.rolling(365, min_periods=120).max() - 1; F["dlo365"] = CF / CF.rolling(365, min_periods=120).min() - 1
    F["dhi30"] = CF / CF.rolling(30, min_periods=20).max() - 1
    medv = V.rolling(30, min_periods=20).median()
    F["lmedv"] = np.log(medv.replace(0, np.nan)); F["v7_90"] = V.rolling(7, min_periods=5).median() / V.rolling(90, min_periods=45).median()
    F["amihud"] = (lr.abs() / V.replace(0, np.nan)).rolling(30, min_periods=15).mean() * 1e9
    btc = lr["KRW-BTC"]
    cov = lr.rolling(90, min_periods=60).cov(btc); var = btc.rolling(90, min_periods=60).var()
    F["beta90"] = cov.div(var, axis=0); F["corr90"] = lr.rolling(90, min_periods=60).corr(btc)
    F["idio90"] = (lr - F["beta90"].mul(btc, axis=0)).rolling(90, min_periods=60).std()
    F["ac1_30"] = lr.rolling(30, min_periods=20).corr(lr.shift(1))
    F["trendw"] = pd.DataFrame({m: S.trendw(CF[m]) for m in CF.columns})
    return F


RANKED = ["r7", "r28", "r90", "rv30", "dnvol30", "max30", "min30", "dhi365", "lmedv", "v7_90", "beta90", "trendw"]


def market(U):
    lc = np.log(CF); lr = lc.diff(); aidx = np.log(S.alt_index())
    ur = lr.where(U)
    return {"btc_r7": lc["KRW-BTC"] - lc["KRW-BTC"].shift(7), "btc_r28": lc["KRW-BTC"] - lc["KRW-BTC"].shift(28),
            "eth_r7": lc["KRW-ETH"] - lc["KRW-ETH"].shift(7), "btc_tw": S.trendw(CF["KRW-BTC"]),
            "btc_rv30": lr["KRW-BTC"].rolling(30, min_periods=20).std(), "alt_r7": aidx - aidx.shift(7),
            "alt_r28": aidx - aidx.shift(28), "alt_rv20": aidx.diff().rolling(20, min_periods=15).std(),
            "breadth": ((CF > CF.rolling(50, min_periods=50).mean()) & U).sum(axis=1) / U.sum(axis=1).replace(0, np.nan),
            "disp7": (lc - lc.shift(7)).where(U).std(axis=1), "mkt_r1": ur.mean(axis=1)}


def main():
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True)
    U = universe(); F = feats(U); M = market(U)
    cols = list(CF.columns); On = O.to_numpy()
    Fn = {k: v.reindex(index=IDX, columns=cols).to_numpy() for k, v in F.items()}
    Mn = {k: v.reindex(IDX).to_numpy() for k, v in M.items()}
    Un = U.reindex(index=IDX, columns=cols).fillna(False).to_numpy()
    lr = np.log(CF).diff().to_numpy(); lv = np.log(V.replace(0, np.nan)).diff().to_numpy()
    blr = lr[:, cols.index("KRW-BTC")]; alr = np.log(S.alt_index()).diff().to_numpy()
    rows, seqs, seq_keys = [], [], []
    for i, d in enumerate(IDX):
        if d < START:
            continue
        js = np.flatnonzero(Un[i])
        if len(js) < 10:
            continue
        df = pd.DataFrame({k: Fn[k][i, js] for k in Fn})
        for k in RANKED:
            df[f"rk_{k}"] = df[k].rank(pct=True)
        for k in Mn:
            df[k] = Mn[k][i]
        df["day"] = d; df["coin"] = [cols[j] for j in js]
        for h in HS:
            y = np.log(On[i + 1 + h, js] / On[i + 1, js]) if i + 1 + h < len(IDX) else np.full(len(js), np.nan)
            df[f"yr{h}"] = y; df[f"yd{h}"] = y - np.nanmean(y) if np.isfinite(y).any() else np.nan
        rows.append(df)
        if d.weekday() == 6:                                         # weekly decision days carry sequences
            for j in js:
                w = np.stack([lr[i - SEQ + 1:i + 1, j], lv[i - SEQ + 1:i + 1, j], blr[i - SEQ + 1:i + 1], alr[i - SEQ + 1:i + 1]], 1)
                seqs.append(np.nan_to_num((w - np.nanmean(w, 0)) / (np.nanstd(w, 0) + 1e-9)).astype(np.float32))
                seq_keys.append((d, cols[j]))
    D = pd.concat(rows, ignore_index=True)
    D.to_parquet(OUT / "rows.parquet", index=False)
    np.save(OUT / "seq_weekly.npy", np.stack(seqs))
    pd.DataFrame(seq_keys, columns=["day", "coin"]).to_parquet(OUT / "seq_index.parquet", index=False)
    print("rows", D.shape, "seq", len(seqs), f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
