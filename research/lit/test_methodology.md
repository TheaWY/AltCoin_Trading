# Test methodology: what the academic papers do, and our standard battery (2026-10-01)

Implemented in scripts/aphx.py (tests: tests/test_aphx.py). First full run: B29_BATTERY.

## Models we copy
- Liu, Tsyvinski & Wu (2022, JF): point-in-time universe incl. dead coins, liquidity floor, weekly quintile sorts, LTW factors CMKT / CSMB (30/40/30 size) / CMOM (3-week momentum within size), alphas vs the 3 factors.
- Fieberg, Liedtke, Poddig, Walker & Zaremba (JFQA 2024, trend factor): truncate returns 0.5/99.5%, rolling 52-week combined elastic net, value-weighted quintiles, LTW alphas + GRS, costs 30bp long / 40bp short (+10bp robustness), 53,920 implementation variants.
- Cong, Karolyi, Tang & Zhao (Management Science 2026): stablecoin exclusion, 1/99% truncation, 30/40/30 breakpoints.
- Bianchi & Babiak (2021): total vs predictive R2, IPCA.
- Gu, Kelly & Xiu (2020, RFS): expanding train / rolling validation / test, R2_OOS with non-demeaned denominator, decile long-short of predictions, variable importance.
- Schmeling, Schrimpf & Todorov (BIS 2023, Crypto Carry): NW automatic bandwidth, panel FE.

## Standard battery (every hypothesis, in order)
0. Pre-register, count every variant in M.
1. Data: point-in-time universe, liquidity floor, no stablecoins/TradFi, per-period winsorisation, delisting = last price, signal lagged >= 1 bar.
2. Quintile sorts EW + VW, HML with Newey-West t (lag floor(4(T/100)^(2/9)), overlap h-1); Patton-Timmermann monotonicity (stationary bootstrap, Politis-White block).
3. Fama-MacBeth with controls (size, liquidity, 1-day reversal, momentum, beta), NW t.
4. Dependent double sorts on size / liquidity.
5. Alpha vs LTW factors (NW t); GRS on the quintiles.
6. Costs (Novy-Marx & Velikov 2016): turnover, net of fees + slippage + funding, break-even cost, buy/hold band.
7. Forecasting (time-series claims): Campbell-Thompson R2_OS (+ GKX), Clark-West (nested) / Diebold-Mariano (HLN), Pesaran-Timmermann, recursive Goyal-Welch design, Rapach-Strauss-Zhou combination.
8. Snooping: Harvey-Liu-Zhu |t| >= 3 and BHY across M; Hansen SPA + Romano-Wolf StepM; Deflated Sharpe >= 0.95; PBO < 0.5 (CSCV, S=16).
9. Robustness: subperiods, regimes, size groups, 1-bar implementation lag, alternative breakpoints.
Events (listings, unlocks): market-model ARs, BMP test with Kolari-Pynnonen cross-correlation adjustment, calendar-time portfolios.

## Reporting columns
Q1..Q5 and HML mean (EW, VW), NW t (lag stated), Sharpe, MR p, FM t, double-sort t, alpha (t), GRS p, turnover, net, break-even, band net, subperiod t, 1-bar-lag t, BHY flag, DSR, PBO, M, T.

## Unverified details (check PDFs before relying on exact values)
NW lags in LTW 2022; PT 2010 bootstrap draws and block length; STW 1999 B and q; RSZ weight bounds; Novy-Marx & Velikov exact break-even form.
