from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image


class ExactDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "photos"
        (self.library / "Trip").mkdir(parents=True)
        (self.library / "Backup").mkdir()
        self.database = self.root / "library.sqlite3"
        Image.new("RGB", (40, 30), (10, 20, 30)).save(self.library / "Trip" / "IMG_0001.jpg", quality=90)
        (self.library / "Backup" / "IMG_0001 (copy).jpg").write_bytes(
            (self.library / "Trip" / "IMG_0001.jpg").read_bytes()
        )
        Image.new("RGB", (40, 30), (200, 20, 30)).save(self.library / "Trip" / "IMG_0002.jpg", quality=90)

    def tearDown(self):
        self.temporary.cleanup()

    def groups(self, **kwargs):
        from photo_duplicates import find_exact_duplicate_groups
        from photo_index import connect

        con = connect(self.database)
        try:
            return [[item["filename"] for item in group]
                    for group in find_exact_duplicate_groups(con, **kwargs)]
        finally:
            con.close()

    def test_identical_files_are_grouped_with_the_shortest_name_first(self):
        from photo_index import scan_library

        scan_library(self.library, self.database)

        self.assertEqual(self.groups(), [["IMG_0001.jpg", "IMG_0001 (copy).jpg"]])

    def test_files_sharing_a_fingerprint_but_not_their_contents_are_not_duplicates(self):
        from photo_index import CONTENT_HASH_BLOCK, scan_library

        head = b"\xff\xd8" + b"\x00" * (CONTENT_HASH_BLOCK + 100)
        (self.library / "Trip" / "a.jpg").write_bytes(head + b"tail-one")
        (self.library / "Trip" / "b.jpg").write_bytes(head + b"tail-two")
        scan_library(self.library, self.database)
        con = sqlite3.connect(self.database)
        hashes = {row[0] for row in con.execute(
            "SELECT content_hash FROM assets WHERE filename IN ('a.jpg','b.jpg')"
        )}
        con.close()
        self.assertEqual(len(hashes), 1, "the fixture must collide on the stored fingerprint")

        self.assertEqual(self.groups(), [["IMG_0001.jpg", "IMG_0001 (copy).jpg"]])

    def test_a_copy_in_the_review_bin_no_longer_makes_a_duplicate(self):
        from photo_index import scan_library

        scan_library(self.library, self.database)
        con = sqlite3.connect(self.database)
        con.execute("UPDATE assets SET in_review_bin=1 WHERE filename='IMG_0001 (copy).jpg'")
        con.commit()
        con.close()

        self.assertEqual(self.groups(), [])

    def test_a_path_filter_keeps_the_whole_group_when_one_copy_matches(self):
        from photo_index import scan_library

        scan_library(self.library, self.database)

        self.assertEqual(self.groups(tokens=["backup"]), [["IMG_0001.jpg", "IMG_0001 (copy).jpg"]])
        self.assertEqual(self.groups(tokens=["nowhere"]), [])


if __name__ == "__main__":
    unittest.main()
