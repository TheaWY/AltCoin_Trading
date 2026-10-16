# Results compendium (all batches through 2026-09-28)

352 ledger rows, 17 batches, 5 forward tests running. Costs everywhere: 0.05%/side + liquidity slippage + actual funding.
Discovery 2024-04..2025-08, holdout 2025-09..2026-09 (touched once per test, but touched by ~20 tests in total -> see "holdout status").

## 1. What predicts a pump (detection works)
Matched case-control AUC on holdout, 0.5 = no signal. Pass = CI above 0.5 after BH q=0.10.

| Precursor (measured before onset) | Pumps | Dumps | Batch |
|---|---|---|---|
| Order-book depth at +-1% relative to 24h volume (thin book) | 0.75 | 0.77 | B13 |
| Price range alone (24h high-low) | 0.74 | - | B8 |
| Upbit volume surge 6h (Korean flow) | 0.66 | - | B8 |
| Korean combined surge (Upbit+Bithumb) | 0.63 | - | B11 |
| OI / volume | 0.61 | - | B8 |
| Bid-ask imbalance at 1% (6h) | 0.55 | 0.61 | B13 |
| OI change 24h | 0.59 | - | B8 |
| Low CEX inflow 24h (on-chain) | 0.59 | 0.59 | B10 |
| Book deepening 24h (both sides) | - | 0.60 | B13 |
| Spot share / spot taker imbalance 6h | 0.57 / 0.56 | - | B11 |
| Funding level, taker ratio 6h | 0.56, 0.58 | - | B8 |

21 precursors pass. Deep models on the same inputs: GRU/TCN pump AUC 0.91 at 6h horizon (B14_4) but precision at top-0.5% only 18%;
buying the flagged coins loses (-0.14%/trade). Detection is solved; monetising it is not.

## 2. Pump lifecycle (B15, 21,360 pumps >= +10%/60m)
- Median further gain after onset +9.4%, median peak at ~ +1.5h; median 24h return from m0+1 is -3.3%, mean +0.2% (heavy right tail).
- Hazard model for "peak within 5 min": AUC 0.72/0.80/0.89 at 5/15/60 min. Exit rule beats 24h hold by +0.5pp only (CI crosses 0); +4.6pp on 25%+ pumps.
- Shape clusters (DTW, k=3): slow-start-continuing cluster +5.8% net over 24h, other two -3.5%/-4.6%. First-10-minute CNN predicts cluster 44.7% vs 41.8% majority -> not tradable (-0.3%/trade).
- Coordinated (>=2 unrelated coins within +-3 min): dump LESS from peak (-5.7% vs -11.2% at +6h); the effect is market-wide bursts. No trade.
- Tick fingerprint (2,700 pumps with aggTrades): whale-driven onsets (few huge trades) dump deeper at +6h in every sample (-12.5% vs -7.9%), but the long spread shrank from 5.6pp to 1.8pp on fresh pumps; tick features add no AUC. Risk marker only.
- Post-peak dump model (B15_5): AUC 0.68 but base rate 83% (nearly every pump gives back 50%); short net -1.06%.
- Short whale-driven pumps after confirmed peak (B15_6): 68% win rate, median +2%, MEAN -4.4%: 10% of trades run +35% against the short, 1% run +190%. Squeeze tails kill it.

## 3. Return prediction / factor work (fails as trades)
- 24 mechanism factors (B7): rank IC up to 0.109 (idiosyncratic vol), long-short net <= 0 in every case. Factors predict the MEDIAN, not the mean: quintile median spread -2.0%..-0.3% but the mean is ~-0.15% in every quintile because of pump skew.
- GP alpha mining: holdout IC 0.124, net -0.23%/day. Deep residual stat-arb -0.14%/day. LambdaRank +0.11%/day (CI crosses 0). 0/72 exploratory variants.
- B14 (1.35M rows, 75 features, HMM regimes, IPCA, PCMCI+): 0/7. Price-only ranker flipped sign in holdout (IC -0.037). Only lead: top-10 long from price+positioning+Korean+spot +0.61%/day CI [0.13, 1.09] (registered as forward candidate). Quantile median IC 0.128, Sharpe negative.
- B2/B3/B4 rule sweeps (140 rows): no signal-timeframe pair beats 0 after cost except crash rebound (below). Calendar/funding-cycle effects: none. Random-entry control: -0.2%/day.
- Loss ML on paper trades (B6): AUC 0.54 on all trades, 0.51 on 유리's real 123 trades. Losers were pairs_statarb and momentum (paused).

## 4. Things that made money (all thin)
- Crash rebound long (coin -25%/24h, BTC -3%, close below 30d low, hold 24h): +5.65% CI [1.9, 7.2] on 2025-26, +3.1% in meta-filtered B6; 2024 holdout +8.6% but only 9 crash days (CI crosses 0). Forward F3 running. Any stop-loss on it costs 6pp.
- Binance spot listing of an existing perp: +7.5%/60m (n=19). Upbit listing 2s-speed long: +11% per trade (n small). Both event-driven, rare, latency-bound. Forward F1 (Upbit) running.
- Unlock short: +1.3% per event, CI crosses 0. Forward F5 running. Spot-led pump short: -1.9%/24h on both periods for +10% spot-led pumps. Forward F4 running.

## 5. Exits and stops (B16)
No exit policy beats a 24h hold on any entry set with CI > 0. Stops cut the average loss (-11.9% -> -7.3%) but cut winners too; loss-cutting efficiency negative for every policy. A learned exit (LightGBM on position state) matched but did not beat time exits.

## 6. Lessons that constrain the next design
1. Everything at 1-minute or coarser resolution that enters AFTER a +10% move is already too late: by m0 the median pump has 9% left and 50% of that is gone within 90 minutes.
2. Precursors work 6-24h ahead but have a low base rate (~0.5% of coin-hours). The trade that follows a precursor has never been tested at the right granularity: enter BEFORE the move, size small, exit on the first +X%.
3. Shorts fail through the right tail (squeezes), longs fail through the left tail (dumps) - so every remaining hypothesis must be evaluated on the mean AND the 90/99th percentile of adverse excursion, with a hard stop as a design variable, not an afterthought.
4. Holdout status: 2025-09..2026-09 has now been read by ~20 test families. It is no longer clean. B17 (next batch) treats 2026-04..2026-09 as a validation slice for selection only; the confirmation for any survivor is a pre-registered FORWARD paper test with a fixed stop date and fixed sample size.
5. Slicing has never flipped a verdict but regime matters: price factors reversed sign across the 2024 and 2025-26 regimes (B14_1). Any pump strategy is conditioned on HMM state by default.

## 7. Forward tests live
F1 Upbit listing, F2 pump CNN (verdict mid-Nov), F3 crash rebound, F4 spot-led short, F5 unlock short. Position tracker (advisory) on data/positions.yaml.
