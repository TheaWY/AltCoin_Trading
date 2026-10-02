# Autonomous research status - 2026-10-02 12:16 KST

Hypotheses: 480 tested (0 errors), 1 passed the full rule, 168 queued. Running BHY over all 480 holdout p-values. Families tested: {'method': 306, 'vol': 56, 'korea': 48, 'funding': 24, 'price': 14, 'flow': 10, 'volume': 8, 'oi': 6, 'cross': 6, 'positioning': 2}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| AR9b8d9048 | layered | vol_of_vol_7d | mkt_vol_high | -1.24 | +0.95 | +0.20 | +0.19 | -3.75 | False | False |
| AR6daf3778 | layered | vol_of_vol_7d | breadth_low | -0.29 | +0.55 | -0.98 | -1.59 | -23.56 | False | False |
| ARc273d5f2 | layered | vol_of_vol_7d | funding_crowded | -0.54 | -3.16 | +0.96 | +0.57 | +16.09 | False | False |
| ARdd704bec | layered | vol_of_vol_7d | weekend | -0.38 | +0.42 | +0.43 | -0.45 | -18.59 | False | False |
| AR8aa1a5f5 | layered | vol_of_vol_7d | korea_hot | -0.19 | +0.53 | +0.13 | -0.26 | -6.84 | False | False |
| ARa298c2f4 | layered | session_asia_var_share | btc_30d_down | +0.30 | +0.64 | +0.00 | -0.28 | -10.94 | False | False |
| ARda7d3689 | layered | session_asia_var_share | mkt_vol_high | -0.19 | +0.45 | +0.62 | +1.24 | -7.06 | False | False |
| ARc0d54b0e | layered | session_asia_var_share | breadth_low | +1.08 | +0.40 | +0.48 | +0.55 | -10.04 | False | False |
| AR842858f2 | layered | session_asia_var_share | funding_crowded | +0.87 | -1.43 | -0.76 | -0.95 | -25.37 | False | False |
| ARadcf9093 | layered | session_asia_var_share | weekend | +0.47 | -1.00 | -0.38 | +0.26 | -14.89 | False | False |
| AR1e81cfe2 | layered | session_asia_var_share | korea_hot | +1.41 | -0.27 | -0.10 | -0.29 | -13.03 | False | False |
| ARdf2a0451 | factor_momentum | vol_surprise |  | +1.52 | -1.30 | -1.30 | -0.52 | -19.79 | False | False |
| AR518f4f2f | factor_momentum | amihud_7d |  | +0.63 | -1.40 | -1.29 | -0.95 | -13.41 | False | False |
| AR3e49119c | factor_momentum | trades_per_dollar |  | +1.99 | -0.02 | -2.14 | -2.07 | -30.22 | False | False |
| AR64e2be31 | factor_momentum | avg_trade_size_z |  | +1.74 | -0.45 | -0.09 | +0.40 | -16.63 | False | False |
| AR0f78fc66 | layered | funding_7d | btc_30d_down | +0.39 | -0.64 | -0.76 | -0.55 | -16.54 | False | False |
| AR9722634f | layered | funding_7d | mkt_vol_high | +1.42 | -0.91 | -1.70 | -1.39 | -23.39 | False | False |
| ARd9c6cf3f | layered | funding_7d | breadth_low | +1.70 | +0.25 | -0.29 | -0.34 | -16.84 | False | False |
| ARfa232c0e | layered | funding_7d | funding_crowded | +3.38 | +0.85 | +0.30 | +0.81 | +11.31 | False | False |
| AR880bcb81 | layered | funding_7d | weekend | +1.15 | -0.89 | -0.52 | -1.14 | -19.41 | False | False |
| AR38e4d1ec | layered | funding_7d | korea_hot | +3.08 | -1.39 | -1.83 | -1.43 | -25.24 | False | False |
| AR0fbe75fa | layered | funding_dev | btc_30d_down | +1.66 | +0.50 | +1.08 | +1.20 | -23.98 | False | False |
| AR157ba391 | layered | funding_dev | mkt_vol_high | +1.33 | +0.11 | +0.03 | +0.49 | -22.35 | False | False |
| AR5925ffa1 | layered | funding_dev | breadth_low | +1.13 | +1.42 | +1.07 | +1.12 | -20.22 | False | False |
| ARf88f2b35 | layered | funding_dev | funding_crowded | +1.71 | +1.63 | +3.00 | +2.29 | +53.07 | False | False |
| AR9c1df4ab | layered | funding_dev | weekend | +1.85 | +0.39 | +0.88 | +0.66 | -15.38 | False | False |
| AR6fef1946 | layered | funding_dev | korea_hot | +2.52 | +0.16 | +0.00 | +0.41 | -18.54 | False | False |
| AR175d355e | layered | funding_z | btc_30d_down | +0.62 | +1.31 | +1.73 | +2.34 | +4.15 | False | False |
| ARcc5b7dfe | layered | funding_z | mkt_vol_high | +1.13 | -0.03 | +1.11 | +0.89 | -11.49 | False | False |
| ARb8a51786 | layered | funding_z | breadth_low | +0.77 | +0.25 | +0.96 | +2.03 | -8.01 | False | False |
| AR5ad5cd4a | layered | funding_z | funding_crowded | -2.22 | +2.51 | +2.77 | +2.46 | +18.88 | False | False |
| AR92e8b375 | layered | funding_z | weekend | -0.59 | +2.23 | +0.81 | +1.13 | -3.06 | False | False |
| AR3b063c70 | layered | funding_z | korea_hot | -0.64 | +3.01 | +2.37 | +2.33 | +2.92 | False | False |
| AR1e887c9f | factor_momentum | gap_vs_btc_24h |  | +0.63 | -1.21 | -1.45 | -1.11 | -36.46 | False | False |
| AR41b305ea | factor_momentum | beta_30d |  | +1.08 | +0.38 | +1.10 | +0.67 | -2.33 | False | False |
| ARe41f174c | factor_momentum | corr_btc_7d |  | +2.00 | +0.51 | -0.98 | -1.96 | -27.82 | False | False |
| ARcbd6c788 | layered | taker_share_24h | btc_30d_down | +1.16 | +0.44 | +0.34 | +0.84 | -15.94 | False | False |
| AReda400a6 | layered | taker_share_24h | mkt_vol_high | +0.98 | +0.60 | +1.30 | +1.56 | -10.06 | False | False |
| AR84fe3f4f | layered | taker_share_24h | breadth_low | +0.17 | -0.63 | -1.82 | +0.20 | -16.65 | False | False |
| ARf3f42a52 | layered | taker_share_24h | funding_crowded | +1.09 | +0.77 | +0.97 | +1.26 | -1.85 | False | False |

## D-series: market direction (sign of the EW market return over the next h; holdout)

304 (variable x horizon) tested; a direction pass needs slope t >= 2, BHY, in-sample sign, PT p < .05, AUC CI low > .5, Clark-West p < .05, utility gain > 0 and Sharpe > hist-mean timing.

| id | variable | h | in t | hold t | PT hit | PT p | AUC [lo] | R2os | CW p | SR net / HM | CT gain | checks ok |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ARd22b969d | korea_heavy_minus_rest_24h | 24h | +1.21 | +2.24 | 0.494 | 0.543 | 0.523 [0.472] | +0.0069 | 0.035 | +1.47 / +1.76 | +0.380 | 3/7 |
| AR54a31455 | vrp_30d | 24h|btc_30d_down | +1.01 | +1.85 | 0.548 | 0.081 | 0.575 [0.519] | +0.0051 | 0.099 | +0.74 / +1.13 | +0.080 | 2/7 |
| AR5101d2bd | ALL | 24h | -0.03 | +1.73 | 0.530 | 0.200 | 0.536 [0.491] | -0.0520 | 0.048 | +1.42 / +1.76 | -0.283 | 1/7 |
| ARc303d4db | agg_funding_24h | 24h|mkt_vol_high | +0.61 | +1.72 | 0.507 | 0.418 | 0.530 [0.470] | +0.0058 | 0.091 | -0.45 / -0.74 | +0.311 | 2/7 |
| ARf5c5eba3 | mkt_ret_1h | 1w | +0.80 | +1.71 | 0.406 | 0.933 | 0.486 [0.377] | +0.0057 | 0.098 | +1.99 / +1.99 | +0.104 | 1/7 |
| ARde1b2dbb | vrp_30d | 24h|weekend | +1.44 | +1.55 | 0.539 | 0.177 | 0.579 [0.490] | +0.0118 | 0.077 | +2.21 / +0.98 | -0.378 | 1/7 |
| AR0405ee75 | xs_skew_24h | 4h | -1.41 | +1.54 | 0.503 | 0.377 | 0.508 [0.486] | -0.0013 | 0.994 | +1.97 / +1.86 | -0.125 | 0/7 |
| AR8be15975 | btc_minus_alts_24h | 1w | -0.39 | +1.54 | 0.422 | 0.905 | 0.478 [0.357] | +0.0003 | 0.410 | +1.99 / +1.99 | -0.003 | 0/7 |
| AR37fc7861 | vrp_30d | 1h|btc_30d_down | +0.71 | +1.51 | 0.504 | 0.244 | 0.508 [0.494] | +0.0002 | 0.150 | +1.10 / +1.01 | -0.134 | 1/7 |
| AR1f2f7b43 | agg_funding_24h | 4h|mkt_vol_high | +0.27 | +1.48 | 0.514 | 0.159 | 0.525 [0.489] | +0.0005 | 0.184 | +0.15 / +1.13 | +0.154 | 1/7 |
| ARbdf67335 | xs_skew_24h | 1h | -1.35 | +1.46 | 0.498 | 0.589 | 0.500 [0.488] | -0.0004 | 0.990 | +1.74 / +1.83 | -0.324 | 0/7 |
| AR507fd743 | agg_taker_buy_24h | 24h | +1.44 | +1.46 | 0.499 | 0.488 | 0.502 [0.456] | +0.0003 | 0.105 | +1.82 / +1.76 | +0.021 | 2/7 |
| AR735486ca | agg_funding_24h | 1h|mkt_vol_high | +0.23 | +1.36 | 0.493 | 0.837 | 0.500 [0.484] | +0.0001 | 0.194 | +0.21 / +0.57 | +0.000 | 1/7 |
| ARc54dcb6b | agg_ls_global | 4h | +1.35 | +1.35 | 0.512 | 0.123 | 0.510 [0.485] | +0.0004 | 0.121 | +1.30 / +1.86 | -0.219 | 1/7 |
| AR8b57f660 | korea_share_total_24h | 4h | -0.65 | +1.35 | 0.500 | 0.589 | 0.495 [0.472] | -0.0004 | 0.727 | +1.86 / +1.86 | -0.071 | 0/7 |
| AR078da74c | agg_ls_global | 1w | +1.04 | +1.33 | 0.641 | 0.004 | 0.639 [0.546] | +0.0049 | 0.119 | +1.80 / +1.99 | -0.284 | 3/7 |
| AR8e346e37 | vrp_30d | 24h | +0.12 | +1.30 | 0.535 | 0.043 | 0.541 [0.505] | -0.0007 | 0.496 | +1.76 / +1.76 | -0.022 | 3/7 |
| AR04ef3922 | agg_ls_global | 1h | +1.27 | +1.26 | 0.498 | 0.768 | 0.494 [0.484] | +0.0001 | 0.140 | +1.03 / +1.83 | +0.114 | 1/7 |
| AR857dcc40 | vrp_30d | 4h|weekend | +0.99 | +1.24 | 0.513 | 0.358 | 0.516 [0.481] | +0.0013 | 0.125 | +0.46 / +0.90 | -0.432 | 1/7 |
| AR4106d7fd | vrp_30d | 1w | +0.12 | +1.21 | 0.438 | 0.708 | 0.515 [0.382] | -0.0006 | 0.403 | +1.99 / +1.99 | -0.022 | 1/7 |
| AReca9a40d | vrp_30d | 1h|weekend | +0.99 | +1.21 | 0.510 | 0.238 | 0.504 [0.489] | +0.0003 | 0.140 | -0.07 / +0.86 | -0.044 | 1/7 |
| ARdb9d9839 | agg_funding_chg_7d | 1w | +2.18 | +1.20 | 0.578 | 0.153 | 0.634 [0.491] | -0.0022 | 0.146 | +3.07 / +1.99 | +0.889 | 2/7 |
| AR0ab55205 | vrp_30d | 4h|btc_30d_down | +0.96 | +1.19 | 0.506 | 0.326 | 0.506 [0.479] | +0.0002 | 0.202 | +0.31 / +0.06 | -0.067 | 1/7 |
| AR8409ab90 | korea_share_total_24h | 1h | -0.57 | +1.17 | 0.502 | 0.672 | 0.495 [0.485] | -0.0001 | 0.707 | +1.83 / +1.83 | -0.057 | 0/7 |
| ARb6455628 | agg_ls_global | 24h | +0.92 | +1.11 | 0.492 | 0.566 | 0.488 [0.438] | +0.0002 | 0.223 | +1.45 / +1.76 | -0.067 | 1/7 |
| AR137adff6 | korea_share_total_24h | 1w | -0.49 | +1.10 | 0.359 | 0.894 | 0.519 [0.433] | -0.0102 | 0.873 | +1.99 / +1.99 | +0.006 | 0/7 |
| ARefe6daab | korea_heavy_minus_rest_24h | 1h | +0.17 | +1.08 | 0.505 | 0.243 | 0.504 [0.495] | -0.0001 | 0.675 | +1.58 / +1.83 | -0.049 | 1/7 |
| AR2f94a7d2 | vrp_30d | 1h | -0.23 | +0.99 | 0.507 | 0.160 | 0.504 [0.495] | -0.0000 | 0.677 | +1.83 / +1.83 | -0.058 | 0/7 |
| AR186993bc | agg_taker_buy_24h | 1w | -1.09 | +0.96 | 0.547 | 0.174 | 0.563 [0.395] | -0.0130 | 0.895 | +1.50 / +1.99 | -0.341 | 0/7 |
| AR4a69fbb6 | rv_ratio_24h_720h | 24h | -1.19 | +0.96 | 0.465 | 0.875 | 0.474 [0.428] | -0.0082 | 0.890 | +1.05 / +1.76 | -0.497 | 0/7 |
| ARe55d5bf7 | korea_heavy_minus_rest_24h | 4h | -0.01 | +0.90 | 0.510 | 0.156 | 0.514 [0.491] | -0.0004 | 0.855 | +1.82 / +1.86 | -0.040 | 0/7 |
| AR51d2a092 | vrp_30d | 4h | -0.11 | +0.87 | 0.509 | 0.195 | 0.504 [0.484] | -0.0001 | 0.661 | +1.86 / +1.86 | -0.074 | 0/7 |
| ARa47e41e5 | rv_ratio_24h_720h | 4h | -1.81 | +0.80 | 0.497 | 0.689 | 0.503 [0.481] | -0.0038 | 0.882 | +0.72 / +1.86 | -0.471 | 0/7 |
| AR33ac4df9 | agg_funding_chg_7d | 24h | -0.02 | +0.79 | 0.499 | 0.535 | 0.498 [0.454] | -0.0009 | 0.502 | +1.69 / +1.76 | -0.025 | 0/7 |
| AR09315250 | dvol_level | 1h|mkt_vol_high | +1.09 | +0.76 | 0.492 | 0.725 | 0.497 [0.481] | -0.0002 | 0.380 | -0.32 / +0.57 | -0.439 | 1/7 |
| AR6bc75592 | dvol_level | 24h|mkt_vol_high | +1.38 | +0.74 | 0.498 | 0.534 | 0.498 [0.431] | -0.0085 | 0.396 | +0.57 / -0.74 | -0.701 | 1/7 |
| AR19567573 | large_minus_small_24h | 1w | -1.77 | +0.70 | 0.578 | 0.095 | 0.606 [0.460] | -0.0132 | 0.809 | +1.99 / +1.99 | -0.083 | 0/7 |
| ARd4e247a7 | mkt_ret_1h | 24h | +0.91 | +0.68 | 0.514 | 0.281 | 0.518 [0.467] | -0.0010 | 0.399 | +0.95 / +1.76 | -0.085 | 1/7 |
| ARb0062a43 | agg_funding_chg_7d | 1h|mkt_vol_high | -0.17 | +0.61 | 0.491 | 0.885 | 0.495 [0.480] | -0.0001 | 0.576 | +0.33 / +0.57 | -0.270 | 0/7 |
| AR9d4664e2 | dvol_level | 4h|mkt_vol_high | +1.47 | +0.61 | 0.502 | 0.395 | 0.506 [0.478] | -0.0016 | 0.432 | +0.70 / +1.13 | -0.220 | 1/7 |

## Survivors (full rule)

- ARd5d71ff6 factor_momentum korea_share_24h: holdout FM t +4.21, alpha t +2.55, band net +5.17 bp/day -> forward

## Near misses (holdout FM t >= 1.5, failed a check)

- AR4cc495f9 xs_sort korea_share_24h: holdout t +3.15; failed ['bhy', 'insample_sign', 'lag']
- ARa200b4d5 xs_sort upbit_share_7d: holdout t +3.12; failed ['bhy']
- AR3b063c70 layered funding_z x korea_hot: holdout t +3.01; failed ['bhy', 'insample_sign', 'interaction']
- AR4de21b01 factor_momentum upbit_share_7d: holdout t +2.96; failed ['bhy']
- AR780a3b80 layered korea_share_24h x mkt_vol_high: holdout t +2.84; failed ['bhy', 'lag', 'interaction']
- AR834cc5b1 xs_sort ls_top_minus_global: holdout t +2.80; failed ['bhy', 'insample_sign']
- AR3410ef58 layered korea_share_24h x btc_30d_down: holdout t +2.77; failed ['bhy', 'insample_sign', 'lag', 'dsort', 'interaction']
- ARd82ec1b3 layered korea_share_24h x funding_crowded: holdout t +2.70; failed ['bhy', 'interaction']

## Forward paper tests (clean evidence)

| test | closed | needed | mean net % | CI low % |
|---|---|---|---|---|
| F1 Upbit notice | 0 | 15 | – | – |
| F2 pump CNN | 33 | 300 | +2.08 | -2.65 |
| F3 crash rebound | 0 | 30 | – | – |
| F4 spot-led | 2 | 100 | -26.74 | – |
| F5 unlock short | 1 | 60 | +6.04 | – |
| F7 Upbit share weekly | 0 | 12 | – | – |
| F8 late-session | 1 | 120 | -0.97 | – |
| F9 Upbit-listing fade | 0 | 30 | – | – |
| AR28741da3 upbit_share_24h | 1 | 60 | -0.37 | – |
| ARa200b4d5 upbit_share_7d | 1 | 60 | -0.57 | – |
| ARd5d71ff6 korea_share_24h | 0 | 60 | – | – |

## Next (queue head) and why

- next 30 queued by method/state: {'layered/weekend': 5, 'layered/korea_hot': 5, 'layered/btc_30d_down': 5, 'layered/mkt_vol_high': 5, 'layered/breadth_low': 5, 'layered/funding_crowded': 5}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
