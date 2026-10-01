# Autonomous research status - 2026-10-02 00:15 KST

Hypotheses: 14 tested (0 errors), 1 passed the full rule, 330 queued. Running BHY over all 14 holdout p-values. Families tested: {'vol': 7, 'korea': 6, 'funding': 1}

## Last hour

| id | method | variable | state | in FM t | holdout FM t | alpha t | lag t | band net bp | BHY | pass |
|---|---|---|---|---|---|---|---|---|---|---|
| AR28741da3 | xs_sort | upbit_share_24h |  | +3.44 | +2.59 | +2.56 | +1.57 | +5.26 | False | False |
| ARa200b4d5 | xs_sort | upbit_share_7d |  | +2.24 | +3.12 | +2.56 | +1.80 | +7.29 | True | True |
| AR4061dbe0 | xs_sort | upbit_share_chg |  | +3.10 | +0.40 | +1.23 | +0.89 | -9.71 | False | False |
| AR4cc495f9 | xs_sort | korea_share_24h |  | -0.19 | +3.15 | +1.83 | +1.47 | +2.59 | True | False |
| ARd224444c | xs_sort | kimchi_prem_rel |  | +1.02 | +1.69 | +1.68 | +0.90 | -4.13 | False | False |
| AR2816e16c | xs_sort | kimchi_prem_chg |  | +1.64 | +2.01 | +2.20 | +0.80 | -6.43 | False | False |
| AR9455e217 | xs_sort | rv_7d |  | +1.06 | +0.01 | -0.99 | -1.48 | -12.53 | False | False |
| ARc07576b7 | xs_sort | ivol_7d |  | +1.42 | +2.13 | -0.90 | -1.41 | -11.74 | False | False |
| AR3bc90575 | xs_sort | rsj_7d |  | +0.73 | -0.03 | +0.62 | -0.82 | -12.27 | False | False |
| AR688524d8 | xs_sort | jump_share_7d |  | -0.86 | -0.04 | -0.46 | -0.92 | -11.68 | False | False |
| AR08727cd2 | xs_sort | skew_7d |  | +1.51 | +0.11 | -0.20 | -1.17 | -12.60 | False | False |
| AR0ffc06ae | xs_sort | vol_of_vol_7d |  | -0.52 | +0.27 | -0.35 | -0.91 | -11.01 | False | False |
| AR1e94a090 | xs_sort | session_asia_var_share |  | +0.96 | +0.44 | +0.46 | +0.45 | -7.10 | False | False |
| AR9a8d223e | xs_sort | funding_7d |  | +2.65 | -0.57 | -0.95 | -0.89 | -14.62 | False | False |

## Survivors (full rule)

- ARa200b4d5 xs_sort upbit_share_7d: holdout FM t +3.12, alpha t +2.56, band net +7.29 bp/day -> forward

## Near misses (holdout FM t >= 1.5, failed a check)

- AR4cc495f9 xs_sort korea_share_24h: holdout t +3.15; failed ['insample_sign', 'lag']
- AR28741da3 xs_sort upbit_share_24h: holdout t +2.59; failed ['bhy']
- ARc07576b7 xs_sort ivol_7d: holdout t +2.13; failed ['bhy', 'lag', 'dsort', 'band_net']
- AR2816e16c xs_sort kimchi_prem_chg: holdout t +2.01; failed ['bhy', 'lag', 'dsort', 'band_net']
- ARd224444c xs_sort kimchi_prem_rel: holdout t +1.69; failed ['holdout_t2', 'bhy', 'lag', 'band_net']

## Forward paper tests (clean evidence)

| test | closed | needed | mean net % | CI low % |
|---|---|---|---|---|
| F1 Upbit notice | 0 | 15 | – | – |
| F2 pump CNN | 28 | 300 | +2.41 | -2.55 |
| F3 crash rebound | 0 | 30 | – | – |
| F4 spot-led | 2 | 100 | -26.74 | – |
| F5 unlock short | 0 | 60 | – | – |
| F7 Upbit share weekly | 0 | 12 | – | – |
| F8 late-session | 0 | 120 | – | – |
| F9 Upbit-listing fade | 0 | 30 | – | – |
| AR28741da3 upbit_share_24h | 0 | 60 | – | – |
| ARa200b4d5 upbit_share_7d | 0 | 60 | – | – |

## Next (queue head) and why

- next 30 queued by method/state: {'xs_sort': 23, 'factor_momentum': 6, 'ctrend_combo': 1}
- order = literature strength first (Korea retail, funding/carry, higher moments), then flow/OI, then price/volume, then layered state x signal (Nagel 2012, Stambaugh-Yu-Yuan, factor momentum) and model-level combinations (Lewellen/Fieberg CTREND, MFD gate).
- promotion rule: holdout FM t >= 2 with the a-priori sign AND running BHY (q=0.05) over every hypothesis the engine has tested AND in-sample FM t > 0 AND 1h-lag t > 1.5 AND size-double-sort t > 1.5 AND band net > 0 -> promoted to a forward paper test; the forward test passes after >= 60 daily books with day-net bootstrap CI > 0, which admits it to the main book.

## Rules in force

- a-priori sign from the catalogue, never fitted; in-sample 2024-05..2025-06 reported, holdout 2025-07..2026-09 decides
- every hypothesis counted in the running BHY; survivors go to a forward paper test; the main book admits only passed forward tests
