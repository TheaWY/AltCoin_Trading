"""B39 (2026-10-04): F2 pump-CNN loss audit. Which exit rules / filters cut F2's losses without killing its edge?

Frozen F2 ensemble (data/models/pump_cnn, trained 2025-03..11, thresholds set on 2025-12) is replayed on every +10%/1h
pump event in the B2 datasets. Out-of-sample eras: 2024 (2024-03..2025-02, before training) and 2026 (2026-01..09-21,
after validation). In-sample 2025-03..12 is reported for reference only and never used to choose anything.
For every traded event the 1-minute path from entry (open of T+60s) to the 4h exit is loaded from data/cache/k1m*.
Variants: stops (intrabar on highs/lows, filled at the stop or the gapped open; or on closes, filled next open), take
profits, trailing stops, shorter holds, and entry filters on pre-signal features. Each variant is compared with the
baseline on the SAME trades (paired, day-clustered bootstrap of the difference). A variant counts as robust only if it
improves mean net in BOTH out-of-sample eras with the 2026 paired 95% CI above 0 and does not cut the mean by trading
less. Costs: the dataset round trip (2x(fee+slippage)) plus one extra slippage leg for stop fills.
Output: research/b39_f2_loss_audit.md, data/cache/b39_f2_trades.parquet. Paper research only.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from b2_models import CNN2D  # noqa: E402

MOD = ROOT / "data/models/pump_cnn"
TAB = ["ret_1h", "ret_2h", "ret_4h", "ret_24h", "ret_7d", "ret_28d", "rv24", "rv_7d", "max_7d", "ldv", "taker_1h",
       "taker_24h", "vsurge", "dhi30", "dlo30", "range_1h", "upwick_1h", "pumps_30d", "lage", "fund24",
       "btc_ret_1h", "btc_ret_24h", "breadth_pump", "hour_s", "hour_c", "weekday"]
KDIRS = [ROOT / "data/cache" / k for k in ("k1m_2024", "k1m_oot", "k1m")]
H = 240


def era(ts):
    t = pd.Timestamp(int(ts), unit="s")
    if t < pd.Timestamp("2025-03-01"):
        return "2024"
    if t < pd.Timestamp("2026-01-01"):
        return "insample"
    return "2026"


def score():
    meta = json.loads((MOD / "meta.json").read_text())
    ms = []
    for s in meta["seeds"]:
        m = CNN2D(); m.load_state_dict(torch.load(MOD / f"seed{s}.pt", map_location="cpu")); m.eval(); ms.append(m)
    rows = []
    for f in ("b2_ds_pump_2024.npz", "b2_ds_pump.npz"):
        d = np.load(ROOT / "data/cache" / f, allow_pickle=True)
        img = torch.tensor(d["img"].astype(np.float32)[:, None] / 255.0)
        p = np.zeros(len(img))
        with torch.no_grad():
            for i in range(0, len(img), 512):
                p[i:i + 512] = np.mean([torch.sigmoid(m(img[i:i + 512])).squeeze(-1).numpy() for m in ms], axis=0)
        df = pd.DataFrame(d["tab"], columns=TAB)
        df["ts"], df["code"], df["gross_long"], df["cost"], df["p"] = d["ts"].astype(int), d["code"], d["gross"], d["cost"], p
        rows.append(df)
    e = pd.concat(rows, ignore_index=True).drop_duplicates(["code", "ts"])
    e["side"] = np.where(e["p"] >= meta["hi"], 1, np.where(e["p"] <= meta["lo"], -1, 0))
    e["margin"] = np.where(e["side"] > 0, e["p"] - meta["hi"], meta["lo"] - e["p"])
    e["era"] = e["ts"].map(era)
    return e[e["side"] != 0].reset_index(drop=True), meta


def load_min(code):
    parts = [pd.read_parquet(d / f"{code}.parquet", columns=["ts", "o", "h", "l", "c"]) for d in KDIRS if (d / f"{code}.parquet").exists()]
    if not parts:
        return None
    return pd.concat(parts).drop_duplicates("ts").sort_values("ts").set_index("ts")


def paths(e):
    """per trade: 241x4 array of o,h,l,c from entry minute (T+60) to exit minute (T+60+4h)."""
    out = {}
    for code, g in e.groupby("code"):
        d = load_min(code)
        if d is None:
            continue
        for i, r in g.iterrows():
            idx = np.arange(r.ts + 60, r.ts + 60 + (H + 1) * 60, 60)
            w = d.reindex(idx)
            if w["o"].isna().iloc[[0, -1]].any():
                continue
            w["c"] = w["c"].ffill(); w["o"] = w["o"].fillna(w["c"]); w["h"] = w["h"].fillna(w["c"]); w["l"] = w["l"].fillna(w["c"])
            out[i] = w[["o", "h", "l", "c"]].to_numpy(float)
    return out


def run_exit(P, side, cost, rule):
    """return net for one trade under an exit rule. P: (241,4) o,h,l,c; entry = P[0,0]."""
    e0 = P[0, 0]
    fav = (P[:, 1] if side > 0 else P[:, 2]) / e0 - 1          # best intrabar move in our favour (raw)
    adv = (P[:, 2] if side > 0 else P[:, 1]) / e0 - 1          # worst intrabar move against us (raw)
    rc = side * (P[:, 3] / e0 - 1)                              # close return in our direction
    ro = side * (P[:, 0] / e0 - 1)                              # open return in our direction
    fav, adv = side * fav, side * adv
    kind, a, b = rule
    hold = H
    extra = 0.0
    if kind == "hold":
        hold = a
        return ro[hold] - cost
    if kind == "base":
        return ro[H] - cost
    for k in range(H):
        if kind in ("sl_hl", "sl_tp") and adv[k] <= -a:
            g = min(ro[k], -a) if k > 0 else -a              # gapped open fills worse
            return g - cost - cost / 2
        if kind == "sl_tp" and fav[k] >= b:
            g = max(ro[k], b) if k > 0 else b
            return g - cost
        if kind == "sl_c" and rc[k] <= -a:
            return ro[k + 1] - cost - cost / 2
        if kind == "tp" and fav[k] >= a:
            g = max(ro[k], a) if k > 0 else a
            return g - cost
        if kind == "trail":                                     # arm after +a on closes, exit when close gives back b from peak
            pk = rc[:k + 1].max()
            if pk >= a and rc[k] <= pk - b:
                return ro[k + 1] - cost - cost / 2
    return ro[H] - cost


RULES = [("base", 0, 0)] + [("hold", h, 0) for h in (60, 120)] \
    + [("sl_hl", x, 0) for x in (0.03, 0.05, 0.08, 0.12, 0.20)] + [("sl_c", x, 0) for x in (0.05, 0.08, 0.12)] \
    + [("tp", x, 0) for x in (0.08, 0.15, 0.25)] + [("trail", a, b) for a, b in ((0.05, 0.05), (0.10, 0.07), (0.15, 0.10))] \
    + [("sl_tp", 0.08, 0.15), ("sl_tp", 0.12, 0.25)]


def name(r):
    k, a, b = r
    return {"base": "baseline 4h", "hold": f"hold {a // 60}h", "sl_hl": f"stop {a:.0%} intrabar", "sl_c": f"stop {a:.0%} on close",
            "tp": f"take-profit {a:.0%}", "trail": f"trail arm {a:.0%} give-back {b:.0%}", "sl_tp": f"stop {a:.0%} + TP {b:.0%}"}[k]


def boot_diff(d, day, n=2000, seed=7):
    rng = np.random.default_rng(seed)
    groups = [d[day == u] for u in np.unique(day)]
    if len(groups) < 5:
        return np.nan, np.nan
    m = [np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]).mean() for _ in range(n)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def stats(x):
    x = np.asarray(x)
    eq = np.cumsum(x); dd = float((np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max())
    return dict(n=len(x), mean=x.mean(), median=np.median(x), win=(x > 0).mean(), p10=np.percentile(x, 10), worst=x.min(), maxdd=dd)


def main():
    t0 = time.time()
    e, meta = score()
    print("traded events", e.groupby("era").size().to_dict(), flush=True)
    P = paths(e)
    e = e.loc[sorted(P)].copy()
    for r in RULES:
        e[name(r)] = [run_exit(P[i], int(e.at[i, "side"]), float(e.at[i, "cost"]), r) for i in e.index]
    e["day"] = e["ts"] // 86400
    e.drop(columns=[]).to_parquet(ROOT / "data/cache/b39_f2_trades.parquet")
    L = ["# B39 F2 pump-CNN loss audit (2026-10-04)", "",
         f"Frozen ensemble replayed on {len(e)} traded +10%/1h pump events with 1-minute exit paths. Out-of-sample eras: "
         "2024 (before training) and 2026-01..09 (after validation); in-sample 2025-03..12 shown for reference only. "
         "Net = per-trade return after round-trip fee+slippage (stop fills pay one extra slippage leg).", ""]
    L += ["## Baseline by era", "", "| era | n | mean | median | win | p10 | worst | maxDD (sum of nets) |", "|---|---|---|---|---|---|---|---|"]
    for er in ("2024", "insample", "2026"):
        s = stats(e.loc[e.era == er, "baseline 4h"])
        L.append(f"| {er} | {s['n']} | {s['mean']:+.2%} | {s['median']:+.2%} | {s['win']:.0%} | {s['p10']:+.1%} | {s['worst']:+.1%} | {s['maxdd']:.2f} |")
    L += ["", "## Exit rules (paired vs baseline, same trades)", "",
          "| rule | 2024 mean | 2024 Δ | 2026 mean | 2026 Δ | 2026 Δ 95% CI | 2026 p10 | 2026 worst | 2026 maxDD | robust |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    verdict = {}
    for r in RULES:
        nm = name(r)
        row = [nm]
        ok = True
        for er in ("2024", "2026"):
            x = e.loc[e.era == er]
            d = (x[nm] - x["baseline 4h"]).to_numpy()
            row += [f"{x[nm].mean():+.2%}", f"{d.mean():+.2%}"]
            ok &= d.mean() > 0
            if er == "2026":
                lo, hi = boot_diff(d, x["day"].to_numpy())
                row += [f"[{lo:+.2%}, {hi:+.2%}]" if lo == lo else "n/a"]
                ok &= (lo == lo) and lo > 0
                s = stats(x[nm]); row += [f"{s['p10']:+.1%}", f"{s['worst']:+.1%}", f"{s['maxdd']:.2f}"]
        verdict[nm] = ok and nm != "baseline 4h"
        row.append("YES" if verdict[nm] else "")
        L.append("| " + " | ".join(row) + " |")
    # entry filters: keep only trades with the feature on one side of its 2024-era median (median fixed on 2024, applied to 2026)
    L += ["", "## Entry filters (baseline exit; cut point = 2024-era median, then applied unchanged to 2026)", "",
          "| filter | 2024 kept n | 2024 kept mean | 2024 dropped mean | 2026 kept n | 2026 kept mean | 2026 dropped mean | 2026 kept-minus-all 95% CI | robust |",
          "|---|---|---|---|---|---|---|---|---|"]
    feats = [("side", None), ("margin", "hi"), ("ldv", "hi"), ("ldv", "lo"), ("ret_1h", "lo"), ("ret_1h", "hi"), ("rv24", "lo"),
             ("pumps_30d", "lo"), ("btc_ret_1h", "lo"), ("btc_ret_1h", "hi"), ("upwick_1h", "lo"), ("fund24", "lo"),
             ("dhi30", "lo"), ("taker_1h", "hi")]
    for f, keep in feats:
        x24, x26 = e[e.era == "2024"], e[e.era == "2026"]
        if f == "side":
            masks = [("longs only", x24.side > 0, x26.side > 0), ("shorts only", x24.side < 0, x26.side < 0)]
        else:
            c = float(x24[f].median())
            m24 = x24[f] >= c if keep == "hi" else x24[f] < c
            m26 = x26[f] >= c if keep == "hi" else x26[f] < c
            masks = [(f"{f} {'>=' if keep == 'hi' else '<'} {c:.3g}", m24, m26)]
        for nm, m24, m26 in masks:
            b24, b26 = x24["baseline 4h"], x26["baseline 4h"]
            # kept-minus-all: bootstrap of (mean of kept) - (mean of all) via day clusters
            rng = np.random.default_rng(11); days = np.unique(x26["day"]); g = {u: (b26[x26.day == u].to_numpy(), m26[x26.day == u].to_numpy()) for u in days}
            bs = []
            for _ in range(2000):
                pick = rng.choice(days, len(days))
                v = np.concatenate([g[u][0] for u in pick]); k = np.concatenate([g[u][1] for u in pick])
                if k.sum() > 0:
                    bs.append(v[k].mean() - v.mean())
            lo, hi = np.percentile(bs, [2.5, 97.5])
            ok = (b24[m24].mean() > b24.mean()) and lo > 0
            L.append(f"| {nm} | {int(m24.sum())} | {b24[m24].mean():+.2%} | {b24[~m24].mean():+.2%} | {int(m26.sum())} | {b26[m26].mean():+.2%} | "
                     f"{b26[~m26].mean():+.2%} | [{lo:+.2%}, {hi:+.2%}] | {'YES' if ok else ''} |")
    rob = [k for k, v in verdict.items() if v]
    L += ["", f"Robust exit rules (improve both OOS eras, 2026 paired CI > 0): {', '.join(rob) if rob else 'none'}.",
          f"Runtime {time.time() - t0:.0f}s."]
    (ROOT / "research/b39_f2_loss_audit.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
