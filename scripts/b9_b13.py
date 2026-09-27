"""B9-B13 analyses exactly per research/batch_B9_B13.yaml (registered before this script ran).
  .venv/bin/python -W ignore scripts/b9_b13.py b9|b10|b11|b12|b13
Out: data/reports/b9_b13/<batch>.json"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import b7_lib as L  # noqa: E402
from b7_lib import M, S, lag, safe_div  # noqa: E402
from b8_study import aligned, boot_auc, pair_auc  # noqa: E402

C = ROOT / "data/cache"
OUT = ROOT / "data/reports/b9_b13"
H_, D_ = 3600, 86400
RNG = np.random.default_rng(21)


def period(ts):
    return np.where((ts >= L.DISC[0]) & (ts < L.DISC[1]), "disc", np.where((ts >= L.HOLD[0]) & (ts < L.HOLD[1]), "hold", "out"))


def bh(pvals, q=0.10):
    items = sorted(pvals.items(), key=lambda x: x[1])
    m, keep = len(items), set()
    for r, (k, p) in enumerate(items, 1):
        if p <= q * r / m:
            keep = {kk for kk, _ in items[:r]}
    return keep


def day_ci(x, t, reps=4000):
    d = pd.DataFrame({"x": x, "d": np.asarray(t) // D_}).dropna()
    g = d.groupby("d")["x"].agg(["sum", "count"])
    su, cn = g["sum"].to_numpy(), g["count"].to_numpy()
    b = np.array([su[k].sum() / cn[k].sum() for k in (RNG.integers(0, len(su), len(su)) for _ in range(reps))])
    return np.percentile(b, [2.5, 97.5]).tolist(), float((b <= 0).mean())


def code_index():
    ts, codes, X = L.data()
    idx = {c: j for j, c in enumerate(codes)}

    def find(base):
        base = base.upper()
        for pre, mult in (("", 1.0), ("1000", 1000.0), ("1000000", 1e6)):
            if f"{pre}{base}USDT" in idx:
                return idx[f"{pre}{base}USDT"], mult
        return None, 1.0
    return find


# ------------------------------------------------------------------ events with matched controls (pumps or dumps)
def events(kind="pump"):
    ts, codes, X = L.data()
    c = X["c"]
    T, N = c.shape
    ext = np.full((T, N), np.nan, np.float32)
    for k in range(1, 7):
        ext = (np.fmax if kind == "pump" else np.fmin)(ext, lag(c, -k))
    mv = ext / c - 1
    univ = (X["dv24"] >= 2e6) & (X["age"] >= 720) & np.isfinite(c)
    hit = univ & ((mv >= 0.20) if kind == "pump" else (mv <= -0.20))
    ev = []
    for j in range(N):
        last = -10 ** 9
        for i in np.flatnonzero(hit[:, j]):
            if i - last >= 72:
                ev.append((i, j))
                last = i
    ev = pd.DataFrame(ev, columns=["i", "j"])
    ev["ts"] = ts[ev["i"]]
    csum = np.cumsum(np.vstack([np.zeros((1, N)), hit]), 0)
    vol = L.VOL7()
    ctl = []
    for e in ev.itertuples():
        i = e.i
        a, b = max(0, i - 72), min(T, i + 73)
        ok = univ[i] & ((csum[b] - csum[a]) == 0) & np.isfinite(vol[i])
        ok[e.j] = False
        cand = np.flatnonzero(ok)
        if len(cand) < 5 or not np.isfinite(vol[i, e.j]):
            continue
        dq = np.quantile(X["dv24"][i][univ[i]], [1 / 3, 2 / 3])
        vq = np.quantile(vol[i][univ[i] & np.isfinite(vol[i])], [1 / 3, 2 / 3])
        same = cand[(np.digitize(X["dv24"][i][cand], dq) == np.digitize(X["dv24"][i, e.j], dq))
                    & (np.digitize(vol[i][cand], vq) == np.digitize(vol[i, e.j], vq))]
        for jj in RNG.choice(same, min(5, len(same)), replace=False) if len(same) else []:
            ctl.append((e.Index, i, jj))
    return ev, pd.DataFrame(ctl, columns=["ev", "i", "j"])


def case_control(F, kind, label):
    """B8_1 method: matched AUC, sign from discovery, holdout AUC with event-day cluster bootstrap."""
    ev, ctl = events(kind)
    per = period(ev["ts"].to_numpy())
    out = {}
    for k, f in F.items():
        case = np.array([f[i, j] for i, j in zip(ev["i"], ev["j"])], np.float64)
        cv = np.array([f[i, j] for i, j in zip(ctl["i"], ctl["j"])], np.float64)
        s = pair_auc(case, cv, ctl["ev"].to_numpy())
        r = {}
        for pp in ("disc", "hold"):
            ss = s[s.index.isin(np.flatnonzero(per == pp))]
            r[pp] = dict(n=int(len(ss)), auc=float(ss.mean()) if len(ss) else None)
        sign = 1 if (r["disc"]["auc"] or 0.5) >= 0.5 else -1
        ss = s[s.index.isin(np.flatnonzero(per == "hold"))]
        if len(ss) >= 20:
            sa = ss if sign == 1 else 1 - ss
            ci, p = boot_auc(sa, ev.loc[sa.index, "ts"].to_numpy() // D_)
            r["hold"].update(auc_signed=float(sa.mean()), ci=ci, p=p)
        r["sign"] = sign
        out[f"{label}:{k}"] = r
        print(label, kind, k, json.dumps(r, default=float), flush=True)
    return out


def finish(name, res, pkeys, extra_rule=None):
    ps = {k: res[k]["hold"].get("p", 1.0) for k in pkeys}
    keep = bh(ps)
    for k in pkeys:
        h = res[k]["hold"]
        res[k]["pass"] = bool(k in keep and h.get("ci", [0])[0] > 0.5 and (extra_rule(res[k]) if extra_rule else True))
    res["passed"] = [k for k in pkeys if res[k]["pass"]]
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(res, open(OUT / f"{name}.json", "w"), indent=1, default=float)
    print(name, "PASSED", res["passed"])


# ------------------------------------------------------------------ B9 token unlocks
def b9():
    ts, codes, X = L.data()
    find = code_index()
    from dotenv import load_dotenv
    import os
    import psycopg
    load_dotenv(ROOT / ".env")
    con = psycopg.connect(os.environ["DATABASE_URL"])
    cg2sym = {cid: s.replace("/USDT", "") for s, cid in con.execute("SELECT DISTINCT symbol, cg_id FROM cg_daily WHERE cg_id IS NOT NULL").fetchall()}
    rows = []
    for f in (C / "unlocks").glob("*.json"):
        if f.name == "protocols.json":
            continue
        d = json.load(open(f))
        base = cg2sym.get(d.get("gecko_id"))
        if not base:
            continue
        j, _ = find(base)
        if j is None:
            continue
        # circulating proxy: cumulative unlocked across documented sections, daily
        circ = {}
        for sec in (d.get("documentedData") or {}).get("data", []):
            for p in sec.get("data", []):
                circ[p["timestamp"]] = circ.get(p["timestamp"], 0.0) + float(p.get("unlocked") or 0)
        if not circ:
            continue
        cts = np.array(sorted(circ))
        cval = np.array([circ[t] for t in cts])
        for e in (d.get("metadata") or {}).get("events", []):
            if e.get("unlockType") != "cliff":
                continue
            t = int(e["timestamp"])
            k = np.searchsorted(cts, t - D_) - 1
            if k < 0 or cval[k] <= 0:
                continue
            rows.append(dict(j=j, code=codes[j], t=t, tokens=float(sum(e.get("noOfTokens") or [0])), circ=float(cval[k])))
    E = pd.DataFrame(rows)
    E = E.groupby(["j", "code", E["t"] // D_]).agg(t=("t", "min"), tokens=("tokens", "sum"), circ=("circ", "first")).reset_index(drop=False)
    E["size"] = E["tokens"] / E["circ"]
    E = E[(E["size"] >= 0.01) & (E["t"] >= L.DISC[0]) & (E["t"] < L.HOLD[1] - 3 * D_)].sort_values("t")
    E = E.groupby("j", group_keys=False).apply(lambda g: g[g["t"].diff().fillna(1e9) >= D_])
    c, f8 = X["c"], X["f8"]

    def trade(j, t):
        i0 = np.searchsorted(ts, t - 72 * H_)
        i1 = np.searchsorted(ts, t + 24 * H_)
        i2 = np.searchsorted(ts, t + 72 * H_)
        if i2 >= len(ts) or not (np.isfinite(c[i0, j]) and np.isfinite(c[i1, j])):
            return np.nan, np.nan
        g = c[i1, j] / c[i0, j] - 1
        fund = np.nansum(f8[i0 + 1:i1 + 1, j]) / 8
        cost = 2 * (L.FEE + float(L.slip(np.array([X["dv24"][i0, j]]))[0]))
        r72 = c[i2, j] / c[i0, j] - 1 if np.isfinite(c[i2, j]) else np.nan
        return -g - cost + fund, r72
    E[["net_short", "r_m72_p72"]] = [trade(r.j, r.t) for r in E.itertuples()]
    E = E.dropna(subset=["net_short"])
    E["per"] = period(E["t"].to_numpy())
    # placebo: same coins, random times >= 7d from any unlock of that coin, same trade
    plc = []
    for r in E.itertuples():
        for _ in range(3):
            t = int(RNG.uniform(L.DISC[0], L.HOLD[1] - 4 * D_))
            if np.min(np.abs(E.loc[E["j"] == r.j, "t"].to_numpy() - t)) < 7 * D_:
                continue
            n, _ = trade(r.j, t)
            if np.isfinite(n):
                plc.append(dict(j=r.j, t=t, net_short=n))
    P = pd.DataFrame(plc)
    P["per"] = period(P["t"].to_numpy())
    res = {"n_events": E["per"].value_counts().to_dict()}
    for pp in ("disc", "hold"):
        e, p_ = E[E["per"] == pp], P[P["per"] == pp]
        ci, pv = day_ci(e["net_short"].to_numpy(), e["t"].to_numpy())
        slope = np.polyfit(e["size"].clip(upper=0.5), e["r_m72_p72"].fillna(0), 1)[0] if len(e) > 5 else np.nan
        bs = []
        for _ in range(2000):
            k = RNG.integers(0, len(e), len(e))
            ee = e.iloc[k]
            bs.append(np.polyfit(ee["size"].clip(upper=0.5), ee["r_m72_p72"].fillna(0), 1)[0])
        diff = e["net_short"].mean() - p_["net_short"].mean()
        res[pp] = dict(n=int(len(e)), B9_1_short_net=float(e["net_short"].mean()), B9_1_ci=ci, B9_1_p=pv,
                       B9_2_slope=float(slope), B9_2_ci=np.percentile(bs, [2.5, 97.5]).tolist(),
                       B9_3_placebo_net=float(p_["net_short"].mean()), B9_3_diff=float(diff), n_placebo=int(len(p_)))
        print(pp, json.dumps(res[pp], default=float), flush=True)
    h = res["hold"]
    res["tier"] = "confirmatory" if min(res["n_events"].get("disc", 0), res["n_events"].get("hold", 0)) >= 60 else "exploratory"
    res["B9_1_pass"] = bool(h["B9_1_short_net"] > 0 and h["B9_1_ci"][0] > 0 and h["B9_3_diff"] > 0)
    res["B9_2_pass"] = bool(h["B9_2_slope"] < 0 and h["B9_2_ci"][1] < 0)
    OUT.mkdir(parents=True, exist_ok=True)
    E.to_csv(OUT / "b9_events.csv", index=False)
    json.dump(res, open(OUT / "b9.json", "w"), indent=1, default=float)
    print("B9", res["tier"], "B9_1", res["B9_1_pass"], "B9_2", res["B9_2_pass"])


# ------------------------------------------------------------------ B10 on-chain exchange flows
def b10():
    ts, codes, X = L.data()
    idx = {c: j for j, c in enumerate(codes)}
    fl = pd.concat([pd.read_parquet(p) for p in sorted((C / "onchain").glob("flows_*.parquet"))])
    fl = fl[fl["code"].isin(idx)]
    Min = np.zeros(X["c"].shape, np.float32)
    Mout = np.zeros(X["c"].shape, np.float32)
    have = np.zeros(X["c"].shape[1], bool)
    pos = np.searchsorted(ts, fl["ts"].to_numpy())
    ok = (pos < len(ts)) & (ts[np.minimum(pos, len(ts) - 1)] == fl["ts"].to_numpy())
    jj = fl["code"].map(idx).to_numpy()
    np.add.at(Min, (pos[ok], jj[ok]), fl["inflow"].to_numpy()[ok].astype(np.float32))
    np.add.at(Mout, (pos[ok], jj[ok]), fl["outflow"].to_numpy()[ok].astype(np.float32))
    have[np.unique(jj)] = True
    px = X["c"]
    inu, outu = Min * px, Mout * px
    dv = X["dv24"]
    mask = np.where(have[None, :], 1.0, np.nan)
    F = {"IN6": safe_div(S(np.nan_to_num(inu), 6), dv) * mask, "IN24": safe_div(S(np.nan_to_num(inu), 24), dv) * mask,
         "NET24": safe_div(S(np.nan_to_num(inu - outu), 24), dv) * mask}
    res = {"n_tokens": int(have.sum())}
    res.update(case_control(F, "pump", "pump"))
    res.update(case_control(F, "dump", "dump"))
    # B10_2 top-decile 24h inflow -> short 24h
    rows = L.eval_rows((L.DISC[0], L.HOLD[1]))
    tr = []
    c = X["c"]
    for i in rows:
        if i + 24 >= len(ts):
            break
        m = X["U"][i] & np.isfinite(F["IN24"][i]) & (F["IN24"][i] > 0)
        if m.sum() < 20:
            continue
        thr = np.nanquantile(F["IN24"][i][m], 0.9)
        for j in np.flatnonzero(m & (F["IN24"][i] >= thr)):
            g = c[i + 24, j] / c[i, j] - 1
            cost = 2 * (L.FEE + float(L.slip(np.array([dv[i, j]]))[0]))
            fund = np.nansum(X["f8"][i + 1:i + 25, j]) / 8
            tr.append((ts[i], -g - cost + fund))
    T2 = pd.DataFrame(tr, columns=["t", "net"])
    T2["per"] = period(T2["t"].to_numpy())
    for pp in ("disc", "hold"):
        e = T2[T2["per"] == pp]
        ci, pv = day_ci(e["net"].to_numpy(), e["t"].to_numpy())
        res[f"B10_2_{pp}"] = dict(n=int(len(e)), net=float(e["net"].mean()), ci=ci)
        print("B10_2", pp, res[f"B10_2_{pp}"], flush=True)
    res["B10_2_pass"] = bool(res["B10_2_hold"]["net"] > 0 and res["B10_2_hold"]["ci"][0] > 0)
    finish("b10", res, [k for k in res if ":" in k])


# ------------------------------------------------------------------ B11 Korean multi-venue + spot-led
def b11():
    ts, codes, X = L.data()
    find = code_index()
    idx = {c: j for j, c in enumerate(codes)}
    U = aligned(C / "upbit1h_hist", codes, ts, ["c", "value_krw"], find)
    B = aligned(C / "bithumb1h_hist", codes, ts, ["c", "value_krw"], find)
    Sp = aligned(C / "spot1h_hist", codes, ts, ["c", "qv", "tbq"],
                 lambda s: (idx.get(s) if s in idx else idx.get("1000" + s), 1.0 if s in idx else 1000.0))
    fx = pd.read_parquet(C / "upbit1h_hist/USDT.parquet").set_index("ts")["c"].reindex(ts).ffill(limit=6).to_numpy()[:, None]
    kr = np.nan_to_num(U["value_krw"]) + np.nan_to_num(B["value_krw"])
    has_kr = np.isfinite(U["c"]) | np.isfinite(B["c"])
    qv = np.nan_to_num(X["qv"])
    sq = np.nan_to_num(Sp["qv"])
    has_sp = np.isfinite(Sp["c"])
    share1 = safe_div(sq, sq + qv)
    basis = safe_div(X["c"], Sp["c"]) - 1
    basis[np.abs(basis) > 0.2] = np.nan
    bprem = safe_div(B["c"], X["c"] * fx) - 1
    bprem[np.abs(bprem) > 0.5] = np.nan
    F = {"K6_korea_combined_surge_6h": np.where(has_kr, np.log(safe_div(S(kr, 6) + 1, M(kr, 168) * 6 + 1)), np.nan),
         "K7_bithumb_premium": bprem,
         "S1_spot_share_6h": np.where(has_sp, safe_div(S(sq, 6), S(sq + qv, 6)) - safe_div(S(sq, 168), S(sq + qv, 168)), np.nan),
         "S2_basis_change_6h": basis - lag(basis, 6),
         "S3_spot_taker_imbalance_6h": np.where(has_sp, safe_div(S(2 * np.nan_to_num(Sp["tbq"]) - sq, 6), S(sq, 6)), np.nan)}
    res = case_control(F, "pump", "pump")
    # B11_2: +10% hourly pumps, spot-led (S1 top tercile, thresholds from discovery) -> long 24h
    c = X["c"]
    r1 = X["r1"]
    univ = (X["dv24"] >= 2e6) & (X["age"] >= 720)
    pe = []
    for j in range(c.shape[1]):
        last = -10 ** 9
        for i in np.flatnonzero(univ[:, j] & (np.expm1(r1[:, j]) >= 0.10)):
            if i - last >= 24 and i + 24 < len(ts) and np.isfinite(F["S1_spot_share_6h"][i, j]):
                cost = 2 * (L.FEE + float(L.slip(np.array([X["dv24"][i, j]]))[0]))
                fund = np.nansum(X["f8"][i + 1:i + 25, j]) / 8
                pe.append((ts[i], F["S1_spot_share_6h"][i, j], c[i + 24, j] / c[i, j] - 1 - cost - fund))
                last = i
    Pm = pd.DataFrame(pe, columns=["t", "s1", "net"])
    Pm["per"] = period(Pm["t"].to_numpy())
    q1, q2 = Pm.loc[Pm["per"] == "disc", "s1"].quantile([1 / 3, 2 / 3])
    for pp in ("disc", "hold"):
        e = Pm[Pm["per"] == pp]
        top, bot = e[e["s1"] >= q2], e[e["s1"] <= q1]
        ci, _ = day_ci(top["net"].to_numpy(), top["t"].to_numpy())
        res[f"B11_2_{pp}"] = dict(n_top=int(len(top)), top_net=float(top["net"].mean()), top_ci=ci,
                                  bottom_net=float(bot["net"].mean()), all_net=float(e["net"].mean()))
        print("B11_2", pp, res[f"B11_2_{pp}"], flush=True)
    res["B11_2_pass"] = bool(res["B11_2_hold"]["top_net"] > 0 and res["B11_2_hold"]["top_ci"][0] > 0)
    finish("b11", res, [k for k in res if ":" in k])


# ------------------------------------------------------------------ B12 listing announcements, 5 exchanges
TICK = re.compile(r"\(([A-Z0-9]{2,12})\)|\b([A-Z0-9]{2,12})USDT\b")
LIST_WORDS = re.compile(r"(will list|new listing|listing|list\b|추가|신규|launch)", re.I)
EXCL = re.compile(r"(delist|remove|suspend|종료|유의|warning|margin|earn|convert|loan|collateral|futures will launch)", re.I)


def b12():
    import os
    os.environ["B2_ERA"] = "all"
    import b2_panel as bp
    ts, codes, X = L.data()
    find = code_index()
    ann = []
    for f in (C / "notices").glob("*.parquet"):
        ex = f.stem.split("_")[0]
        d = pd.read_parquet(f)
        if "title" not in d or "ts" not in d:
            continue
        if "delist" in f.stem:
            continue
        for r in d.itertuples():
            ann.append((ex, int(r.ts), str(r.title)))
    from dotenv import load_dotenv
    import psycopg
    load_dotenv(ROOT / ".env")
    con = psycopg.connect(os.environ["DATABASE_URL"])
    for src, t, title in con.execute("SELECT source, ts, title FROM exchange_notices WHERE kind='listing'").fetchall():
        ann.append((src, int(t) // 1000 if int(t) > 1e11 else int(t), str(title)))
    rows = []
    for ex, t, title in ann:
        if not LIST_WORDS.search(title) or EXCL.search(title):
            continue
        for a, b in TICK.findall(title):
            base = a or b
            j, _ = find(base)
            if j is not None:
                rows.append((ex, t, base, j))
    A = pd.DataFrame(rows, columns=["ex", "t", "base", "j"]).drop_duplicates(["ex", "base"]).sort_values("t")   # first per exchange x coin
    A = A[(A["t"] >= L.DISC[0]) & (A["t"] < L.HOLD[1])]
    out = []
    cache = {}
    for r in A.itertuples():
        code = codes[r.j]
        if code not in cache:
            cache[code] = bp.load_minutes(code)
        d = cache[code]
        if d is None:
            continue
        m0 = (r.t // 60 + 1) * 60                         # next minute open after the announcement
        if m0 - int(d.index[0]) < 3 * D_:                 # must already trade as a Binance perp for >= 3 days
            continue
        o = d["o"]
        if m0 not in o.index:
            continue
        p0 = o.at[m0]
        i_h = np.searchsorted(ts, m0)
        dv = X["dv24"][max(i_h - 1, 0), r.j]
        cost = 2 * (L.FEE + float(L.slip(np.array([np.nan_to_num(dv)]))[0]))
        rec = dict(ex=r.ex, t=r.t, base=r.base)
        for h, mins in (("15m", 15), ("60m", 60), ("24h", 1440)):
            m1 = m0 + mins * 60
            rec[h] = o.at[m1] / p0 - 1 - cost if m1 in o.index else np.nan
        out.append(rec)
    R = pd.DataFrame(out)
    R["per"] = period(R["t"].to_numpy())
    res, ps = {"n": R.groupby(["ex", "per"]).size().astype(int).to_dict()}, {}
    res["n"] = {f"{k[0]}|{k[1]}": v for k, v in res["n"].items()}
    for ex in sorted(R["ex"].unique()):
        for h in ("15m", "60m", "24h"):
            key = f"{ex}:{h}"
            rr = {}
            for pp in ("disc", "hold"):
                e = R[(R["ex"] == ex) & (R["per"] == pp)].dropna(subset=[h])
                if len(e) < 10:
                    rr[pp] = dict(n=int(len(e)))
                    continue
                ci, pv = day_ci(e[h].to_numpy(), e["t"].to_numpy())
                rr[pp] = dict(n=int(len(e)), net=float(e[h].mean()), median=float(e[h].median()), ci=ci, p=pv)
            res[key] = rr
            ps[key] = rr["hold"].get("p", 1.0)
            print(key, json.dumps(rr, default=float), flush=True)
    keep = bh(ps)
    res["passed"] = [k for k in ps if k in keep and res[k]["hold"].get("net", -1) > 0 and res[k]["hold"]["ci"][0] > 0]
    # B12_2: listing on exchange B within 7 days after exchange A (second announcement)
    R = R.sort_values("t")
    R["prev_t"] = R.groupby("base")["t"].shift(1)
    sec = R[(R["t"] - R["prev_t"]) <= 7 * D_]
    res["B12_2_second_announcement"] = {h: dict(n=int(sec[h].notna().sum()), net=float(sec[h].mean())) for h in ("15m", "60m", "24h")}
    OUT.mkdir(parents=True, exist_ok=True)
    R.to_csv(OUT / "b12_events.csv", index=False)
    json.dump(res, open(OUT / "b12.json", "w"), indent=1, default=float)
    print("B12 PASSED", res["passed"], res["B12_2_second_announcement"])


# ------------------------------------------------------------------ B13 order-book depth precursors
def b13():
    ts, codes, X = L.data()
    idx = {c: j for j, c in enumerate(codes)}
    cols = ["bid_1", "ask_1", "imb_1", "imb_5"]
    A = aligned(C / "depth1h", codes, ts, cols, lambda s: (idx.get(s), 1))
    # 2026-03.. 5m bookdepth -> hourly
    for f in (C / "bookdepth").glob("*.parquet"):
        j = idx.get(f.stem)
        if j is None:
            continue
        d = pd.read_parquet(f)
        if not set(cols) <= set(d.columns):
            continue
        d["h"] = (d["ts"] // 3600 + 1) * 3600
        h = d.groupby("h")[cols].mean()
        pos = np.searchsorted(ts, h.index.to_numpy())
        ok = (pos < len(ts)) & (ts[np.minimum(pos, len(ts) - 1)] == h.index.to_numpy())
        for c_ in cols:
            A[c_][pos[ok], j] = h[c_].to_numpy(np.float32)[ok]
    F = {"ASK1_change_24h": np.log(safe_div(A["ask_1"], lag(A["ask_1"], 24))),
         "BID1_change_24h": np.log(safe_div(A["bid_1"], lag(A["bid_1"], 24))),
         "IMB1_6h": M(A["imb_1"], 6),
         "DEPTH1_to_volume": safe_div(A["bid_1"] + A["ask_1"], X["dv24"])}
    res = case_control(F, "pump", "pump")
    res.update(case_control(F, "dump", "dump"))
    res.update({f"secondary:{k}": v for k, v in case_control({"IMB5_6h": M(A["imb_5"], 6)}, "pump", "pump").items()})
    finish("b13", res, [k for k in res if ":" in k and not k.startswith("secondary")])


if __name__ == "__main__":
    {"b9": b9, "b10": b10, "b11": b11, "b12": b12, "b13": b13}[sys.argv[1]]()
