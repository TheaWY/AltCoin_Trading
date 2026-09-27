# B7-B12 results (2026-09-28)

**Protocol.** Everything was registered in git before it was run: research/batch_B7.yaml, batch_B8.yaml and batch_B9_B13.yaml.
- Discovery 2024-04..2025-08; the holdout 2025-09..2026-09 was used once.
- Signs were fixed on discovery.
- Uncertainty uses a day-clustered bootstrap, with Benjamini-Hochberg q=0.10 per batch.
- Costs are fees plus volume slippage plus actual funding.

## Trading tests: 0 pass
| Test | Holdout result | Verdict |
|---|---|---|
| B7_A 24 mechanism factors | rank IC up to 0.11, net L/S <= 0 | FAIL |
| B7_B GP pool (3,405 formulas) | rank IC 0.124, net -0.23%/day | FAIL |
| B7_C deep residual stat-arb (3 seeds) | -0.14%/day, CI [-0.57, +0.32]% | FAIL |
| B7_D LambdaRank increment | net +0.11%/day, CI [-0.04, +0.27]% | FAIL |
| B7 exploratory vol-scaled / 72h | 0 of 72 variants with CI > 0 | - |
| B8_3 pump-probability model | top 0.5% of scores: 12% pumps vs 0.48% base; the new data adds nothing; top-5 long +0.53%/24h, CI crosses 0 | FAIL |
| B9_1 short 72h before a >=1% cliff unlock | +1.25% (disc, n=200), +1.34% (hold, n=137), beats placebo by 1.3 and 2.4 pp; CI [-0.96, +3.46]% | FAIL, consistent |
| B10_2 short top-decile CEX inflow | -0.18%/trade | FAIL |
| B11_2 long spot-led +10% pumps | -1.9% per 24h in BOTH periods, CI below 0 | FAIL (sign reversed) |
| B12 listing announcements, entry +60s | Binance spot listing of existing perps: +7.5%/60m (n=19, CI crosses 0); Upbit: edge gone at +60s, 24h median -8.5% | FAIL |

## Precursors of +20%-in-6h pumps (matched case-control, holdout AUC)
- **Korean flow**
  - Upbit+Bithumb volume surge: 0.63 (n=763)
  - Upbit surge alone: 0.66 (n=336)
  - Upbit share: 0.60
- **Positioning:** OI/volume (low) 0.62, OI change 24h 0.59, taker ratio 0.58, funding 0.56.
- **Spot:** spot share 0.57, spot taker buying 0.56.
- **On-chain:** LOW 24h exchange inflow comes before both pumps (0.59) and dumps (0.59).
- **Caveat: the recent 6h range alone scores 0.74.** Many pumps are already underway at t0.
  - Jointly, only OI change 24h, funding, taker flow and OI/volume stay significant beyond price.
- **Lead time:** most signals fade by 12-24h before the pump. The Upbit surge is 0.60 at 6h before and 0.58 at 12h.

## New leads (post-hoc, need their own registration + forward test)
1. **Short spot-led +10% pumps** (spot share of volume in the top tercile). Long lost 1.9%/24h in both periods.
2. **Short into large token unlocks** (B9_1): same sign in both periods, needs more events.
3. **Binance spot listing announcements for coins that already have perps**: large but only 19 holdout events.

**Pending:** B13 order-book depth (download running) and B8_4 exploratory short-history sources.

## B13 order-book depth precursors (run 2026-09-28, holdout AUC, matched case-control)
- Depth at +-1% relative to 24h volume (thin book), LOW before pumps: 0.749 (CI 0.735-0.763), and before dumps: 0.766 (CI 0.679-0.856). This is the strongest precursor found so far. It says a big move is coming, not which way. Volume sits in the denominator, so it partly overlaps a volume surge.
- Both sides of the book deepening over 24h before dumps: ask 0.595, bid 0.595. Bid-ask imbalance at 1% (6h mean): pumps 0.549, dumps 0.612.
- Depth change before pumps: no signal (0.50).
- 6 of 8 pass (BH q=0.10, CI > 0.5). Short history before 2026-03 comes from the hourly bookDepth backfill.
