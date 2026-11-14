from __future__ import annotations

import sqlite3
import tempfile
import unittest
import unittest.mock
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


class LibraryLookupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = self.root / "photos"
        self.library.mkdir()
        self.database = self.root / "library.sqlite3"
        self.original = self.library / "IMG_0001.jpg"
        Image.new("RGB", (40, 30), (10, 20, 30)).save(self.original, quality=90)

    def lookup(self, candidate):
        from photo_duplicates import library_has_identical_file
        from photo_index import connect, scan_library

        scan_library(self.library, self.database)
        con = connect(self.database)
        try:
            return library_has_identical_file(con, candidate)
        finally:
            con.close()

    def test_a_byte_identical_file_elsewhere_is_recognised(self):
        incoming = self.root / "incoming.jpg"
        incoming.write_bytes(self.original.read_bytes())
        self.assertTrue(self.lookup(incoming))

    def test_a_different_picture_is_not(self):
        incoming = self.root / "incoming.jpg"
        Image.new("RGB", (40, 30), (200, 20, 30)).save(incoming, quality=90)
        self.assertFalse(self.lookup(incoming))

    def test_a_file_with_the_same_head_and_size_but_other_contents_is_not(self):
        from photo_index import CONTENT_HASH_BLOCK

        head = b"\xff\xd8" + b"\x00" * (CONTENT_HASH_BLOCK + 100)
        (self.library / "a.jpg").write_bytes(head + b"tail-one")
        incoming = self.root / "b.jpg"
        incoming.write_bytes(head + b"tail-two")
        self.assertFalse(self.lookup(incoming))


class StoredHashAndCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = self.root / "photos"
        (self.library / "Trip").mkdir(parents=True)
        (self.library / "Backup").mkdir()
        self.database = self.root / "library.sqlite3"
        Image.new("RGB", (40, 30), (10, 20, 30)).save(self.library / "Trip" / "IMG_0001.jpg", quality=90)
        (self.library / "Backup" / "IMG_0001 (copy).jpg").write_bytes(
            (self.library / "Trip" / "IMG_0001.jpg").read_bytes()
        )
        from photo_index import scan_library

        scan_library(self.library, self.database)

    def connect(self):
        from photo_index import connect

        return connect(self.database)

    def test_full_hashes_are_stored_and_not_computed_twice(self):
        import photo_duplicates

        con = self.connect()
        self.addCleanup(con.close)
        first = photo_duplicates.find_exact_duplicate_groups(con)
        self.assertEqual(len(first), 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM asset_full_hashes").fetchone()[0], 2)

        photo_duplicates._full_hash_cache.clear()
        with unittest.mock.patch.object(photo_duplicates, "_full_hash", side_effect=AssertionError("re-read")):
            again = photo_duplicates.find_exact_duplicate_groups(con)
        self.assertEqual(again, first)

    def test_a_changed_file_is_hashed_again(self):
        import photo_duplicates

        con = self.connect()
        self.addCleanup(con.close)
        photo_duplicates.find_exact_duplicate_groups(con)
        con.execute("UPDATE asset_full_hashes SET mtime_ns=mtime_ns+1")
        photo_duplicates._full_hash_cache.clear()
        with unittest.mock.patch.object(photo_duplicates, "_full_hash", wraps=photo_duplicates._full_hash) as spy:
            photo_duplicates.find_exact_duplicate_groups(con)
        self.assertEqual(spy.call_count, 2)

    def test_grouping_is_remembered_until_the_library_changes(self):
        import photo_duplicates

        photo_duplicates.clear_group_cache()
        con = self.connect()
        self.addCleanup(con.close)
        calls = []

        def compute(connection, where, values, tokens):
            calls.append(1)
            return photo_duplicates.find_exact_duplicate_groups(connection, where, values, tokens)

        where = "a.in_review_bin=0"
        first = photo_duplicates.cached_groups(con, "duplicates", compute, where, [], [])
        second = photo_duplicates.cached_groups(con, "duplicates", compute, where, [], [])
        self.assertEqual((len(calls), second), (1, first))

        con.execute("UPDATE assets SET in_review_bin=1 WHERE filename LIKE '%copy%'")
        con.commit()
        third = photo_duplicates.cached_groups(con, "duplicates", compute, where, [], [])
        self.assertEqual((len(calls), third), (2, []))


class KeepBestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = self.root / "photos"
        (self.library / "Birthday 2019").mkdir(parents=True)
        (self.library / "Misc").mkdir()
        self.database = self.root / "library.sqlite3"
        Image.new("RGB", (40, 30), (10, 20, 30)).save(self.library / "Misc" / "IMG_0001.jpg", quality=90)
        (self.library / "Birthday 2019" / "IMG_0001 (copy).jpg").write_bytes(
            (self.library / "Misc" / "IMG_0001.jpg").read_bytes()
        )
        from photo_index import connect, scan_library

        scan_library(self.library, self.database)
        self.con = connect(self.database)
        self.addCleanup(self.con.close)
        self.original = int(self.con.execute("SELECT id FROM assets WHERE filename='IMG_0001.jpg'").fetchone()[0])
        self.copy = int(self.con.execute("SELECT id FROM assets WHERE filename='IMG_0001 (copy).jpg'").fetchone()[0])
        self.original_path = self.con.execute(
            "SELECT relative_path FROM assets WHERE id=?", (self.original,)).fetchone()[0]
        self.copy_path = self.con.execute(
            "SELECT relative_path FROM assets WHERE id=?", (self.copy,)).fetchone()[0]

    def plan(self):
        from photo_duplicates import find_exact_duplicate_groups, plan_keep_best

        return plan_keep_best(self.con, find_exact_duplicate_groups(self.con))

    def test_the_likely_original_is_kept_and_the_copy_is_the_extra(self):
        plan = self.plan()
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["keeper"]["id"], self.original)
        self.assertEqual([extra["id"] for extra in plan[0]["extras"]], [self.copy])

    def test_the_token_changes_when_the_plan_does(self):
        from photo_duplicates import plan_token

        plan = self.plan()
        self.assertEqual(plan_token(plan), plan_token(self.plan()))
        self.assertNotEqual(plan_token(plan), plan_token([]))

    def test_merging_carries_the_copys_work_onto_the_keeper_and_leaves_the_copy_intact(self):
        from photo_duplicates import merge_into_keeper

        self.con.execute("INSERT INTO asset_ratings(relative_path, rating, updated_at) VALUES (?,4,'now')", (self.copy_path,))
        self.con.execute("INSERT INTO asset_ratings(relative_path, rating, updated_at) VALUES (?,2,'now')", (self.original_path,))
        self.con.execute(
            "INSERT INTO asset_annotations(relative_path, subject, tags) VALUES (?,?,?)",
            (self.copy_path, "Cake", "candles;balloons"),
        )
        self.con.execute(
            "INSERT INTO asset_annotations(relative_path, subject, tags) VALUES (?,?,?)",
            (self.original_path, "", "Candles"),
        )
        self.con.execute("INSERT INTO people(name) VALUES ('Sample Person')")
        person = int(self.con.execute("SELECT id FROM people").fetchone()[0])
        self.con.execute(
            "INSERT INTO asset_people(asset_id, person_id, state, source, updated_at) VALUES (?,?,'confirmed','test','now')",
            (self.copy, person),
        )
        self.con.execute("INSERT OR IGNORE INTO text_data(asset_id) VALUES (?)", (self.copy,))
        self.con.execute("UPDATE text_data SET caption='A cake' WHERE asset_id=?", (self.copy,))

        merged = merge_into_keeper(self.con, self.original, [self.copy])

        self.assertEqual(self.con.execute(
            "SELECT rating FROM asset_ratings WHERE relative_path=?", (self.original_path,)).fetchone()[0], 4)
        note = self.con.execute(
            "SELECT subject, tags FROM asset_annotations WHERE relative_path=?", (self.original_path,)).fetchone()
        self.assertEqual(note["subject"], "Cake")
        self.assertEqual(sorted(tag.casefold() for tag in note["tags"].split(";")),
                         ["balloons", "birthday", "candles"],
                         "a tag the keeper already has is not repeated, and the folder's tag is carried over")
        self.assertEqual(self.con.execute(
            "SELECT state FROM asset_people WHERE asset_id=? AND person_id=?", (self.original, person)).fetchone()[0],
            "confirmed")
        self.assertEqual(self.con.execute(
            "SELECT caption FROM text_data WHERE asset_id=?", (self.original,)).fetchone()[0], "A cake")
        searchable = self.con.execute("SELECT tags FROM search_fts WHERE asset_id=?", (self.original,)).fetchone()[0]
        self.assertIn("Sample Person", searchable)
        self.assertEqual(self.con.execute(
            "SELECT rating FROM asset_ratings WHERE relative_path=?", (self.copy_path,)).fetchone()[0], 4,
            "the copy keeps its own records")
        self.assertEqual((merged["people"], merged["rating"]), (1, 1))

    def test_merging_never_lowers_a_rating_or_overrides_a_subject(self):
        from photo_duplicates import merge_into_keeper

        self.con.execute("INSERT INTO asset_ratings(relative_path, rating, updated_at) VALUES (?,1,'now')", (self.copy_path,))
        self.con.execute("INSERT INTO asset_ratings(relative_path, rating, updated_at) VALUES (?,5,'now')", (self.original_path,))
        self.con.execute("INSERT INTO asset_annotations(relative_path, subject, tags) VALUES (?,?,'')", (self.copy_path, "Other"))
        self.con.execute("INSERT INTO asset_annotations(relative_path, subject, tags) VALUES (?,?,'')", (self.original_path, "Mine"))

        merge_into_keeper(self.con, self.original, [self.copy])

        self.assertEqual(self.con.execute(
            "SELECT rating FROM asset_ratings WHERE relative_path=?", (self.original_path,)).fetchone()[0], 5)
        self.assertEqual(self.con.execute(
            "SELECT subject FROM asset_annotations WHERE relative_path=?", (self.original_path,)).fetchone()[0], "Mine")
