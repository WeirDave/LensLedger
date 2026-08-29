from __future__ import annotations

import array
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path


def unit_vector(angle_degrees: float) -> tuple[float, float]:
    radians = math.radians(angle_degrees)
    return (math.cos(radians), math.sin(radians))


class ThresholdTests(unittest.TestCase):
    """Exercise face_learning's decision-boundary constants directly, without
    a database, against a tight cluster, a clear outlier, and (via learn())
    an ambiguous pair and the suggestion-scoring path."""

    def test_a_tight_cluster_of_faces_forms_a_high_cohesion_profile(self):
        from face_learning import Face, build_profile

        asset_faces = [
            [Face(id=index, asset_id=index, vector=unit_vector(angle))]
            for index, angle in enumerate((0.0, 5.0, -5.0))
        ]
        profile = build_profile(person_id=1, name="Friend", asset_faces=asset_faces)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.asset_count, 3)
        self.assertGreater(profile.cohesion, 0.99)

    def test_a_clear_outlier_is_excluded_from_the_profile(self):
        from face_learning import Face, build_profile

        cluster_angles = (0.0, 5.0, -5.0)
        asset_faces = [
            [Face(id=index, asset_id=index, vector=unit_vector(angle))]
            for index, angle in enumerate(cluster_angles)
        ]
        outlier_id = len(cluster_angles)
        asset_faces.append([Face(id=outlier_id, asset_id=outlier_id, vector=unit_vector(90.0))])

        profile = build_profile(person_id=1, name="Friend", asset_faces=asset_faces)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.asset_count, 3)
        self.assertNotIn(outlier_id, profile.face_ids)

    def test_too_few_agreeing_faces_yields_no_profile(self):
        from face_learning import Face, build_profile

        # A single usable face can never clear MIN_PROFILE_FACES=2, regardless
        # of how well it matches itself.
        asset_faces = [[Face(id=1, asset_id=1, vector=unit_vector(0.0))]]
        self.assertIsNone(build_profile(person_id=1, name="Friend", asset_faces=asset_faces))


class FaceLearningTests(unittest.TestCase):
    def test_group_photo_labels_require_an_exact_face_before_training(self):
        from face_learning import learn
        from photo_index import connect, utc_now

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "library.sqlite3"
            con = connect(database)
            person_id = int(con.execute("INSERT INTO people(name) VALUES ('Friend')").lastrowid)
            exact_face_ids = []
            for index in range(3):
                relative = f"group-{index}.jpg"
                asset_id = int(con.execute(
                    """INSERT INTO assets(path,relative_path,folder,filename,extension,media_type,
                           size_bytes,mtime_ns,capture_date,indexed_at)
                       VALUES (?,?,?,?,'.jpg','image',10,1,'2026-08-09',?)""",
                    (str(Path(temporary) / relative), relative, "", relative, utc_now()),
                ).lastrowid)
                common = array.array("f", [1.0, 0.0]).tobytes()
                other = array.array("f", [0.0, 1.0 if index % 2 == 0 else -1.0]).tobytes()
                exact_face_ids.append(int(con.execute(
                    """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,
                           dimensions,embedding_f32) VALUES ('test',?,?,?,2,?)""",
                    (index * 2 + 1, asset_id, relative, common),
                ).lastrowid))
                con.execute(
                    """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,
                           dimensions,embedding_f32) VALUES ('test',?,?,?,2,?)""",
                    (index * 2 + 2, asset_id, relative, other),
                )
                con.execute(
                    """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
                       VALUES (?,?,'confirmed',1,'manual',?)""",
                    (asset_id, person_id, utc_now()),
                )
            con.commit()
            con.close()

            result = learn(database)
            self.assertFalse(any(profile["name"] == "Friend" for profile in result["profile_summary"]))
            con = sqlite3.connect(database)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM person_face_profiles WHERE person_id=?", (person_id,)
            ).fetchone()[0], 0)
            rows = con.execute(
                "SELECT asset_id FROM asset_people WHERE person_id=? ORDER BY asset_id", (person_id,)
            ).fetchall()
            for row, face_id in zip(rows, exact_face_ids):
                con.execute(
                    "UPDATE asset_people SET face_id=? WHERE asset_id=? AND person_id=?",
                    (face_id, row[0], person_id),
                )
            con.commit()
            con.close()

            result = learn(database)
            profile = next(profile for profile in result["profile_summary"] if profile["name"] == "Friend")
            self.assertEqual(profile["training_assets"], 3)


def _insert_confirmed_face(con, temporary: Path, person_id: int, relative: str,
                           angle: float, source_id: int) -> int:
    """Insert one asset with a single confirmed face embedding at the given angle."""
    from photo_index import utc_now

    asset_id = int(con.execute(
        """INSERT INTO assets(path,relative_path,folder,filename,extension,media_type,
               size_bytes,mtime_ns,capture_date,indexed_at)
           VALUES (?,?,?,?,'.jpg','image',10,1,'2026-08-09',?)""",
        (str(temporary / relative), relative, "", relative, utc_now()),
    ).lastrowid)
    vector = array.array("f", unit_vector(angle)).tobytes()
    con.execute(
        """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,
               dimensions,embedding_f32) VALUES ('test',?,?,?,2,?)""",
        (source_id, asset_id, relative, vector),
    )
    con.execute(
        """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
           VALUES (?,?,'confirmed',1,'manual',?)""",
        (asset_id, person_id, utc_now()),
    )
    return asset_id


class LearnOrchestrationTests(unittest.TestCase):
    """learn()'s own decision logic: collision quarantine and suggestion scoring,
    both of which live in the orchestration function rather than build_profile()."""

    def test_learn_quarantines_an_ambiguous_profile_pair(self):
        from face_learning import learn
        from photo_index import connect

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "library.sqlite3"
            con = connect(database)
            alice_id = int(con.execute("INSERT INTO people(name) VALUES ('Alice')").lastrowid)
            alicia_id = int(con.execute("INSERT INTO people(name) VALUES ('Alicia')").lastrowid)
            # Two names, each with its own tight cluster of confirmed faces, but the
            # two clusters sit only 15 degrees apart (cos(15deg) ~= 0.966) -- well
            # past PROFILE_COLLISION_THRESHOLD (0.82). This is the real-world shape
            # of a naming mistake: the same person labeled under two names.
            for index, angle in enumerate((0.0, 3.0, -3.0)):
                _insert_confirmed_face(con, root, alice_id, f"alice-{index}.jpg", angle, index * 2 + 1)
            for index, angle in enumerate((15.0, 18.0, 12.0)):
                _insert_confirmed_face(con, root, alicia_id, f"alicia-{index}.jpg", angle, index * 2 + 100)
            con.commit()
            con.close()

            result = learn(database)
            self.assertTrue(any(
                {pair["left"], pair["right"]} == {"Alice", "Alicia"}
                for pair in result["ambiguous_profile_pairs"]
            ))
            self.assertNotIn("Alice", result["suggestions_by_person"])
            self.assertNotIn("Alicia", result["suggestions_by_person"])

    def test_learn_suggests_moderate_matches_and_auto_confirms_near_certain_ones(self):
        from face_learning import learn
        from photo_index import connect, utc_now

        def add_face(con, root, name, angle, source_id):
            asset_id = int(con.execute(
                """INSERT INTO assets(path,relative_path,folder,filename,extension,media_type,
                       size_bytes,mtime_ns,capture_date,indexed_at)
                   VALUES (?,?,?,?,'.jpg','image',10,1,'2026-08-09',?)""",
                (str(root / name), name, "", name, utc_now()),
            ).lastrowid)
            con.execute(
                """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,
                       dimensions,embedding_f32) VALUES ('test',?,?,?,2,?)""",
                (source_id, asset_id, name, array.array("f", unit_vector(angle)).tobytes()),
            )
            return asset_id

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "library.sqlite3"
            con = connect(database)
            person_id = int(con.execute("INSERT INTO people(name) VALUES ('Friend')").lastrowid)
            for index, angle in enumerate((0.0, 4.0, -4.0)):
                _insert_confirmed_face(con, root, person_id, f"known-{index}.jpg", angle, index * 2 + 1)

            # Near-identical to the trained cluster (cos(2deg) ~= 0.999): well
            # past AUTO_CONFIRM_THRESHOLD (0.90) -- should confirm immediately
            # rather than sit in the review queue.
            auto_asset = add_face(con, root, "near-certain.jpg", 2.0, 900)
            # 30 degrees off (cos(30deg) ~= 0.866): clears SUGGESTION_THRESHOLD
            # (0.76) but not AUTO_CONFIRM_THRESHOLD -- should be proposed for
            # human review, not auto-confirmed.
            moderate_asset = add_face(con, root, "moderate.jpg", 30.0, 901)
            # 48 degrees off (cos(48deg) ~= 0.669): below the suggestion
            # threshold entirely -- should not appear anywhere.
            weak_asset = add_face(con, root, "weak.jpg", 48.0, 902)
            con.commit()
            con.close()

            result = learn(database)
            suggested_assets = {sample["asset_id"] for sample in result["proposal_samples"].get("Friend", [])}
            auto_confirmed_assets = {entry["asset_id"] for entry in result["auto_confirmed"]}

            self.assertIn(auto_asset, auto_confirmed_assets)
            self.assertNotIn(auto_asset, suggested_assets)
            self.assertIn(moderate_asset, suggested_assets)
            self.assertNotIn(moderate_asset, auto_confirmed_assets)
            self.assertNotIn(weak_asset, suggested_assets)
            self.assertNotIn(weak_asset, auto_confirmed_assets)

            # The auto-confirmed match should behave exactly like a manual
            # confirmation: a real 'confirmed' row, tagged as the person, and
            # findable through the same full-text index tags feed.
            con = sqlite3.connect(database)
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT state, source FROM asset_people WHERE asset_id=? AND person_id=?",
                (auto_asset, person_id),
            ).fetchone()
            self.assertEqual(row["state"], "confirmed")
            self.assertEqual(row["source"], "learned_face_auto")
            tag = con.execute(
                """SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                   WHERE at.asset_id=? AND at.source='person' AND t.name='Friend'""",
                (auto_asset,),
            ).fetchone()
            self.assertIsNotNone(tag)
            con.close()

            # Re-running learn() must not re-delete or duplicate the
            # auto-confirmed row -- it is durable state now, not a
            # transient suggestion.
            learn(database)
            con = sqlite3.connect(database)
            count = con.execute(
                "SELECT COUNT(*) FROM asset_people WHERE asset_id=? AND person_id=?",
                (auto_asset, person_id),
            ).fetchone()[0]
            con.close()
            self.assertEqual(count, 1)

    def test_learn_excludes_faces_marked_not_a_person_or_unknown(self):
        # Both People-review dispositions ("Not a person" -> ignored_at,
        # "Unknown person" -> unknown_at) must remove a face from every
        # future learn() pass -- for any person, not just the one it was
        # reviewed under -- even when it would otherwise score as a
        # near-certain match.
        from face_learning import learn
        from photo_index import connect, utc_now

        def add_face(con, root, name, angle, source_id):
            asset_id = int(con.execute(
                """INSERT INTO assets(path,relative_path,folder,filename,extension,media_type,
                       size_bytes,mtime_ns,capture_date,indexed_at)
                   VALUES (?,?,?,?,'.jpg','image',10,1,'2026-08-09',?)""",
                (str(root / name), name, "", name, utc_now()),
            ).lastrowid)
            face_id = int(con.execute(
                """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,
                       dimensions,embedding_f32) VALUES ('test',?,?,?,2,?)""",
                (source_id, asset_id, name, array.array("f", unit_vector(angle)).tobytes()),
            ).lastrowid)
            return asset_id, face_id

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "library.sqlite3"
            con = connect(database)
            person_id = int(con.execute("INSERT INTO people(name) VALUES ('Friend')").lastrowid)
            for index, angle in enumerate((0.0, 4.0, -4.0)):
                _insert_confirmed_face(con, root, person_id, f"known-{index}.jpg", angle, index * 2 + 1)

            not_a_person_asset, not_a_person_face = add_face(con, root, "not-a-person.jpg", 1.0, 900)
            unknown_asset, unknown_face = add_face(con, root, "unknown.jpg", 1.5, 901)
            con.execute("UPDATE face_embeddings SET ignored_at=? WHERE id=?", (utc_now(), not_a_person_face))
            con.execute("UPDATE face_embeddings SET unknown_at=? WHERE id=?", (utc_now(), unknown_face))
            con.commit()
            con.close()

            result = learn(database)
            suggested_assets = {sample["asset_id"] for sample in result["proposal_samples"].get("Friend", [])}
            auto_confirmed_assets = {entry["asset_id"] for entry in result["auto_confirmed"]}

            for excluded_asset in (not_a_person_asset, unknown_asset):
                self.assertNotIn(excluded_asset, suggested_assets)
                self.assertNotIn(excluded_asset, auto_confirmed_assets)


if __name__ == "__main__":
    unittest.main()
