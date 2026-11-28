from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import photo_index


class ScanRemovalTests(unittest.TestCase):
    """Removing many photos is saved a batch at a time and reads the search table once per batch."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.patches = [
            patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(self.root / "data")}),
            patch.object(photo_index, "REMOVAL_BATCH", 3),
            patch.object(photo_index, "REMOVAL_PAUSE_SECONDS", 0),
        ]
        for item in self.patches:
            item.start()
        self.library = self.root / "photos"
        (self.library / "gone").mkdir(parents=True)
        self.keepers = [self.library / f"kept{index}.jpg" for index in range(2)]
        self.leavers = [self.library / "gone" / f"leaving{index}.jpg" for index in range(8)]
        for index, path in enumerate(self.keepers + self.leavers):
            Image.new("RGB", (16, 16), (index * 20, 30, 40)).save(path)
            os.utime(path, ns=(10**18 + index * 10**9, 10**18 + index * 10**9))
        self.database = self.root / "library.sqlite3"
        self.assertEqual(self.scan(), 0)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def scan(self, **kwargs):
        return photo_index.scan_library(self.library, self.database, quiet=True, **kwargs)

    def query(self, sql, values=()):
        con = sqlite3.connect(self.database)
        try:
            return con.execute(sql, values).fetchall()
        finally:
            con.close()

    def names(self):
        return sorted(row[0] for row in self.query("SELECT filename FROM assets"))

    def assert_one_search_row_per_photo(self):
        self.assertEqual(
            self.query("SELECT asset_id, path FROM search_fts ORDER BY asset_id"),
            self.query("SELECT id, relative_path FROM assets ORDER BY id"),
        )

    def traced_scan(self, **kwargs):
        statements: list[str] = []
        real_connect = photo_index.connect

        def connect(path):
            con = real_connect(path)
            con.set_trace_callback(statements.append)
            return con

        with patch.object(photo_index, "connect", connect):
            result = self.scan(**kwargs)
        return result, [text for text in statements if text.startswith("DELETE FROM search_fts")]

    def delete_leavers(self):
        shutil.rmtree(self.library / "gone")

    def test_missing_photos_are_removed_in_batches_with_progress(self):
        self.delete_leavers()
        removed: list[int] = []

        result, search_deletes = self.traced_scan(
            progress=lambda counts: removed.append(counts["removed"]))

        self.assertEqual(result, 0)
        self.assertEqual(self.names(), ["kept0.jpg", "kept1.jpg"])
        self.assert_one_search_row_per_photo()
        self.assertEqual(self.query("SELECT COUNT(*) FROM text_data")[0][0], 2)
        self.assertEqual(sorted(set(removed)), [0, 3, 6, 8])
        # One for leftover rows at the start, then one for each of three batches.
        self.assertEqual(len(search_deletes), 4)

    def test_other_work_can_write_between_batches(self):
        self.delete_leavers()
        seen: list[int] = []

        def progress(counts):
            if not counts["removed"]:
                return
            other = sqlite3.connect(self.database, timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
                seen.append(other.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
                other.rollback()
            finally:
                other.close()

        self.assertEqual(self.scan(progress=progress), 0)

        self.assertEqual(seen[:3], [7, 4, 2])

    def test_cancelling_stops_after_a_batch_and_the_next_scan_finishes(self):
        self.delete_leavers()
        removing = []

        result = self.scan(
            progress=lambda counts: removing.append(True) if counts["removed"] else None,
            should_cancel=lambda: bool(removing))

        self.assertEqual(result, 3)
        self.assertEqual(len(self.names()), 7)
        self.assert_one_search_row_per_photo()
        self.assertEqual(
            self.query("SELECT removed, cancelled, finished_at IS NOT NULL FROM runs ORDER BY id DESC LIMIT 1"),
            [(3, 1, 1)])

        self.assertEqual(self.scan(), 0)
        self.assertEqual(self.names(), ["kept0.jpg", "kept1.jpg"])
        self.assert_one_search_row_per_photo()

    def test_photos_trashed_or_put_back_since_the_walk_are_kept(self):
        originals = {path.name: path.read_bytes() for path in self.leavers}
        self.delete_leavers()
        changed = []

        def progress(counts):
            if counts["removed"] != 3 or changed:
                return
            changed.append(True)
            con = sqlite3.connect(self.database, timeout=0)
            with con:
                con.execute("UPDATE assets SET in_review_bin=1 WHERE filename='leaving6.jpg'")
            con.close()
            (self.library / "gone").mkdir()
            (self.library / "gone" / "leaving7.jpg").write_bytes(originals["leaving7.jpg"])

        self.assertEqual(self.scan(progress=progress), 0)

        self.assertEqual(self.names(), ["kept0.jpg", "kept1.jpg", "leaving6.jpg", "leaving7.jpg"])
        self.assert_one_search_row_per_photo()

    def test_moved_photos_keep_their_tags_with_one_search_delete_for_all(self):
        con = photo_index.connect(self.database)
        ids = dict(con.execute("SELECT filename, id FROM assets"))
        for name, asset_id in ids.items():
            photo_index.set_source_tags(con, asset_id, "embedded_xmp", [f"invented {name}"])
            photo_index.rebuild_search_row(con, asset_id)
        con.commit()
        con.close()
        (self.library / "gone").rename(self.library / "moved")

        result, search_deletes = self.traced_scan()

        self.assertEqual(result, 0)
        self.assertEqual(dict(self.query("SELECT filename, id FROM assets")), ids)
        self.assert_one_search_row_per_photo()
        for path, tags in self.query("SELECT path, tags FROM search_fts"):
            self.assertIn(f"invented {path.rsplit('/', 1)[-1]}", tags)
        self.assertEqual(sum(path.startswith("moved/") for (path,) in self.query("SELECT path FROM search_fts")), 8)
        # Leftover rows at the start, then the moved photos together.
        self.assertEqual(len(search_deletes), 2)

    def test_a_new_photo_does_not_inherit_a_leftover_search_row(self):
        next_id = self.query("SELECT MAX(id) FROM assets")[0][0] + 1
        con = sqlite3.connect(self.database)
        with con:
            con.execute("INSERT INTO search_fts(asset_id, path) VALUES (?, 'leftover.jpg')", (next_id,))
        con.close()
        Image.new("RGB", (16, 16), (1, 2, 3)).save(self.library / "added.jpg")

        self.assertEqual(self.scan(), 0)

        self.assertEqual(self.query("SELECT id FROM assets WHERE filename='added.jpg'"), [(next_id,)])
        self.assert_one_search_row_per_photo()

    def test_a_changed_photo_keeps_a_single_search_row(self):
        Image.new("RGB", (20, 20), (9, 9, 9)).save(self.keepers[0])

        self.assertEqual(self.scan(), 0)

        self.assert_one_search_row_per_photo()

    def test_rebuilding_the_search_index_gives_one_row_per_photo(self):
        from database_tools import rebuild_search

        self.assertEqual(rebuild_search(self.database), 0)

        self.assert_one_search_row_per_photo()


if __name__ == "__main__":
    unittest.main()
