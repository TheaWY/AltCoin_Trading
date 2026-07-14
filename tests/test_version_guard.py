"""Tests for src/version_guard.py -- the worker/dashboard version-parity
guard (divergence class #6)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from src import version_guard
from src.data.storage import Storage


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db", database_url="")


class GitShaTests(unittest.TestCase):
    def test_reads_this_repos_head(self) -> None:
        sha = version_guard.current_git_sha()
        self.assertIsNotNone(sha)
        self.assertRegex(sha, re.compile(r"^[0-9a-f]{40}$"))

    def test_missing_repo_returns_none_not_raise(self) -> None:
        self.assertIsNone(version_guard.current_git_sha(Path(tempfile.mkdtemp())))

    def test_loaded_sha_captured_at_import(self) -> None:
        self.assertEqual(version_guard.LOADED_SHA, version_guard.current_git_sha())


class ParityCheckTests(unittest.TestCase):
    def _publish(self, storage: Storage, sha: str) -> None:
        storage.set_system_status(
            version_guard.WORKER_BUILD_KEY,
            json.dumps({"sha": sha, "published_at": 123}),
        )

    def test_match_when_worker_publishes_same_sha(self) -> None:
        storage = _storage()
        self._publish(storage, version_guard.LOADED_SHA)
        parity = version_guard.check_version_parity(storage)
        self.assertTrue(parity["match"])
        self.assertFalse(parity["unknown"])

    def test_mismatch_when_worker_on_different_sha(self) -> None:
        storage = _storage()
        self._publish(storage, "deadbeef" * 5)
        parity = version_guard.check_version_parity(storage)
        self.assertFalse(parity["match"])
        self.assertFalse(parity["unknown"])
        self.assertEqual(parity["worker_sha"], "deadbeef" * 5)

    def test_unknown_when_worker_never_published(self) -> None:
        storage = _storage()
        parity = version_guard.check_version_parity(storage)
        self.assertTrue(parity["unknown"])
        self.assertFalse(parity["match"])

    def test_publish_then_check_roundtrip(self) -> None:
        storage = _storage()
        version_guard.publish_worker_build(storage)
        parity = version_guard.check_version_parity(storage)
        self.assertTrue(parity["match"])


if __name__ == "__main__":
    unittest.main()
