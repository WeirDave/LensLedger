from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image


class FilterParsingTests(unittest.TestCase):
    def test_valid_values_are_kept_and_invalid_ones_dropped(self):
        from photo_filters import parse_filters

        filters = parse_filters({
            "from": ["2020-01-01"], "to": ["not-a-date"], "people": ["3", "x", "3,5"],
            "kind": ["video"], "shape": ["triangle"], "gps": ["has"], "camera": [" Nikon "], "folder": ["Trip"],
        })
        self.assertEqual(
            (filters.from_date, filters.to_date, filters.people, filters.kind, filters.shape,
             filters.gps, filters.camera, filters.folder),
            ("2020-01-01", "", [3, 5], "video", "", "has", "Nikon", "Trip"),
        )
        self.assertTrue(filters.active)

    def test_nothing_set_means_not_active_and_no_conditions(self):
        from photo_filters import filter_clauses, parse_filters

        filters = parse_filters({})
        self.assertFalse(filters.active)
        self.assertEqual(filter_clauses(filters), ([], []))
        self.assertEqual(filter_clauses(None), ([], []))


class FilterQueryTests(unittest.TestCase):
    """Every filter, run against a real catalog of invented photos."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = self.root / "photos"
        (self.library / "Summer Trip").mkdir(parents=True)
        (self.library / "Office").mkdir()
        Image.new("RGB", (60, 40), (10, 20, 30)).save(self.library / "Summer Trip" / "2021-07-04 wide.jpg")
        Image.new("RGB", (40, 60), (30, 20, 10)).save(self.library / "Summer Trip" / "2021-07-09 tall.jpg")
        Image.new("RGB", (50, 50), (90, 20, 10)).save(self.library / "Office" / "2022-01-05 square.jpg")
        (self.library / "Office" / "2022-02-01 clip.mp4").write_bytes(b"\x00" * 64)
        self.database = self.root / "library.sqlite3"
        from photo_index import connect, scan_library

        scan_library(self.library, self.database)
        self.con = connect(self.database)
        self.addCleanup(self.con.close)

    def names(self, **fields):
        from photo_filters import filter_clauses, parse_filters

        clauses, values = filter_clauses(parse_filters({key: [value] if isinstance(value, str) else value
                                                         for key, value in fields.items()}))
        where = " AND ".join(["a.in_review_bin=0", *clauses])
        return sorted(row[0] for row in self.con.execute(
            f"SELECT a.filename FROM assets a WHERE {where}", values))

    def test_size_and_shape_are_read_when_a_library_is_scanned(self):
        row = self.con.execute(
            "SELECT width, height, facets_scanned FROM assets WHERE filename='2021-07-09 tall.jpg'").fetchone()
        self.assertEqual((row["width"], row["height"]), (40, 60))
        self.assertEqual(row["facets_scanned"], 1)

    def test_shape(self):
        self.assertEqual(self.names(shape="landscape"), ["2021-07-04 wide.jpg"])
        self.assertEqual(self.names(shape="portrait"), ["2021-07-09 tall.jpg"])
        self.assertEqual(self.names(shape="square"), ["2022-01-05 square.jpg"])

    def test_kind(self):
        self.assertEqual(self.names(kind="video"), ["2022-02-01 clip.mp4"])
        self.assertEqual(len(self.names(kind="image")), 3)

    def test_date_range_is_inclusive_and_open_ended(self):
        self.assertEqual(self.names(**{"from": "2021-07-05", "to": "2022-01-05"}),
                         ["2021-07-09 tall.jpg", "2022-01-05 square.jpg"])
        self.assertEqual(self.names(**{"from": "2022-01-06"}), ["2022-02-01 clip.mp4"])
        self.assertEqual(self.names(to="2021-07-04"), ["2021-07-04 wide.jpg"])

    def test_folder_is_a_literal_substring(self):
        self.assertEqual(len(self.names(folder="Summer")), 2)
        self.assertEqual(self.names(folder="%"), [], "a percent sign is searched for, not treated as a wildcard")

    def test_camera_matches_make_and_model_together(self):
        self.con.execute(
            "UPDATE assets SET camera_make='Fabrikam', camera_model='Zoom 9' WHERE filename='2021-07-04 wide.jpg'")
        self.assertEqual(self.names(camera="fabrikam zoom"), ["2021-07-04 wide.jpg"])
        self.assertEqual(self.names(camera="zoom 9"), ["2021-07-04 wide.jpg"])
        self.assertEqual(self.names(camera="Contoso"), [])

    def test_location(self):
        self.con.execute("UPDATE assets SET gps_latitude=10.5, gps_longitude=20.5 WHERE filename='2021-07-04 wide.jpg'")
        self.assertEqual(self.names(gps="has"), ["2021-07-04 wide.jpg"])
        self.assertEqual(len(self.names(gps="none")), 3)

    def test_several_people_must_all_be_confirmed_in_the_photo(self):
        self.con.execute("INSERT INTO people(name) VALUES ('Person One'), ('Person Two')")
        one, two = [int(row[0]) for row in self.con.execute("SELECT id FROM people ORDER BY name")]
        wide, tall = [int(self.con.execute("SELECT id FROM assets WHERE filename=?", (name,)).fetchone()[0])
                      for name in ("2021-07-04 wide.jpg", "2021-07-09 tall.jpg")]
        for asset, person, state in ((wide, one, "confirmed"), (wide, two, "confirmed"),
                                     (tall, one, "confirmed"), (tall, two, "suggested")):
            self.con.execute(
                "INSERT INTO asset_people(asset_id, person_id, state, source, updated_at) VALUES (?,?,?,'t','now')",
                (asset, person, state))
        self.assertEqual(self.names(people=[str(one)]), ["2021-07-04 wide.jpg", "2021-07-09 tall.jpg"])
        self.assertEqual(self.names(people=[str(one), str(two)]), ["2021-07-04 wide.jpg"],
                         "a suggestion is not a confirmation")

    def test_scanning_again_fills_in_photos_indexed_before_these_details_existed(self):
        from photo_index import scan_library

        self.con.execute("UPDATE assets SET width=0, height=0, facets_scanned=0")
        self.con.commit()
        scan_library(self.library, self.database)
        check = sqlite3.connect(self.database)
        self.addCleanup(check.close)
        self.assertEqual(check.execute(
            "SELECT width, height FROM assets WHERE filename='2021-07-04 wide.jpg'").fetchone(), (60, 40))

    def test_orientation_recorded_by_the_camera_swaps_width_and_height(self):
        from photo_index import extract_facets

        rotated = self.root / "rotated.jpg"
        image = Image.new("RGB", (60, 40))
        exif = image.getexif()
        exif[0x0112] = 6
        exif[0x010F] = "Fabrikam"
        exif[0x0110] = "Zoom 9"
        image.save(rotated, exif=exif)
        facets = extract_facets(rotated)
        self.assertEqual((facets["width"], facets["height"]), (40, 60))
        self.assertEqual((facets["camera_make"], facets["camera_model"]), ("Fabrikam", "Zoom 9"))

    def test_a_long_list_of_ids_is_checked_in_chunks(self):
        from photo_filters import keep_matching_ids

        real = [int(row[0]) for row in self.con.execute("SELECT id FROM assets")]
        ids = list(range(10_000, 40_000)) + real
        self.assertEqual(keep_matching_ids(self.con, ids, "a.in_review_bin=0", []), set(real))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        from photo_index import connect

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.con = connect(Path(self.temporary.name) / "library.sqlite3")
        self.addCleanup(self.con.close)

    def test_only_what_describes_the_search_is_kept(self):
        from photo_filters import collection_query, collection_url

        query = collection_query("q=beach&page=4&selected=9&people=1&people=2&kind=video&unknown=x&rating=")
        self.assertEqual(query, {"q": ["beach"], "people": ["1", "2"], "kind": ["video"]})
        self.assertEqual(collection_url(query), "/?q=beach&people=1&people=2&kind=video")
        self.assertEqual(collection_url({}), "/")

    def test_save_list_replace_and_delete(self):
        from photo_filters import delete_collection, list_collections, save_collection

        first, replaced = save_collection(self.con, "  Summer   portraits ", "kind=image&shape=portrait&page=3")
        self.assertFalse(replaced)
        again, replaced = save_collection(self.con, "summer PORTRAITS", "kind=video")
        self.assertEqual((again, replaced), (first, True), "the same name, in any case, replaces the collection")
        saved = list_collections(self.con)
        self.assertEqual([(item["name"], item["url"]) for item in saved], [("Summer portraits", "/?kind=video")])
        self.assertTrue(delete_collection(self.con, first))
        self.assertFalse(delete_collection(self.con, first))
        self.assertEqual(list_collections(self.con), [])

    def test_an_empty_search_or_a_missing_name_is_refused(self):
        from photo_filters import save_collection

        with self.assertRaises(ValueError):
            save_collection(self.con, "Nothing", "page=2")
        with self.assertRaises(ValueError):
            save_collection(self.con, "   ", "kind=video")

    def test_the_number_of_collections_is_limited(self):
        from photo_filters import MAX_COLLECTIONS, save_collection

        for number in range(MAX_COLLECTIONS):
            save_collection(self.con, f"Collection {number}", "kind=video")
        with self.assertRaises(ValueError):
            save_collection(self.con, "One too many", "kind=video")
        save_collection(self.con, "Collection 0", "kind=image")


if __name__ == "__main__":
    unittest.main()
