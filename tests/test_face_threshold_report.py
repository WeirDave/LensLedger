from __future__ import annotations

import array
import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

import face_math

NAMES = ("Zzyzx Invented", "Qwerty Fictional", "Plugh Madeup")
FOLDER = "invented-folder-name"


def unit(angle_degrees: float) -> bytes:
    radians = math.radians(angle_degrees)
    return array.array("f", [math.cos(radians), math.sin(radians)]).tobytes()


def build_catalogue(path: Path, groups: dict[str, list[float]], unnamed_angles: list[float] = ()) -> None:
    """One invented person per name, each with a face at every listed angle."""
    from photo_index import connect, utc_now

    con = connect(path)
    counter = 0

    def add_face(angle: float, person_id: int | None) -> None:
        nonlocal counter
        relative = f"{FOLDER}/synthetic-{counter}.jpg"
        asset_id = int(con.execute(
            """INSERT INTO assets(path,relative_path,folder,filename,extension,media_type,
                   size_bytes,mtime_ns,capture_date,indexed_at)
               VALUES (?,?,?,?,'.jpg','image',10,1,'2026-01-01',?)""",
            (relative, relative, FOLDER, relative, utc_now()),
        ).lastrowid)
        face_id = int(con.execute(
            """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,dimensions,
                   embedding_f32,box_left,box_top,box_right,box_bottom)
               VALUES ('test',?,?,?,2,?,0.1,0.1,0.4,0.4)""",
            (counter, asset_id, relative, unit(angle)),
        ).lastrowid)
        if person_id is not None:
            con.execute(
                """INSERT INTO asset_people(asset_id,person_id,state,face_id,source,updated_at)
                   VALUES (?,?,'confirmed',?,'manual_face',?)""",
                (asset_id, person_id, face_id, f"2026-02-01T00:00:{counter % 60:02d}"),
            )
            con.execute("UPDATE face_embeddings SET person_id=? WHERE id=?", (person_id, face_id))
        counter += 1

    for name, angles in groups.items():
        person_id = int(con.execute("INSERT INTO people(name) VALUES (?)", (name,)).lastrowid)
        for angle in angles:
            add_face(angle, person_id)
    for angle in unnamed_angles:
        add_face(angle, None)
    con.commit()
    con.close()


@unittest.skipIf(face_math.np is None, "NumPy is not installed")
class ThresholdReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "library.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def report(self, **options):
        from face_threshold_report import build_report
        return build_report(self.path, options.get("seeds", 8), options.get("seed", 1))

    def test_well_separated_people_are_reached_by_every_cutoff_with_no_false_matches(self):
        build_catalogue(self.path, {
            NAMES[0]: [0, 3, -3, 5, -5, 2],
            NAMES[1]: [90, 93, 87, 95, 85, 92],
            NAMES[2]: [180, 183, 177, 185, 175, 182],
        })
        named = self.report()["all_named_faces"]
        self.assertEqual((named["faces"], named["people"]), (18, 3))
        row = named["by_cutoff"]["0.70"]
        self.assertEqual(row["same_person_pairs_reached"], 1.0)
        self.assertEqual(row["different_person_pairs_matched"], 0.0)
        self.assertEqual(row["faces_a_wrong_centroid_reaches"], 0.0)
        self.assertEqual(row["faces_with_a_wrong_face_this_close"], 0.0)
        self.assertEqual(row["faces_whose_own_centroid_reaches"], 1.0)

    def test_two_people_who_look_alike_show_up_as_wrong_matches(self):
        build_catalogue(self.path, {
            NAMES[0]: [0, 4, 8, 12, 16],
            NAMES[1]: [10, 14, 18, 22, 26],
            NAMES[2]: [180, 184, 176, 188, 172],
        })
        row = self.report()["all_named_faces"]["by_cutoff"]["0.90"]
        self.assertGreater(row["faces_with_a_wrong_face_this_close"], 0.0)
        self.assertGreater(row["faces_a_wrong_centroid_reaches"], 0.0)
        self.assertGreater(row["different_person_pairs_matched"], 0.0)

    def test_a_cutoff_between_the_spreads_splits_same_person_pairs(self):
        build_catalogue(self.path, {
            NAMES[0]: [0, 60, 120],
            NAMES[1]: [200, 260, 320],
        })
        by_cutoff = self.report()["all_named_faces"]["by_cutoff"]
        # Pairs are 0-60 and 60-120 (cos 60 = 0.5) and 0-120 (cos 120 = -0.5).
        self.assertAlmostEqual(by_cutoff["0.45"]["same_person_pairs_reached"], 2 / 3, places=3)
        self.assertEqual(by_cutoff["0.55"]["same_person_pairs_reached"], 0.0)

    def test_earliest_block_uses_only_each_persons_first_faces(self):
        build_catalogue(self.path, {NAMES[0]: [0, 2, 4, 6, 8, 10], NAMES[1]: [90, 92, 94, 96, 98, 100]})
        earliest = self.report(seeds=3)["earliest_named_faces"]
        self.assertEqual((earliest["per_person"], earliest["faces"]), (3, 6))

    def test_unnamed_faces_report_how_many_have_a_look_alike(self):
        build_catalogue(
            self.path, {NAMES[0]: [0, 3], NAMES[1]: [90, 93]},
            unnamed_angles=[45, 47, 49, 225],
        )
        unnamed = self.report()["unnamed"]
        self.assertEqual(unnamed["unnamed_faces"], 4)
        self.assertEqual(unnamed["by_cutoff"]["0.90"]["share_with_a_look_alike"], 0.75)
        self.assertEqual(unnamed["by_cutoff"]["0.30"]["share_with_a_look_alike"], 0.75)

    def test_nothing_that_identifies_a_person_or_photo_reaches_the_output(self):
        from face_threshold_report import render

        build_catalogue(
            self.path, {NAMES[0]: [0, 3, -3], NAMES[1]: [90, 93, 87]}, unnamed_angles=[45, 47],
        )
        report = self.report()
        for text in (render(report), json.dumps(report)):
            for name in NAMES:
                self.assertNotIn(name.split()[0], text)
            self.assertNotIn(FOLDER, text)
            self.assertNotIn("synthetic-", text)
            self.assertNotIn(self.temporary.name, text)

    def test_the_catalogue_is_never_modified(self):
        build_catalogue(self.path, {NAMES[0]: [0, 3, -3], NAMES[1]: [90, 93, 87]}, unnamed_angles=[45])
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.report()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        # SQLite may leave empty -wal/-shm side files for a WAL-mode catalogue; they hold no data.
        for side_file in self.path.parent.glob("*-wal"):
            self.assertEqual(side_file.stat().st_size, 0)
        self.assertFalse(list(self.path.parent.glob("*-journal")))

    def test_too_few_people_is_reported_not_crashed(self):
        build_catalogue(self.path, {NAMES[0]: [0, 3, -3]})
        named = self.report()["all_named_faces"]
        self.assertIn("note", named)

    def test_no_named_faces_is_reported(self):
        build_catalogue(self.path, {}, unnamed_angles=[0, 5])
        self.assertIn("note", self.report())

    def test_a_missing_catalogue_is_a_clear_error(self):
        with self.assertRaises(SystemExit) as raised:
            self.report_for(self.path.parent / "absent.sqlite3")
        self.assertIn("--db", str(raised.exception))

    def test_an_old_catalogue_asks_for_one_launch_of_the_app(self):
        old = sqlite3.connect(self.path)
        old.executescript(
            """CREATE TABLE assets(id INTEGER PRIMARY KEY, in_review_bin INTEGER);
               CREATE TABLE face_embeddings(id INTEGER PRIMARY KEY, embedding_f32 BLOB);
               CREATE TABLE asset_people(asset_id INTEGER, person_id INTEGER, state TEXT, face_id INTEGER, updated_at TEXT);"""
        )
        old.close()
        with self.assertRaises(SystemExit) as raised:
            self.report()
        self.assertIn("Open it once", str(raised.exception))

    def report_for(self, path: Path):
        from face_threshold_report import build_report
        return build_report(path, 8, 1)


class WithoutNumpyTests(unittest.TestCase):
    def test_it_says_numpy_is_needed(self):
        from unittest.mock import patch
        import face_threshold_report

        with patch.object(face_threshold_report, "np", None):
            with self.assertRaises(SystemExit) as raised:
                face_threshold_report.build_report(Path("unused.sqlite3"), 8, 1)
        self.assertIn("NumPy", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
