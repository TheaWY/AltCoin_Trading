# Literature: predicting bad days and timing strategies (compiled 2026-10-01)

Consensus: the SIZE of tomorrow's move and crash risk are forecastable; the SIGN of tomorrow's return mostly is not.

- HAR-RV beats GARCH for BTC vol (Bergsli, Lind, Molnar & Polasik 2022, RIBAF 59).
- Vol-managed portfolios: Moreira & Muir 2017 (JF) in-sample; Cederburg, O'Doherty, Wang & Yan 2020 (JFE) not better out of sample across 103 strategies; Harvey et al. 2018 (JPM) vol targeting cuts tails, raises Sharpe for risk assets only.
- Crypto: vol-managed momentum doubles weekly return (Grobys et al. 2025, FMPM), no OOS/cost test.
- Time-series momentum: Liu & Tsyvinski 2021 (RFS): 1-SD week return -> +3.2% next week (sample to 2018, in-sample, no costs). Liu, Tsyvinski & Wu 2022 (JF): market, size, momentum factors.
- Crowding -> crashes: Schmeling, Schrimpf & Todorov "Crypto Carry" (BIS WP 1087, 2023): high carry predicts crashes. He, Manela, Ross & von Wachter (arXiv 2212.06888): perp-spot gaps 60-90%/yr, momentum-driven.
- Weak/none: Tether issuance (Lyons & Viswanath-Natraj), Fear & Greed (weekly only), kimchi premium (Eom 2021), macro surprises mostly orthogonal (Benigno & Rosa 2023, NY Fed SR 1052), day of week fragile.
- Regime switching: HMM beats random walk in density forecasts (Koki et al., arXiv 2011.03741) but no trading test; factor timing adds ~nothing after costs (Asness et al. 2017), works only with heavily shrunk signals (Haddad, Kozak & Santosh 2020).
- Pitfalls: direction accuracy ~50%; purge/embargo; Deflated Sharpe; funding costs; survivorship.

## Our replication
- B24: strategy rotation from regime features: AUC ~0.5, selectors lose.
- B25: day-level targets: only HIVOL predictable (AUC 0.73); DOWN 0.50, CRASH 0.56, FADE 0.49.
- B26: HAR-RV + DVOL + funding forecast works (R2 0.37 vs 0.16) but sizing beats neither unmanaged nor constant leverage on both Sharpe and drawdown.

## Free data worth adding
Deribit DVOL (added: data/cache/deribit_dvol_btc_1d.parquet), Binance funding/mark/index (have), CoinGlass liquidations (key), FRED (DGS10, DTWEXBGS, NASDAQCOM, VIXCLS), FOMC/CPI calendars, alternative.me Fear & Greed.
