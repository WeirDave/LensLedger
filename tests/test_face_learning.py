from __future__ import annotations

import array
import sqlite3
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
