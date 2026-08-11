from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(self.root / "data")})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_initialize_creates_empty_current_database(self):
        from database_tools import initialize
        from photo_index import SCHEMA_VERSION, SQLITE_BUSY_TIMEOUT_MS, connect

        database = self.root / "library.sqlite3"
        self.assertEqual(initialize(database), 0)
        con = sqlite3.connect(database)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)
        con.close()
        with connect(database) as configured:
            self.assertEqual(configured.execute("PRAGMA busy_timeout").fetchone()[0], SQLITE_BUSY_TIMEOUT_MS)

    def test_version_one_database_migrates_cancelled_run_history(self):
        from photo_index import SCHEMA_VERSION, connect

        database = self.root / "library.sqlite3"
        con = sqlite3.connect(database)
        con.execute(
            """CREATE TABLE runs (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                scanned INTEGER NOT NULL DEFAULT 0,
                changed INTEGER NOT NULL DEFAULT 0,
                unchanged INTEGER NOT NULL DEFAULT 0,
                removed INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0
            )"""
        )
        con.execute("PRAGMA user_version=1")
        con.commit()
        con.close()

        migrated = connect(database)
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(runs)")}
        self.assertIn("cancelled", columns)
        asset_columns = {row[1] for row in migrated.execute("PRAGMA table_info(assets)")}
        self.assertTrue({"location_scanned", "gps_latitude", "gps_longitude"} <= asset_columns)
        face_columns = {row[1] for row in migrated.execute("PRAGMA table_info(face_embeddings)")}
        self.assertTrue({
            "box_left", "box_top", "box_right", "box_bottom",
            "localization_similarity", "localized_at",
        } <= face_columns)
        self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        migrated.close()

    def test_face_import_accepts_normalized_bounds(self):
        from photo_index import import_face_db, scan_library

        library = self.root / "photos"
        library.mkdir()
        photo = library / "bounded.jpg"
        photo.write_bytes(b"synthetic")
        database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(library, database), 0)
        source = self.root / "faces.tsv"
        source.write_text("7\tbounded.jpg\tF\t0.1\t0.2\t0.4\t0.6\t0.5,0.25\n", encoding="utf-8")
        self.assertEqual(import_face_db(database, source), 0)
        con = sqlite3.connect(database)
        row = con.execute(
            "SELECT box_left,box_top,box_right,box_bottom FROM face_embeddings"
        ).fetchone()
        con.close()
        self.assertEqual(row, (0.1, 0.2, 0.4, 0.6))

    def test_scan_records_embedded_gps_coordinates(self):
        from photo_index import scan_library

        library = self.root / "photos"
        library.mkdir()
        (library / "located.jpg").write_bytes(b"synthetic")
        database = self.root / "library.sqlite3"
        with patch("photo_index.extract_gps_coordinates", return_value=(33.6846, -117.8265)):
            self.assertEqual(scan_library(library, database), 0)
        con = sqlite3.connect(database)
        row = con.execute(
            "SELECT location_scanned,gps_latitude,gps_longitude FROM assets"
        ).fetchone()
        con.close()
        self.assertEqual(row, (1, 33.6846, -117.8265))

    def test_scan_infers_folder_tags_from_a_descriptive_folder_name(self):
        from photo_index import scan_library

        library = self.root / "photos"
        folder = library / "2026" / "2026_07_04 - July 4th Boat and Fireworks"
        folder.mkdir(parents=True)
        (folder / "photo.jpg").write_bytes(b"synthetic")
        database = self.root / "library.sqlite3"

        self.assertEqual(scan_library(library, database), 0)
        con = sqlite3.connect(database)
        tags = {row[0] for row in con.execute(
            """SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
               WHERE at.source='folder_rule'"""
        )}
        con.close()
        self.assertTrue({"Boating", "Fireworks"} <= tags)

    def test_scan_falls_back_to_the_folder_name_when_no_category_matches(self):
        from photo_index import scan_library

        library = self.root / "photos"
        folder = library / "2026" / "2026_06_20 - Out with Candy"
        folder.mkdir(parents=True)
        (folder / "photo.jpg").write_bytes(b"synthetic")
        database = self.root / "library.sqlite3"

        self.assertEqual(scan_library(library, database), 0)
        con = sqlite3.connect(database)
        tags = {row[0] for row in con.execute(
            """SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
               WHERE at.source='folder_rule'"""
        )}
        con.close()
        self.assertIn("Out with Candy", tags)

    def test_backfill_folder_tags_covers_folders_indexed_before_a_pattern_existed(self):
        from database_tools import backfill_folder_tags
        from photo_index import scan_library

        library = self.root / "photos"
        folder = library / "2026" / "2026_06_20 - Birthday Party"
        folder.mkdir(parents=True)
        (folder / "photo.jpg").write_bytes(b"synthetic")
        database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(library, database), 0)

        # Simulate a folder that was indexed before its tag pattern was ever
        # recognized -- ordinary scans never revisit unchanged files, so
        # nothing but an explicit backfill can fix a gap like this.
        con = sqlite3.connect(database)
        con.execute("DELETE FROM folder_tags")
        con.execute("DELETE FROM asset_tags WHERE source='folder_rule'")
        con.commit()
        con.close()

        self.assertEqual(backfill_folder_tags(database), 0)
        con = sqlite3.connect(database)
        tags = {row[0] for row in con.execute(
            """SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
               WHERE at.source='folder_rule'"""
        )}
        fts_hit = con.execute(
            "SELECT COUNT(*) FROM search_fts WHERE search_fts MATCH 'Birthday'"
        ).fetchone()[0]
        con.close()
        self.assertIn("Birthday", tags)
        self.assertEqual(fts_hit, 1)

    def test_scan_is_incremental_and_backup_is_valid(self):
        from database_tools import backup, verify
        from photo_index import scan_library

        library = self.root / "photos"
        library.mkdir()
        photo = library / "2026-08-09 sample.jpg"
        photo.write_bytes(b"not-a-real-jpeg")
        database = self.root / "library.sqlite3"

        self.assertEqual(scan_library(library, database), 0)
        self.assertEqual(scan_library(library, database), 0)
        con = sqlite3.connect(database)
        runs = con.execute("SELECT changed,unchanged,errors FROM runs ORDER BY id").fetchall()
        con.close()
        self.assertEqual(runs, [(1, 0, 0), (0, 1, 0)])

        destination = self.root / "backup.sqlite3"
        self.assertEqual(backup(database, destination), 0)
        self.assertEqual(verify(destination), 0)

    def test_cancelled_scan_is_resumable_and_does_not_remove_unseen_assets(self):
        from photo_index import scan_library

        library = self.root / "photos"
        library.mkdir()
        (library / "one.jpg").write_bytes(b"one")
        removed_before_cancel = library / "two.jpg"
        removed_before_cancel.write_bytes(b"two")
        database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(library, database), 0)
        removed_before_cancel.unlink()

        progress = []
        self.assertEqual(
            scan_library(library, database, progress=progress.append, should_cancel=lambda: True), 3
        )
        con = sqlite3.connect(database)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT cancelled FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0], 1)
        con.close()
        self.assertTrue(progress[-1]["cancelled"])

        self.assertEqual(scan_library(library, database), 0)
        con = sqlite3.connect(database)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1)
        con.close()

    def test_raw_files_are_inventoried_and_wav_files_are_ignored(self):
        from photo_index import scan_library

        library = self.root / "photos"
        library.mkdir()
        (library / "camera.dng").write_bytes(b"raw")
        (library / "voice.wav").write_bytes(b"audio")
        database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(library, database), 0)
        con = sqlite3.connect(database)
        rows = con.execute("SELECT filename,media_type FROM assets ORDER BY filename").fetchall()
        con.close()
        self.assertEqual(rows, [("camera.dng", "raw")])

    def test_cloud_placeholder_detection_trusts_allocation_size_not_just_attributes(self):
        from types import SimpleNamespace

        import photo_index

        recall_on_data_access = 0x00400000
        flagged_stat = SimpleNamespace(st_file_attributes=recall_on_data_access, st_size=1000)
        plain_stat = SimpleNamespace(st_file_attributes=0, st_size=1000)
        fake_path = Path("C:/fake/file.jpg")

        self.assertFalse(photo_index.is_cloud_placeholder(plain_stat, fake_path))

        # Dropbox/OneDrive set these attribute bits on fully-downloaded files
        # too, so a cluster-rounded allocation at or above the logical size
        # must not be treated as a placeholder.
        with patch.object(photo_index, "_actual_allocation_size", return_value=4096):
            self.assertFalse(photo_index.is_cloud_placeholder(flagged_stat, fake_path))

        # A real placeholder has near-zero allocation despite reporting the
        # full logical size.
        with patch.object(photo_index, "_actual_allocation_size", return_value=0):
            self.assertTrue(photo_index.is_cloud_placeholder(flagged_stat, fake_path))

        # No path given: fall back to the conservative attribute-only read.
        self.assertTrue(photo_index.is_cloud_placeholder(flagged_stat))

    def test_ocr_can_pause_resume_and_remembers_images_without_text(self):
        from photo_index import ocr_assets, scan_library

        library = self.root / "photos"
        library.mkdir()
        for name in ("one.jpg", "two.jpg", "three.jpg"):
            (library / name).write_bytes(name.encode("ascii"))
        database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(library, database), 0)

        cancel = {"value": False}

        def progress(counts):
            if counts["attempted"] >= 1:
                cancel["value"] = True

        def fake_ocr(_script, path):
            text = "recognized words" if path.endswith("one.jpg") else ""
            return path, text, None

        with patch("photo_index.run_windows_ocr", side_effect=fake_ocr) as worker:
            self.assertEqual(
                ocr_assets(
                    database, None, 1, progress=progress,
                    should_cancel=lambda: cancel["value"],
                ),
                3,
            )
            self.assertEqual(worker.call_count, 1)

        con = sqlite3.connect(database)
        self.assertEqual(con.execute("SELECT SUM(ocr_scanned) FROM text_data").fetchone()[0], 1)
        con.close()

        with patch("photo_index.run_windows_ocr", side_effect=fake_ocr) as worker:
            self.assertEqual(ocr_assets(database, None, 2), 0)
            self.assertEqual(worker.call_count, 2)
        con = sqlite3.connect(database)
        self.assertEqual(con.execute("SELECT SUM(ocr_scanned) FROM text_data").fetchone()[0], 3)
        con.close()

        with patch("photo_index.run_windows_ocr") as worker:
            self.assertEqual(ocr_assets(database, None, 2), 0)
            worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
