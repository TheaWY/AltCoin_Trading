"""The suite must not depend on the machine it runs on.

config reads env at IMPORT time, so whichever test module imports it first
freezes every flag for the entire run from whatever .env that box happens to
carry. On the Mac mini, a live paper-trading .env with SETUP_FUNDING_ENABLED
=false turned two real assertions red. The dangerous case is the other one:
a flag that makes a test PASS for the wrong reason leaves no trace at all.

So a suite whose verdict moves with the operator's .env is not a safety net,
and these checks fail loudly rather than letting that go unnoticed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from src import config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HermeticSuiteTests(unittest.TestCase):
    def test_no_dotenv_leaked_into_this_run(self):
        self.assertFalse(
            config.DOTENV_LOADED,
            "a .env was loaded into this test run, so these results describe "
            "this machine rather than the code. Run the suite via "
            "scripts/run_safety_tests.sh (it exports CONFIG_SKIP_DOTENV=1), "
            "or export CONFIG_SKIP_DOTENV=1 before python -m unittest.",
        )

    def test_runner_exports_the_hermetic_flag(self):
        # The guard above only helps if the documented entry point sets it.
        runner = (PROJECT_ROOT / "scripts" / "run_safety_tests.sh").read_text()
        self.assertIn(
            "CONFIG_SKIP_DOTENV=1",
            runner,
            "run_safety_tests.sh must export CONFIG_SKIP_DOTENV=1 or the suite "
            "silently inherits the machine's .env",
        )

    def test_skip_flag_actually_suppresses_dotenv(self):
        """Pin the mechanism, not just its output.

        Runs in a subprocess because config is import-time: re-importing it in
        this process would not re-run load_dotenv().
        """
        probe = (
            "from src import config; "
            "print(int(config.SKIP_DOTENV), int(config.DOTENV_LOADED))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "CONFIG_SKIP_DOTENV": "1"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1 0")

    def test_default_still_loads_dotenv(self):
        """The hermetic flag must not change how the worker reads config.

        Without CONFIG_SKIP_DOTENV the loader behaves exactly as before; only
        an explicit opt-in suppresses it.
        """
        probe = "from src import config; print(int(config.SKIP_DOTENV))"
        env = {k: v for k, v in os.environ.items() if k != "CONFIG_SKIP_DOTENV"}
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0")


class SetupFlagIsolationTests(unittest.TestCase):
    """Tests that assert on a setup must pin that setup themselves.

    A module-level os.environ.setdefault() cannot do it: by the time the module
    loads, an earlier test module has usually already imported config and
    triggered load_dotenv(), so the key is set and setdefault is a no-op. This
    is the exact bug that made test_evaluation_setups machine-dependent.
    """

    def test_no_test_pins_a_flag_at_module_level(self):
        """Match the defect, not the word.

        A module-level call is one at zero indentation; the same text inside a
        docstring or a setUp body is fine, so the check is anchored rather than
        a substring search.
        """
        pattern = re.compile(r"^os\.environ\.(setdefault|update)\(", re.M)
        offenders = []
        for path in sorted((PROJECT_ROOT / "tests").glob("test_*.py")):
            if pattern.search(path.read_text()):
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            f"{offenders} set config flags at module level. That is a no-op "
            "once any earlier test module has imported config and triggered "
            "load_dotenv, so the flag ends up being whatever the machine's "
            ".env says. Pin it per-test in setUp with mock.patch.dict or "
            "mock.patch.object instead.",
        )

    def test_evaluation_setups_pins_both_flag_styles(self):
        source = (PROJECT_ROOT / "tests" / "test_evaluation_setups.py").read_text()
        # SETUP_FAILED_PUMP is read from os.environ at call time: pinned per test
        # either with mock.patch.dict or the file's own _env() context manager
        self.assertTrue("mock.patch.dict" in source or "with _env(" in source)
        # ...while SETUP_FUNDING was resolved on config at import time.
        self.assertIn("mock.patch.object", source)


if __name__ == "__main__":
    unittest.main()
