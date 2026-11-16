from __future__ import annotations

import os
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
        patcher = mock.patch("ingest_pipeline.STABILITY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        data = mock.patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(Path(self.tmp.name) / "data")})
        data.start()
        self.addCleanup(data.stop)

        import console_log

        self.addCleanup(console_log.shutdown)

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

    def test_the_import_log_is_written_to_the_test_data_folder_not_the_real_one(self):
        from ingest_pipeline import ingest_log_path

        self.add_photo("sample_one.jpg")
        self.make().run_once()

        self.assertEqual(ingest_log_path(), (Path(self.tmp.name) / "data" / "ingest-log.json").resolve())
        self.assertTrue(ingest_log_path().is_file())

    def test_run_once_requests_no_scan_when_nothing_was_imported(self):
        self.make().run_once()
        self.assertEqual(self.batches, [])


class TestIngestDates(unittest.TestCase):
    def test_video_without_exif_uses_the_header_date_not_the_modified_time(self):
        import datetime as dt
        import ingest_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip.mp4"
            clip.write_bytes(b"not a real video")
            with mock.patch("ingest_pipeline.capture_date_for", return_value="2019-07-04"):
                self.assertEqual(ingest_pipeline._capture_date(clip), dt.datetime(2019, 7, 4))

    def test_falls_back_to_the_modified_time_when_no_date_is_found(self):
        import datetime as dt
        import ingest_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip.mp4"
            clip.write_bytes(b"x")
            with mock.patch("ingest_pipeline.capture_date_for", return_value=None):
                found = ingest_pipeline._capture_date(clip)
            self.assertAlmostEqual(found.timestamp(), clip.stat().st_mtime, delta=2)


class TestIngestSettling(unittest.TestCase):
    def test_one_shared_wait_covers_the_whole_batch(self):
        import ingest_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for index in range(5):
                path = Path(tmp) / f"p{index}.jpg"
                path.write_bytes(b"data")
                files.append(path)
            empty = Path(tmp) / "empty.jpg"
            empty.write_bytes(b"")
            with mock.patch("ingest_pipeline.time.sleep") as sleep:
                settled = ingest_pipeline._settled(files + [empty])
            self.assertEqual(settled, files)
            sleep.assert_called_once()

    def test_a_file_that_grew_during_the_wait_is_not_settled(self):
        import ingest_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            growing = Path(tmp) / "growing.jpg"
            growing.write_bytes(b"abc")
            still = Path(tmp) / "still.jpg"
            still.write_bytes(b"abc")

            def grow(_seconds):
                growing.write_bytes(b"abcdef")

            with mock.patch("ingest_pipeline.time.sleep", side_effect=grow):
                self.assertEqual(ingest_pipeline._settled([growing, still]), [still])


class TestIngestSkipsKnownFiles(unittest.TestCase):
    def setUp(self):
        from ingest_pipeline import IngestPipeline

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "incoming"
        self.destination = Path(self.tmp.name) / "library"
        self.source.mkdir()
        patcher = mock.patch("ingest_pipeline.STABILITY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        log = mock.patch("ingest_pipeline._log_action")
        self.logged = log.start()
        self.addCleanup(log.stop)
        self.IngestPipeline = IngestPipeline

    def test_a_copy_already_in_the_library_stays_in_the_source_folder(self):
        (self.source / "known.jpg").write_bytes(b"known image")
        (self.source / "fresh.jpg").write_bytes(b"fresh image")
        pipeline = self.IngestPipeline(
            source_folder=str(self.source), destination_folder=str(self.destination),
            is_already_in_library=lambda path: path.name == "known.jpg",
        )
        delta = pipeline.run_once()
        pipeline.run_once()

        self.assertEqual(delta["imported"], 1)
        self.assertEqual(delta["already_in_library"], 1)
        self.assertTrue((self.source / "known.jpg").exists())
        self.assertFalse((self.source / "fresh.jpg").exists())
        skipped = [call for call in self.logged.call_args_list if call.args[0]["action"] == "skip_duplicate"]
        self.assertEqual(len(skipped), 1, "a known file is reported once, not on every pass")

    def test_a_failing_lookup_never_blocks_an_import(self):
        (self.source / "photo.jpg").write_bytes(b"photo image")

        def broken(_path):
            raise RuntimeError("catalog unavailable")

        pipeline = self.IngestPipeline(
            source_folder=str(self.source), destination_folder=str(self.destination),
            is_already_in_library=broken,
        )
        self.assertEqual(pipeline.run_once()["imported"], 1)


if __name__ == "__main__":
    unittest.main()
