from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestIngestRequestsAScan(unittest.TestCase):
    """Every import, scheduled or manual, must ask for a scan of what it moved."""

    def setUp(self):
        from ingest_pipeline import IngestPipeline

        self.IngestPipeline = IngestPipeline
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "incoming"
        self.destination = Path(self.tmp.name) / "library"
        self.source.mkdir()
        self.batches: list[int] = []
        patcher = mock.patch("ingest_pipeline._is_stable", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self):
        return self.IngestPipeline(
            source_folder=str(self.source),
            destination_folder=str(self.destination),
            on_batch_complete=self.batches.append,
        )

    def add_photo(self, name):
        (self.source / name).write_bytes(b"synthetic image bytes " + name.encode())

    def test_run_once_requests_a_scan_after_importing(self):
        self.add_photo("sample_one.jpg")
        self.add_photo("sample_two.jpg")
        delta = self.make().run_once()

        self.assertEqual(delta["imported"], 2)
        self.assertEqual(self.batches, [2])

    def test_run_once_requests_no_scan_when_nothing_was_imported(self):
        self.make().run_once()
        self.assertEqual(self.batches, [])


if __name__ == "__main__":
    unittest.main()
