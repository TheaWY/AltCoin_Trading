"""Regression guard for the exact bug that silently blocked rel_strength_
rotation in both backtest and live: evaluate_symbol() checks
config.holding_style_allowed()/max_hold_hours_for_style() on a setup's RAW
style label directly, before cycle.py's STYLE_MAP ever translates it. Every
new setup style must be recognized by config.normalize_holding_style() in
its raw (Korean-label) form, not just its translated English key.
"""

from __future__ import annotations

import unittest

from src import config
from src.engine.cycle import STYLE_MAP
from src.engine.evaluation import (
    STYLE_CAPITULATION_BOUNCE,
    STYLE_PUMP24_EXTREME,
    STYLE_REL_STRENGTH_NEUTRAL,
    STYLE_VOLUME_ZSCORE,
)

RAW_STYLE_TO_EXPECTED_HOLD_HOURS = {
    STYLE_REL_STRENGTH_NEUTRAL: 72.0,
    STYLE_CAPITULATION_BOUNCE: 72.0,
    STYLE_VOLUME_ZSCORE: 72.0,
    STYLE_PUMP24_EXTREME: 24.0,
}


class RawStyleLabelRecognitionTests(unittest.TestCase):
    """evaluate_symbol() calls holding_style_allowed(setup['style']) on the
    RAW label BEFORE any translation -- this must return True directly,
    not just after cycle.py's STYLE_MAP runs."""

    def test_raw_labels_allowed_without_translation(self) -> None:
        for raw_style in RAW_STYLE_TO_EXPECTED_HOLD_HOURS:
            with self.subTest(style=raw_style):
                self.assertTrue(
                    config.holding_style_allowed(raw_style),
                    f"{raw_style!r} blocked by holding_style_allowed() in its raw form -- "
                    "evaluate_symbol() would silently drop this setup via policy_blocked_setups",
                )

    def test_raw_labels_get_correct_hold_hours(self) -> None:
        for raw_style, expected_hours in RAW_STYLE_TO_EXPECTED_HOLD_HOURS.items():
            with self.subTest(style=raw_style):
                self.assertEqual(config.max_hold_hours_for_style(raw_style), expected_hours)

    def test_style_map_translation_is_consistent_with_raw_recognition(self) -> None:
        """cycle.py's STYLE_MAP (used for the live process_signal() path) must
        translate to the SAME normalized key raw recognition already uses --
        two independent paths, one answer."""
        for raw_style in RAW_STYLE_TO_EXPECTED_HOLD_HOURS:
            with self.subTest(style=raw_style):
                self.assertIn(raw_style, STYLE_MAP, f"{raw_style!r} missing from cycle.STYLE_MAP")
                translated = STYLE_MAP[raw_style]
                self.assertEqual(
                    config.normalize_holding_style(raw_style),
                    config.normalize_holding_style(translated),
                )


if __name__ == "__main__":
    unittest.main()
