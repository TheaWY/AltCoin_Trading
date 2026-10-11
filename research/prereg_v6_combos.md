# Pre-registration v6: combined systems (up/volatile detector + down detector + loss minimiser) on top of F17
(written 2026-10-05 17:40 KST, BEFORE running; the registry in scripts/b55_combos.py is the exact list)

Asked by 유리: mix rules - detect rising and volatile coins + detect falls + minimise losses. Each book =
(1 - s) x F17 core (BTC/ETH trend + crash brake + vol scaling) + s x an alt satellite. Long only, Upbit KRW daily,
same engine/costs/1-bar lag as B54 (engine reproduces F15 1.55 / F17 1.72).

## Factorial grid (3 x 3 x 3 x 2 = 54 books)
Satellite (UP detector, 10 slots x 10% of the satellite):
  S1 breakout   : 20d closing high + value >= 1.5x 30d median + BTC trend >= 0.5, exit 20% trailing stop (B54 F18a)
  S2 breakout+  : same + coin's own trend weight >= 0.75, exit Donchian-10 (B54 F18b)
  S3 up&volatile: 7d return >= +15% AND 7d realised vol >= 1.5x its 90d level AND close > SMA20 AND BTC trend >= 0.5,
                  exit first close < SMA10, 60d cap
DOWN detector (forces the whole satellite to cash while on):
  N0 none beyond the satellite's own BTC gate
  N1 alt-index crash brake: alt index (EW top-30 alts) daily <= -10% or <= 0.85 x its 20d high -> off until > SMA20
  N2 breadth: share of top-30 alts closing above their SMA50 < 30% -> off
LOSS minimiser on the satellite:
  L0 none
  L1 volatility scaling by min(1, median 365d of alt-index rv20 / current rv20)
  L2 book drawdown brake: if the whole book is > 15% below its peak (lagged one day), satellite halved
Satellite share s in {20%, 40%}.

## Protocol (as v5)
Benchmark = F17 alone. Discovery 2018-01-01..2023-12-31: Sharpe(book) - Sharpe(F17), centred stationary bootstrap
(block 20, 2,000 draws), BHY q <= 0.10 across all 54; survivor also needs maxDD not worse than F17 by > 5 points.
Holdout 2024-01-01..2026-10-04, survivors only: Sharpe diff > 0 with p < 0.05 / n_survivors and maxDD not worse than
F17 by > 5 points. Prior evidence (B54): satellites S1/S2 at 20-30% LOWERED holdout Sharpe without a down detector, so
the honest prior is FAIL; the question is whether N1/N2/L1/L2 fix that.
PASS -> forward paper. Everything logged as batch B55 (54 trials).
