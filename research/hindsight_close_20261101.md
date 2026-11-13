# Closability test (section 4), ex-ante only

Window B = 2024-01-01..2026-10-29, window A = 2026-10-06..2026-10-29. Universe U30 (top-30 alts by 30d value).
IC = daily Spearman(signal at close t, next-day open->open return). Quintile = long-only top 20% by signal, daily
rebalance, net of engine costs, minus EW30 net. Rule: closable iff |t_B| > 2.5 AND positive months >= 60%.

| signal | dir | IC_B | t_B | IC_A | t_A | spread/yr B | +months B | spread/yr A | verdict |
|---|---|---|---|---|---|---|---|---|---|
| ret1d | low | -0.004 | -0.5 | +0.067 | +1.5 | -138% | 9% (34) | -142% | not knowable |
| ret5d | low | -0.024 | -3.1 | -0.039 | -1.0 | -55% | 18% (34) | -37% | not knowable |
| ret20d | low | -0.029 | -3.5 | -0.122 | -2.5 | -45% | 29% (34) | +37% | not knowable |
| ret60d | low | -0.041 | -5.0 | -0.133 | -2.4 | -6% | 47% (34) | +49% | not knowable |
| vol20d | low | -0.103 | -12.2 | -0.193 | -3.2 | +38% | 68% (34) | +116% | closable |
| value_z20 | low | -0.040 | -5.5 | -0.017 | -0.3 | -42% | 29% (34) | -74% | not knowable |
| dist_hi20 | high | +0.026 | +3.3 | +0.000 | +0.0 | -39% | 44% (34) | -110% | not knowable |
| sma_stack | low | -0.044 | -5.9 | -0.119 | -1.8 | +18% | 50% (34) | +183% | not knowable |
| max5d | low | -0.062 | -7.7 | -0.081 | -1.3 | -10% | 53% (34) | +77% | not knowable |

## Market timing, next-day EW30 net return on window B (n=1033)

| regressor | beta | t |
|---|---|---|
| const | -0.0033 | -0.8 |
| btc_ret5d | +0.0099 | +0.3 |
| btc_ret20d | +0.0106 | +0.8 |
| ew_vol20d | +0.0753 | +0.7 |
| f17_overlay | -0.0020 | -0.5 |
| alt_ret5d | -0.0067 | -0.4 |

Any |t| > 2 on window B counts as a timing signal worth a pre-registered test; otherwise timing is labelled luck.
