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

## B17 F01 / F01b / F01c precursor anticipation (run 2026-09-28 after the onset-label fix; validation = 2026-01..09; 109 tests)
- Detection: 7 models on the 22 precursors, P(pump within 6h). Validation AUC: LightGBM 0.883, CatBoost 0.877, TCN 0.873, transformer 0.869, GRU 0.859, MLP 0.841, logit 0.762. Horizon sweep (LightGBM): 0.91 at 1h down to 0.86 at 24h. At the top-0.5% flag rate about a third of flagged coin-hours pump within 6h (base rate 2.8%) and ~62% within 24h.
- Path after a flag (top 0.5%, every model): the coin drifts to roughly -0.5..-1% over 24h on average; the pump arrives after a median 6h and the coin first draws down ~10% (median) before it. The flag is right about the pump and wrong about the level.
- Trades (per model: immediate / dip 3-8% within 6-24h / re-break of the flag high / enter only at the onset trigger, x 3 exits, x 3 flag rates): 0 of 109 pass. Best cells are +0.3..+0.9% with CIs of +-2-3% and MAE p90 30-45%; none beats the random control after cost. Immediate entries are negative for every model (-0.1% to -3.3%).
- Reading: knowing a pump is coming is not enough because it comes from a lower price and the exact timing (hour) is unknown; the entries that wait for the dip or the trigger give back the edge to volatility and costs. This closes the "enter before the pump on precursors" idea at hourly resolution; the second-level onset family (F02) is the remaining route.

## B17 F03 / F15 / F06 (run 2026-09-28; validation 2026-01..09)
- F03 continuation filters (long at m0+5m only if a condition holds; 14 conditions x 3 exits x with/without 5% stop): 0/84. Best filtered cells (+0.5..+0.7%: low funding, thin-book precursor, OI up, positive acceleration, all with the 5% stop and 24h exit) beat their complements by ~0.9pp with CIs of +-1.3pp. No condition turns pump-following into a positive-expectancy trade.
- F15 stops on pumps: a 5% stop lifts the m0+1 long from -0.68% to +0.02% per trade (improvement CI +0.16..+1.22pp) but its loss-cutting efficiency is <= 0: it stops out as many eventual winners as it saves losers. Stops on post-peak shorts do nothing. 0/10 by the registered rule.
- F06 multi-day: serial pumpers, day 2-3 drift, post-retrace rebound, listing/unlock/weekend/session timing, second pump within 6h, 7d falling volume: 0/10, all within +-1% of zero. A first run with log returns showed two "passes" for shorts (+2.8% and +2.5%); under pump skew E[-log(1+x)] overstates a short's mean, so the code was corrected to simple returns before logging.
- Cross-check that caught it: minute-level short from m0+60..240 held to 24h is +0.07% (CI -0.9..+1.0%) in validation.

## B17 F02 part A: second-level onset triggers, timing only (run 2026-09-28; 3,713 pumps with 1-second bars; validation 1,251 pumps)
- Setup: trigger evaluated inside the pump hour [m0-60m, m0+1m) against a 5-minute quiet baseline; entry at +5/30/120s; exits 15m/1h/4h/trail. Every window CONTAINS a pump, so this measures where in the hour a trigger puts you, relative to a random second in the same hour. It says nothing about false positives.
- Fire rates and timing: trades-per-second surge 90%, change-point 98%, Hawkes 79%, sweep proxy 51%, taker share 60% of pumps; the 1s versions fire within the first 1-2 minutes of the pump hour (median m0-58 min). 15s/1m versions fire later (m0-45..-12 min).
- Paired vs random second: 1s change-point / tps / sweep with 1h or 4h exits gain +3.5..+5.1pp (CI > 0, 57 of 180 cells). Trailing exits are worse than random (-5..-13pp: the trail gets hit in the noise before the move). Latency 5 s vs 120 s: < 0.5pp difference; latency is not the binding constraint at this resolution.
- The f02.json 'pass' list (180 BH) tests against zero and is not meaningful here; the paired table is data/reports/b17/f02_paired.csv. Verdict on tradability waits for part B (3,713 matched no-pump windows, downloading).

## B17 F02 part B: onset triggers, tradability (run 2026-09-28; 3,374 matched no-pump windows)
- Fire rate in pump windows vs quiet windows: trades/sec surge 90% / 99%, change-point 98% / 99%, taker share 60% / 84%, Hawkes 15s 93% / 35%, sweep proxy 1s 51% / 9%. The first three fire in almost every quiet hour: against a 5-minute quiet baseline, ordinary activity looks like an onset. Only the sweep proxy (large up-range with buy-initiated volume) is selective.
- With the market base rate (0.5% of coin-hours pump), P(pump | sweep fired) = 2.8%. Weighted net: +0.49% per trade for the 1h exit, CI -0.2..+1.2%; 15-minute exits ~0; every other trigger is at or below zero. 0 of 180 pass.
- Reading: second-level resolution and latency are not the constraint (5 s vs 120 s changes < 0.5pp). The constraint is specificity: the pump-hour signature (bursty buy-initiated volume) is common in quiet hours too. F12 (models on the same windows) is the direct test of whether a learned combination separates them.
- F13 change-point is covered inside F02 (cp trigger). F17 book microstructure is not runnable: no free best-bid/ask tick history after 2024-03 and the futures websocket is geo-blocked.

## B17 F12 / F04 / F05 / F18 (run 2026-09-28; validation 2026)
- F12 tick ML (5 models x 5m/15m/60m lookback, pump + quiet windows): separating "+5% within 15 min" decision points is easy (AUC 0.82-0.91); the top-10% flags fire on 5-25% of pump decision points and on 0-6 of 1,126 quiet-window points. Among flagged pump points the next 60 minutes net +3.8..+16.5%. This is the first family where the false-positive side looks small, but quiet windows (no pump within 24h) are an easy negative set; the hard negatives (near-miss +4-10% hours, volatile neighbourhood; 3,000 windows) are downloading and decide it. No pass claimed.
- F04 dump shorts: one trigger stands out. Short when 5-minute open interest has fallen 3% from its post-onset high (median ~4.5h after onset), exit +4h, 5-10% stop: validation +0.78% per trade (CI 0.58-0.97), discovery +0.37% (0.09-0.61), positive in every HMM regime and every third of 2026, 6,732 validation trades; recomputed independently. It was the only BH survivor and failed only the MAE-p90 rule, which turned out to be measured past the stop (bug); re-run pending. Every other trigger/stop/exit cell averages -0.9%.
- F05 squeeze abstention: predicting >20% adverse excursion on post-peak shorts has AUC 0.63; abstaining does not turn the short positive (+0.2% vs +0.1%). 0/6.
- F18: OI +10% during the pump predicts CONTINUATION, not a deeper dump (short -4.4%, CI < 0): pumps financed by new longs keep going. Pre-settlement pumps shorted at funding settlement +1.2% (CI crosses 0). Liquidation-based statements had no eligible events (Binance liquidation table too sparse per 5-minute window). 0/7.

## B17 F12b: onset-proximity models vs hard negatives (run 2026-09-28; validation 2026)

- 45 model cells (lgbm, hawkes, tcn, gru, transformer x 5/15/60m lookback x y1/y2/y3). Detection AUC 0.82-0.91 for y1 (+5% in 15m), 0.55-0.60 for y2/y3.
- The raw report listed 4 BH passes. They are an artifact: the near-miss hard negatives were chosen as hours that rose 4-10%, and each window ends inside that hour, so their forward returns are positive by construction (+0.9-1.0% after cost). Volatile-neighbour and quiet negatives sit at about minus cost.
- Recomputed without near-miss negatives: 0 passes. The three y2 cells turn negative; transformer 60m y1 g15 CI [-0.33%, +2.6%].
- Lead: 60m-lookback y1 models fire on 0.4-2.7% of unbiased negatives, and those flagged negatives rise too (hawkes 60m y1 g60 net_real +2.1% [0.4, 3.6]). Only 10-16 flagged negatives, below the pre-registered 20. Next step F12c: 10k random-hour null windows not selected on outcome.
- Fix for future samples: any negative set must be drawn without reference to the forward window.
