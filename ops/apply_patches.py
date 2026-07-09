#!/usr/bin/env python3
"""Apply the 4 small edits this drop needs, idempotently, onto YOUR current
files — never overwriting them. Safe to run multiple times.

    python3 ops/apply_patches.py

Patches:
  1. src/config.py         — promotion overrides overlay (after load_dotenv)
  2. scripts/backtest.py   — closed_trade_pnls in the result dict
  3. src/api/main.py       — research router + /experiments page route
  4. src/dashboard/index.html — 실험 tab button in the bottom nav

If an anchor string is missing (your file diverged), the script prints a
MANUAL step instead of guessing.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
results: list[str] = []


def patch(path: str, anchor: str, addition: str, marker: str, before: bool = False) -> None:
    p = ROOT / path
    if not p.exists():
        results.append(f"[MANUAL] {path}: file not found")
        return
    src = p.read_text()
    if marker in src:
        results.append(f"[skip] {path}: already patched")
        return
    if anchor not in src:
        results.append(f"[MANUAL] {path}: anchor not found — apply by hand, "
                       f"see PATCHES.md section for this file")
        return
    new = (addition + anchor) if before else (anchor + addition)
    p.write_text(src.replace(anchor, new, 1))
    results.append(f"[ok]   {path}: patched")


# ---- 1. config.py: overrides overlay -------------------------------------
patch(
    "src/config.py",
    'load_dotenv(_PROJECT_ROOT / ".env")',
    '''

# --- Promotion overrides overlay (research stack) ---
# src/research/promotion.py writes vetted config changes to
# data/config_overrides.json. Applied before the os.getenv() calls below so a
# promoted value wins over .env. Whitelist enforced at read AND write time.
# Delete the file (or run the rollback command) to return to .env values.
import json as _json

_OVERRIDE_PREFIXES = (
    "MEANREV_", "CONFLUENCE_", "LSR_", "SETUP_", "SCAN_", "COOLDOWN_",
    "FEE_", "FUNDING_RATE_", "CARRY_", "POS_SHORT_", "BREAKOUT_", "TSMOM_",
)
_OVERRIDE_EXACT = {"MIN_CONFIDENCE", "MAX_OPEN_POSITIONS"}
_OVERRIDES_FILE = Path(
    os.getenv("DATA_DIR", str(_PROJECT_ROOT / "data"))
) / "config_overrides.json"
if _OVERRIDES_FILE.exists():
    try:
        for _key, _value in _json.loads(_OVERRIDES_FILE.read_text()).items():
            if (
                isinstance(_key, str)
                and isinstance(_value, (str, int, float, bool))
                and (_key in _OVERRIDE_EXACT or _key.startswith(_OVERRIDE_PREFIXES))
            ):
                os.environ[_key] = str(_value)
    except Exception:
        pass
''',
    marker="Promotion overrides overlay",
)

# ---- 2. backtest.py: per-trade pnls in result ------------------------------
patch(
    "scripts/backtest.py",
    '"symbols": self._symbol_results(portfolio.closed_trades, symbol_curves),',
    '''
            # Per-trade detail for the research stack (DSR / expectancy / PF).
            "closed_trade_pnls": [
                {
                    "symbol": t.symbol,
                    "strategy": getattr(t, "strategy", None),
                    "pnl": round(float(t.pnl or 0.0), 6),
                    "fees": round(float(getattr(t, "fees", 0.0) or 0.0), 6),
                    "opened_at": getattr(t, "opened_at", None),
                    "closed_at": getattr(t, "closed_at", None),
                }
                for t in portfolio.closed_trades
            ],''',
    marker="closed_trade_pnls",
)

# ---- 3. main.py: research router + /experiments route ----------------------
patch(
    "src/api/main.py",
    "app.include_router(trades.router, prefix=\"/api\")",
    '''
from src.api.routes import research as _research  # research stack
app.include_router(_research.router, prefix="/api")


@app.get("/experiments")
async def experiments_page() -> FileResponse:
    return FileResponse(config.DASHBOARD_DIR / "experiments.html")
''',
    marker="experiments_page",
)

# ---- 4. index.html: 실험 tab button ----------------------------------------
patch(
    "src/dashboard/index.html",
    '<button class="tab-btn" type="button" data-tab="method">전략</button>',
    '''
    <button class="tab-btn" type="button" onclick="location.href='/experiments'">실험</button>''',
    marker="location.href='/experiments'",
)

print("\n".join(results))
if any(r.startswith("[MANUAL]") for r in results):
    print("\nSome patches need manual application — see above.")
    sys.exit(1)
print("\nAll patches applied (or already present).")
