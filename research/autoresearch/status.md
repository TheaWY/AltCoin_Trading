# Autonomous research status - 2026-10-02 02:15 KST

Hypotheses: 38 tested (0 errors), 0 passed the full rule, 306 queued. Running BHY over all 38 holdout p-values. Families tested: {'korea': 7, 'vol': 7, 'price': 7, 'volume': 4, 'funding': 3, 'flow': 3, 'oi': 3, 'cross': 3, 'method': 1}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| ARfcc37771 | xs_sort | funding_dev |  | +2.20 | +0.64 | +0.84 | +1.10 | -20.96 | False | False |
| ARf5add01c | xs_sort | funding_z |  | -0.81 | +1.40 | +0.89 | +1.40 | -5.95 | False | False |
| AR6a939563 | xs_sort | taker_share_24h |  | +0.02 | +1.32 | +1.16 | +2.05 | -6.96 | False | False |
| ARe1c863dd | xs_sort | taker_share_7d |  | +1.50 | +2.13 | +2.63 | +3.02 | +7.18 | False | False |
| AR3020160a | xs_sort | taker_var_compression |  | +0.82 | -0.51 | +0.13 | +0.97 | -11.61 | False | False |
| ARcfe84e50 | xs_sort | oi_chg_7d |  | +0.65 | -0.90 | -0.49 | -1.49 | -14.03 | False | False |
| AR80684593 | xs_sort | oi_to_volume |  | -1.66 | +1.11 | +0.62 | +0.00 | -5.97 | False | False |
| AR58d0e056 | xs_sort | fund_x_oi |  | +3.57 | -0.79 | -0.87 | -1.40 | -13.91 | False | False |
| AR903e5796 | ctrend_combo | ALL |  | +2.00 | +0.13 | -0.30 | -0.07 | -8.99 | False | False |
| AR6bd75e46 | xs_sort | rev_1d |  | +1.59 | +0.60 | +1.14 | -0.02 | -12.60 | False | False |
| ARd6d82d51 | xs_sort | rev_3d |  | +1.51 | -1.39 | -0.31 | -1.59 | -17.93 | False | False |
| AR8d3299c2 | xs_sort | mom_3w |  | +0.90 | +0.95 | +0.17 | +1.61 | -1.85 | False | False |
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

## Survivors (full rule)

- none yet

## Near misses (holdout FM t >= 1.5, failed a check)

- AR4cc495f9 xs_sort korea_share_24h: holdout t +3.15; failed ['bhy', 'insample_sign', 'lag']
- ARa200b4d5 xs_sort upbit_share_7d: holdout t +3.12; failed ['bhy']
- AR28741da3 xs_sort upbit_share_24h: holdout t +2.59; failed ['bhy']
- ARc07576b7 xs_sort ivol_7d: holdout t +2.13; failed ['bhy', 'lag', 'dsort', 'band_net']
- ARe1c863dd xs_sort taker_share_7d: holdout t +2.13; failed ['bhy']
- AR018061a7 factor_momentum upbit_share_24h: holdout t +2.09; failed ['bhy', 'lag', 'dsort']
- AR2816e16c xs_sort kimchi_prem_chg: holdout t +2.01; failed ['bhy', 'lag', 'dsort', 'band_net']
- ARd224444c xs_sort kimchi_prem_rel: holdout t +1.69; failed ['holdout_t2', 'bhy', 'lag', 'band_net']

## Forward paper tests (clean evidence)

| test | closed | needed | mean net % | CI low % |
|---|---|---|---|---|
| F1 Upbit notice | 0 | 15 | – | – |
| F2 pump CNN | 29 | 300 | +2.66 | -1.86 |
| F3 crash rebound | 0 | 30 | – | – |
| F4 spot-led | 2 | 100 | -26.74 | – |
| F5 unlock short | 0 | 60 | – | – |
| F7 Upbit share weekly | 0 | 12 | – | – |
| F8 late-session | 0 | 120 | – | – |
| F9 Upbit-listing fade | 0 | 30 | – | – |
| AR28741da3 upbit_share_24h | 0 | 60 | – | – |
| ARa200b4d5 upbit_share_7d | 0 | 60 | – | – |

## Next (queue head) and why

- next 30 queued by method/state: {'factor_momentum': 21, 'xs_sort': 2, 'mfd_gate': 1, 'layered/btc_30d_down': 1, 'layered/mkt_vol_high': 1, 'layered/breadth_low': 1, 'layered/funding_crowded': 1, 'layered/weekend': 1, 'layered/korea_hot': 1}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
