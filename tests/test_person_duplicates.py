from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class NameMatchTests(unittest.TestCase):
    def reason(self, left: str, right: str, owners: dict[str, int] | None = None) -> str:
        from person_duplicates import name_reason, normalize_name

        return name_reason([normalize_name(left)], [normalize_name(right)], owners or {})

    def test_punctuation_accents_and_capitals_are_ignored(self):
        self.assertIn("Same name", self.reason("Zoë O'Hara", "zoe o hara"))

    def test_an_initial_matches_the_full_name(self):
        self.assertIn("initial", self.reason("J. Robert Thornton III", "James Robert Thornton III"))

    def test_a_shorter_name_inside_a_longer_one(self):
        self.assertIn("part of", self.reason("Mira Castellan", "Mira Jo Castellan"))
        self.assertIn("part of", self.reason("Mira", "Mira Castellan", {"mira": 2}))

    def test_a_common_first_name_alone_is_not_enough(self):
        self.assertEqual(self.reason("Mira", "Mira Castellan", {"mira": 5}), "")

    def test_a_small_misspelling(self):
        self.assertIn("almost", self.reason("Katherine Vollmer", "Kathrine Vollmer"))

    def test_different_generations_are_not_duplicates(self):
        self.assertEqual(self.reason("Aldo Brennick Jr", "Aldo Brennick Sr"), "")

    def test_unrelated_names(self):
        self.assertEqual(self.reason("Aldo Brennick", "Mira Castellan"), "")


class FindDuplicatesTests(unittest.TestCase):
    def setUp(self):
        from photo_index import connect

        self.temporary = tempfile.TemporaryDirectory()
        self.con = connect(Path(self.temporary.name) / "library.sqlite3")

    def tearDown(self):
        self.con.close()
        self.temporary.cleanup()

    def add_person(self, name: str) -> int:
        return int(self.con.execute("INSERT INTO people(name) VALUES (?)", (name,)).lastrowid)

    def add_profile(self, person_id: int, vector: tuple[float, ...]) -> None:
        from face_learning import encode_vector
        from photo_index import utc_now

        self.con.execute(
            """INSERT INTO person_face_profiles(person_id,dimensions,centroid_f32,
                   training_face_ids_json,training_assets,cohesion,updated_at)
               VALUES (?,?,?,'[]',2,1.0,?)""",
            (person_id, len(vector), encode_vector(vector), utc_now()),
        )

    def names_of(self, pairs) -> list[set[str]]:
        return [{person["name"] for person in pair["people"]} for pair in pairs]

    def test_matching_names_and_faces_are_listed_first(self):
        from person_duplicates import find_possible_duplicates

        spelled = self.add_person("Katherine Vollmer")
        self.add_person("Kathrine Vollmer")
        face_a = self.add_person("Aldo Brennick")
        face_b = self.add_person("Uncle Al")
        both_a = self.add_person("Mira Castellan")
        both_b = self.add_person("Mira Castelan")
        self.add_person("Tobias Wren")
        self.add_profile(face_a, (1.0, 0.0))
        self.add_profile(face_b, (0.95, 0.31))
        self.add_profile(both_a, (0.0, 1.0))
        self.add_profile(both_b, (0.1, 0.99))
        self.add_profile(spelled, (-1.0, 0.0))

        pairs = find_possible_duplicates(self.con)
        self.assertEqual(self.names_of(pairs), [
            {"Mira Castellan", "Mira Castelan"},
            {"Aldo Brennick", "Uncle Al"},
            {"Katherine Vollmer", "Kathrine Vollmer"},
        ])
        self.assertEqual(len(pairs[0]["reasons"]), 2)
        self.assertEqual(pairs[1]["reasons"], ["Their learned faces look alike"])

    def test_people_confirmed_in_one_photo_are_not_duplicates(self):
        from person_duplicates import find_possible_duplicates
        from photo_index import utc_now

        left = self.add_person("Katherine Vollmer")
        right = self.add_person("Kathrine Vollmer")
        asset = int(self.con.execute(
            """INSERT INTO assets(path,relative_path,folder,filename,extension,media_type,size_bytes,mtime_ns,indexed_at)
               VALUES ('a.jpg','a.jpg','','a.jpg','.jpg','image',1,0,'2026-01-01')"""
        ).lastrowid)
        for person_id in (left, right):
            self.con.execute(
                """INSERT INTO asset_people(asset_id,person_id,state,source,updated_at)
                   VALUES (?,?,'confirmed','manual',?)""",
                (asset, person_id, utc_now()),
            )
        self.assertEqual(find_possible_duplicates(self.con), [])

    def test_a_dismissed_pair_stays_dismissed(self):
        from person_duplicates import dismiss_pair, find_possible_duplicates

        left = self.add_person("Katherine Vollmer")
        right = self.add_person("Kathrine Vollmer")
        self.assertEqual(len(find_possible_duplicates(self.con)), 1)
        dismiss_pair(self.con, right, left)
        self.assertEqual(find_possible_duplicates(self.con), [])
        self.con.execute("DELETE FROM people WHERE id=?", (right,))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM person_duplicate_dismissals").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            dismiss_pair(self.con, left, left)


if __name__ == "__main__":
    unittest.main()
