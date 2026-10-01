"""B22 rule check: do the walk-back rules (72h cooldown after a loss, -10% per-trade loss cap) help F2 on its 2024 and 2026 backtest trades? Out-of-hindsight check."""
import sys, numpy as np, pandas as pd, torch; torch.set_num_threads(1)
sys.path.insert(0,'scripts'); import b18_f2fix as FX; from m7_stress import load
ms, meta = load()
for tag, f, sel in (("2024", "data/cache/b2_ds_pump_2024.npz", None), ("2026", "data/cache/b2_ds_pump.npz", "val")):
    z = np.load(f, allow_pickle=True); m = np.ones(len(z["ts"]), bool) if sel is None else z["ts"] >= FX.VA_END
    d = FX.frame(z, m, ms, meta); t = d[d.side != 0].sort_values("ts").copy(); t["net"] = t.side*t.g - t.c
    def cool(t, H):
        keep, last = [], {}
        for r in t.itertuples():
            if r.ts - last.get(r.code, -1e18) < H*3600: continue
            keep.append(r.Index)
            if r.net < 0: last[r.code] = r.ts + 4*3600
        return t.loc[keep]
    cap = t.net.clip(lower=-0.10)
    print(tag, "base n=%d mean=%+.4f" % (len(t), t.net.mean()), "| cool72 n=%d mean=%+.4f" % (len(cool(t,72)), cool(t,72).net.mean()),
          "| losscap10%% mean=%+.4f" % cap.mean(), "| both mean=%+.4f" % cool(t,72).net.clip(lower=-0.10).mean())
