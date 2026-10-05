"""B57 sealed holdout test (prereg v8). Run ONCE, only after research/b57_selection.json is committed.
Window 2025-07-01..2026-10-04. Predictions = dev + sealed concatenated so positions carry over the boundary.
PASS (per candidate, 2 candidates): Sharpe(cand) - Sharpe(F17) > 0 with stationary-bootstrap p < 0.025 (block 20,
5000 draws) AND maxDD(cand) >= maxDD(F17) - 0.05. Writes research/b57_final.md; refuses to run twice."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b57_eval as E  # noqa: E402

ROOT = E.ROOT
OUT = ROOT / "research/b57_final.md"
SEALED = ("2025-07-01", "2026-10-05")


def main():
    if OUT.exists():
        sys.exit("b57_final already run; sealed data is spent")
    st = subprocess.run(["git", "status", "--porcelain", "research/b57_selection.json"], cwd=ROOT,
                        capture_output=True, text=True).stdout.strip()
    if st:
        sys.exit("commit research/b57_selection.json first")
    sel = json.load(open(ROOT / "research/b57_selection.json"))
    cands = [c for c in (sel["candidate_A"], sel["candidate_B"]) if c]
    dev, sea = E.load_preds("preds"), E.load_preds("sealed")
    f17 = E.win(E.S.run(E.F17W), *SEALED)
    f19 = E.win(E.S.run(E.K.book(E.K.sat_upvol(), 0.2, E.K.down_n1(), 1.0, False)), *SEALED)
    lines = ["# B57 sealed holdout (2025-07-01..2026-10-04), opened once\n",
             f"F17: Sharpe {E.B.sharpe(f17):.2f}, maxDD {E.B.maxdd(f17):.1%}, CAGR {E.B.cagr(f17):.1%}",
             f"F19: Sharpe {E.B.sharpe(f19):.2f}, maxDD {E.B.maxdd(f19):.1%}, CAGR {E.B.cagr(f19):.1%}\n"]
    for c in cands:
        u, rest = c.split("_", 1); m, h = rest.rsplit("_h", 1); h = int(h)
        t = "tim" if u == "U3" else "sel"
        p = pd.concat([dev[(m, h, t)], sea[(m, h, t)]]).sort_index()
        fn = {"U1": E.W_U1, "U2": E.W_U2, "U3": E.W_U3}[u]
        r = E.win(E.S.run(fn(p)), *SEALED)
        a, b = r.to_numpy(), f17.reindex(r.index).fillna(0).to_numpy()
        pv = E.B.boot_p(a, b, E.B.sharpe, 20, draws=5000)
        d_s = E.B.sharpe(a) - E.B.sharpe(b); dd_ok = E.B.maxdd(a) >= E.B.maxdd(b) - 0.05
        verdict = "PASS" if (d_s > 0 and pv < 0.025 and dd_ok) else "FAIL"
        lines.append(f"{c}: Sharpe {E.B.sharpe(a):.2f} (diff {d_s:+.2f}, p={pv:.4f}), maxDD {E.B.maxdd(a):.1%}, "
                     f"CAGR {E.B.cagr(a):.1%} -> {verdict}")
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
