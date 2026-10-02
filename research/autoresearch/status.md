# Autonomous research status - 2026-10-02 13:16 KST

Hypotheses: 600 tested (0 errors), 0 passed the full rule, 48 queued. Running BHY over all 600 holdout p-values. Families tested: {'method': 306, 'vol': 63, 'price': 56, 'korea': 54, 'volume': 32, 'funding': 27, 'flow': 24, 'oi': 24, 'cross': 10, 'positioning': 4}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| ARb060b42e | layered | rangepos_24h | weekend | +0.66 | -1.48 | -0.70 | -1.32 | -30.66 | False | False |
| AR4c646cbe | layered | rangepos_24h | korea_hot | +1.02 | -0.69 | -0.81 | -1.20 | -23.28 | False | False |
| AR90ad7c94 | layered | rv_7d | strategy_lost_7d | +0.98 | -0.18 | -0.95 | -0.96 | -9.65 | False | False |
| AR1b46d4a9 | layered | ivol_7d | strategy_lost_7d | +0.40 | +1.60 | -0.74 | -0.67 | -7.50 | False | False |
| AR83407c24 | layered | rsj_7d | strategy_lost_7d | +0.76 | +0.99 | +1.34 | +0.77 | -6.91 | False | False |
| AR7e83130c | layered | jump_share_7d | strategy_lost_7d | -1.22 | -0.54 | -0.77 | -1.41 | -13.49 | False | False |
| AR038f2a72 | layered | skew_7d | strategy_lost_7d | +0.71 | +0.04 | -0.12 | -0.63 | -10.30 | False | False |
| ARf27d2db1 | layered | vol_of_vol_7d | strategy_lost_7d | -0.21 | +1.42 | -0.05 | +0.05 | -5.10 | False | False |
| ARd9c05970 | layered | session_asia_var_share | strategy_lost_7d | +0.57 | +0.08 | -0.37 | +0.07 | -11.97 | False | False |
| AR716c4a43 | layered | vol_surprise | btc_30d_down | +1.10 | -0.85 | -0.27 | -0.15 | -14.58 | False | False |
| AR40c59681 | layered | vol_surprise | mkt_vol_high | -0.32 | -1.63 | -2.25 | -0.70 | -22.62 | False | False |
| AR9860660c | layered | vol_surprise | breadth_low | +1.03 | +0.31 | +0.05 | +0.72 | -7.49 | False | False |
| AR515cd173 | layered | vol_surprise | funding_crowded | +1.38 | -0.46 | -2.12 | -1.34 | -30.94 | False | False |
| ARa486ecb0 | layered | vol_surprise | weekend | +1.54 | +0.73 | -0.11 | +1.12 | -9.95 | False | False |
| AR9d886751 | layered | vol_surprise | korea_hot | +1.34 | -0.10 | -0.66 | -0.15 | -11.39 | False | False |
| AR4311daa4 | layered | amihud_7d | btc_30d_down | +0.76 | -1.57 | +0.86 | +0.57 | -10.26 | False | False |
| ARf3136269 | layered | amihud_7d | mkt_vol_high | -0.28 | +0.09 | +0.45 | +1.78 | +5.47 | False | False |
| AR2310d17b | layered | amihud_7d | breadth_low | +0.75 | -0.50 | +1.28 | +1.51 | +4.36 | False | False |
| AR8a6522e5 | layered | amihud_7d | funding_crowded | -1.76 | -1.59 | -0.88 | -1.07 | -13.33 | False | False |
| AR06ad0f39 | layered | amihud_7d | weekend | +0.84 | -0.43 | +0.20 | +0.39 | -6.24 | False | False |
| ARb06bc25e | layered | amihud_7d | korea_hot | -0.28 | +0.43 | +0.91 | +1.34 | +0.08 | False | False |
| AR4ef51dfc | layered | trades_per_dollar | btc_30d_down | +0.64 | -1.41 | -2.82 | -2.18 | -24.03 | False | False |
| ARff291217 | layered | trades_per_dollar | mkt_vol_high | +0.51 | -1.07 | -2.49 | -2.14 | -20.71 | False | False |
| AR04a2497e | layered | trades_per_dollar | breadth_low | +1.44 | -0.33 | -2.29 | -2.40 | -28.51 | False | False |
| ARf888ea8f | layered | trades_per_dollar | funding_crowded | +1.17 | -0.94 | -0.61 | -0.56 | -14.35 | False | False |
| AR50d9424c | layered | trades_per_dollar | weekend | +1.27 | -0.32 | -0.45 | -0.66 | -18.49 | False | False |
| AR7f2b5019 | layered | trades_per_dollar | korea_hot | +1.43 | -0.84 | -0.45 | -0.78 | -15.57 | False | False |
| AR694bbe87 | layered | avg_trade_size_z | btc_30d_down | +1.80 | -1.22 | -1.37 | -0.70 | -24.23 | False | False |
| ARe532c2cf | layered | avg_trade_size_z | mkt_vol_high | +0.48 | -1.43 | -1.57 | -0.28 | -22.18 | False | False |
| ARacc96681 | layered | avg_trade_size_z | breadth_low | +1.37 | -0.01 | +0.10 | +0.98 | -13.52 | False | False |
| ARe7c4d603 | layered | avg_trade_size_z | funding_crowded | -0.20 | -0.18 | +0.56 | +0.95 | -3.48 | False | False |
| AR810bc679 | layered | avg_trade_size_z | weekend | +2.05 | +0.52 | -0.52 | +0.29 | -15.04 | False | False |
| AR0b6a6189 | layered | avg_trade_size_z | korea_hot | +0.75 | +0.77 | +0.91 | +1.33 | -11.72 | False | False |
| ARe2f259ef | layered | funding_7d | strategy_lost_7d | +2.16 | -0.39 | -0.60 | -0.53 | -17.16 | False | False |
| AR3dfcb595 | layered | funding_dev | strategy_lost_7d | +1.50 | +0.72 | +0.69 | +1.37 | -17.02 | False | False |
| ARf1bef8c9 | layered | funding_z | strategy_lost_7d | +0.14 | -0.04 | -0.72 | -0.26 | -15.61 | False | False |
| AR07c07e58 | layered | gap_vs_btc_24h | btc_30d_down | +0.07 | -0.70 | -1.14 | -0.36 | -29.82 | False | False |
| AR8add515c | layered | gap_vs_btc_24h | mkt_vol_high | -1.08 | -1.28 | -1.24 | -0.32 | -29.07 | False | False |
| AR6ae5228d | layered | gap_vs_btc_24h | breadth_low | -2.59 | -1.09 | -2.08 | -1.03 | -28.56 | False | False |
| AR1c21bfc1 | layered | gap_vs_btc_24h | funding_crowded | -0.32 | +0.21 | -0.44 | +0.77 | -7.98 | False | False |

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

- none yet

## Near misses (holdout FM t >= 1.5, failed a check)

- ARd5d71ff6 factor_momentum korea_share_24h: holdout t +4.21; failed ['bhy']
- AR4cc495f9 xs_sort korea_share_24h: holdout t +3.15; failed ['bhy', 'insample_sign', 'lag']
- ARa200b4d5 xs_sort upbit_share_7d: holdout t +3.12; failed ['bhy']
- AR3b063c70 layered funding_z x korea_hot: holdout t +3.01; failed ['bhy', 'insample_sign', 'interaction']
- AR4de21b01 factor_momentum upbit_share_7d: holdout t +2.96; failed ['bhy']
- AR780a3b80 layered korea_share_24h x mkt_vol_high: holdout t +2.84; failed ['bhy', 'lag', 'interaction']
- AR834cc5b1 xs_sort ls_top_minus_global: holdout t +2.80; failed ['bhy', 'insample_sign']
- AR3410ef58 layered korea_share_24h x btc_30d_down: holdout t +2.77; failed ['bhy', 'insample_sign', 'lag', 'dsort', 'interaction']

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

- next 30 queued by method/state: {'layered/strategy_lost_7d': 6, 'layered/weekend': 4, 'layered/korea_hot': 4, 'layered/btc_30d_down': 4, 'layered/mkt_vol_high': 4, 'layered/breadth_low': 4, 'layered/funding_crowded': 4}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
