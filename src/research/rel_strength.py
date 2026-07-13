"""Pure math for the 7d relative-strength-vs-BTC spread.

Shared by three call sites so the definition can never drift between them:
  - src/research/candle_signals.py   (event-study signal, offline)
  - src/engine/evaluation.py         (_rel_strength_setup, live)
  - src/strategies/rel_strength_rotation.py (walk-forward backtest validation)

No I/O here on purpose -- callers own fetching price history and pass in
plain floats/lists so this stays trivially testable and reusable across the
snapshot (point-in-time) and live storage backends.
"""

from __future__ import annotations

# Shared by _rel_strength_setup() (evaluation.py, live) and
# RelStrengthRotationStrategy (strategies/, backtest validation) so both
# apply the identical entry criterion to the identical amount of history.
LOOKBACK_BARS = 17_520  # ~2y hourly bars; bounded approximation of the event
                         # study's true expanding-since-inception window --
                         # recomputing that from scratch every symbol every
                         # cycle is too expensive for a live gate.
MIN_HISTORY = 720        # 30 days of prior spread observations required
PCTL = 0.95
SEVEN_DAYS_S = 7 * 86400


def spread(
    sym_now: float | None,
    sym_then: float | None,
    btc_now: float | None,
    btc_then: float | None,
) -> float | None:
    """7d return spread: symbol's 7d return minus BTC's 7d return."""
    if not sym_now or not sym_then or not btc_now or not btc_then:
        return None
    ret_sym = (sym_now - sym_then) / sym_then
    ret_btc = (btc_now - btc_then) / btc_then
    return ret_sym - ret_btc


def percentile_rank(value: float, history: list[float]) -> float:
    """Fraction of history strictly below value. Empty history -> 0.0."""
    if not history:
        return 0.0
    return sum(1 for v in history if v < value) / len(history)
