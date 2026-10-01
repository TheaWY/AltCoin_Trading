# Autonomous research status - 2026-10-02 08:15 KST

Hypotheses: 108 tested (0 errors), 1 passed the full rule, 236 queued. Running BHY over all 108 holdout p-values. Families tested: {'korea': 48, 'vol': 17, 'price': 14, 'funding': 6, 'flow': 6, 'oi': 6, 'volume': 4, 'cross': 3, 'method': 2, 'positioning': 2}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| ARf6e119d3 | layered | korea_share_24h | weekend | +1.00 | +1.45 | +1.86 | +2.25 | +6.88 | False | False |
| AR578173e2 | layered | korea_share_24h | korea_hot | -0.24 | +2.29 | +1.78 | +1.62 | +3.76 | False | False |
| ARdf62fab5 | layered | kimchi_prem_rel | btc_30d_down | -0.73 | +1.30 | +0.98 | +0.44 | -6.38 | False | False |
| AR3c53c4c0 | layered | kimchi_prem_rel | mkt_vol_high | +0.75 | +0.95 | -0.07 | +0.78 | -2.10 | False | False |
| AR21a4bcb0 | layered | kimchi_prem_rel | breadth_low | +0.46 | +1.24 | +0.87 | +0.00 | -12.69 | False | False |
| AR95769b47 | layered | kimchi_prem_rel | funding_crowded | +0.82 | -1.63 | -1.49 | -0.80 | -24.00 | False | False |
| AR0598c622 | layered | kimchi_prem_rel | weekend | -0.14 | +1.58 | +1.89 | +2.27 | +5.44 | False | False |
| ARe1ccb9f4 | layered | kimchi_prem_rel | korea_hot | +0.93 | +1.07 | +1.08 | +0.64 | -6.30 | False | False |
| AR21c3218d | layered | kimchi_prem_chg | btc_30d_down | +0.34 | +1.50 | +2.09 | +1.06 | -2.18 | False | False |
| AR7b24da9e | layered | kimchi_prem_chg | mkt_vol_high | +1.31 | +2.05 | +1.16 | +1.29 | +0.92 | False | False |
| AR9af2d20e | layered | kimchi_prem_chg | breadth_low | -0.05 | +0.74 | +1.26 | +0.30 | -6.33 | False | False |
| AR792d2bd7 | layered | kimchi_prem_chg | funding_crowded | +1.19 | -0.71 | -0.85 | -1.25 | -25.11 | False | False |
| AR36cd51ff | layered | kimchi_prem_chg | weekend | +0.54 | +1.50 | +1.82 | +0.87 | -1.62 | False | False |
| AR2d8e45b7 | layered | kimchi_prem_chg | korea_hot | +1.53 | +0.92 | +1.10 | -0.54 | -8.12 | False | False |
| ARd0fe706c | factor_momentum | rev_1d |  | +2.65 | -0.27 | +0.25 | -0.63 | -20.62 | False | False |
| AR7ac49a0e | factor_momentum | rev_3d |  | +2.20 | -1.49 | -2.34 | -1.79 | -33.32 | False | False |
| AR94b62b28 | factor_momentum | mom_3w |  | +0.60 | +1.21 | -0.79 | +1.36 | -0.08 | False | False |
| AR4e2c8f9b | factor_momentum | mom_4w_skip1w |  | -0.01 | -0.56 | -0.34 | -0.16 | -11.12 | False | False |
| ARa0584b4a | factor_momentum | max_1h_7d |  | +0.33 | -0.20 | -1.53 | -2.76 | -35.84 | False | False |
| ARf905015a | factor_momentum | dist_from_30d_high |  | +1.01 | +0.89 | -1.08 | +0.80 | -7.65 | False | False |
| ARc9ab887b | factor_momentum | rangepos_24h |  | +2.00 | +0.62 | +0.24 | -0.27 | -17.02 | False | False |
| ARf6c9a5d6 | layered | rv_7d | btc_30d_down | +0.12 | +0.06 | -2.28 | -2.29 | -26.20 | False | False |
| AR70c5a2e5 | layered | rv_7d | mkt_vol_high | +1.29 | +0.44 | -0.41 | -0.48 | -8.78 | False | False |
| AR2bc0b6d9 | layered | rv_7d | breadth_low | +0.19 | +0.88 | -1.93 | -2.44 | -27.30 | False | False |

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

- next 30 queued by method/state: {'layered/funding_crowded': 5, 'layered/weekend': 5, 'layered/korea_hot': 5, 'layered/btc_30d_down': 5, 'layered/mkt_vol_high': 5, 'layered/breadth_low': 5}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
