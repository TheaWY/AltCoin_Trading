"""B6 scoring inputs: features for arbitrary (code, hour, side) rows, built exactly like lossml_data.
Two row sets:
  ev  = B2 primaries in the OOS window (P1 short = pump fade, P1 long = pump follow, BH long = 30d breakout,
        D24 long = crash rebound), 24h cooldown per coin, net24 on hourly closes (same as the training label)
  pt  = the real-exit paper trades (Postgres paper_trades + paper_trades_archive), entry hour = last closed hour
Out: data/cache/lossml_{ev,pt}.parquet and data/cache/lossml_{ev,pt}_seq.npy"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lossml_data import FEE, SEQ, SIGNED, UNSIGNED, panel, slip  # noqa: E402

H_, D_ = 3600, 86400
OOS0, OOS1 = int(pd.Timestamp("2024-12-01").timestamp()), int(pd.Timestamp("2026-09-25").timestamp())
RESETS = {"manual_reset", "benchmark_baseline_reset", "strategy_reset", "risk_reset", "test_cleanup", "ledger_rebuild",
          "excluded"}


def cooldown(e):
    e = e.sort_values(["code", "ts"])
    keep, lc, lt = [], None, -10 ** 12
    for c, t in zip(e["code"], e["ts"]):
        if c != lc:
            lc, lt = c, -10 ** 12
        ok = t - lt >= D_
        keep.append(ok)
        if ok:
            lt = t
    return e[np.array(keep)]


def b2_events(P):
    U = P[(P["dv24"] >= 2e6) & (P["age_h"] >= 72) & (P["ts"] >= OOS0) & (P["ts"] <= OOS1)]
    out = []
    for name, m, side in (("pump_fade_S", U["ret_1h"] >= 0.10, -1), ("pump_follow_L", U["ret_1h"] >= 0.10, 1),
                          ("breakout30d_L", (U["dhi30"] > 0) & (U["ret_24h"] > 0.05), 1),
                          ("crash_rebound_L", U["ret_24h"] <= -0.25, 1)):
        e = cooldown(U[m][["code", "ts"]])
        out.append(e.assign(primary=name, side=side))
    return pd.concat(out, ignore_index=True)


def paper_trades():
    from dotenv import load_dotenv
    import psycopg
    load_dotenv(ROOT / ".env")
    con = psycopg.connect(os.environ["DATABASE_URL"])
    q = ("select '{t}' src, id, strategy, symbol, direction, entry_price, exit_price, quantity, pnl, fees, opened_at, "
         "closed_at, exit_reason from {t} where status = 'closed'")
    rows = []
    for t in ("paper_trades", "paper_trades_archive"):
        cur = con.execute(q.format(t=t))
        rows.append(pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description]))
    D = pd.concat(rows, ignore_index=True)
    D = D[~D["exit_reason"].isin(RESETS)].copy()
    D["code"] = D["symbol"].str.replace("/", "", regex=False).str.split(":").str[0]
    D["side"] = np.where(D["direction"].str.upper() == "LONG", 1, -1)
    D["ts"] = (D["opened_at"].astype("int64") // H_) * H_                 # last closed hourly bar at entry
    D["ret"] = D["side"] * (D["exit_price"].astype(float) / D["entry_price"].astype(float) - 1)
    D["notional"] = D["entry_price"].astype(float) * D["quantity"].astype(float).abs()
    D["net_pct"] = D["pnl"].astype(float) / D["notional"]
    D["loss"] = (D["pnl"].astype(float) < 0).astype(int)
    return D


def featurize(P, R):
    """R: code, ts, side (+ anything). Returns R with features, net24, and a seq array aligned to rows (NaN rows dropped)."""
    R = R.reset_index(drop=True).copy()
    U = P[(P["dv24"] >= 2e6) & (P["age_h"] >= 72) & (P["ts"].isin(set(R["ts"])))]
    g = U.groupby("ts")
    xs = pd.DataFrame({"code": U["code"], "ts": U["ts"], "xs_ret24": g["ret_24h"].rank(pct=True) - 0.5,
                       "xs_rv": g["rv24"].rank(pct=True), "xs_dv": g["dv24"].rank(pct=True)})
    feats, seqs, keep = [], [], []
    for code, rr in R.groupby("code"):
        gg = P[P["code"] == code].reset_index(drop=True)
        if gg.empty:
            continue
        ts = gg["ts"].to_numpy()
        pos = np.searchsorted(ts, rr["ts"].to_numpy())
        c = gg["c"].to_numpy(float)
        r1, lq = gg["ret_1h"].to_numpy(float), np.log1p(gg["qv"].to_numpy(float))
        tk, rg = gg["taker_1h"].to_numpy(float) - 0.5, gg["range_1h"].to_numpy(float)
        f24 = gg["fund24"].to_numpy(float)
        for (ri, row), i in zip(rr.iterrows(), pos):
            if i >= len(ts) or ts[i] != row["ts"] or i < SEQ - 1:
                continue
            s = slice(i - SEQ + 1, i + 1)
            seqs.append(np.stack([r1[s], lq[s] - np.nanmean(lq[s]), tk[s], rg[s]]).astype(np.float16))
            b = gg.iloc[i]
            j = i + 24
            fwd = c[j] / c[i] - 1 if j < len(ts) and ts[j] - ts[i] == 24 * H_ else np.nan
            fn = f24[j] * 3 if j < len(ts) and np.isfinite(f24[j]) else 0.0
            d = {"_r": ri}
            side = row["side"]
            for k in SIGNED:
                if k in ("tk1", "tk24", "xs_ret24"):
                    continue
                d[f"s_{k}"] = side * b[k]
            d["s_tk1"], d["s_tk24"] = side * (b["taker_1h"] - 0.5), side * (b["taker_24h"] - 0.5)
            for k in ("rv24", "rv_7d", "max_7d", "vsurge", "range_1h", "upwick_1h", "pumps_30d", "breadth_pump", "weekday"):
                d[k] = b[k]
            d["ldv"], d["lage"] = np.log1p(b["dv24"]), np.log1p(b["age_h"])
            d["hour_s"], d["hour_c"] = np.sin(2 * np.pi * b["hour"] / 24), np.cos(2 * np.pi * b["hour"] / 24)
            d["net24"] = side * fwd - 2 * (FEE + float(slip([b["dv24"]])[0])) - side * fn
            feats.append(d)
            keep.append(ri)
    F = pd.DataFrame(feats).set_index("_r")
    R = R.loc[keep].join(F)
    R = R.merge(xs, on=["code", "ts"], how="left")
    R["s_xs_ret24"] = R["side"] * R["xs_ret24"]
    for k in ("xs_rv", "xs_dv"):
        R[k] = R[k].fillna(0.5)
    R["s_xs_ret24"] = R["s_xs_ret24"].fillna(0.0)
    R["loss24"] = (R["net24"] < 0).astype(float).where(R["net24"].notna())
    return R, np.stack(seqs)


def main():
    P = panel()
    ev, ev_seq = featurize(P, b2_events(P))
    ev.to_parquet(ROOT / "data/cache/lossml_ev.parquet", index=False)
    np.save(ROOT / "data/cache/lossml_ev_seq.npy", ev_seq)
    print("events", ev.groupby("primary").size().to_dict(), flush=True)
    D = paper_trades()
    pt, pt_seq = featurize(P, D)
    pt.to_parquet(ROOT / "data/cache/lossml_pt.parquet", index=False)
    np.save(ROOT / "data/cache/lossml_pt_seq.npy", pt_seq)
    print("paper trades", len(D), "scored", len(pt), pt.groupby("strategy").size().to_dict())
    print("unscored:", sorted(set(D["code"]) - set(pt["code"]))[:20])


if __name__ == "__main__":
    main()
