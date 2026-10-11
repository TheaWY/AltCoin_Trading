# B31: new hypotheses (a-priori signs, holdout 2025-07-01..2026-09-23, BHY over 9)

| hypothesis | sign | insample_t | holdout_t | holdout_t_signed | p_one | bhy | verdict | extra |
|---|---|---|---|---|---|---|---|---|
| A1_funding_pre_settlement | -1 | -0.32 | -2.98 | +2.98 | +0.00 | True | CONFIRMED | hml_holdout_bp 2.84, hml_holdout_t 2.93, note gross per 1-2h hold; round trip ~12bp, insample_wald_p nan, hour_mean_stability_corr nan, avg_coins nan, holdout_mean_bp nan, n_events nan, holdout_mean_bp_per_week nan |
| A2_funding_post_settlement | 1 | +3.34 | +4.29 | +4.29 | +0.00 | True | CONFIRMED | hml_holdout_bp 11.9, hml_holdout_t 7.08, note gross per 1-2h hold; round trip ~12bp, insample_wald_p nan, hour_mean_stability_corr nan, avg_coins nan, holdout_mean_bp nan, n_events nan, holdout_mean_bp_per_week nan |
| B_intraday_periodicity | 1 | +0.68 | -2.15 | -2.15 | +0.98 | False | no | hml_holdout_bp -1.25, hml_holdout_t -2.47, note gross per 1h hold, insample_wald_p nan, hour_mean_stability_corr nan, avg_coins nan, holdout_mean_bp nan, n_events nan, holdout_mean_bp_per_week nan |
| C_calendar_hour_weekday | 0 | +nan | +nan | -1.45 | +0.93 | False | no | hml_holdout_bp nan, hml_holdout_t nan, note nan, insample_wald_p 0.747, hour_mean_stability_corr 0.113, avg_coins nan, holdout_mean_bp nan, n_events nan, holdout_mean_bp_per_week nan |
| D1_new_binance_listing_days1-30 | -1 | -0.56 | +0.78 | -0.78 | +0.78 | False | no | hml_holdout_bp nan, hml_holdout_t nan, note nan, insample_wald_p nan, hour_mean_stability_corr nan, avg_coins 12.6, holdout_mean_bp 21.4, n_events nan, holdout_mean_bp_per_week nan |
| D2_upbit_listing_days1-10 | -1 | -0.83 | -2.43 | +2.43 | +0.01 | True | CONFIRMED | hml_holdout_bp nan, hml_holdout_t nan, note nan, insample_wald_p nan, hour_mean_stability_corr nan, avg_coins nan, holdout_mean_bp -68.1, n_events 235, holdout_mean_bp_per_week nan |
| E1_korea_weekly | 1 | +2.17 | +2.75 | +2.75 | +0.00 | True | CONFIRMED | hml_holdout_bp nan, hml_holdout_t nan, note nan, insample_wald_p nan, hour_mean_stability_corr nan, avg_coins nan, holdout_mean_bp nan, n_events nan, holdout_mean_bp_per_week 154 |
| E2_korea_large_tercile | 1 | +2.56 | +2.75 | +2.75 | +0.00 | True | CONFIRMED | hml_holdout_bp nan, hml_holdout_t nan, note nan, insample_wald_p nan, hour_mean_stability_corr nan, avg_coins nan, holdout_mean_bp nan, n_events nan, holdout_mean_bp_per_week nan |
| E3_coin_kimchi_premium | 1 | +0.85 | +1.77 | +1.77 | +0.04 | False | no | hml_holdout_bp nan, hml_holdout_t nan, note nan, insample_wald_p nan, hour_mean_stability_corr nan, avg_coins nan, holdout_mean_bp nan, n_events nan, holdout_mean_bp_per_week nan |

Calendar detail (bp/hour, alt index): in-sample hour means [1.35, -7.35, -4.32, 0.26, 4.08, -1.77, 6.02, -4.91, 1.65, -2.5, 3.24, -6.57, -1.54, -3.13, -4.45, -0.69, -0.99, -4.69, -2.47, -5.19, -2.76, 1.66, 5.54, 2.6]
holdout hour means [-6.0, -2.33, -0.05, 0.4, -1.69, -0.78, -2.48, -5.44, -0.84, -2.46, -1.05, -3.33, 2.13, -1.02, -5.51, -0.88, -4.84, 4.3, 0.99, -2.08, -1.48, -0.01, 0.24, -0.03]
