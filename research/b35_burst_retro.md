# B35 - SAND 2026-10-02 retrospective: what strategy would have earned it, and would that strategy earn in general?

Question (유리): SAND/USDT rose ~+35% on 2026-10-02 after an Upbit "거래 유의 종목 지정 해제" notice at 16:00:05 KST. Which
strategy would have been in it, and what separates it from the many bursts that fade?

## 1. The path (Binance perp, vs the 16:00 KST price)
+2.8% at 5 min, +6.8% at 10, +10% at 15, +14% at 60, +22% at 2.5 h, +35% at 21:00, -9% off the peak by 23:30.
Volume 10-28x normal for ~2.5 h; taker buy share ~50% (two-sided, not a buy stampede). Our watcher saw the notice in 5.7 s.
The same coin had a burst at 09:00 KST the same day that LOST 2.5% over 4 h.

## 2. Burst rules on all 528 perps, 23 days (2026-09-10 .. 10-02), 4h hold, one trigger per coin per 4h, 15 bp cost
Burst = 30-min return >= +5%, 30-min volume >= 5x the prior-2h average, dv24 >= $5M: n = 646.

| subset | n | mean 4h | 95% CI | hit | median |
|---|---|---|---|---|---|
| all bursts | 646 | -0.1% | [-1.0, +0.8] | 39% | -1.8% |
| 7-day return <= 0 (not run up) | 83 | +3.7% | [+0.7, +7.7] | 57% | +1.3% |
| 7-day return below median | 209 | +2.4% | [+0.8, +4.4] | 51% | +0.2% |
| 7-day return > +10% (extended) | 272 | +1.4% | [-0.1, +3.0] | 46% | -0.9% |
| Upbit share of volume > 20% (Korea-led) | 135 | -1.5% (top quartile -2.8%) | | | |
| OI change 1h top quartile (leverage piling in) | | rho -0.13, p 0.001 | | | |
| preceded by an Upbit notice within 30 min | 1 | +26.3% (SAND) | | | |
| not preceded by a notice | 645 | -0.15% | | | |

Tape features that do NOT separate winners: size of the burst (r30), volume multiple, volume build-up before the trigger,
taker buy share, drawdown from the 30-day high, hour of day, dv24 (all |rho| < 0.05, p > 0.3).
Features that separate (in the fade direction): already-extended coins, Korea-led volume, OI building during the burst.
The "7-day return <= 0" subset is positive but 76% of its sum comes from 5 trades (mean without them +0.9%).
With a 5% trailing stop every rule exits SAND at ~+4%: the exit has to be a hard stop + time, not a trail.

## 3. Conclusions
1. No notice-free burst-chaser is a strategy: it pays for one SAND with ~100 small losses.
2. The notice is the one discriminator in the data (1 of 150 triggers, the winner). That is F11 (registered 10-02 23:55).
3. The only tape split with a CI above zero is "fresh" vs "extended" bursts; it is in-sample and thin, so it is a forward
   test, not a finding: F12 (registered 10-03 00:30). SAND itself was +7% over the prior week (below the median of
   triggers, but not <= 0), so F12 as registered would NOT have taken SAND; F11 would.
4. Korea-led bursts (Upbit share > 20%) fade on average: consistent with F4/F9 and the M-series lead-lag (Binance -> Korea).
