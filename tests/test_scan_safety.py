from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


class ScanSafetyTests(unittest.TestCase):
    """A scan that cannot see the photos must never conclude that they were deleted."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(self.root / "data")})
        self.environment.start()
        self.library = self.root / "photos"
        (self.library / "trip").mkdir(parents=True)
        for index, folder in enumerate(["", "trip", "trip", ""]):
            Image.new("RGB", (16, 16), (index * 40, 20, 30)).save(self.library / folder / f"photo{index}.jpg")
        self.database = self.root / "library.sqlite3"

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def scan(self, root=None, **kwargs):
        from photo_index import scan_library

        return scan_library(root or self.library, self.database, quiet=True, **kwargs)

    def asset_count(self) -> int:
        con = sqlite3.connect(self.database)
        try:
            return int(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        finally:
            con.close()

    def test_a_folder_that_is_not_there_is_refused_and_the_index_is_kept(self):
        self.assertEqual(self.scan(), 0)
        self.assertEqual(self.asset_count(), 4)

        with self.assertRaises(FileNotFoundError):
            self.scan(self.root / "unplugged drive")

        self.assertEqual(self.asset_count(), 4)

    def test_a_folder_that_comes_back_with_no_photos_leaves_the_index_alone(self):
        self.assertEqual(self.scan(), 0)
        for path in self.library.rglob("*.jpg"):
            path.unlink()

        with self.assertRaises(OSError):
            self.scan()

        self.assertEqual(self.asset_count(), 4)

    def test_photos_deleted_from_a_readable_library_are_still_removed(self):
        self.assertEqual(self.scan(), 0)
        (self.library / "photo0.jpg").unlink()

        self.assertEqual(self.scan(), 0)

        self.assertEqual(self.asset_count(), 3)

    def test_a_first_scan_of_an_empty_folder_is_fine(self):
        empty = self.root / "empty"
        empty.mkdir()

        self.assertEqual(self.scan(empty), 0)
        self.assertEqual(self.asset_count(), 0)

    def test_photos_in_a_folder_that_cannot_be_listed_are_kept(self):
        self.assertEqual(self.scan(), 0)
        locked = self.library / "trip"
        (self.library / "photo0.jpg").unlink()
        real_walk = os.walk

        def walk_without_listing_locked(top, onerror=None, **kwargs):
            for entry in real_walk(top, onerror=onerror, **kwargs):
                if Path(entry[0]) == locked:
                    if onerror:
                        onerror(PermissionError(13, "Access is denied", str(locked)))
                    entry[1][:] = []
                    continue
                yield entry

        with patch("photo_index.os.walk", walk_without_listing_locked):
            result = self.scan()

        self.assertEqual(result, 2, "an unreadable folder is reported as an error")
        con = sqlite3.connect(self.database)
        names = sorted(row[0] for row in con.execute("SELECT filename FROM assets"))
        con.close()
        self.assertEqual(names, ["photo1.jpg", "photo2.jpg", "photo3.jpg"], "photo0 was deleted; the locked folder's photos stay")

    def test_the_folders_that_cannot_be_listed_are_named_in_the_error_details(self):
        self.assertEqual(self.scan(), 0)
        locked = self.library / "trip"
        real_walk = os.walk

        def walk_without_listing_locked(top, onerror=None, **kwargs):
            for entry in real_walk(top, onerror=onerror, **kwargs):
                if Path(entry[0]) == locked:
                    onerror(PermissionError(13, "Access is denied", str(locked)))
                    continue
                yield entry

        seen = {}

        def progress(counts):
            seen.update(counts)

        with patch("photo_index.os.walk", walk_without_listing_locked):
            self.scan(progress=progress)

        self.assertEqual([item["path"] for item in seen["error_details"]], ["trip"])

    def test_iter_media_skips_the_application_folders(self):
        from photo_index import iter_media

        hidden = self.library / "!LensLedger"
        hidden.mkdir()
        Image.new("RGB", (8, 8)).save(hidden / "thumb.jpg")

        names = sorted(rel.as_posix() for _path, rel in iter_media(self.library))

        self.assertEqual(names, ["photo0.jpg", "photo3.jpg", "trip/photo1.jpg", "trip/photo2.jpg"])


if __name__ == "__main__":
    unittest.main()
