"""Batch B3: run the registered 2024 holdout tests once (research/batch_B3.yaml).

Needs: data/cache/k1m_2024 + funding_2024 (backfill), data/cache/b2_hourly_2024.parquet and
data/cache/b2_ds_pump_2024.npz (B2_ERA=2024 runs of b2_panel.py / b2_dataset.py).
"""

from __future__ import annotations

import os

os.environ["B2_ERA"] = "2024"

import math  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from b2_panel import OUT as PANEL24, load_funding, load_minutes  # noqa: E402
from b2_hypotheses import FEE, cooldown, events, sim_code, slip  # noqa: E402
from b2_notices import second_level, syms  # noqa: E402

OUTD = ROOT / "data/reports/b3"
LEDGER = ROOT / "research/trial_ledger.csv"
S24 = int(pd.Timestamp("2024-03-08").timestamp())
E24 = int(pd.Timestamp("2025-02-26").timestamp())
TR_END = int(pd.Timestamp("2025-12-01").timestamp())
VA_END = int(pd.Timestamp("2026-01-01").timestamp())


def boot_day(x, day, n=3000):
    df = pd.DataFrame({"x": x, "d": day})
    groups = [g["x"].to_numpy() for _, g in df.groupby("d")]
    if len(groups) < 3:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(3)
    m = np.array([np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))]).mean()
                  for _ in range(n)])
    p = min(1.0, 2 * min((m <= 0).mean(), (m >= 0).mean()))
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975)), p


# ------------------------------------------------------------------ rules

def rules() -> dict[str, pd.DataFrame]:
    P = pd.read_parquet(PANEL24)
    data_end = int(P["ts"].max())
    spec = {"B3_D24_L_24h_2024": ("D24", "L", 1440), "B3_D24_L_4h_2024": ("D24", "L", 240),
            "B3_P24_S_4h_2024": ("P24", "S", 240), "B3_NL_S_24h_2024": ("NL", "S", 1440),
            "B3_FU_L_24h_2024": ("FU", "L", 1440)}
    rows = []
    for hid, (fid, side, hold) in spec.items():
        e = cooldown(events(P, fid, side))
        rows.append(e[["code", "ts", "dv24"]].assign(hid=hid, side=side, hold=hold))
    T = pd.concat(rows, ignore_index=True)
    res = []
    with ProcessPoolExecutor(8) as ex:
        for r in ex.map(sim_code, [(c, g.to_dict("records"), data_end) for c, g in T.groupby("code")], chunksize=2):
            res += r
    X = pd.DataFrame(res)
    out = {hid: X[X["hid"] == hid][["ts", "net"]] for hid in spec}
    # N4: new perp short 72h
    nl = P[(P["listed_in_window"] == 1) & (P["age_h"] == 1)]
    n4 = []
    for r in nl.itertuples(index=False):
        d = load_minutes(r.code)
        if d is None:
            continue
        t0 = int(d.index[0])
        i = (int(r.ts) + 60 - t0) // 60
        j = i + 72 * 60
        if j >= len(d):
            continue
        o = d["o"].to_numpy()
        fu = load_funding(r.code)
        fs = float(fu[(fu["ts"] > t0 + i * 60) & (fu["ts"] <= t0 + j * 60)]["f"].sum())
        dv = float(d["qv"].iloc[max(0, i - 1440):i].sum()) * (1440 / max(1, min(i, 1440)))
        n4.append(dict(ts=int(r.ts), net=-(o[j] / o[i] - 1) - 2 * (FEE + float(slip(dv))) + fs))
    out["B3_N4_new_perp_short_72h_2024"] = pd.DataFrame(n4)
    return out


# ------------------------------------------------------------------ Upbit notices 2024

def upbit_2024() -> pd.DataFrame:
    s = requests.Session()
    rows, page = [], 1
    while page < 800:
        r = s.get("https://api-manager.upbit.com/api/v1/announcements",
                  params={"os": "web", "page": page, "per_page": 20, "category": "trade"}, timeout=30)
        ns = r.json().get("data", {}).get("notices", [])
        if not ns:
            break
        for n in ns:
            rows.append((n["id"], int(datetime.fromisoformat(n.get("first_listed_at") or n["listed_at"]).timestamp()),
                         n["title"]))
        if min(x[1] for x in rows[-len(ns):]) < S24 - 5 * 86400:
            break
        page += 1
        time.sleep(0.25)
    d = pd.DataFrame(rows, columns=["id", "ts", "title"]).drop_duplicates("id")
    return d[(d["ts"] >= S24) & (d["ts"] <= E24)]


def perp24(sym: str, at: int) -> str | None:
    for c in (f"{sym}USDT", f"1000{sym}USDT"):
        if (ROOT / "data/cache/k1m_2024" / f"{c}.parquet").exists():
            d = load_minutes(c)
            if d is not None and d.index[0] <= at - 86400 and d.index[-1] >= at + 3700:
                return c
    return None


def notices() -> dict[str, pd.DataFrame]:
    up = upbit_2024()
    evs = []
    for r in up.itertuples(index=False):
        t = r.title
        if ("신규 거래지원" in t or "디지털 자산 추가" in t) and "KRW" in t and "취소" not in t:
            hid, side = "B3_E1b_upbit_listing_2s_2024", 1
            ss = [x for x in re.findall(r"[A-Z][A-Z0-9]{1,11}", t) if x not in ("KRW", "BTC", "USDT")]
        elif "유의 종목 지정" in t and "해제" not in t and "연장" not in t:
            hid, side, ss = "B3_N1_upbit_warning_short_2024", -1, syms(t)
        elif "거래지원 종료" in t and "취소" not in t:
            hid, side, ss = "B3_N2_upbit_delist_short_2024", -1, syms(t)
        else:
            continue
        for s_ in dict.fromkeys(ss):
            c = perp24(s_, r.ts)
            if c:
                evs.append(dict(hid=hid, side=side, code=c, ts=r.ts, title=t))
    print("2024 notice events", pd.Series([e["hid"] for e in evs]).value_counts().to_dict(), flush=True)
    with ThreadPoolExecutor(6) as ex:
        res = [x for x in ex.map(second_level, evs) if x]
    X = pd.DataFrame(res)
    X.to_csv(OUTD / "notices_2024.csv", index=False)
    return {h: X[X["hid"] == h][["ts", "net"]] for h in
            ("B3_E1b_upbit_listing_2s_2024", "B3_N1_upbit_warning_short_2024", "B3_N2_upbit_delist_short_2024")}


# ------------------------------------------------------------------ models (pump, frozen, 2024)

def models() -> dict[str, pd.DataFrame]:
    subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "scripts/b3_tree.py")], check=True)
    r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "scripts/b3_torch.py")], check=True)
    t = np.load(ROOT / "data/cache/b3_tree_preds.npz")
    q = np.load(ROOT / "data/cache/b3_torch_preds.npz")
    h = np.load(ROOT / "data/cache/b2_ds_pump_2024.npz", allow_pickle=True)
    va = {"M1": t["M1_va"], "M2": t["M2_va"], "M4": q["M4_va"], "M7": q["M7_va"]}
    p24 = {"M1": t["M1_24"], "M2": t["M2_24"], "M4": q["M4_24"], "M7": q["M7_24"]}
    va["M8"] = (va["M2"] + va["M4"] + va["M7"]) / 3
    p24["M8"] = (p24["M2"] + p24["M4"] + p24["M7"]) / 3
    out = {}
    from sklearn.metrics import roc_auc_score
    y24 = (h["gross"] > 0).astype(int)
    for k in ("M7", "M8", "M1", "M2"):
        lo, hi = np.quantile(va[k], 0.3), np.quantile(va[k], 0.7)
        side = np.where(p24[k] >= hi, 1, np.where(p24[k] <= lo, -1, 0))
        sel = side != 0
        net = side[sel] * h["gross"][sel] - h["cost"][sel]
        out[f"B3_{k}_pump_2024"] = pd.DataFrame({"ts": h["ts"][sel], "net": net})
        print(k, "AUC 2024", round(roc_auc_score(y24, p24[k]), 4), "trades", int(sel.sum()), flush=True)
    return out


# ------------------------------------------------------------------ G1 frozen

def g1() -> dict[str, pd.DataFrame]:
    r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "scripts/b3_torch.py"), "--g1"], check=True)
    d = pd.read_parquet(ROOT / "data/cache/b3_g1_2024.parquet")
    return {"B3_G1_xs_mlp_2024": d}


def main() -> int:
    OUTD.mkdir(parents=True, exist_ok=True)
    led = pd.read_csv(LEDGER)
    if led["hypothesis"].astype(str).str.startswith("B3_").any():
        print("B3 already run; refusing")
        return 1
    res = {}
    res.update(rules())
    print("rules done", flush=True)
    res.update(models())
    print("models done", flush=True)
    res.update(g1())
    print("g1 done", flush=True)
    res.update(notices())
    rows = []
    for hid, d in res.items():
        if d is None or len(d) == 0:
            rows.append(dict(id=hid, n=0))
            continue
        lo, hi, p = boot_day(d["net"].to_numpy(), (d["ts"] // 86400).to_numpy())
        rows.append(dict(id=hid, n=len(d), mean=d["net"].mean(), median=d["net"].median(), hit=(d["net"] > 0).mean(),
                         ci_lo=lo, ci_hi=hi, p=p))
    S = pd.DataFrame(rows)
    ok = S["p"].notna()
    ps = S.loc[ok, "p"].to_numpy()
    o = np.argsort(ps)
    q = ps[o] * len(ps) / (np.arange(len(ps)) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    qq = np.empty_like(ps)
    qq[o] = np.minimum(q, 1)
    S.loc[ok, "q"] = qq
    S["pass"] = (S["mean"] > 0) & (S["ci_lo"] > 0) & (S["q"] < 0.10)
    S.to_csv(OUTD / "summary.csv", index=False)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    add = pd.DataFrame([dict(ts=now, run_id=f"B3-{int(time.time())}", hypothesis=r["id"], tier="confirmatory",
                             period="2024-03..2025-02", mean_daily_net=r.get("mean"), n_trades=r["n"],
                             note="per-trade mean") for r in rows])
    pd.concat([led, add], ignore_index=True).to_csv(LEDGER, index=False)
    print(S.round(4).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
