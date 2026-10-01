# Autonomous research status - 2026-10-02 05:15 KST

Hypotheses: 74 tested (0 errors), 1 passed the full rule, 270 queued. Running BHY over all 74 holdout p-values. Families tested: {'korea': 24, 'vol': 14, 'price': 7, 'funding': 6, 'flow': 6, 'oi': 6, 'volume': 4, 'cross': 3, 'method': 2, 'positioning': 2}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| ARfce704dd | factor_momentum | vol_of_vol_7d |  | -2.51 | -0.72 | -1.65 | -2.56 | -39.65 | False | False |
| ARd0a2eeaf | factor_momentum | session_asia_var_share |  | +1.23 | +0.24 | +0.67 | +0.43 | -2.73 | False | False |
| AR86033591 | factor_momentum | funding_7d |  | +2.40 | -1.10 | -1.28 | -1.27 | -23.37 | False | False |
| ARa1b38cb9 | factor_momentum | funding_dev |  | +2.56 | +0.28 | +0.17 | +0.35 | -19.88 | False | False |
| AR955d3a48 | factor_momentum | funding_z |  | -0.96 | +1.75 | +1.45 | +1.40 | -4.26 | False | False |
| ARe87addba | mfd_gate | ALL |  | +1.04 | +1.84 | – | – | +3.45 | False | False |
| AR8a7fc594 | factor_momentum | taker_share_24h |  | +0.31 | +0.26 | -0.10 | +0.07 | -11.73 | False | False |
| AR2aa58417 | factor_momentum | taker_share_7d |  | +1.06 | +1.15 | +1.32 | +1.81 | +1.00 | False | False |
| AR43091915 | factor_momentum | taker_var_compression |  | +0.31 | -1.17 | -0.81 | -0.05 | -17.46 | False | False |
| AR0fd733c7 | factor_momentum | oi_chg_7d |  | -0.08 | -1.04 | -0.61 | -0.99 | -12.71 | False | False |
| AR09ae0651 | factor_momentum | oi_to_volume |  | -1.29 | +0.25 | -0.56 | -1.02 | -15.81 | False | False |
| AR0f56a6f7 | factor_momentum | fund_x_oi |  | +3.19 | -1.45 | -1.21 | -0.92 | -16.43 | False | False |
| ARf9e61b4a | layered | upbit_share_24h | btc_30d_down | +0.56 | +2.00 | +0.88 | +0.72 | -0.29 | False | False |
| ARc2017434 | layered | upbit_share_24h | mkt_vol_high | +2.59 | +1.16 | +2.63 | +0.99 | +5.28 | False | False |
| AR09f748d6 | layered | upbit_share_24h | breadth_low | +1.59 | +1.26 | -0.37 | -1.16 | -11.95 | False | False |
| AR9f694c28 | layered | upbit_share_24h | funding_crowded | +0.92 | – | -1019458157879029.50 | – | +5.79 | False | False |
| ARffa1bbb5 | layered | upbit_share_24h | weekend | +2.14 | +1.94 | +1.95 | +2.32 | +11.46 | False | False |
| AR71f9392d | layered | upbit_share_24h | korea_hot | +2.49 | +1.12 | +2.33 | +1.65 | +6.69 | False | False |
| AR8d4baad4 | layered | upbit_share_7d | btc_30d_down | -0.07 | +2.34 | +0.76 | +0.82 | +0.82 | False | False |
| AR1dd75492 | layered | upbit_share_7d | mkt_vol_high | +1.41 | +2.10 | +3.14 | +1.14 | +5.11 | False | False |
| AR3c521a77 | layered | upbit_share_7d | breadth_low | +0.61 | +1.35 | -0.72 | -1.19 | -11.66 | False | False |
| ARcabb9992 | layered | upbit_share_7d | funding_crowded | +0.92 | – | +1189159185947655.00 | – | +22.90 | False | False |
| ARb9f21b35 | layered | upbit_share_7d | weekend | +1.39 | +2.35 | +2.47 | +2.87 | +13.60 | False | False |
| AR84541aaf | layered | upbit_share_7d | korea_hot | +1.54 | +1.92 | +3.07 | +2.39 | +11.41 | False | False |

## Survivors (full rule)

- ARd5d71ff6 factor_momentum korea_share_24h: holdout FM t +4.21, alpha t +2.55, band net +5.17 bp/day -> forward

## Near misses (holdout FM t >= 1.5, failed a check)

- AR4cc495f9 xs_sort korea_share_24h: holdout t +3.15; failed ['bhy', 'insample_sign', 'lag']
- ARa200b4d5 xs_sort upbit_share_7d: holdout t +3.12; failed ['bhy']
- AR4de21b01 factor_momentum upbit_share_7d: holdout t +2.96; failed ['bhy']
- AR834cc5b1 xs_sort ls_top_minus_global: holdout t +2.80; failed ['bhy', 'insample_sign']
- AR28741da3 xs_sort upbit_share_24h: holdout t +2.59; failed ['bhy']
- ARb9f21b35 layered upbit_share_7d x weekend: holdout t +2.35; failed ['bhy', 'interaction']
- AR8d4baad4 layered upbit_share_7d x btc_30d_down: holdout t +2.34; failed ['bhy', 'insample_sign', 'lag', 'dsort', 'interaction']
- ARc07576b7 xs_sort ivol_7d: holdout t +2.13; failed ['bhy', 'lag', 'dsort', 'band_net']

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

- next 30 queued by method/state: {'factor_momentum': 6, 'layered/btc_30d_down': 4, 'layered/mkt_vol_high': 4, 'layered/breadth_low': 4, 'layered/funding_crowded': 4, 'layered/weekend': 4, 'layered/korea_hot': 4}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
