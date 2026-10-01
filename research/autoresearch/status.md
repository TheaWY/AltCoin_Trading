# Autonomous research status - 2026-10-02 04:19 KST

Hypotheses: 62 tested (0 errors), 1 passed the full rule, 282 queued. Running BHY over all 62 holdout p-values. Families tested: {'vol': 14, 'korea': 12, 'price': 7, 'funding': 6, 'flow': 6, 'oi': 6, 'volume': 4, 'cross': 3, 'method': 2, 'positioning': 2}

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

- next 30 queued by method/state: {'layered/btc_30d_down': 5, 'layered/mkt_vol_high': 5, 'layered/breadth_low': 5, 'layered/funding_crowded': 5, 'layered/weekend': 5, 'layered/korea_hot': 5}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
