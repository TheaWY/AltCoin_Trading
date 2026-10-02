"""Autonomous research engine (2026-10-02). Pre-registers, tests, promotes and reports hypotheses without a human in the loop.

  propose   enumerate untested hypotheses from ar_catalog (variables x methods x conditioners), a-priori signs, priority
            = literature strength, then "near misses" and follow-ups from the daily autopsy -> research/autoresearch/queue.jsonl
  run       pop up to --n hypotheses (time budget --minutes): build the signal on the research panel, run the academic battery
            (b30_rigorous.battery: in-sample 2024-05..2025-06 / HOLDOUT 2025-07..2026-09), layered/factor-momentum variants,
            running BHY over everything ever tested -> results.jsonl + research/trial_ledger.csv
  promote   survivors of PASS_RULE that are live-computable -> research/forward_auto.yaml + table fa_<id> (ar_live.py trades
            them daily, paper only) -> main_book admission candidates automatically
  status    research/autoresearch/status.md (hourly summary: what was tested, what survived, forward progress, what is next and why)
  autopsy   daily 12:00 KST: main-book attribution since BOOK_START, forward-test scoreboard, hypotheses derived from the
            losses -> research/autoresearch/autopsy_<date>.md and new queue items
  cycle     propose -> run -> promote -> status (launchd com.altcoin.autoresearch, hourly)
Everything is paper. LIVE_TRADING is never read or changed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import aphx as A  # noqa: E402
import ar_catalog as C  # noqa: E402
import b7_lib as L  # noqa: E402

AR = ROOT / "research/autoresearch"
QUEUE, RESULTS, STATUS = AR / "queue.jsonl", AR / "results.jsonl", AR / "status.md"
FWD_AUTO = ROOT / "research/forward_auto.yaml"
LEDGER = ROOT / "research/trial_ledger.csv"
D = 86400
_PANEL = {}


def jl_read(p):
    return [json.loads(x) for x in open(p)] if p.exists() else []


def jl_append(p, rows):
    with open(p, "a") as f:
        for r in rows:
            f.write(json.dumps(r, default=float) + "\n")


def hid(spec):
    return "AR" + hashlib.sha1(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:8]


# ------------------------------------------------------------------ research panel
def panel():
    if _PANEL:
        return _PANEL
    import b30_rigorous as B30
    ts, codes, X = L.data()
    from b29_battery import crypto_mask
    cm = crypto_mask(codes); bi = codes.index("BTCUSDT")
    P = {"ts": ts, "codes": codes, "btc": bi, "lc": X["lc"].astype(np.float64), "r1": X["r1"].astype(np.float64), "qv": np.nan_to_num(X["qv"]).astype(np.float64),
         "tbq": np.nan_to_num(X["tbq"]).astype(np.float64), "n": np.nan_to_num(X["n"]).astype(np.float64), "h": X["h"], "l": X["l"], "f8": X["f8"].astype(np.float64),
         "beta": X["beta"].astype(np.float64)}
    dv = np.nan_to_num(X["dv24"])
    P["U"] = (dv >= 5e6) & (X["age"] >= 720) & cm[None, :] & np.isfinite(X["c"])
    for k, src in (("up_qv", "up_qv"), ("bt_qv", "bt_qv"), ("up_lc", "up_lc"), ("oi", "m_oi_usd"), ("ls_top", "m_ls_top_pos"), ("ls_global", "m_ls_global"), ("taker", "m_taker_ratio")):
        a = np.asarray(B30.P(src)).astype(np.float64)
        P[k] = np.nan_to_num(a) if k in ("up_qv", "bt_qv") else a
    d = B30.build_xs()
    P["d"] = d; P["i0"] = np.flatnonzero((ts % D == 0) & (ts >= B30.START) & (ts < B30.END))
    P["i0"] = P["i0"][P["i0"] + 25 < len(ts)]
    P["F"] = A.ltw_factors(d["R"], d["size"], d["mom21"], d["mask"], mkt_w=d["adv30"])
    P["hold"] = d["day"] >= B30.HOLD
    _PANEL.update(P)
    return P


def build_signal(P, var):
    sign, fam, fn, src, live = C.VARIABLES[var]
    with np.errstate(all="ignore"):
        S = fn(P)
    return (sign * np.asarray(S, dtype=np.float64))[P["i0"]]


def build_state(P, cond, S_hourly_daily=None):
    if cond == "strategy_lost_7d":
        return None                                              # handled in run_one via the base signal's L/S
    fn, _ = C.CONDITIONERS[cond]
    with np.errstate(all="ignore"):
        v = np.asarray(fn(P)).astype(bool)
    return v[P["i0"]]


# ------------------------------------------------------------------ propose
def propose(max_new=200):
    tested = {r["id"] for r in jl_read(RESULTS)} | {r["id"] for r in jl_read(QUEUE)}
    specs = []
    prio = {"korea": 0, "funding": 1, "vol": 1, "flow": 2, "oi": 2, "price": 3, "volume": 3, "cross": 3, "positioning": 4}
    for var, (sign, fam, fn, src, live) in C.VARIABLES.items():
        specs.append({"method": "xs_sort", "var": var, "cond": None, "prio": prio.get(fam, 5)})
    for var, (sign, fam, fn, src, live) in C.VARIABLES.items():
        specs.append({"method": "factor_momentum", "var": var, "cond": None, "prio": prio.get(fam, 5) + 3})
        for cond in C.CONDITIONERS:
            specs.append({"method": "layered", "var": var, "cond": cond, "prio": prio.get(fam, 5) + 5 + (2 if cond == "strategy_lost_7d" else 0)})
    specs.append({"method": "ctrend_combo", "var": "ALL", "cond": None, "prio": 2})
    specs.append({"method": "mfd_gate", "var": "ALL", "cond": None, "prio": 4})
    # D-series: market DIRECTION (ar_direction.DVARS x horizon). Highest priority: direction of the whole market is the
    # stated goal; cond = horizon so the hypothesis id and the a-priori sign are fixed before any data is seen.
    import ar_direction as DR
    for var in list(DR.DVARS) + ["ALL"]:
        for hz in DR.HORIZONS:
            specs.append({"method": "ts_direction", "var": var, "cond": hz, "prio": -1})
    new = []
    for s in sorted(specs, key=lambda s: s["prio"]):
        s["id"] = hid({k: s[k] for k in ("method", "var", "cond")})
        if s["id"] in tested:
            continue
        s["registered"] = time.strftime("%F %T")
        if s["method"] == "ts_direction":
            s["sign"] = DR.DVARS[s["var"]][0] if s["var"] in DR.DVARS else +1
            s["source"] = DR.DVARS[s["var"]][3] if s["var"] in DR.DVARS else "recursive ridge over all D-series variables (market as a whole)"
        else:
            s["sign"] = C.VARIABLES[s["var"]][0] if s["var"] in C.VARIABLES else +1
            s["source"] = C.VARIABLES[s["var"]][3] if s["var"] in C.VARIABLES else C.METHODS[s["method"]]
        new.append(s); tested.add(s["id"])
        if len(new) >= max_new:
            break
    jl_append(QUEUE, new)
    return new


# ------------------------------------------------------------------ run
def run_one(spec, P):
    import b30_rigorous as B30
    d, F, hold = P["d"], P["F"], P["hold"]
    out = {"id": spec["id"], "method": spec["method"], "var": spec["var"], "cond": spec.get("cond"), "sign": spec.get("sign", 1), "run": time.strftime("%F %T")}
    if spec["method"] == "ts_direction":
        import ar_direction as DR
        out.update(DR.run(P, spec["var"], spec["cond"], B30.START, B30.HOLD * 86400, B30.END))
        return out
    if spec["method"] in ("xs_sort", "layered", "factor_momentum"):
        S = build_signal(P, spec["var"])
        if spec["method"] == "factor_momentum":
            # trailing 30-day L/S of the signal (rows t-31..t-2) must be positive; otherwise the signal is zeroed (flat)
            hml = A.long_short(A.sort_portfolios(A.winsor_rows(np.where(d["mask"], S, np.nan)), d["R"], n=5, mask=d["mask"])["EW"]).to_numpy()
            trail = pd.Series(hml).shift(2).rolling(30, min_periods=15).sum().to_numpy()
            S = np.where((trail > 0)[:, None], S, np.nan)
        if spec["method"] == "layered":
            cond = spec["cond"]
            if cond == "strategy_lost_7d":
                hml = A.long_short(A.sort_portfolios(A.winsor_rows(np.where(d["mask"], S, np.nan)), d["R"], n=5, mask=d["mask"])["EW"]).to_numpy()
                state = (pd.Series(hml).shift(2).rolling(7, min_periods=4).sum() < 0).to_numpy()
            else:
                state = build_state(P, cond)
            # claim: effect stronger when state on. Battery on the ON subsample; interaction t from FM on-off difference.
            G_on, _ = A.fama_macbeth(d["R"], {"sig": A.winsor_rows(np.where(d["mask"], S, np.nan))}, mask=d["mask"] & state[:, None])
            G_off, _ = A.fama_macbeth(d["R"], {"sig": A.winsor_rows(np.where(d["mask"], S, np.nan))}, mask=d["mask"] & ~state[:, None])
            # interaction: difference of the two slope means, Welch-style with NW variances (on/off days never overlap)
            for tag, sel in (("insample", ~hold), ("holdout", hold)):
                a, b = G_on["sig"].to_numpy()[sel], G_off["sig"].to_numpy()[sel]
                ma, ta = A.nw_t(a); mb, tb = A.nw_t(b)
                sa, sb = (abs(ma / ta) if ta not in (0, np.nan) and np.isfinite(ta) and ta != 0 else np.nan), (abs(mb / tb) if np.isfinite(tb) and tb != 0 else np.nan)
                out[f"{tag}_interaction_t"] = float((ma - mb) / np.sqrt(sa ** 2 + sb ** 2)) if np.isfinite(sa) and np.isfinite(sb) else np.nan
                out[f"{tag}_on_t"] = A.nw_t(G_on["sig"].to_numpy()[sel])[1]; out[f"{tag}_off_t"] = A.nw_t(G_off["sig"].to_numpy()[sel])[1]
                out[f"{tag}_on_days"] = int(np.isfinite(G_on["sig"].to_numpy()[sel]).sum())
            out["state_on_share"] = float(np.nanmean(state[hold]))
            if np.nansum(state[hold]) < 30 or np.nansum(state[~hold]) < 30:
                out["error"] = f"state on only {int(np.nansum(state[hold]))} holdout days / {int(np.nansum(state[~hold]))} in-sample days: untestable"
                return out
            S = np.where(state[:, None], S, np.nan)
        for tag, sel in (("insample", ~hold), ("holdout", hold)):
            r, _ = B30.battery(spec["id"], S, d, F, sel)
            for k in ("fm_t", "t_ew", "t_vw", "mr_p", "dsort_t", "alpha_t", "lag1h_t", "weekly_t", "net_bp", "band_net_bp", "breakeven_bp", "turnover", "hml_bp", "sharpe"):
                out[f"{tag}_{k}"] = r.get(k)
    elif spec["method"] == "ctrend_combo":
        import b30_rigorous as B30r
        ch = {v: build_signal(P, v) for v in C.VARIABLES if C.VARIABLES[v][4]}
        S = B30r.lewellen(ch, d["R"], d["mask"], window=180)
        for tag, sel in (("insample", (~hold) & (np.arange(len(hold)) >= 185)), ("holdout", hold)):
            r, _ = B30.battery(spec["id"], S, d, F, sel)
            for k in ("fm_t", "t_ew", "t_vw", "alpha_t", "lag1h_t", "dsort_t", "net_bp", "band_net_bp", "hml_bp"):
                out[f"{tag}_{k}"] = r.get(k)
    elif spec["method"] == "mfd_gate":
        ch = {v: build_signal(P, v) for v in C.VARIABLES if C.VARIABLES[v][4]}
        names = list(ch); T, N = d["R"].shape
        Z = np.stack([pd.DataFrame(np.where(d["mask"], ch[k], np.nan)).rank(axis=1, pct=True).to_numpy() - 0.5 for k in names], -1)
        rng = np.random.default_rng(7); preds = []
        # 20 ridge models on random 50% feature subsets, fitted on the rolling previous 180 days (no in-sample weights)
        for b in range(20):
            cols = rng.choice(len(names), max(3, len(names) // 2), replace=False)
            Pb = np.full((T, N), np.nan)
            for t in range(185, T):
                tr = slice(t - 182, t - 2)
                Xtr = np.nan_to_num(Z[tr][:, :, cols]).reshape(-1, len(cols)); ytr = d["R"][tr].reshape(-1)
                ok = np.isfinite(ytr) & d["mask"][tr].reshape(-1)
                if ok.sum() < 500:
                    continue
                Xm = Xtr[ok]; w = np.linalg.solve(Xm.T @ Xm + 50.0 * np.eye(len(cols)), Xm.T @ ytr[ok])
                Pb[t] = np.nan_to_num(Z[t][:, cols]) @ w
            preds.append(Pb)
        Pm = np.nanmean(np.stack(preds), 0); Sd = np.nanstd(np.stack(preds), 0)
        agree = Sd <= np.nanquantile(np.where(d["mask"], Sd, np.nan), 0.5, axis=1, keepdims=True)
        S_gate = np.where(agree, Pm, np.nan); S_all = Pm; S_mfd = -Sd
        for nm, S in (("gated", S_gate), ("ungated", S_all), ("mfd_short_disagreement", S_mfd)):
            for tag, sel in (("insample", (~hold) & (np.arange(T) >= 185)), ("holdout", hold)):
                r, _ = B30.battery(spec["id"], S, d, F, sel)
                for k in ("fm_t", "t_ew", "alpha_t", "net_bp", "band_net_bp", "hml_bp"):
                    out[f"{nm}_{tag}_{k}"] = r.get(k)
        out["holdout_fm_t"] = out["gated_holdout_fm_t"]; out["insample_fm_t"] = out["gated_insample_fm_t"]
        for k in ("lag1h_t", "dsort_t", "band_net_bp"):
            out[f"holdout_{k}"] = out.get(f"gated_holdout_{k}", np.nan)
    return out


def verdict(out, bhy_flag):
    if out.get("method") == "ts_direction":
        import ar_direction as DR
        ok = DR.verdict(out); ok["bhy"] = bool(bhy_flag)
        return ok, all(ok.values())
    ok = {"holdout_t2": bool((out.get("holdout_fm_t") or -9) >= 2), "bhy": bool(bhy_flag), "insample_sign": bool((out.get("insample_fm_t") or -9) > 0),
          "lag": bool((out.get("holdout_lag1h_t") or -9) > 1.5), "dsort": bool((out.get("holdout_dsort_t") or -9) > 1.5), "band_net": bool((out.get("holdout_band_net_bp") or -9) > 0)}
    if out.get("method") == "layered":
        ok["interaction"] = bool((out.get("holdout_interaction_t") or -9) >= 2)
    return ok, all(ok.values())


def rebhy():
    rows = jl_read(RESULTS)
    p = np.array([1 - stats.norm.cdf(r.get("holdout_fm_t") if r.get("holdout_fm_t") is not None and np.isfinite(r.get("holdout_fm_t")) else -9) for r in rows])
    flags = A.bhy(p) if len(p) else np.array([], bool)
    for r, f in zip(rows, flags):
        r["bhy"] = bool(f); r["checks"], r["pass"] = verdict(r, f)
    with open(RESULTS, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=float) + "\n")
    return rows


def run(n=10, minutes=45):
    t0 = time.time()
    q = jl_read(QUEUE); done = {r["id"] for r in jl_read(RESULTS)}
    todo = sorted([s for s in q if s["id"] not in done], key=lambda s: s.get("prio", 5))[:n]   # priority first, then registration order
    if not todo:
        return []
    P = panel()
    out = []
    for spec in todo:
        if time.time() - t0 > minutes * 60:
            break
        try:
            r = run_one(spec, P)
        except Exception:  # noqa: BLE001
            r = {"id": spec["id"], "method": spec["method"], "var": spec["var"], "cond": spec.get("cond"), "error": traceback.format_exc()[-800:], "run": time.strftime("%F %T")}
        r["source"] = spec.get("source"); r["sign"] = spec.get("sign")
        jl_append(RESULTS, [r]); out.append(r)
        print(spec["id"], spec["method"], spec["var"], spec.get("cond"), f"holdout FM t {r.get('holdout_fm_t', float('nan')):+.2f}" if "error" not in r else "ERROR", flush=True)
    rows = rebhy()
    by = {r["id"]: r for r in rows}
    with open(LEDGER, "a") as f:
        for r in out:
            rr = by.get(r["id"], r)
            note = ("ERROR " + rr["error"][-120:].replace('"', "'").replace("\n", " ")) if "error" in rr else \
                f"in FM t {rr.get('insample_fm_t', float('nan')):+.2f}; holdout FM t {rr.get('holdout_fm_t', float('nan')):+.2f} alpha t {rr.get('holdout_alpha_t', float('nan')):+.2f} lag t {rr.get('holdout_lag1h_t', float('nan')):+.2f} band {rr.get('holdout_band_net_bp', float('nan')):+.1f}bp; BHY {rr.get('bhy')}; pass {rr.get('pass')}"
            f.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S")},{rr["id"]},"{rr["method"]} {rr["var"]}{(" x " + rr["cond"]) if rr.get("cond") else ""} (a-priori sign {rr.get("sign")}; {str(rr.get("source", "")).replace(chr(34), "")})",autonomous,in 2024-05..2025-06 / holdout 2025-07..2026-09,,,,"{note}"\n')
    return out


# ------------------------------------------------------------------ promote
def promote():
    rows = rebhy()
    fa = yaml.safe_load(open(FWD_AUTO)) if FWD_AUTO.exists() else {}
    fa = fa or {}
    new = []
    for r in rows:
        if not r.get("pass") or r["id"] in fa:
            continue
        var = r["var"]
        if r["method"] == "ts_direction":
            # a market-direction survivor is a beta/leverage overlay on the whole book, not a L/S book: logged here, and
            # wired into the main book as a gross-exposure tilt only after it has a forward record (ar_live direction table)
            fa[r["id"]] = {"status": "survivor_direction", "spec": {k: r[k] for k in ("method", "var", "cond", "sign")}, "registered": time.strftime("%F"),
                           "evidence": {k: r.get(k) for k in ("insample_fm_t", "holdout_fm_t", "holdout_pt_hit", "holdout_pt_p", "holdout_auc", "holdout_auc_lo", "holdout_cw_p", "holdout_sharpe_net", "holdout_hm_sharpe", "holdout_ct_gain")}}
            continue
        if var not in C.VARIABLES or not C.VARIABLES[var][4] or r["method"] not in ("xs_sort", "factor_momentum", "layered"):
            fa[r["id"]] = {"status": "survivor_not_live", "spec": {k: r[k] for k in ("method", "var", "cond", "sign")}, "registered": time.strftime("%F")}
            continue
        fa[r["id"]] = {"status": "forward", "spec": {k: r[k] for k in ("method", "var", "cond", "sign")}, "registered": time.strftime("%F"),
                       "table": f"fa_{r['id'].lower()}", "construction": "daily 00:05 UTC, band L/S enter 20% keep 30%, EW, gross 1.0, costs fee+slip, funding",
                       "pass": ">= 60 daily books AND day-net bootstrap 95% CI > 0", "kill": "60 books with mean net <= 0",
                       "evidence": {k: r.get(k) for k in ("insample_fm_t", "holdout_fm_t", "holdout_alpha_t", "holdout_lag1h_t", "holdout_dsort_t", "holdout_band_net_bp")}}
        new.append(r["id"])
    yaml.safe_dump(fa, open(FWD_AUTO, "w"), sort_keys=False)
    return new


# ------------------------------------------------------------------ status
def forward_scoreboard():
    from src.data.storage import get_storage
    from oi_drop_short_paper import q
    st = get_storage()
    rows = []
    specs = {"F1 Upbit notice": ("upbit_notice_paper", "net60", "detect_ts", 15, "trade"), "F2 pump CNN": ("pump_cnn_paper", "net", "ts_signal", 300, "trade"),
             "F3 crash rebound": ("crash_rebound_paper", "net", "ts_signal", 30, "trade"), "F4 spot-led": ("spot_led_paper", "net", "ts_signal", 100, "trade"),
             "F5 unlock short": ("unlock_short_paper", "net", "ts_open", 60, "trade"), "F7 Upbit share weekly": ("f7_vshare_paper", "net", "ts_signal", 12, "book"),
             "F8 late-session": ("f8_latesession_paper", "net", "day", 120, "event"), "F9 Upbit-listing fade": ("f9_listing_fade_paper", "net", "entry_ts", 30, "event")}
    fa = yaml.safe_load(open(FWD_AUTO)) if FWD_AUTO.exists() else {}
    for k, v in (fa or {}).items():
        if v.get("status") == "forward":
            specs[f"{k} {v['spec']['var']}"] = (v["table"], "net", "ts_signal", 60, "book")
    for name, (tab, ncol, tcol, need, unit) in specs.items():
        try:
            d = q(st, f"SELECT {tcol} AS t, {ncol} AS net FROM {tab} WHERE status LIKE 'closed%%' AND {ncol} IS NOT NULL")
        except Exception:  # noqa: BLE001
            rows.append({"test": name, "closed": 0, "need": need, "mean_net_pct": None, "ci_lo_pct": None}); continue
        if d is None or not len(d):
            rows.append({"test": name, "closed": 0, "need": need, "mean_net_pct": None, "ci_lo_pct": None}); continue
        if unit == "book":
            d = d.groupby("t", as_index=False)["net"].sum()
        x = d["net"].to_numpy(); rng = np.random.default_rng(1)
        lo = float(np.percentile([x[rng.integers(0, len(x), len(x))].mean() for _ in range(1000)], 2.5)) if len(x) > 5 else None
        rows.append({"test": name, "closed": int(len(d)), "need": need, "mean_net_pct": float(x.mean() * 100), "ci_lo_pct": lo * 100 if lo is not None else None})
    return rows


def status():
    rows = jl_read(RESULTS); q = jl_read(QUEUE)
    done = {r["id"] for r in rows}; pending = [s for s in q if s["id"] not in done]
    ok = [r for r in rows if "error" not in r]
    passed = [r for r in ok if r.get("pass")]
    near = sorted([r for r in ok if (r.get("holdout_fm_t") or -9) >= 1.5 and not r.get("pass")], key=lambda r: -(r.get("holdout_fm_t") or 0))[:8]
    def _age(r):
        try:
            return time.time() - time.mktime(time.strptime(r.get("run", ""), "%Y-%m-%d %H:%M:%S"))
        except Exception:  # noqa: BLE001
            return 1e9
    recent = [r for r in rows if _age(r) <= 3700][-40:]
    fa = yaml.safe_load(open(FWD_AUTO)) if FWD_AUTO.exists() else {}
    fam_counts = pd.Series([C.VARIABLES.get(r["var"], (0, "method"))[1] for r in ok]).value_counts().to_dict() if ok else {}
    nxt = pd.Series([s["method"] + ("/" + s["cond"] if s.get("cond") else "") for s in pending[:30]]).value_counts().to_dict() if pending else {}
    Lm = [f"# Autonomous research status - {time.strftime('%Y-%m-%d %H:%M KST')}", "",
          f"Hypotheses: {len(rows)} tested ({len(rows) - len(ok)} errors), {len(passed)} passed the full rule, {len(pending)} queued. "
          f"Running BHY over all {len(ok)} holdout p-values. Families tested: {fam_counts}", "",
          "## Last hour", "", "| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda v: "–" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:+.2f}"  # noqa: E731
    for r in recent:
        if "error" in r:
            Lm.append(f"| {r['id']} | {r['method']} | {r['var']} | {r.get('cond') or ''} | ERROR | | | | | | |"); continue
        Lm.append(f"| {r['id']} | {r['method']} | {r['var']} | {r.get('cond') or ''} | {f(r.get('insample_fm_t'))} | {f(r.get('holdout_fm_t'))} | {f(r.get('holdout_alpha_t'))} | {f(r.get('holdout_lag1h_t'))} | {f(r.get('holdout_band_net_bp'))} | {r.get('bhy')} | {r.get('pass')} |")
    dr = [r for r in ok if r.get("method") == "ts_direction"]
    if dr:
        Lm += ["", "## D-series: market direction (sign of the EW market return over the next h; holdout)", "",
               f"{len(dr)} (variable x horizon) tested; a direction pass needs slope t >= 2, BHY, in-sample sign, PT p < .05, AUC CI low > .5, Clark-West p < .05, utility gain > 0 and Sharpe > hist-mean timing.", "",
               "| id | variable | h | in t | hold t | PT hit | PT p | AUC [lo] | R2os | CW p | SR net / HM | CT gain | checks ok |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        g = lambda v, s="{:.3f}": "–" if v is None or (isinstance(v, float) and not np.isfinite(v)) else s.format(v)  # noqa: E731
        for r in sorted(dr, key=lambda r: -(r.get("holdout_fm_t") or -9))[:40]:
            Lm.append(f"| {r['id']} | {r['var']} | {r.get('cond')} | {f(r.get('insample_fm_t'))} | {f(r.get('holdout_fm_t'))} | {g(r.get('holdout_pt_hit'))} | {g(r.get('holdout_pt_p'))} | {g(r.get('holdout_auc'))} [{g(r.get('holdout_auc_lo'))}] | {g(r.get('holdout_r2os'), '{:+.4f}')} | {g(r.get('holdout_cw_p'))} | {f(r.get('holdout_sharpe_net'))} / {f(r.get('holdout_hm_sharpe'))} | {g(r.get('holdout_ct_gain'), '{:+.3f}')} | {sum((r.get('checks') or {}).values())}/{len(r.get('checks') or {})} |")
    Lm += ["", "## Survivors (full rule)", ""] + ([f"- {r['id']} {r['method']} {r['var']}{' x ' + r['cond'] if r.get('cond') else ''}: holdout FM t {f(r.get('holdout_fm_t'))}, alpha t {f(r.get('holdout_alpha_t'))}, band net {f(r.get('holdout_band_net_bp'))} bp/day -> {fa.get(r['id'], {}).get('status', 'pending promotion')}" for r in passed] or ["- none yet"])
    Lm += ["", "## Near misses (holdout FM t >= 1.5, failed a check)", ""] + ([f"- {r['id']} {r['method']} {r['var']}{' x ' + r['cond'] if r.get('cond') else ''}: holdout t {f(r.get('holdout_fm_t'))}; failed {[k for k, v in (r.get('checks') or {}).items() if not v]}" for r in near] or ["- none"])
    Lm += ["", "## Forward paper tests (clean evidence)", "", "| test | closed | needed | mean net % | CI low % |", "|---|---|---|---|---|"]
    try:
        for r in forward_scoreboard():
            Lm.append(f"| {r['test']} | {r['closed']} | {r['need']} | {f(r['mean_net_pct'])} | {f(r['ci_lo_pct'])} |")
    except Exception as e:  # noqa: BLE001
        Lm.append(f"| scoreboard error | {type(e).__name__} | | | |")
    Lm += ["", "## Next (queue head) and why", "", f"- next 30 queued by method/state: {nxt}",
           "- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal "
           "(Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).",
           f"- promotion rule: {C.PASS_RULE}", "",
           "## Rules in force", "", "- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides",
           "- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests", ""]
    STATUS.write_text("\n".join(Lm))
    return "\n".join(Lm)


# ------------------------------------------------------------------ autopsy
def autopsy():
    from src.data.storage import get_storage
    from oi_drop_short_paper import q
    from main_book import BOOK_START
    st = get_storage(); now = int(time.time())
    T = q(st, "SELECT id, symbol, direction, strategy, entry_price, exit_price, quantity, pnl, fees, opened_at, closed_at, exit_reason, status FROM paper_trades WHERE opened_at >= ?", (BOOK_START,))
    eq = q(st, "SELECT book, timestamp, equity FROM benchmark_equity WHERE book <> 'random' AND timestamp >= ?", (BOOK_START,))
    date = time.strftime("%Y-%m-%d")
    Lm = [f"# Portfolio autopsy {date} 12:00 KST (main paper book since {time.strftime('%F', time.gmtime(BOOK_START))})", ""]
    new_specs = []
    if T is None or not len(T):
        Lm.append("No trades in the main book since the reset.")
    else:
        T["pnl"] = T.pnl.astype(float); T["notional"] = (T.entry_price * T.quantity).astype(float); T["ret"] = T.pnl / T.notional
        cl = T[T.status == "closed"]
        Lm += [f"Closed trades {len(cl)}, open {int((T.status == 'open').sum())}, realised P&L {cl.pnl.sum():+.2f} USDT, fees {cl.fees.fillna(0).sum():.2f}", "",
               "## By strategy", "", "| strategy | trades | P&L | mean % | hit | worst |", "|---|---|---|---|---|---|"]
        for s, g in cl.groupby("strategy"):
            Lm.append(f"| {s} | {len(g)} | {g.pnl.sum():+.2f} | {g.ret.mean() * 100:+.2f} | {(g.pnl > 0).mean():.0%} | {g.pnl.min():+.2f} |")
        Lm += ["", "## By exit reason", ""] + [f"- {k}: {len(g)} trades, {g.pnl.sum():+.2f}" for k, g in cl.groupby("exit_reason")]
        Lm += ["", "## Worst 5 trades", ""] + [f"- {r.symbol} {r.direction} {r.strategy} {r.pnl:+.2f} ({r.ret * 100:+.1f}%) {r.exit_reason}" for r in cl.sort_values("pnl").head(5).itertuples()]
        # diagnostics -> hypotheses
        hr = pd.to_datetime(cl.opened_at, unit="s").dt.hour
        by_h = cl.groupby((hr + 9) % 24 // 8).ret.mean()
        Lm += ["", "## Diagnostics", "", f"- mean return by KST session (0=09-17h, 1=17-01h, 2=01-09h): {by_h.round(4).to_dict()}",
               f"- long vs short mean %: {cl.groupby('direction').ret.mean().mul(100).round(2).to_dict()}"]
        if len(cl) >= 10:
            worst_dir = cl.groupby("direction").ret.mean().idxmin()
            new_specs.append({"method": "layered", "var": "upbit_share_24h", "cond": "btc_30d_down", "note": f"autopsy {date}: {worst_dir} side lost; test the survivor under bear state"})
            if (cl.exit_reason.fillna("").str.contains("stop")).mean() > 0.3:
                new_specs.append({"method": "layered", "var": "rv_7d", "cond": "mkt_vol_high", "note": f"autopsy {date}: stops dominate exits; test low-vol tilt in high-vol state"})
    if eq is not None and len(eq):
        last = eq.sort_values("timestamp").groupby("book").last()
        first = eq.sort_values("timestamp").groupby("book").first()
        Lm += ["", "## Equity vs benchmarks", ""] + [f"- {b}: {first.loc[b, 'equity']:.2f} -> {last.loc[b, 'equity']:.2f} ({(last.loc[b, 'equity'] / first.loc[b, 'equity'] - 1) * 100:+.2f}%)" for b in last.index]
    Lm += ["", "## Forward tests", "", "| test | closed | needed | mean net % | CI low % |", "|---|---|---|---|---|"]
    for r in forward_scoreboard():
        m_ = "–" if r["mean_net_pct"] is None else f"{r['mean_net_pct']:+.2f}"; c_ = "–" if r["ci_lo_pct"] is None else f"{r['ci_lo_pct']:+.2f}"
        Lm.append(f"| {r['test']} | {r['closed']} | {r['need']} | {m_} | {c_} |")
    rows = jl_read(RESULTS); ok = [r for r in rows if "error" not in r]
    Lm += ["", "## Research since last autopsy", "", f"- hypotheses tested so far {len(rows)}, passed {sum(1 for r in ok if r.get('pass'))}, queued {len([s for s in jl_read(QUEUE) if s['id'] not in {r['id'] for r in rows}])}"]
    if new_specs:
        tested = {r["id"] for r in rows} | {r["id"] for r in jl_read(QUEUE)}
        add = []
        for s in new_specs:
            s["id"] = hid({k: s[k] for k in ("method", "var", "cond")})
            if s["id"] in tested:
                continue
            s.update({"prio": 0, "registered": time.strftime("%F %T"), "sign": C.VARIABLES[s["var"]][0], "source": "autopsy follow-up: " + s["note"]}); add.append(s)
        jl_append(QUEUE, add)
        Lm += ["", "## New hypotheses queued from this autopsy", ""] + [f"- {s['id']} {s['method']} {s['var']} x {s['cond']}: {s['note']}" for s in add]
    p = AR / f"autopsy_{date}.md"; p.write_text("\n".join(Lm))
    return "\n".join(Lm)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["propose", "run", "promote", "status", "autopsy", "cycle"])
    ap.add_argument("--n", type=int, default=12); ap.add_argument("--minutes", type=int, default=45)
    a = ap.parse_args(); AR.mkdir(parents=True, exist_ok=True)
    if a.cmd == "propose":
        print(len(propose()), "queued")
    elif a.cmd == "run":
        run(a.n, a.minutes)
    elif a.cmd == "promote":
        print("promoted", promote())
    elif a.cmd == "status":
        print(status())
    elif a.cmd == "autopsy":
        print(autopsy())
    elif a.cmd == "cycle":
        propose(); run(a.n, a.minutes); print("promoted", promote()); status()
        import subprocess
        subprocess.run(["git", "add", "research/autoresearch", "research/forward_auto.yaml", "research/trial_ledger.csv"], cwd=ROOT)
        subprocess.run(["git", "commit", "-qm", f"autoresearch cycle {time.strftime('%F %H:%M')}\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01KV1QeD2ctJ7msUEcqq5Zkj"], cwd=ROOT)
        subprocess.run(["git", "push", "-q"], cwd=ROOT)


if __name__ == "__main__":
    main()
