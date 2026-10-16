"""Safety tests for paper-trade cost attribution.

The headline return says a strategy lost; attribution says whether it lost on
direction or on friction. Those are different repairs, so the verdict must not
blur them. Network-free and DB-free.
"""

from __future__ import annotations

import unittest

from src.research.paper_analysis import cost_attribution, turnover


def _trade(pnl: float, fees: float, qty: float = 1.0, entry: float = 100.0,
           opened_at: int = 0, closed_at: int = 3600):
    return {
        "pnl": pnl,
        "fees": fees,
        "quantity": qty,
        "entry_price": entry,
        "opened_at": opened_at,
        "closed_at": closed_at,
    }


class AttributionTests(unittest.TestCase):
    def test_gross_is_net_plus_friction(self):
        trades = [_trade(pnl=-1.0, fees=0.5), _trade(pnl=2.0, fees=0.5)]
        result = cost_attribution(trades)
        self.assertAlmostEqual(result["net_pnl"], 1.0)
        self.assertAlmostEqual(result["friction_paid"], 1.0)
        self.assertAlmostEqual(result["gross_price_pnl"], 2.0)

    def test_friction_dominates_when_entries_were_right_but_costs_ate_it(self):
        # Gross +4, friction 10, so the direction calls were fine and churn lost it.
        trades = [_trade(pnl=-1.2, fees=2.0) for _ in range(5)]
        result = cost_attribution(trades)
        self.assertGreater(result["gross_price_pnl"], 0)
        self.assertIn("friction_dominates", result["verdict"])

    def test_no_edge_when_gross_is_negative_before_costs(self):
        trades = [_trade(pnl=-5.0, fees=0.1) for _ in range(10)]
        result = cost_attribution(trades)
        self.assertLess(result["gross_price_pnl"], 0)
        self.assertIn("entries", result["verdict"])
        self.assertNotIn("friction_dominates", result["verdict"])

    def test_breakeven_win_rate_reflects_the_payoff_shape(self):
        # 2:1 reward-to-risk needs one win in three to break even.
        trades = [_trade(pnl=20.0, fees=0.0)] + [_trade(pnl=-10.0, fees=0.0)] * 2
        result = cost_attribution(trades)
        self.assertAlmostEqual(result["breakeven_win_rate"], 1 / 3, places=3)
        self.assertAlmostEqual(result["win_rate"], 1 / 3, places=3)
        self.assertAlmostEqual(result["win_rate_gap"], 0.0, places=3)

    def test_win_rate_below_breakeven_is_a_negative_gap(self):
        trades = [_trade(pnl=10.0, fees=0.0)] + [_trade(pnl=-10.0, fees=0.0)] * 4
        result = cost_attribution(trades)
        self.assertLess(result["win_rate_gap"], 0)

    def test_net_positive_is_named_as_such(self):
        trades = [_trade(pnl=5.0, fees=0.1) for _ in range(10)]
        self.assertEqual(cost_attribution(trades)["verdict"], "net_positive")

    def test_low_n_is_flagged_not_hidden(self):
        result = cost_attribution([_trade(pnl=1.0, fees=0.1)])
        self.assertTrue(result["low_n"])

    def test_empty_input_does_not_invent_a_verdict(self):
        self.assertEqual(cost_attribution([]), {"n": 0})

    def test_missing_fields_are_treated_as_zero_not_fatal(self):
        trades = [{"pnl": None, "fees": None, "quantity": None, "entry_price": None}]
        result = cost_attribution(trades)
        self.assertEqual(result["net_pnl"], 0.0)


class TurnoverTests(unittest.TestCase):
    def test_turnover_counts_notional_against_capital(self):
        trades = [_trade(pnl=0.0, fees=0.0, qty=1.0, entry=100.0) for _ in range(7)]
        result = turnover(trades, starting_capital=350.0)
        self.assertAlmostEqual(result["turnover_x"], 2.0)

    def test_friction_is_expressed_against_capital(self):
        trades = [_trade(pnl=0.0, fees=1.0) for _ in range(10)]
        result = turnover(trades, starting_capital=1000.0)
        self.assertAlmostEqual(result["friction_pct_of_capital"], 1.0)

    def test_average_hold_is_reported_in_hours(self):
        trades = [_trade(pnl=0.0, fees=0.0, opened_at=0, closed_at=7200)]
        self.assertAlmostEqual(turnover(trades, 1000.0)["avg_hold_hours"], 2.0)

    def test_zero_capital_does_not_divide(self):
        self.assertEqual(turnover([_trade(1.0, 0.1)], 0.0), {"n": 1})


if __name__ == "__main__":
    unittest.main()
