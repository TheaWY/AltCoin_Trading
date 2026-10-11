# B57 sealed holdout (2025-07-01..2026-10-04), opened once

F17: Sharpe 1.43, maxDD -19.2%, CAGR 31.6%
F19: Sharpe 1.26, maxDD -17.6%, CAGR 22.8%

U3_ens_all_h7: Sharpe 1.28 (diff -0.15, p=0.8132), maxDD -18.6%, CAGR 25.6% -> FAIL

## Reading
- 90 configs (9 models x 3 horizons x 3 usages + ensembles) on development 2023-01..2025-06. Best raw dev Sharpe U2_cat_h1 2.10
  but maxDD -52% and turnover 256x/yr. CSCV PBO 0.54: the in-sample winner is as likely as not below median out of sample.
- No config reached the top-decile OOS rank (best 0.87), so candidate A is empty by the committed rule.
- Candidate B (all-model ensemble, BTC/ETH timing, weekly) beat F17 in development (1.84 vs 1.71, p=0.10) but lost in the sealed
  period: Sharpe 1.28 vs 1.43, p=0.81. FAIL. Its drawdown was similar (-18.6% vs -19.2%).
- Models do rank coins (IC 0.05..0.20) but the edge does not survive conversion into a long-only book that beats the trend rule.
  F17 stays the base strategy.

## B57i interpretation (prereg v9, dev 2023-01..2025-06, no tests)
- Top univariate features vs 7d relative return are all volatility / lottery measures with NEGATIVE sign:
  upside vol 30d -0.10, idiosyncratic vol -0.10, rv7/rv30 -0.10, max daily return 30d -0.09, rv180 -0.09;
  correlation with BTC +0.08.
- Model predictions correlate +0.2..+0.5 with low-volatility rank (GBMs ~0.45-0.49); GBMs put BTC/ETH in the
  top 3 about 38% of the time.
- So the ML mostly rediscovered "avoid volatile, lottery-like alts, prefer majors". F17 already holds only
  BTC/ETH, which is why the models could not beat it.
