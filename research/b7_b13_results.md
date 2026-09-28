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

## B15_2 / B15_3 / B15_4 pump lifecycle (run 2026-09-28, holdout 2025-09..2026-09)
- B15_4 coordinated pumps (>=2 unrelated coins pumping within +-3 min): dump depth at +6h is SHALLOWER than matched single-coin pumps (-5.7% vs -11.2%, diff CI 1.7-7.1pp; same in discovery). Driven by market-wide bursts (>=10 coins at once); excluding bursts, no difference (CI -1.2..+0.9pp). Shorting coordinated pumps: -3.3% net, CI crosses 0. No trade.
- B15_2 shape clusters (DTW k-medoids on first 60 min, k=3, silhouette 0.17): clusters differ strongly in 24h outcome (slow-start/continuing cluster +5.8% net from m0+10, others -3.5%/-4.6%), but labels use minutes 1-60 so this is descriptive. 1D-CNN from first 10 min: accuracy 44.7% vs 41.8% majority (gap CI 1.6-4.0pp) - real but small. Trade on predicted cluster: -0.3%/trade, CI -1.5..+0.7%. FAIL.
- B15_3 tick onset fingerprint (aggTrades, 450 discovery + 598 holdout pumps): pumps whose onset is dominated by a few huge trades (top concentration tercile) do worse for longs (-1.4% vs +4.3% bottom tercile holdout; -2.2% vs +3.5% discovery) and dump deeper (-12.6% vs -7.5% at +6h), but CIs cross 0. Tick features lift AUC for "long loses" 0.47 -> 0.67, gain CI -0.03..+0.30, p=0.19. FAIL (sample too small to confirm; direction consistent across periods).
- Net: pump detection works, pump trading does not survive costs. The one consistent hint: organic, broad-based onsets (many small trades) continue better than whale-driven onsets.

## B15_3b tick fingerprint confirmation (run 2026-09-28, 1,662 fresh pumps; primary = 1,090 new holdout pumps)
- T1 whale-driven vs crowd-driven onset, 24h long net: +0.7% vs +2.6%. Same direction as B15_3 but the gap shrank (5.6pp -> 1.8pp); CI -6.6..+4.1pp. FAIL.
- T2 tick features for "long loses": AUC 0.557 -> 0.581, gain CI -0.06..+0.08. FAIL.
- Trades: long crowd-driven +2.6% (CI -2.2..+6.1%), short whale-driven -1.2%. Both FAIL.
- What does replicate in all three samples: whale-driven onsets dump deeper at +6h (-12.5% vs -7.9% new holdout; -9.1% vs -6.4% discovery). A risk marker (size down / tighter watch), not a return edge.

## B15_6 short whale-driven pumps after confirmed peak (run 2026-09-28)
- Rule: after a confirmed 3% drop from the running high, short; take profit at 50% retrace of the pump, else exit at +24h.
- Fresh holdout, whale-driven: wins 68% of the time (median +2%/trade) but mean net is negative - 10% of trades run +35% against the short and the worst 1% run +190%. FAIL; pooled holdout also negative (CI entirely < 0).
- Whale-driven is no better than crowd-driven for this short (-1.8pp, CI crosses 0). The deeper dump is real but it comes with fatter squeeze tails.
