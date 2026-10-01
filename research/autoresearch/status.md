# Autonomous research status - 2026-10-02 06:15 KST

Hypotheses: 84 tested (0 errors), 1 passed the full rule, 260 queued. Running BHY over all 84 holdout p-values. Families tested: {'korea': 34, 'vol': 14, 'price': 7, 'funding': 6, 'flow': 6, 'oi': 6, 'volume': 4, 'cross': 3, 'method': 2, 'positioning': 2}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| ARf9e61b4a | layered | upbit_share_24h | btc_30d_down | +0.56 | +2.00 | +0.88 | +0.72 | -0.29 | False | False |
| ARc2017434 | layered | upbit_share_24h | mkt_vol_high | +2.59 | +1.16 | +2.63 | +0.99 | +5.28 | False | False |
| AR09f748d6 | layered | upbit_share_24h | breadth_low | +1.59 | +1.26 | -0.37 | -1.16 | -11.95 | False | False |
| ARffa1bbb5 | layered | upbit_share_24h | weekend | +2.14 | +1.94 | +1.95 | +2.32 | +11.46 | False | False |
| AR71f9392d | layered | upbit_share_24h | korea_hot | +2.49 | +1.12 | +2.33 | +1.65 | +6.69 | False | False |
| AR8d4baad4 | layered | upbit_share_7d | btc_30d_down | -0.07 | +2.34 | +0.76 | +0.82 | +0.82 | False | False |
| AR1dd75492 | layered | upbit_share_7d | mkt_vol_high | +1.41 | +2.10 | +3.14 | +1.14 | +5.11 | False | False |
| AR3c521a77 | layered | upbit_share_7d | breadth_low | +0.61 | +1.35 | -0.72 | -1.19 | -11.66 | False | False |
| ARb9f21b35 | layered | upbit_share_7d | weekend | +1.39 | +2.35 | +2.47 | +2.87 | +13.60 | False | False |
| AR84541aaf | layered | upbit_share_7d | korea_hot | +1.54 | +1.92 | +3.07 | +2.39 | +11.41 | False | False |
| AR9f694c28 | layered | upbit_share_24h | funding_crowded | +2.75 | -0.35 | +1.15 | +1.68 | +17.71 | False | False |
| ARcabb9992 | layered | upbit_share_7d | funding_crowded | +2.03 | -0.12 | +1.69 | +2.13 | +22.83 | False | False |
| AR156ea12a | layered | upbit_share_chg | btc_30d_down | +2.98 | +0.36 | +1.57 | +0.93 | -2.62 | False | False |
| AR8491f28a | layered | upbit_share_chg | mkt_vol_high | +1.79 | +0.87 | -0.00 | +0.95 | -4.39 | False | False |
| AR40819f7f | layered | upbit_share_chg | breadth_low | +3.57 | +0.82 | +1.65 | +1.05 | -5.69 | False | False |
| ARde683ebe | layered | upbit_share_chg | funding_crowded | +0.42 | -1.26 | -1.32 | -1.45 | -32.80 | False | False |
| ARa6f97de4 | layered | upbit_share_chg | weekend | +1.04 | -0.30 | -0.01 | -0.11 | -13.25 | False | False |
| AR459d16c6 | layered | upbit_share_chg | korea_hot | +2.93 | -0.87 | -0.53 | -0.29 | -20.23 | False | False |
| AR3410ef58 | layered | korea_share_24h | btc_30d_down | -1.87 | +2.77 | +0.78 | +0.88 | +1.13 | False | False |
| AR780a3b80 | layered | korea_share_24h | mkt_vol_high | +0.02 | +2.84 | +2.30 | +1.24 | +2.94 | False | False |
| ARe55a2411 | layered | korea_share_24h | breadth_low | -0.96 | +2.19 | -0.04 | -0.38 | -5.00 | False | False |
| ARd82ec1b3 | layered | korea_share_24h | funding_crowded | +0.29 | +2.70 | +2.94 | +2.81 | +8.67 | False | False |

## Survivors (full rule)

- ARd5d71ff6 factor_momentum korea_share_24h: holdout FM t +4.21, alpha t +2.55, band net +5.17 bp/day -> forward

## Near misses (holdout FM t >= 1.5, failed a check)

- AR4cc495f9 xs_sort korea_share_24h: holdout t +3.15; failed ['bhy', 'insample_sign', 'lag']
- ARa200b4d5 xs_sort upbit_share_7d: holdout t +3.12; failed ['bhy']
- AR4de21b01 factor_momentum upbit_share_7d: holdout t +2.96; failed ['bhy']
- AR780a3b80 layered korea_share_24h x mkt_vol_high: holdout t +2.84; failed ['bhy', 'lag', 'interaction']
- AR834cc5b1 xs_sort ls_top_minus_global: holdout t +2.80; failed ['bhy', 'insample_sign']
- AR3410ef58 layered korea_share_24h x btc_30d_down: holdout t +2.77; failed ['bhy', 'insample_sign', 'lag', 'dsort', 'interaction']
- ARd82ec1b3 layered korea_share_24h x funding_crowded: holdout t +2.70; failed ['bhy', 'interaction']
- AR28741da3 xs_sort upbit_share_24h: holdout t +2.59; failed ['bhy']

## Forward paper tests (clean evidence)

| test | closed | needed | mean net % | CI low % |
|---|---|---|---|---|
| F1 Upbit notice | 0 | 15 | – | – |
| F2 pump CNN | 31 | 300 | +1.90 | -2.69 |
| F3 crash rebound | 0 | 30 | – | – |
| F4 spot-led | 2 | 100 | -26.74 | – |
| F5 unlock short | 0 | 60 | – | – |
| F7 Upbit share weekly | 0 | 12 | – | – |
| F8 late-session | 0 | 120 | – | – |
| F9 Upbit-listing fade | 0 | 30 | – | – |
| AR28741da3 upbit_share_24h | 0 | 60 | – | – |
| ARa200b4d5 upbit_share_7d | 0 | 60 | – | – |
| ARd5d71ff6 korea_share_24h | 0 | 60 | – | – |

## Next (queue head) and why

- next 30 queued by method/state: {'factor_momentum': 7, 'layered/weekend': 4, 'layered/korea_hot': 4, 'layered/btc_30d_down': 4, 'layered/mkt_vol_high': 4, 'layered/breadth_low': 4, 'layered/funding_crowded': 3}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
