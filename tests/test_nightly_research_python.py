"""launchd must not be able to point nightly research at system Python."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class NightlyResearchPythonTests(unittest.TestCase):
    def test_script_hardcodes_venv_python(self):
        text = (REPO / "ops" / "nightly_research.sh").read_text()
        self.assertIn('PY="$REPO/.venv/bin/python"', text)
        self.assertNotIn("${PY:-", text)
        self.assertIn('[ ! -x "$PY" ]', text)

    def test_launchd_plist_pins_venv_on_path(self):
        text = (REPO / "ops" / "com.altcoin.research.plist").read_text()
        self.assertIn("__REPO_PATH__/.venv/bin/python", text)
        self.assertIn("<key>EnvironmentVariables</key>", text)
        self.assertIn("__REPO_PATH__/.venv/bin:", text)


if __name__ == "__main__":
    unittest.main()
