# Autonomous research status - 2026-10-02 03:15 KST

Hypotheses: 50 tested (0 errors), 1 passed the full rule, 294 queued. Running BHY over all 50 holdout p-values. Families tested: {'korea': 12, 'vol': 12, 'price': 7, 'volume': 4, 'funding': 3, 'flow': 3, 'oi': 3, 'cross': 3, 'positioning': 2, 'method': 1}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| ARcda0b6a2 | xs_sort | mom_4w_skip1w |  | +0.32 | +0.41 | +0.01 | +0.64 | -4.32 | False | False |
| ARc91405d8 | xs_sort | max_1h_7d |  | +1.61 | +0.24 | -0.59 | -1.23 | -11.08 | False | False |
| ARc3494dfe | xs_sort | dist_from_30d_high |  | +0.10 | +0.96 | -0.69 | +0.89 | -5.89 | False | False |
| ARf4d3aa70 | xs_sort | rangepos_24h |  | +1.35 | +0.27 | +0.01 | -0.73 | -18.05 | False | False |
| AR25ab99e5 | xs_sort | vol_surprise |  | +1.71 | -0.33 | -0.72 | +0.25 | -10.21 | False | False |
| AR5f6e058e | xs_sort | amihud_7d |  | -0.44 | -0.59 | +0.45 | +1.31 | -4.23 | False | False |
| AR9eef64a1 | xs_sort | trades_per_dollar |  | +2.01 | -1.59 | -2.23 | -2.04 | -17.09 | False | False |
| ARd566a207 | xs_sort | avg_trade_size_z |  | +1.56 | -0.48 | -0.67 | +0.47 | -13.87 | False | False |
| AR3e5b3ea6 | xs_sort | gap_vs_btc_24h |  | -1.69 | -0.62 | -1.04 | +0.07 | -22.91 | False | False |
| ARf6d14611 | xs_sort | beta_30d |  | +0.59 | +0.49 | +1.77 | +1.85 | +6.54 | False | False |
| ARa79e1f0f | xs_sort | corr_btc_7d |  | +1.87 | +0.91 | -1.18 | -1.58 | -13.23 | False | False |
| AR018061a7 | factor_momentum | upbit_share_24h |  | +2.77 | +2.09 | +2.08 | +1.22 | +3.83 | False | False |
| AR4de21b01 | factor_momentum | upbit_share_7d |  | +2.21 | +2.96 | +2.47 | +1.74 | +7.53 | False | False |
| AR135a0cad | factor_momentum | upbit_share_chg |  | +2.47 | +0.90 | +1.50 | +1.03 | -3.61 | False | False |
| ARd5d71ff6 | factor_momentum | korea_share_24h |  | +0.95 | +4.21 | +2.55 | +2.10 | +5.17 | True | True |
| ARd7d1090c | factor_momentum | kimchi_prem_rel |  | +0.90 | +1.07 | +1.21 | +0.69 | -4.14 | False | False |
| ARd481ec5f | factor_momentum | kimchi_prem_chg |  | +1.31 | +1.76 | +2.29 | +1.17 | -1.43 | False | False |
| AR834cc5b1 | xs_sort | ls_top_minus_global |  | -0.59 | +2.80 | +3.04 | +2.57 | +3.90 | False | False |
| AR2fc91cc8 | xs_sort | taker_ratio_24h |  | +1.36 | -0.54 | -0.35 | -0.56 | -15.89 | False | False |
| AR6d1a9285 | factor_momentum | rv_7d |  | +1.40 | -0.26 | -1.16 | -2.03 | -31.73 | False | False |
| ARff2cc7c3 | factor_momentum | ivol_7d |  | +1.56 | +0.96 | -2.17 | -3.16 | -42.78 | False | False |
| ARab955928 | factor_momentum | rsj_7d |  | +1.12 | -1.24 | -1.64 | -1.96 | -24.01 | False | False |
| ARcc58de45 | factor_momentum | jump_share_7d |  | -1.04 | -1.02 | -1.42 | -1.59 | -25.08 | False | False |
| ARbedef628 | factor_momentum | skew_7d |  | +1.48 | +0.55 | -1.18 | -1.09 | -12.77 | False | False |

## Survivors (full rule)

- ARd5d71ff6 factor_momentum korea_share_24h: holdout FM t +4.21, alpha t +2.55, band net +5.17 bp/day -> forward

## Near misses (holdout FM t >= 1.5, failed a check)

- AR4cc495f9 xs_sort korea_share_24h: holdout t +3.15; failed ['bhy', 'insample_sign', 'lag']
- ARa200b4d5 xs_sort upbit_share_7d: holdout t +3.12; failed ['bhy']
- AR4de21b01 factor_momentum upbit_share_7d: holdout t +2.96; failed ['bhy']
- AR834cc5b1 xs_sort ls_top_minus_global: holdout t +2.80; failed ['bhy', 'insample_sign']
- AR28741da3 xs_sort upbit_share_24h: holdout t +2.59; failed ['bhy']
- ARc07576b7 xs_sort ivol_7d: holdout t +2.13; failed ['bhy', 'lag', 'dsort', 'band_net']
- ARe1c863dd xs_sort taker_share_7d: holdout t +2.13; failed ['bhy']
- AR018061a7 factor_momentum upbit_share_24h: holdout t +2.09; failed ['bhy', 'lag', 'dsort']

## Forward paper tests (clean evidence)

| test | closed | needed | mean net % | CI low % |
|---|---|---|---|---|
| F1 Upbit notice | 0 | 15 | – | – |
| F2 pump CNN | 30 | 300 | +1.95 | -2.82 |
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

- next 30 queued by method/state: {'factor_momentum': 11, 'layered/btc_30d_down': 3, 'layered/mkt_vol_high': 3, 'layered/breadth_low': 3, 'layered/funding_crowded': 3, 'layered/weekend': 3, 'layered/korea_hot': 3, 'mfd_gate': 1}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
