# Pre-registration v4 (written 2026-10-05 15:00 KST, BEFORE running): G1 strict surge ride, G2 ETH/BTC rotation

Asked by 유리 ("테스트 진행 해") after B52. Same data/costs/eras/1-bar lag/bootstrap as v2-v3. Family K = 2,
Bonferroni one-sided p < 0.025. One rule each, no search. HONESTY FLAG: G1 is informed by B52's failure (we saw R lose
in 2022 and 2025-26), so its backtest is optimistic by construction; it can only become a forward paper test, and a
backtest PASS is weaker evidence than v2/v3. A kimchi-premium brake was considered and dropped before running: KRW-USDT
daily history only starts 2024-06, too short for the 5-era test.

## G1: strict surge ride (R with a stronger regime)
Identical to prereg v3 R (universe, 20d closing-high breakout, value >= 2x 30d median, Donchian-10 exit, 60d cap,
10 slots x 10%, costs) EXCEPT the regime condition at close t is: F15 ensemble weight of KRW-BTC == 1.0 (above all four
SMAs) AND the coin's own F15 ensemble weight >= 0.75.
PASS = same three conditions as R: trade-mean day-clustered CI lower bound > 0; beats random-entry control drawn under the
SAME stricter regime with p < 0.025; mean > 0 in >= 4 of 5 eras with >= 10 trades (eras with < 10 trades count as
not positive).

## G2: ETH/BTC rotation inside F17
Ratio r_t = close(ETH)/close(BTC). Tilt t_r = F15 ensemble weight computed on r (SMA 20/50/100/200 of the ratio).
ETH sleeve share = 0.25 + 0.5 x t_r, BTC share = 1 - ETH share (so 25/75 .. 75/25). Each sleeve keeps its F17 (P)
weight; portfolio return = share-weighted sleeve returns; extra turnover from share changes is charged at the coin's cost.
Benchmark = F17 (50/50).
PASS = Sharpe(G2) - Sharpe(F17) > 0 with stationary-bootstrap p < 0.025 AND maxDD(G2) not worse than F17 by more than
5 points AND Sharpe(G2) >= Sharpe(F17) in >= 4 of 5 eras.

## After
PASS -> forward paper next to F15/F17. FAIL -> logged, not re-tuned.
