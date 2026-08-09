from __future__ import annotations

import array
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class FaceLocationTests(unittest.TestCase):
    def test_recovery_persists_only_an_unambiguous_exact_match(self):
        from face_locations import recover_face_locations
        from photo_index import connect, scan_library, utc_now

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "library.sqlite3"
            library = root / "photos"
            library.mkdir()
            (library / "group.jpg").write_bytes(b"image")
            self.assertEqual(scan_library(library, database), 0)
            con = connect(database)
            asset_id = int(con.execute("SELECT id FROM assets WHERE relative_path='group.jpg'").fetchone()[0])
            person_id = int(con.execute("INSERT INTO people(name) VALUES ('Test Person')").lastrowid)
            vector = array.array("f", [1.0, 0.0]).tobytes()
            face_id = int(con.execute(
                """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,
                       dimensions,embedding_f32) VALUES ('test',1,?,'group.jpg',2,?)""",
                (asset_id, vector),
            ).lastrowid)
            con.execute(
                """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
                   VALUES (?,?,'suggested',.95,?,'test',?)""",
                (asset_id, person_id, face_id, utc_now()),
            )
            con.commit()
            con.close()

            image = SimpleNamespace(shape=(100, 200, 3))
            cv2 = SimpleNamespace(IMREAD_COLOR=1, imdecode=lambda *_: image)
            np = SimpleNamespace(uint8="uint8", fromfile=lambda *_args, **_kwargs: b"image")
            faces = [
                SimpleNamespace(normed_embedding=[1.0, 0.0], bbox=[20, 10, 80, 60]),
                SimpleNamespace(normed_embedding=[0.0, 1.0], bbox=[100, 20, 150, 70]),
            ]
            analyzer = SimpleNamespace(get=lambda _image: faces)
            with patch("face_locations._load_runtime", return_value=(cv2, np, analyzer)):
                result = recover_face_locations(database, library)

            self.assertEqual(result["localized"], 1)
            con = sqlite3.connect(database)
            row = con.execute(
                "SELECT box_left,box_top,box_right,box_bottom,localization_similarity "
                "FROM face_embeddings WHERE id=?", (face_id,)
            ).fetchone()
            con.close()
            self.assertEqual(row[:4], (0.1, 0.1, 0.4, 0.6))
            self.assertGreater(row[4], 0.99)


if __name__ == "__main__":
    unittest.main()
