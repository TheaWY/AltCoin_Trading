# Hindsight results, window A = 2026-10-06..2026-10-29 (24 held days), B51_END=2026-10-31

Engine: b54_sweep100.run (open->open, fee 5bp + slippage tier). Oracles look ahead; everything else is ex-ante.

## Scoreboard

| portfolio | side | total | maxDD | Sharpe(ann) | rebal days | cost | avg exposure |
|---|---|---|---|---|---|---|---|
| O1 best coin BTC | hindsight | -9.3% | -9.2% | -5.87 | 1 | +0.1% | 100% |
| O2 perfect daily switch | hindsight | +248.5% | -0.1% | 31.46 | 22 | +4.9% | 88% |
| O3 perfect BTC timing | hindsight | +5.9% | -0.1% | 9.72 | 12 | +1.1% | 42% |
| O4 F19 perfect cash timing | hindsight | +2.2% | -0.1% | 8.70 | 14 | +0.5% | 18% |
| O5 ex-post max-Sharpe (DD<=10%) | hindsight | +0.0% | +0.0% | nan | 0 | +0.0% | 0% |
| O6 best of 100 (A_macd) | hindsight | +0.0% | +0.0% | nan | 0 | +0.0% | 0% |
| F19 (chosen) | ex-ante | -7.3% | -7.2% | -7.88 | 13 | +0.1% | 44% |
| F17 | ex-ante | -8.7% | -8.6% | -7.72 | 11 | +0.1% | 55% |
| F18b | ex-ante | -6.2% | -4.5% | -5.97 | 6 | +0.1% | 15% |
| BTC hold | ex-ante | -9.3% | -9.2% | -5.87 | 1 | +0.1% | 100% |
| ETH hold | ex-ante | -17.0% | -16.8% | -8.35 | 1 | +0.1% | 100% |
| EW30 | ex-ante | -28.9% | -27.4% | -8.58 | 16 | +0.2% | 100% |
| cash | ex-ante | +0.0% | +0.0% | nan | 0 | +0.0% | 0% |
| O7 mom20 top3 (ex-ante) | ex-ante | -49.1% | -47.2% | -13.76 | 21 | +2.3% | 100% |
| O7 mom20 top5 (ex-ante) | ex-ante | -42.3% | -40.8% | -11.25 | 22 | +1.9% | 100% |
| O7 mom20 top10 (ex-ante) | ex-ante | -35.4% | -34.1% | -9.45 | 23 | +1.4% | 100% |

## O1 single coins, buy-and-hold over A (top 8 / bottom 5)

- BTC: -9.3%
- BCH: -13.8%
- IOST: -17.0%
- ETH: -17.0%
- XRP: -18.0%
- SOL: -20.9%
- DOGE: -21.0%
- SHIB: -21.4%
- ...
- WLD: -33.6%
- VIRTUAL: -33.7%
- SEI: -35.9%
- SOPH: -44.0%
- FLOCK: -44.4%

Median single coin: -25.7%; share of coins positive: 0%

## O5 weights

- cash: 100.0%

## O6 the 100 pre-registered B54 strategies on window A

best A_macd +0.0%; median -9.0%; worst B_own_trend -41.2%; positive: 0/100

top 10:
- A_macd: +0.0%  (MACD(12,26) > signal(9))
- A_rsi2_mr: -0.3%  (above SMA200: enter RSI2<10, exit close>SMA5)
- E_weekends_only: -1.0%  (F17 only Sat/Sun)
- E_tom_only: -1.1%  (F17 only days 25..31 and 1..5 (turn of month))
- A_donch20_10: -1.6%  (Donchian enter 20d high, exit 10d low)
- A_sma30: -4.4%  (BTC/ETH hold when close > SMA30)
- E_no_thu: -5.5%  (F17 but flat on thu)
- E_no_wed: -5.6%  (F17 but flat on wed)
- A_ema12_26: -5.7%  (EMA12 > EMA26)
- E_no_first_week: -5.8%  (F17 except days 1-7)

## Gap decomposition, F19 vs each oracle (sum over A; + means oracle earned more)

| oracle | total gap | exposure/timing | selection | cost | residual | (concentration within oracle) |
|---|---|---|---|---|---|---|
| O1 best coin BTC | -2.0% | -6.4% | +4.4% | +0.0% | +0.0% | +0.0% |
| O2 perfect daily switch | +136.9% | +2.3% | +139.4% | -4.8% | -0.0% | +0.0% |
| O3 perfect BTC timing | +13.3% | +14.4% | -0.1% | -0.9% | +0.0% | +0.0% |
| O4 F19 perfect cash timing | +9.7% | +10.1% | -0.0% | -0.4% | +0.0% | -0.0% |
| O5 ex-post max-Sharpe (DD<=10%) | +7.5% | +7.4% | -0.0% | +0.1% | +0.0% | +0.0% |
| O6 best of 100 (A_macd) | +7.5% | +7.4% | -0.0% | +0.1% | +0.0% | +0.0% |

## Per-day: F19, EW30, BTC, O2 pick

| day | F19 net | F19 exp | EW30 | BTC | O2 pick | O2 ret |
|---|---|---|---|---|---|---|
| 2026-10-06 | -0.2% | 81% | -2.0% | -0.0% | AVAX | +4.2% |
| 2026-10-07 | -2.4% | 79% | -4.0% | -2.1% | NEAR | +5.6% |
| 2026-10-08 | -1.2% | 58% | -4.5% | -1.0% | ARK | +4.9% |
| 2026-10-09 | +0.2% | 50% | +2.0% | +0.6% | APT | +9.8% |
| 2026-10-10 | +0.1% | 50% | +2.1% | +0.1% | WLD | +8.5% |
| 2026-10-11 | +0.2% | 50% | +1.6% | +0.2% | NEAR | +10.2% |
| 2026-10-12 | -1.6% | 52% | -7.5% | -2.2% | DATA | +6.0% |
| 2026-10-13 | +0.9% | 50% | +1.8% | +1.6% | UNI | +7.8% |
| 2026-10-14 | -1.3% | 50% | -3.7% | -2.0% | cash | -0.1% |
| 2026-10-15 | +0.3% | 40% | +1.1% | +0.5% | ARB | +7.6% |
| 2026-10-16 | -0.3% | 50% | -1.2% | -0.6% | SEI | +4.1% |
| 2026-10-17 | -0.2% | 40% | -3.2% | -0.1% | SAND | +10.5% |
| 2026-10-18 | +0.1% | 40% | -0.7% | +0.4% | NEAR | +2.5% |
| 2026-10-19 | -0.1% | 40% | +0.2% | -0.3% | WLD | +8.6% |
| 2026-10-20 | +0.2% | 40% | +1.5% | +0.1% | WLD | +8.8% |
| 2026-10-21 | +0.5% | 40% | +1.6% | +1.4% | ENA | +8.6% |
| 2026-10-22 | -1.4% | 50% | -5.6% | -2.7% | cash | -0.1% |
| 2026-10-23 | +0.0% | 40% | -1.6% | -0.0% | DATA | +2.9% |
| 2026-10-24 | -0.3% | 40% | -1.8% | -0.3% | DATA | +3.4% |
| 2026-10-25 | -0.4% | 30% | -1.5% | -1.2% | ENA | +5.1% |
| 2026-10-26 | -0.1% | 30% | -0.1% | -0.3% | UNI | +2.9% |
| 2026-10-27 | +0.2% | 20% | +0.2% | +0.8% | NEAR | +3.3% |
| 2026-10-28 | +0.2% | 20% | +1.3% | +1.1% | XPL | +4.4% |
| 2026-10-29 | -0.7% | 20% | -8.5% | -3.3% | cash | -0.1% |
