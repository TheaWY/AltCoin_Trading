# B25: can we tell a bad day before it starts? (OOS 2025-01..2026-09, walk-forward)

| target | base rate | persistence AUC | logistic AUC [CI] | LightGBM AUC [CI] | 2025 / 2026 (best) | pass |
|---|---|---|---|---|---|---|
| DOWN | 51% | 0.484 | 0.466 [0.426, 0.507] | 0.504 [0.463, 0.546] | 0.481 / 0.544 | - |
| CRASH | 14% | 0.405 | 0.550 [0.495, 0.606] | 0.562 [0.503, 0.619] | 0.556 / 0.519 | - |
| BIGMOVE | 38% | 0.471 | 0.559 [0.508, 0.611] | 0.574 [0.521, 0.627] | 0.538 / 0.483 | - |
| HIVOL | 48% | 0.656 | 0.692 [0.644, 0.738] | 0.725 [0.677, 0.767] | 0.710 / 0.750 | PASS |
| FADE | 59% | 0.516 | 0.500 [0.451, 0.552] | 0.488 [0.442, 0.532] | 0.488 / 0.515 | - |
| VSHLOSS | 43% | 0.470 | 0.541 [0.495, 0.584] | 0.511 [0.470, 0.551] | 0.536 / 0.548 | - |

## Applications (Sharpe; pass = better than the plain version in both 2025 and 2026)

| | 2025 | 2026 | pass |
|---|---|---|---|
| A1_switch vs ALTLONG | -0.56 vs -1.45 | -1.18 vs -1.67 | True |
| A2_altlong vs ALTLONG | -1.59 vs -1.45 | -1.64 vs -1.67 | False |
| A2_vsh vs VSH | 1.38 vs 1.78 | 0.77 vs 0.99 | False |
| A2_pfollow vs PFOLLOW | -0.51 vs 0.05 | -1.44 vs -1.57 | False |
| A3_pfollow_gate vs PFOLLOW | -0.23 vs 0.05 | -2.03 vs -1.57 | False |

## Top drivers (LightGBM gain, pre-2026)

- BIGMOVE: {'oi_1d': 373.9, 'vol_1d': 356.7, 'kimchi_chg': 324.7, 'm_ls_top_pos_chg': 319.2, 'pump_follow72': 255.4, 'oi_7d': 250.3}
- HIVOL: {'m_taker_ratio_chg': 837.9, 'weekday': 739.6, 'breadth': 611.5, 'alt_1d': 501.6, 'alt_3d': 440.1, 'inflow_surprise': 409.4}
- DOWN: {'vol_ratio_1_30': 409.4, 'kimchi': 359.8, 'm_ls_top_pos': 303.3, 'upbit_share_chg': 273.6, 'vol_surprise': 271.9, 'dd_30d': 236.3}
- FADE: {'oi_1d': 305.3, 'pump_follow72': 297.3, 'disp': 273.0, 'pump_follow24': 268.8, 'alt_1d': 240.7, 'inflow_surprise': 229.9}
