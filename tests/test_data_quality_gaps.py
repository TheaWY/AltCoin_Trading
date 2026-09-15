"""data_gaps must persist multi-year / post-2038 second counts.

Postgres INTEGER is int4. A collection hole larger than ~68 years, or a
leftover int4 column receiving a 2026 unix timestamp after a botched
ALTER, raises NumericValueOutOfRange. scan_gaps has to write those values
through the Storage layer without overflowing.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.data.storage import Storage
from src.research import data_quality


def _storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "gaps.db", database_url="")


class DataQualityGapScanTests(unittest.TestCase):
    def test_schema_uses_bigint_for_timestamp_columns(self):
        for column in data_quality._GAP_INT64_COLUMNS:
            self.assertIn(f"{column} BIGINT", data_quality._SCHEMA)

    def test_scan_gaps_persists_int4_overflowing_gap_seconds(self):
        storage = _storage()
        now = int(time.time())
        gap_seconds = 3_000_000_000  # > 2**31 - 1
        earlier = now - gap_seconds
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO prices (symbol, timestamp, timeframe, open, high, low, close, volume) "
                "VALUES (?, ?, '1h', 1, 1, 1, 1, 1)",
                ("GAP/USDT", earlier),
            )
            conn.execute(
                "INSERT INTO prices (symbol, timestamp, timeframe, open, high, low, close, volume) "
                "VALUES (?, ?, '1h', 1, 1, 1, 1, 1)",
                ("GAP/USDT", now),
            )

        with patch.object(data_quality, "get_storage", return_value=storage):
            found = data_quality.scan_gaps(days=40_000)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["symbol"], "GAP/USDT")
        self.assertEqual(found[0]["gap_seconds"], gap_seconds)

        with storage._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT gap_start, gap_end, gap_seconds FROM data_gaps WHERE symbol = ?",
                ("GAP/USDT",),
            ).fetchone()
        self.assertIsNotNone(row)
        stored = dict(row)
        self.assertEqual(int(stored["gap_seconds"]), gap_seconds)
        self.assertEqual(int(stored["gap_start"]), earlier)
        self.assertEqual(int(stored["gap_end"]), now)


if __name__ == "__main__":
    unittest.main()
