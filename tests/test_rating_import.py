from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

XMP_NAMESPACE = 'xmlns:xmp="http://ns.adobe.com/xap/1.0/"'


def xmp_packet(rating=None, *, attribute=True, extra=""):
    description = f'<rdf:Description rdf:about="" {XMP_NAMESPACE} {extra}'
    body = ""
    if rating is not None and attribute:
        description += f' xmp:Rating="{rating}"'
    if rating is not None and not attribute:
        body = f"<xmp:Rating>{rating}</xmp:Rating>"
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f"{description}>{body}</rdf:Description></rdf:RDF></x:xmpmeta>"
    ).encode("utf-8")


class ParseRatingTests(unittest.TestCase):
    def test_reads_the_rating_however_it_is_written(self):
        from photo_index import parse_xmp_rating

        cases = {
            "attribute": (xmp_packet(4), 4),
            "element": (xmp_packet(3, attribute=False), 3),
            "decimal": (xmp_packet("5.0"), 5),
            "another prefix": (
                b'<rdf:Description xmlns:xap="http://ns.adobe.com/xap/1.0/" xap:Rating=\'2\'/>', 2),
            "no rating": (xmp_packet(), 0),
            "nothing at all": (b"", 0),
        }
        for name, (data, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(parse_xmp_rating(data), expected)

    def test_ignores_what_is_not_a_one_to_five_star_rating(self):
        from photo_index import parse_xmp_rating

        for value in ("-1", "0", "6", "3.5", "high", ""):
            with self.subTest(value=value):
                self.assertEqual(parse_xmp_rating(xmp_packet(value)), 0)

    def test_does_not_read_the_windows_percentage_rating(self):
        from photo_index import parse_xmp_rating

        packet = xmp_packet(extra='xmlns:MicrosoftPhoto="http://ns.microsoft.com/photo/1.0/" MicrosoftPhoto:Rating="3"')
        self.assertEqual(parse_xmp_rating(packet), 0)

    def test_a_rating_is_read_from_a_file_that_keeps_its_xmp_anywhere(self):
        from photo_index import extract_embedded_rating

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "picture.png"
            path.write_bytes(b"\x89PNG-ish bytes" * 50 + xmp_packet(4) + b"more" * 50)
            self.assertEqual(extract_embedded_rating(path), 4)

    def test_a_file_that_cannot_be_read_is_not_taken_for_an_unrated_one(self):
        from photo_index import extract_embedded_rating

        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(extract_embedded_rating(Path(folder) / "missing.jpg"))
            raw = Path(folder) / "shot.cr2"
            raw.write_bytes(xmp_packet(4))
            self.assertIsNone(extract_embedded_rating(raw), "a type scan does not read embedded tags from")
            not_a_jpeg = Path(folder) / "broken.jpg"
            not_a_jpeg.write_bytes(b"not a jpeg" + xmp_packet(4))
            self.assertIsNone(extract_embedded_rating(not_a_jpeg))


class MergeRuleTests(unittest.TestCase):
    def test_merge(self):
        from photo_index import merge_file_rating

        # (ledger, file, base) -> (rating, new base, outcome)
        cases = {
            "both unrated, never synced": ((0, 0, None), (0, 0, "same")),
            "same rating, never synced": ((3, 3, None), (3, 3, "same")),
            "file rated, ledger not, never synced: import": ((0, 4, None), (4, 4, "imported")),
            "ledger rated, file not, never synced: keep, file stays unrated": ((4, 0, None), (4, 0, "kept")),
            "different ratings, never synced: ledger wins": ((3, 5, None), (3, 5, "kept")),
            "file unchanged, ledger cleared: stays cleared": ((0, 4, 4), (0, 4, "same")),
            "file unchanged, ledger changed: keep": ((3, 4, 4), (3, 4, "same")),
            "file changed, ledger unchanged: file wins": ((4, 2, 4), (2, 2, "imported")),
            "file newly rated, ledger never rated: file wins": ((0, 5, 0), (5, 5, "imported")),
            "rating removed from the file: ledger kept": ((4, 0, 4), (4, 0, "kept")),
            "both changed: ledger wins": ((3, 2, 4), (3, 2, "kept")),
            "ledger cleared then the file changed: ledger wins": ((0, 2, 4), (0, 2, "kept")),
        }
        for name, (given, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(merge_file_rating(*given), expected)


class ScanImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(self.root / "data")})
        self.environment.start()
        self.library = self.root / "photos"
        self.library.mkdir()
        self.database = self.root / "library.sqlite3"
        self.photo = self.library / "frame.jpg"
        self.clock = 1_700_000_000_000_000_000

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def put(self, rating=None, *, path=None, packet=None):
        path = path or self.photo
        data = packet if packet is not None else (xmp_packet(rating) if rating is not None else None)
        image = Image.new("RGB", (16, 16), (30, 90, 160))
        if data is None:
            image.save(path, "JPEG")
        else:
            image.save(path, "JPEG", xmp=data)
        self.clock += 10_000_000_000
        os.utime(path, ns=(self.clock, self.clock))

    def scan(self):
        from photo_index import scan_library

        self.assertEqual(scan_library(self.library, self.database, quiet=True), 0)

    def query(self, sql, *args):
        con = sqlite3.connect(self.database)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def ledger(self, name="frame.jpg"):
        rows = self.query("SELECT rating FROM asset_ratings WHERE relative_path=?", name)
        return rows[0][0] if rows else 0

    def base(self, name="frame.jpg"):
        rows = self.query("SELECT file_rating FROM asset_rating_sync WHERE relative_path=?", name)
        return rows[0][0] if rows else None

    def set_ledger(self, rating, name="frame.jpg"):
        con = sqlite3.connect(self.database)
        con.execute("DELETE FROM asset_ratings WHERE relative_path=?", (name,))
        if rating:
            con.execute("INSERT INTO asset_ratings(relative_path,rating,updated_at) VALUES (?,?,?)",
                        (name, rating, "2026-01-01T00:00:00Z"))
        con.commit()
        con.close()

    def test_a_rating_in_a_new_photo_is_imported(self):
        self.put(4)
        self.scan()
        self.assertEqual((self.ledger(), self.base()), (4, 4))

    def test_an_unrated_photo_imports_nothing(self):
        self.put()
        self.scan()
        self.assertEqual((self.ledger(), self.base()), (0, 0))
        self.assertEqual(self.query("SELECT COUNT(*) FROM asset_ratings"), [(0,)])

    def test_a_rating_set_here_is_not_touched_when_the_file_has_none(self):
        self.put()
        self.scan()
        self.set_ledger(3)
        self.put()
        self.scan()
        self.assertEqual(self.ledger(), 3)

    def test_a_rating_set_here_beats_a_different_one_already_in_the_file(self):
        self.put(5)
        self.scan()
        self.assertEqual(self.ledger(), 5, "first sight with nothing here: imported")
        # A fresh library: the rating exists here first.
        other = self.library / "other.jpg"
        self.put(5, path=other)
        con = sqlite3.connect(self.database)
        con.execute("INSERT INTO asset_ratings(relative_path,rating,updated_at) VALUES ('other.jpg',3,'now')")
        con.commit()
        con.close()
        self.scan()
        self.assertEqual((self.ledger("other.jpg"), self.base("other.jpg")), (3, 5))

    def test_a_rating_changed_in_another_program_is_picked_up_on_the_next_scan(self):
        self.put(4)
        self.scan()
        self.put(2)
        self.scan()
        self.assertEqual((self.ledger(), self.base()), (2, 2))

    def test_a_rating_cleared_here_stays_cleared_while_the_file_is_unchanged(self):
        self.put(4)
        self.scan()
        self.set_ledger(0)
        self.put(4)
        self.scan()
        self.assertEqual(self.ledger(), 0, "the file still holds 4, which is what was last seen")

    def test_a_rating_changed_here_beats_a_change_in_the_file(self):
        self.put(4)
        self.scan()
        self.set_ledger(5)
        self.put(2)
        self.scan()
        self.assertEqual((self.ledger(), self.base()), (5, 2))
        self.put(1)
        self.scan()
        self.assertEqual(self.ledger(), 5, "the rating set here has not been written yet, so it still wins")

    def test_a_rating_that_disappears_from_the_file_does_not_clear_ours(self):
        self.put(4)
        self.scan()
        self.put()
        self.scan()
        self.assertEqual((self.ledger(), self.base()), (4, 0))

    def test_upgrading_reads_ratings_from_photos_already_scanned_once(self):
        self.put(4)
        self.scan()
        con = sqlite3.connect(self.database)
        con.execute("DELETE FROM asset_ratings")
        con.execute("DELETE FROM asset_rating_sync")
        con.execute("UPDATE assets SET tags_scanned=3")
        con.commit()
        con.close()
        self.scan()
        self.assertEqual(self.ledger(), 4, "the photo is unchanged, but an older version never read its rating")
        self.set_ledger(0)
        self.scan()
        self.assertEqual(self.ledger(), 0, "the re-read happens once, not on every scan")

    def test_ratings_and_their_sync_record_follow_a_renamed_photo(self):
        self.put(4)
        self.scan()
        self.photo.rename(self.library / "renamed.jpg")
        self.scan()
        self.assertEqual((self.ledger("renamed.jpg"), self.base("renamed.jpg")), (4, 4))
        self.assertEqual(self.query("SELECT COUNT(*) FROM asset_rating_sync"), [(1,)])

    def test_a_rating_set_here_survives_a_rename_whatever_the_file_says(self):
        self.put(4)
        self.scan()
        self.set_ledger(5)
        self.photo.rename(self.library / "renamed.jpg")
        self.scan()
        self.assertEqual((self.ledger("renamed.jpg"), self.base("renamed.jpg")), (5, 4),
                         "the rename must not replace the rating set here with the file's")
        self.assertEqual(self.query("SELECT COUNT(*) FROM asset_ratings"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM asset_rating_sync"), [(1,)])

    def test_a_rating_cleared_here_stays_cleared_across_a_rename(self):
        self.put(4)
        self.scan()
        self.set_ledger(0)
        self.photo.rename(self.library / "renamed.jpg")
        self.scan()
        self.assertEqual(self.ledger("renamed.jpg"), 0, "the rename must not bring the cleared rating back")
        self.assertEqual(self.query("SELECT COUNT(*) FROM asset_ratings"), [(0,)])

    def test_a_library_upgraded_from_before_ratings_were_synced_keeps_its_rating_across_a_rename(self):
        self.put(4)
        self.scan()
        con = sqlite3.connect(self.database)
        con.execute("DELETE FROM asset_rating_sync")
        con.execute("UPDATE asset_ratings SET rating=5")
        con.commit()
        con.close()
        self.photo.rename(self.library / "renamed.jpg")
        self.scan()
        self.assertEqual((self.ledger("renamed.jpg"), self.base("renamed.jpg")), (5, 4),
                         "no record yet of what the file held, so the rename settles it against the file")

    def test_the_windows_percentage_rating_is_not_imported(self):
        packet = xmp_packet(extra='xmlns:MicrosoftPhoto="http://ns.microsoft.com/photo/1.0/" MicrosoftPhoto:Rating="3"')
        self.put(packet=packet)
        self.scan()
        self.assertEqual((self.ledger(), self.base()), (0, 0))


if __name__ == "__main__":
    unittest.main()
