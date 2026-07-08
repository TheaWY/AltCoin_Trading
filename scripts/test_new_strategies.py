"""Smoke + logic tests for the three new strategies.

Runs against a temp SQLite Storage through the REAL data path
(gather_strategy_data -> generate_signal), the same one live and paper use.

    python scripts/test_new_strategies.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point storage at a temp DB BEFORE importing config/storage
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "test.db")
os.environ["DATABASE_URL"] = ""

from src.data.storage import Storage  # noqa: E402
from src.engine.signal import gather_strategy_data  # noqa: E402
from src.strategies.funding_carry import FundingCarryStrategy, settlement_rates  # noqa: E402
from src.strategies.mean_reversion import MeanReversionStrategy  # noqa: E402
from src.strategies.positioning_short import PositioningShortStrategy  # noqa: E402
from src.strategies.registry import get_strategy, list_strategies  # noqa: E402

SYMBOL = "BTC/USDT"
HOUR = 3600
NOW = int(time.time()) // HOUR * HOUR

passed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed
    status = "OK " if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        sys.exit(1)
    passed += 1


def price_rows(closes: list[float], spread: float = 0.001) -> list[dict]:
    rows = []
    start = NOW - len(closes) * HOUR
    for i, c in enumerate(closes):
        rows.append({
            "symbol": SYMBOL, "timestamp": start + i * HOUR, "timeframe": "1h",
            "open": c, "high": c * (1 + spread), "low": c * (1 - spread),
            "close": c, "volume": 100.0,
        })
    return rows


def fresh_storage() -> Storage:
    path = Path(_tmp) / f"db_{time.time_ns()}.db"
    return Storage(db_path=path)


# ---------------------------------------------------------------- registry
check("registry contains new strategies",
      all(n in list_strategies() for n in ("mean_reversion", "funding_carry", "positioning_short")),
      str(list_strategies()))

# ---------------------------------------------------------- mean_reversion
# Crash scenario: flat then sharp selloff -> RSI oversold + below lower band -> LONG
st = fresh_storage()
closes = [50_000.0] * 30 + [49_500, 48_900, 48_200, 47_400, 46_500, 45_600, 44_800]
st.insert_prices(price_rows(closes))
data = gather_strategy_data(st, MeanReversionStrategy(), SYMBOL)
sig = MeanReversionStrategy().generate_signal(data)
check("mean_reversion LONG on crash", sig.direction.value == "LONG",
      f"rsi={sig.metadata.get('rsi')} %B={sig.metadata.get('percent_b')}")

# Melt-up scenario -> SHORT
st = fresh_storage()
closes = [50_000.0] * 30 + [50_500, 51_200, 52_000, 52_900, 53_900, 55_000, 56_200]
st.insert_prices(price_rows(closes))
data = gather_strategy_data(st, MeanReversionStrategy(), SYMBOL)
sig = MeanReversionStrategy().generate_signal(data)
check("mean_reversion SHORT on melt-up", sig.direction.value == "SHORT",
      f"rsi={sig.metadata.get('rsi')} %B={sig.metadata.get('percent_b')}")

# Quiet market -> NONE
st = fresh_storage()
closes = [50_000 + (i % 3) * 30 for i in range(40)]
st.insert_prices(price_rows([float(c) for c in closes]))
data = gather_strategy_data(st, MeanReversionStrategy(), SYMBOL)
sig = MeanReversionStrategy().generate_signal(data)
check("mean_reversion NONE in quiet market", sig.direction.value == "NONE", sig.reason[:60])

# ----------------------------------------------------------- funding_carry
def funding_rows(rates_per_settlement: list[float]) -> list[dict]:
    """3 collection prints per 8h settlement, last print = settlement rate.

    Real Binance settlements land on 8h UTC boundaries (00/08/16), so the
    fixture aligns windows the same way.
    """
    rows = []
    aligned_now = NOW // (8 * HOUR) * (8 * HOUR)
    start = aligned_now - len(rates_per_settlement) * 8 * HOUR
    for i, rate in enumerate(rates_per_settlement):
        base = start + i * 8 * HOUR
        for j, offset in enumerate((0, 3 * HOUR, 7 * HOUR)):
            rows.append({
                "symbol": SYMBOL, "timestamp": base + offset,
                "funding_rate": rate * (0.9 if j == 0 else 1.0),
            })
    return rows


# settlement bucketing: 3 prints per window must collapse to one rate
rates = settlement_rates(sorted(funding_rows([0.0002, 0.0003]), key=lambda r: r["timestamp"]))
check("settlement bucketing collapses prints", len(rates) == 2 and abs(rates[-1] - 0.0003) < 1e-9,
      str(rates))

# persistent fat funding -> SHORT (carry leg)
st = fresh_storage()
st.insert_prices(price_rows([50_000.0] * 40))
st.insert_funding_rates(funding_rows([0.0003] * 8))
data = gather_strategy_data(st, FundingCarryStrategy(), SYMBOL)
sig = FundingCarryStrategy().generate_signal(data)
check("funding_carry SHORT on persistent funding", sig.direction.value == "SHORT",
      f"mode={sig.metadata.get('execution_mode')}")

# persistent but thin funding -> fee hurdle blocks (0.000101*21=0.0021 < 0.003)
st = fresh_storage()
st.insert_prices(price_rows([50_000.0] * 40))
st.insert_funding_rates(funding_rows([0.000101] * 8))
data = gather_strategy_data(st, FundingCarryStrategy(), SYMBOL)
sig = FundingCarryStrategy().generate_signal(data)
check("funding_carry fee hurdle blocks thin carry", sig.direction.value == "NONE",
      sig.reason[:70])

# one negative print inside the window -> not persistent
st = fresh_storage()
st.insert_prices(price_rows([50_000.0] * 40))
st.insert_funding_rates(funding_rows([0.0003] * 4 + [-0.0001] + [0.0003] * 3))
data = gather_strategy_data(st, FundingCarryStrategy(), SYMBOL)
sig = FundingCarryStrategy().generate_signal(data)
check("funding_carry rejects broken persistence", sig.direction.value == "NONE",
      sig.reason[:70])

# -------------------------------------------------------- positioning_short
def positioning_rows(ratios: list[float], ois: list[float]):
    start = NOW - len(ratios) * HOUR
    ls = [{"symbol": SYMBOL, "timestamp": start + i * HOUR, "ratio": r}
          for i, r in enumerate(ratios)]
    start = NOW - len(ois) * HOUR
    oi = [{"symbol": SYMBOL, "timestamp": start + i * HOUR, "open_interest": v}
          for i, v in enumerate(ois)]
    return ls, oi


# all three conditions met (short history -> abs fallback) -> SHORT
st = fresh_storage()
st.insert_prices(price_rows([50_000.0] * 40))
st.insert_funding_rates(funding_rows([0.0005] * 4))
ls, oi = positioning_rows([1.5] * 100 + [2.8], [80_000.0] * 100 + [81_000.0])
st.insert_ls_ratios(ls)
st.insert_open_interest(oi)
data = gather_strategy_data(st, PositioningShortStrategy(), SYMBOL)
sig = PositioningShortStrategy().generate_signal(data)
check("positioning_short SHORT on crowded extreme", sig.direction.value == "SHORT",
      f"ratio={sig.metadata.get('ls_ratio')} mode={sig.metadata.get('ratio_mode')}")

# funding cold -> NONE even with extreme ratio
st = fresh_storage()
st.insert_prices(price_rows([50_000.0] * 40))
st.insert_funding_rates(funding_rows([0.0001] * 4))
st.insert_ls_ratios(ls)
st.insert_open_interest(oi)
data = gather_strategy_data(st, PositioningShortStrategy(), SYMBOL)
sig = PositioningShortStrategy().generate_signal(data)
check("positioning_short NONE when funding cold", sig.direction.value == "NONE",
      sig.reason[:70])

# OI far below high -> NONE
st = fresh_storage()
st.insert_prices(price_rows([50_000.0] * 40))
st.insert_funding_rates(funding_rows([0.0005] * 4))
ls2, oi2 = positioning_rows([1.5] * 100 + [2.8], [100_000.0] * 50 + [80_000.0] * 51)
st.insert_ls_ratios(ls2)
st.insert_open_interest(oi2)
data = gather_strategy_data(st, PositioningShortStrategy(), SYMBOL)
sig = PositioningShortStrategy().generate_signal(data)
check("positioning_short NONE when OI off its high", sig.direction.value == "NONE",
      sig.reason[:70])

# missing positioning data -> validate_data catches it (engine skips gracefully)
st = fresh_storage()
st.insert_prices(price_rows([50_000.0] * 40))
st.insert_funding_rates(funding_rows([0.0005] * 4))
strategy = PositioningShortStrategy()
data = gather_strategy_data(st, strategy, SYMBOL)
missing = strategy.validate_data(data)
check("positioning_short reports missing data", "ls_ratio_history" in missing, str(missing))

# ------------------------------------------------------------- registry E2E
for name in ("mean_reversion", "funding_carry", "positioning_short"):
    obj = get_strategy(name)
    check(f"get_strategy('{name}') instantiates", obj.name == name)

print(f"\nAll {passed} checks passed.")
