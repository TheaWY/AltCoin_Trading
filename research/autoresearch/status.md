# Autonomous research status - 2026-10-02 10:16 KST

Hypotheses: 240 tested (0 errors), 1 passed the full rule, 408 queued. Running BHY over all 240 holdout p-values. Families tested: {'method': 122, 'korea': 48, 'vol': 29, 'price': 14, 'funding': 6, 'flow': 6, 'oi': 6, 'volume': 4, 'cross': 3, 'positioning': 2}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| ARce003791 | ts_direction | vrp_30d | 4h|mkt_vol_high | -1.33 | -0.35 | – | – | – | False | False |
| ARa94f3025 | ts_direction | vrp_30d | 4h|funding_crowded | -0.98 | +0.50 | – | – | – | False | False |
| AR857dcc40 | ts_direction | vrp_30d | 4h|weekend | +0.99 | +1.24 | – | – | – | False | False |
| AR0ab55205 | ts_direction | vrp_30d | 4h|btc_30d_down | +0.96 | +1.19 | – | – | – | False | False |
| AR293f1dd1 | ts_direction | vrp_30d | 24h|mkt_vol_high | -0.60 | -0.03 | – | – | – | False | False |
| ARce8bf9c0 | ts_direction | vrp_30d | 24h|funding_crowded | -0.67 | – | – | – | – | False | False |
| ARde1b2dbb | ts_direction | vrp_30d | 24h|weekend | +1.44 | +1.55 | – | – | – | False | False |
| AR54a31455 | ts_direction | vrp_30d | 24h|btc_30d_down | +1.01 | +1.85 | – | – | – | False | False |
| AR09315250 | ts_direction | dvol_level | 1h|mkt_vol_high | +1.09 | +0.76 | – | – | – | False | False |
| AR394e062a | ts_direction | dvol_level | 1h|funding_crowded | +0.62 | -0.71 | – | – | – | False | False |
| AR91b013f1 | ts_direction | dvol_level | 1h|weekend | -0.85 | -0.71 | – | – | – | False | False |
| AR00970dee | ts_direction | dvol_level | 1h|btc_30d_down | +2.02 | +0.52 | – | – | – | False | False |
| AR9d4664e2 | ts_direction | dvol_level | 4h|mkt_vol_high | +1.47 | +0.61 | – | – | – | False | False |
| AR92a77a36 | ts_direction | dvol_level | 4h|funding_crowded | +0.27 | -0.59 | – | – | – | False | False |
| AR613b7f2f | ts_direction | dvol_level | 4h|weekend | -0.91 | -0.94 | – | – | – | False | False |
| ARb08e2e35 | ts_direction | dvol_level | 4h|btc_30d_down | +2.33 | +0.54 | – | – | – | False | False |
| AR6bc75592 | ts_direction | dvol_level | 24h|mkt_vol_high | +1.38 | +0.74 | – | – | – | False | False |
| AR15c4beeb | ts_direction | dvol_level | 24h|funding_crowded | +0.00 | – | – | – | – | False | False |
| ARf6a96383 | ts_direction | dvol_level | 24h|weekend | -0.77 | -0.78 | – | – | – | False | False |
| AR096b6229 | ts_direction | dvol_level | 24h|btc_30d_down | +2.60 | +0.41 | – | – | – | False | False |
| AR735486ca | ts_direction | agg_funding_24h | 1h|mkt_vol_high | +0.23 | +1.36 | – | – | – | False | False |
| AR5c6ca548 | ts_direction | agg_funding_24h | 1h|funding_crowded | -1.49 | -0.99 | – | – | – | False | False |
| AR5d5b18fd | ts_direction | agg_funding_24h | 1h|weekend | -1.94 | -0.44 | – | – | – | False | False |
| AR72a2c2b8 | ts_direction | agg_funding_24h | 1h|btc_30d_down | +1.86 | +0.31 | – | – | – | False | False |
| AR1f2f7b43 | ts_direction | agg_funding_24h | 4h|mkt_vol_high | +0.27 | +1.48 | – | – | – | False | False |
| AR715b3ad8 | ts_direction | agg_funding_24h | 4h|funding_crowded | -1.74 | -1.11 | – | – | – | False | False |
| ARd9a9bda4 | ts_direction | agg_funding_24h | 4h|weekend | -2.18 | -0.52 | – | – | – | False | False |
| AR5b026c71 | ts_direction | agg_funding_24h | 4h|btc_30d_down | +1.54 | +0.50 | – | – | – | False | False |
| ARc303d4db | ts_direction | agg_funding_24h | 24h|mkt_vol_high | +0.61 | +1.72 | – | – | – | False | False |
| AR43d22266 | ts_direction | agg_funding_24h | 24h|funding_crowded | -0.90 | – | – | – | – | False | False |
| ARc92a8e52 | ts_direction | agg_funding_24h | 24h|weekend | -2.22 | -0.12 | – | – | – | False | False |
| AR7a5b0d74 | ts_direction | agg_funding_24h | 24h|btc_30d_down | +1.74 | +0.37 | – | – | – | False | False |
| ARb0062a43 | ts_direction | agg_funding_chg_7d | 1h|mkt_vol_high | -0.17 | +0.61 | – | – | – | False | False |
| ARe20b3a8c | ts_direction | agg_funding_chg_7d | 1h|funding_crowded | -1.05 | +0.25 | – | – | – | False | False |
| ARc272f0c5 | ts_direction | agg_funding_chg_7d | 1h|weekend | -1.67 | +0.52 | – | – | – | False | False |
| AR37417959 | ts_direction | agg_funding_chg_7d | 1h|btc_30d_down | +1.06 | +0.91 | – | – | – | False | False |
| AR78f115ef | ts_direction | agg_funding_chg_7d | 4h|mkt_vol_high | -0.11 | +0.97 | – | – | – | False | False |
| AR02cbce45 | ts_direction | agg_funding_chg_7d | 4h|funding_crowded | -1.23 | +0.41 | – | – | – | False | False |
| ARfc22a783 | ts_direction | agg_funding_chg_7d | 4h|weekend | -2.04 | +0.53 | – | – | – | False | False |
| ARd072f28b | ts_direction | agg_funding_chg_7d | 4h|btc_30d_down | +0.85 | +1.11 | – | – | – | False | False |

## D-series: market direction (sign of the EW market return over the next h; holdout)

120 (variable x horizon) tested; a direction pass needs slope t >= 2, BHY, in-sample sign, PT p < .05, AUC CI low > .5, Clark-West p < .05, utility gain > 0 and Sharpe > hist-mean timing.

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
| ARd072f28b | agg_funding_chg_7d | 4h|btc_30d_down | +0.85 | +1.11 | 0.502 | 0.461 | 0.504 [0.473] | +0.0003 | 0.184 | +2.62 / +0.06 | +0.220 | 2/7 |
| ARb6455628 | agg_ls_global | 24h | +0.92 | +1.11 | 0.492 | 0.566 | 0.488 [0.438] | +0.0002 | 0.223 | +1.45 / +1.76 | -0.067 | 1/7 |
| AR137adff6 | korea_share_total_24h | 1w | -0.49 | +1.10 | 0.359 | 0.894 | 0.519 [0.433] | -0.0102 | 0.873 | +1.99 / +1.99 | +0.006 | 0/7 |
| ARefe6daab | korea_heavy_minus_rest_24h | 1h | +0.17 | +1.08 | 0.505 | 0.243 | 0.504 [0.495] | -0.0001 | 0.675 | +1.58 / +1.83 | -0.049 | 1/7 |
| AR2f94a7d2 | vrp_30d | 1h | -0.23 | +0.99 | 0.507 | 0.160 | 0.504 [0.495] | -0.0000 | 0.677 | +1.83 / +1.83 | -0.058 | 0/7 |
| AR78f115ef | agg_funding_chg_7d | 4h|mkt_vol_high | -0.11 | +0.97 | 0.483 | 0.883 | 0.502 [0.475] | -0.0005 | 0.508 | +1.57 / +1.13 | -0.302 | 0/7 |
| AR186993bc | agg_taker_buy_24h | 1w | -1.09 | +0.96 | 0.547 | 0.174 | 0.563 [0.395] | -0.0130 | 0.895 | +1.50 / +1.99 | -0.341 | 0/7 |
| AR4a69fbb6 | rv_ratio_24h_720h | 24h | -1.19 | +0.96 | 0.465 | 0.875 | 0.474 [0.428] | -0.0082 | 0.890 | +1.05 / +1.76 | -0.497 | 0/7 |
| AR37417959 | agg_funding_chg_7d | 1h|btc_30d_down | +1.06 | +0.91 | 0.503 | 0.388 | 0.506 [0.490] | -0.0001 | 0.207 | +1.76 / +1.01 | +0.424 | 2/7 |
| ARe55d5bf7 | korea_heavy_minus_rest_24h | 4h | -0.01 | +0.90 | 0.510 | 0.156 | 0.514 [0.491] | -0.0004 | 0.855 | +1.82 / +1.86 | -0.040 | 0/7 |
| AR51d2a092 | vrp_30d | 4h | -0.11 | +0.87 | 0.509 | 0.195 | 0.504 [0.484] | -0.0001 | 0.661 | +1.86 / +1.86 | -0.074 | 0/7 |
| ARa47e41e5 | rv_ratio_24h_720h | 4h | -1.81 | +0.80 | 0.497 | 0.689 | 0.503 [0.481] | -0.0038 | 0.882 | +0.72 / +1.86 | -0.471 | 0/7 |
| AR33ac4df9 | agg_funding_chg_7d | 24h | -0.02 | +0.79 | 0.499 | 0.535 | 0.498 [0.454] | -0.0009 | 0.502 | +1.69 / +1.76 | -0.025 | 0/7 |
| AR09315250 | dvol_level | 1h|mkt_vol_high | +1.09 | +0.76 | 0.492 | 0.725 | 0.497 [0.481] | -0.0002 | 0.380 | -0.32 / +0.57 | -0.439 | 1/7 |
| AR6bc75592 | dvol_level | 24h|mkt_vol_high | +1.38 | +0.74 | 0.498 | 0.534 | 0.498 [0.431] | -0.0085 | 0.396 | +0.57 / -0.74 | -0.701 | 1/7 |
| AR19567573 | large_minus_small_24h | 1w | -1.77 | +0.70 | 0.578 | 0.095 | 0.606 [0.460] | -0.0132 | 0.809 | +1.99 / +1.99 | -0.083 | 0/7 |

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
| F8 late-session | 1 | 120 | -0.97 | – |
| F9 Upbit-listing fade | 0 | 30 | – | – |
| AR28741da3 upbit_share_24h | 1 | 60 | -0.37 | – |
| ARa200b4d5 upbit_share_7d | 1 | 60 | -0.57 | – |
| ARd5d71ff6 korea_share_24h | 0 | 60 | – | – |

## Next (queue head) and why

- next 30 queued by method/state: {'layered/funding_crowded': 5, 'layered/weekend': 5, 'layered/korea_hot': 5, 'layered/btc_30d_down': 4, 'layered/mkt_vol_high': 4, 'layered/breadth_low': 4, 'factor_momentum': 3}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
