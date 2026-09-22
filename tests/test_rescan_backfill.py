from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from PIL import Image


class FakeEncoder:
    """Stands in for the optional CLIP model so the backfill logic is testable."""

    identity = "test-model/fake"

    def __init__(self, unreadable: set[str] | None = None):
        self.unreadable = unreadable or set()
        self.seen: list[str] = []

    def encode_images(self, paths):
        result = []
        for path in paths:
            self.seen.append(Path(path).name)
            if Path(path).name in self.unreadable:
                result.append("Unreadable image file")
            else:
                result.append(tuple([1.0] + [0.0] * 511))
        return result

    def encode_text(self, text):
        return tuple([1.0] + [0.0] * 511)


class TestSemanticBackfill(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.library = self.root / "photos"
        self.library.mkdir()
        self.environment = unittest.mock.patch.dict(
            os.environ, {"LENSLEDGER_DATA_DIR": str(self.data)}
        )
        self.environment.start()

        from photo_index import scan_library

        self.names = ["a.jpg", "b.jpg", "c.jpg"]
        for index, name in enumerate(self.names):
            Image.new("RGB", (16, 16), (index * 40, 60, 90)).save(self.library / name, quality=90)
        self.database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(self.library, self.database), 0)

    def tearDown(self):
        import console_log

        console_log.shutdown()
        self.environment.stop()
        self.temporary.cleanup()

    def indexed_names(self) -> set[str]:
        from photo_index import connect

        with connect(self.database) as con:
            rows = con.execute(
                "SELECT a.filename FROM semantic_embeddings se JOIN assets a ON a.id=se.asset_id"
            ).fetchall()
        return {row[0] for row in rows}

    def test_new_mode_skips_photos_an_earlier_run_could_not_read(self):
        from semantic_index import build_index

        failing = build_index(self.database, encoder=FakeEncoder(unreadable={"b.jpg"}))
        self.assertEqual(failing["errors"], 1)
        self.assertEqual(self.indexed_names(), {"a.jpg", "c.jpg"})

        # The default pass will not look at b.jpg again -- this is the gap that
        # left photos permanently without meaning data.
        second = FakeEncoder()
        build_index(self.database, encoder=second)
        self.assertNotIn("b.jpg", second.seen)

    def test_missing_mode_fills_the_gap_including_earlier_failures(self):
        from semantic_index import build_index

        build_index(self.database, encoder=FakeEncoder(unreadable={"b.jpg"}))
        self.assertEqual(self.indexed_names(), {"a.jpg", "c.jpg"})

        retry = FakeEncoder()
        result = build_index(self.database, encoder=retry, mode="missing")
        self.assertIn("b.jpg", retry.seen)
        self.assertEqual(result["total"], 1, "only the photo missing data should be processed")
        self.assertEqual(self.indexed_names(), {"a.jpg", "b.jpg", "c.jpg"})

    def test_missing_mode_clears_the_stale_error(self):
        from photo_index import connect
        from semantic_index import build_index

        build_index(self.database, encoder=FakeEncoder(unreadable={"b.jpg"}))
        build_index(self.database, encoder=FakeEncoder(), mode="missing")
        with connect(self.database) as con:
            remaining = con.execute(
                "SELECT COUNT(*) FROM assets WHERE semantic_error<>''"
            ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_all_mode_reads_every_photo_again(self):
        from semantic_index import build_index

        build_index(self.database, encoder=FakeEncoder())
        self.assertEqual(self.indexed_names(), set(self.names))

        rerun = FakeEncoder()
        result = build_index(self.database, encoder=rerun, mode="all")
        self.assertEqual(result["total"], 3)
        self.assertEqual(sorted(rerun.seen), sorted(self.names))

    def test_failures_name_the_file(self):
        from semantic_index import build_index

        result = build_index(self.database, encoder=FakeEncoder(unreadable={"b.jpg"}))
        self.assertEqual(len(result["failures"]), 1)
        failure = result["failures"][0]
        self.assertIn("b.jpg", failure["path"])
        self.assertTrue(failure["error"], "a failure must carry a reason, not just a count")

    def test_status_counts_photos_missing_meaning_data(self):
        from semantic_index import build_index, status

        build_index(self.database, encoder=FakeEncoder(unreadable={"b.jpg"}))
        coverage = status(self.database)
        self.assertEqual(coverage["missing"], 1)
        self.assertEqual(coverage["indexed"], 2)

    def test_unknown_mode_is_refused(self):
        from semantic_index import build_index

        with self.assertRaises(ValueError):
            build_index(self.database, encoder=FakeEncoder(), mode="everything")


class TestOcrRescan(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.library = self.root / "photos"
        self.library.mkdir()
        self.environment = unittest.mock.patch.dict(
            os.environ, {"LENSLEDGER_DATA_DIR": str(self.data)}
        )
        self.environment.start()

        from photo_index import scan_library

        Image.new("RGB", (16, 16), (10, 20, 30)).save(self.library / "a.jpg", quality=90)
        self.database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(self.library, self.database), 0)

    def tearDown(self):
        import console_log

        console_log.shutdown()
        self.environment.stop()
        self.temporary.cleanup()

    def test_rescan_reselects_photos_already_read(self):
        from photo_index import connect, ocr_assets

        with connect(self.database) as con:
            asset_id = int(con.execute("SELECT id FROM assets").fetchone()[0])
            con.execute(
                "INSERT INTO text_data(asset_id, ocr_text, ocr_scanned) VALUES (?, 'old text', 1) "
                "ON CONFLICT(asset_id) DO UPDATE SET ocr_text='old text', ocr_scanned=1",
                (asset_id,),
            )

        totals = {}

        def capture(counts):
            totals.setdefault("total", counts["total"])

        ocr_assets(self.database, None, 1, progress=capture, quiet=True,
                   should_cancel=lambda: True)
        self.assertEqual(totals["total"], 0, "a normal pass skips photos already read")

        totals.clear()
        ocr_assets(self.database, None, 1, progress=capture, quiet=True,
                   should_cancel=lambda: True, rescan=True)
        self.assertEqual(totals["total"], 1, "a re-run must pick the photo up again")


if __name__ == "__main__":
    unittest.main()
