"""B22_WALKBACK (2026-10-01): walk back the whole paper-portfolio history (paper_trades + paper_trades_archive) and
attribute every dollar lost, then replay the history under simple risk rules to see which losses were avoidable.

Counterfactual rules (each applied alone, then all together; trades are removed or capped, nothing else changes):
  R1 cooldown   after a losing exit, no new trade in the same symbol (any strategy) for 72h
  R2 one-open   at most one open trade per symbol at a time
  R3 hedge-cap  pairs: skip trades whose hedge notional is > 2x the main leg (beta from short, noisy windows)
  R4 young      skip coins listed on Binance futures < 60 days before the trade
  R5 loss-cap   per-trade loss capped at 1% of a $1,000 book (-$10), i.e. a hard stop that actually fills
  R0 admission  only strategies that passed a >= 300-trade forward test may trade (none had) -> every trade removed
Output data/reports/b22/walkback.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.storage import get_storage  # noqa: E402

OUT = ROOT / "data/reports/b22"
COLS = "id,symbol,direction,entry_price,exit_price,quantity,pnl,fees,opened_at,closed_at,strategy,exit_reason"


def q(sql):
    st = get_storage()
    with st._connect() as c:  # noqa: SLF001
        cur = c.raw.cursor(); cur.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    a = q(f"SELECT {COLS}, NULL::float hedge_notional, 'jul' era FROM paper_trades_archive WHERE status='closed'")
    b = q(f"SELECT {COLS}, hedge_entry_price*hedge_quantity hedge_notional, 'sep' era FROM paper_trades WHERE status='closed'")
    t = pd.concat([a, b], ignore_index=True)
    t = t[t.pnl.notna()].copy()
    t["pnl"] = t.pnl.astype(float); t["notional"] = (t.entry_price * t.quantity).astype(float)
    t["hedge_notional"] = t.hedge_notional.astype(float)
    t = t.sort_values("opened_at").reset_index(drop=True)
    # listing age
    try:
        info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20).json()["symbols"]
        onboard = {x["symbol"]: x.get("onboardDate", 0) / 1000 for x in info}
    except Exception:  # noqa: BLE001
        onboard = {}
    t["age_d"] = [(o - onboard.get(s.replace("/", ""), np.nan)) / 86400 for s, o in zip(t.symbol, t.opened_at)]

    R = {"total": float(t.pnl.sum()), "n": int(len(t)), "fees": float(t.fees.fillna(0).sum())}
    by = t.groupby(["era", "strategy"]).agg(n=("pnl", "size"), pnl=("pnl", "sum"), wins=("pnl", lambda x: int((x > 0).sum())),
                                            worst=("pnl", "min"), fees=("fees", lambda x: float(x.fillna(0).sum()))).reset_index()
    R["by_strategy"] = by.to_dict("records")
    R["by_exit"] = t.groupby(["strategy", "exit_reason"]).pnl.agg(["size", "sum"]).reset_index().to_dict("records")
    sym = t.groupby("symbol").pnl.agg(["size", "sum"]).sort_values("sum").head(8).reset_index()
    R["worst_symbols"] = sym.to_dict("records")

    # rules
    def cooldown(df):
        keep, last_loss = [], {}
        for r in df.itertuples():
            if r.opened_at - last_loss.get(r.symbol, -1e18) < 72 * 3600:
                continue
            keep.append(r.Index)
            if r.pnl < 0:
                last_loss[r.symbol] = r.closed_at
        return df.loc[keep]

    def one_open(df):
        keep, busy = [], {}
        for r in df.itertuples():
            if busy.get(r.symbol, -1) > r.opened_at:
                continue
            keep.append(r.Index); busy[r.symbol] = r.closed_at
        return df.loc[keep]
    rules = {
        "R1_cooldown72h": cooldown,
        "R2_one_open": one_open,
        "R3_hedge_cap2x": lambda d: d[~((d.strategy == "pairs_statarb") & (d.hedge_notional > 2 * d.notional))],
        "R4_age60d": lambda d: d[~(d.age_d < 60)],
        "R5_losscap10": lambda d: d.assign(pnl=d.pnl.clip(lower=-10)),
    }
    cf = {}
    for k, f in rules.items():
        d = f(t)
        cf[k] = {"pnl": float(d.pnl.sum()), "saved": float(d.pnl.sum() - t.pnl.sum()), "trades_removed": int(len(t) - len(d))}
    d = t
    for f in rules.values():
        d = f(d)
    cf["ALL_R1_R5"] = {"pnl": float(d.pnl.sum()), "saved": float(d.pnl.sum() - t.pnl.sum()), "trades_removed": int(len(t) - len(d))}
    cf["R0_admission"] = {"pnl": 0.0, "saved": float(-t.pnl.sum()), "trades_removed": int(len(t))}
    R["counterfactual"] = cf
    json.dump(R, open(OUT / "walkback.json", "w"), indent=1, default=float)

    L = ["# B22 walk-back of the paper portfolio", "", f"All closed trades: {R['n']}, total P&L ${R['total']:+.2f}, fees ${R['fees']:.2f}", "",
         "## By era and strategy", "", "| era | strategy | trades | P&L | wins | worst | fees |", "|---|---|---|---|---|---|---|"]
    for r in R["by_strategy"]:
        L.append(f"| {r['era']} | {r['strategy']} | {r['n']} | {r['pnl']:+.2f} | {r['wins']} | {r['worst']:+.2f} | {r['fees']:.2f} |")
    L += ["", "## Worst symbols", "", "| symbol | trades | P&L |", "|---|---|---|"]
    L += [f"| {r['symbol']} | {r['size']} | {r['sum']:+.2f} |" for r in R["worst_symbols"]]
    L += ["", "## Counterfactual risk rules", "", "| rule | P&L | saved | trades removed |", "|---|---|---|---|"]
    L += [f"| {k} | {v['pnl']:+.2f} | {v['saved']:+.2f} | {v['trades_removed']} |" for k, v in cf.items()]
    (OUT / "walkback.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
