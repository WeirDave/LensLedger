from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _fake_runtime(faces_per_image):
    image = SimpleNamespace(shape=(100, 200, 3))
    cv2 = SimpleNamespace(IMREAD_COLOR=1, imdecode=lambda *_: image)
    np = SimpleNamespace(uint8="uint8", fromfile=lambda *_args, **_kwargs: b"image")
    analyzer = SimpleNamespace(get=lambda _image: faces_per_image)
    return cv2, np, analyzer


class FaceScanTests(unittest.TestCase):
    def test_scan_writes_embeddings_and_boxes_for_detected_faces(self):
        from face_scan import scan_for_faces, status
        from photo_index import connect, scan_library

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "library.sqlite3"
            library = root / "photos"
            library.mkdir()
            (library / "a.jpg").write_bytes(b"image")
            (library / "b.jpg").write_bytes(b"image")
            self.assertEqual(scan_library(library, database), 0)

            before = status(database)
            self.assertEqual(before["eligible"], 2)
            self.assertEqual(before["scanned"], 0)
            self.assertEqual(before["remaining"], 2)

            faces = [SimpleNamespace(normed_embedding=[1.0, 0.0], bbox=[20, 10, 80, 60])]
            cv2, np, analyzer = _fake_runtime(faces)
            with patch("face_scan.load_insightface_runtime", return_value=(cv2, np, analyzer)):
                result = scan_for_faces(database, library)

            self.assertEqual(result, {"total": 2, "processed": 2, "faces_found": 2, "errors": 0, "cancelled": False})
            con = connect(database)
            rows = con.execute(
                "SELECT source,dimensions,box_left,box_top,box_right,box_bottom FROM face_embeddings "
                "WHERE source='lensledger_scan' ORDER BY id"
            ).fetchall()
            con.close()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["dimensions"], 2)
            self.assertEqual((rows[0]["box_left"], rows[0]["box_top"], rows[0]["box_right"], rows[0]["box_bottom"]), (0.1, 0.1, 0.4, 0.6))

            after = status(database)
            self.assertEqual(after["scanned"], 2)
            self.assertEqual(after["remaining"], 0)
            self.assertEqual(after["faces_found"], 2)

    def test_scan_skips_assets_already_face_scanned(self):
        from face_scan import scan_for_faces
        from photo_index import connect, scan_library, utc_now

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "library.sqlite3"
            library = root / "photos"
            library.mkdir()
            (library / "already-done.jpg").write_bytes(b"image")
            (library / "new.jpg").write_bytes(b"image")
            self.assertEqual(scan_library(library, database), 0)
            con = connect(database)
            done_id = int(con.execute(
                "SELECT id FROM assets WHERE relative_path='already-done.jpg'"
            ).fetchone()[0])
            con.execute(
                """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,dimensions,embedding_f32)
                   VALUES ('recovered_face',1,?,'already-done.jpg',2,?)""",
                (done_id, b"\x00\x00\x80\x3f\x00\x00\x00\x00"),
            )
            con.execute("UPDATE assets SET face_scanned=1 WHERE id=?", (done_id,))
            con.commit()
            con.close()

            cv2, np, analyzer = _fake_runtime([])
            with patch("face_scan.load_insightface_runtime", return_value=(cv2, np, analyzer)):
                result = scan_for_faces(database, library)
            self.assertEqual(result["total"], 1)  # only new.jpg was eligible

    def test_a_photo_with_no_faces_is_not_rescanned_forever(self):
        from face_scan import scan_for_faces, status
        from photo_index import scan_library

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "library.sqlite3"
            library = root / "photos"
            library.mkdir()
            (library / "landscape.jpg").write_bytes(b"image")
            self.assertEqual(scan_library(library, database), 0)

            cv2, np, analyzer = _fake_runtime([])  # no faces in this photo
            with patch("face_scan.load_insightface_runtime", return_value=(cv2, np, analyzer)):
                first = scan_for_faces(database, library)
                self.assertEqual(first, {"total": 1, "processed": 1, "faces_found": 0, "errors": 0, "cancelled": False})
                # A second pass must not pick the same photo back up just
                # because it never got a face_embeddings row.
                second = scan_for_faces(database, library)
                self.assertEqual(second["total"], 0)

            after = status(database)
            self.assertEqual(after["scanned"], 1)
            self.assertEqual(after["remaining"], 0)

    def test_scan_can_be_cancelled_mid_pass(self):
        from face_scan import scan_for_faces
        from photo_index import scan_library

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "library.sqlite3"
            library = root / "photos"
            library.mkdir()
            for name in ("one.jpg", "two.jpg", "three.jpg"):
                (library / name).write_bytes(name.encode("ascii"))
            self.assertEqual(scan_library(library, database), 0)

            cv2, np, analyzer = _fake_runtime([])
            with patch("face_scan.load_insightface_runtime", return_value=(cv2, np, analyzer)):
                result = scan_for_faces(database, library, should_cancel=lambda: True)
            self.assertTrue(result["cancelled"])
            self.assertEqual(result["processed"], 0)


if __name__ == "__main__":
    unittest.main()
