# B30: Claude's own pre-registered study (a-priori signs, holdout 2025-07-01..2026-09-23)

## Part 1: cross-section, HOLDOUT (decisive)

| hypothesis | HML bp/day | NW t | VW t | MR p | FM t | size-dsort t | 3F alpha t | GRS p | turnover | net bp | break-even bp | band net bp | 1h-lag t | weekly t | BHY | confirmed | strong |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H1_CARRY | -13.46 | -0.99 | +0.01 | 0.59 | -0.59 | -1.24 | -0.91 | 0.50 | 0.26 | -21.92 | -25.41 | -14.53 | -0.79 | -0.57 | False | False | False |
| H2_KOREA | +24.77 | +2.32 | +2.72 | 0.00 | +2.60 | +1.87 | +2.45 | 0.19 | 0.33 | +15.42 | +38.11 | +5.29 | +1.59 | +3.72 | False | False | False |
| H3_FLOW | +13.11 | +1.39 | +1.44 | 0.52 | +1.45 | +1.91 | +1.21 | 0.13 | 0.74 | -7.01 | +8.91 | -8.07 | +1.76 | +1.48 | False | False | False |
| H4_MAX | -13.58 | -0.83 | -0.91 | 0.65 | +0.85 | -1.04 | -0.65 | 0.93 | 0.28 | -21.66 | -24.48 | -10.50 | -1.10 | -1.38 | False | False | False |
| H5_IVOL | -21.01 | -1.26 | -1.15 | 0.89 | +1.76 | -1.46 | -1.13 | 0.78 | 0.22 | -27.26 | -48.62 | -12.11 | -1.49 | -1.18 | False | False | False |
| H6_BAB | +25.03 | +1.53 | +0.14 | 0.25 | +0.68 | +1.39 | +1.77 | 0.11 | 0.20 | +18.74 | +62.59 | +5.64 | +1.73 | +1.41 | False | False | False |
| H7_REV | +11.57 | +0.83 | -0.65 | 0.52 | +0.70 | +0.74 | +1.06 | 0.37 | 0.78 | -9.07 | +7.38 | -11.47 | +0.12 | +0.09 | False | False | False |
| H8_MOM3W | +10.03 | +0.63 | +0.46 | 0.83 | +0.56 | +0.57 | +0.12 | 0.09 | 0.29 | +1.47 | +17.02 | -4.52 | +1.18 | +1.16 | False | False | False |
| H9_VOLSHOCK | -2.39 | -0.16 | +0.56 | 0.27 | -0.45 | +0.13 | -0.40 | 0.95 | 0.47 | -15.78 | -2.55 | -10.57 | +0.21 | -1.09 | False | False | False |
| H10_SKEW | -5.80 | -0.51 | -0.30 | 0.92 | +0.41 | +0.04 | -0.19 | 0.61 | 0.38 | -16.80 | -7.57 | -10.53 | -0.81 | +0.53 | False | False | False |
| H11_OIGROW | -14.83 | -1.06 | -2.53 | 0.73 | -0.98 | -0.97 | -0.82 | 0.96 | 0.40 | -26.11 | -18.49 | -13.45 | -1.43 | -1.21 | False | False | False |
| H12_ILLIQ | +7.00 | +0.61 | +0.31 | 0.05 | -0.74 | +1.37 | +0.56 | 0.87 | 0.31 | -3.28 | +11.27 | -4.70 | +1.08 | -0.33 | False | False | False |
| H13_COMPOSITE | -6.27 | -0.56 | -0.63 | 0.22 | +0.43 | -0.83 | -0.53 | 0.88 | 0.45 | -18.97 | -6.90 | -10.89 | -0.75 | -0.08 | False | False | False |

In-sample (2024-05..2025-06) FM t for comparison: H1_CARRY +2.63, H2_KOREA +3.45, H3_FLOW +0.03, H4_MAX +1.66, H5_IVOL +1.46, H6_BAB +0.38, H7_REV +1.56, H8_MOM3W +0.92, H9_VOLSHOCK +1.76, H10_SKEW +1.48, H11_OIGROW +0.63, H12_ILLIQ -0.74, H13_COMPOSITE +1.89

Family on holdout (M=13): SPA p 0.811, StepM superior none, best H2_KOREA (Sharpe 0.91), Deflated Sharpe 0.215, PBO 0.05. LTW factor t on holdout: CMKT +0.02, CSMB +0.08, CMOM +0.63

## Part 2: time series - forecasting the next day of the alt index (OOS from 2025-07-01)

| predictor | R2_OS % | Clark-West p | hit rate | Pesaran-Timmermann p | CER gain bp/yr | BHY | n |
|---|---|---|---|---|---|---|---|
| TSMOM_1d | -0.44 | 0.886 | 49.8% | 0.850 | -44.47 | False | 452 |
| TSMOM_7d | -0.04 | 0.482 | 50.0% | 0.907 | -25.09 | False | 452 |
| TSMOM_28d | -0.04 | 0.500 | 50.7% | 0.500 | +0.00 | False | 452 |
| BTC_7d | -0.47 | 0.742 | 50.7% | 0.463 | -1372.74 | False | 452 |
| FUNDING | -0.51 | 0.545 | 50.7% | 0.446 | -234.13 | False | 452 |
| OI_7d | +0.01 | 0.322 | 49.8% | 0.850 | -356.19 | False | 452 |
| DVOL | -0.49 | 0.760 | 50.9% | 0.155 | +215.15 | False | 452 |
| DVOL_minus_RV | -0.22 | 0.849 | 50.0% | 0.907 | -181.98 | False | 452 |
| BREADTH | -0.06 | 0.411 | 50.7% | 0.477 | -70.57 | False | 452 |
| UPBIT_SHARE | -0.07 | 0.743 | 50.7% | 0.500 | +0.00 | False | 452 |
| KIMCHI | -0.39 | 0.872 | 50.2% | 0.836 | +26.83 | False | 452 |
| VOL_SURPRISE | -0.21 | 0.478 | 51.5% | 0.197 | -178.64 | False | 452 |
| RSZ_MEAN_COMBINATION | -0.11 | 0.786 | 50.7% | 0.492 | -7.15 | False | 452 |
| INTRADAY_00-16_to_16-24 | +1.05 | 0.004 | 50.8% | 0.322 | nan | False | 449 |
