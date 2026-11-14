from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image


class FakeEncoder:
    identity = "ViT-B-32/openai"

    def encode_images(self, paths):
        vectors = {
            "beach.jpg": (1.0, 0.0, 0.0),
            "document.jpg": (0.0, 1.0, 0.0),
            "dog.jpg": (0.0, 0.0, 1.0),
        }
        return [vectors.get(path.name) for path in paths]

    def encode_text(self, text):
        return {
            "ocean vacation": (0.9, 0.1, 0.0),
            "paper receipt": (0.0, 1.0, 0.0),
        }[text]


class FakeEncoderWithErrors(FakeEncoder):
    def encode_images(self, paths):
        vectors = {
            "beach.jpg": (1.0, 0.0, 0.0),
            "document.jpg": (0.0, 1.0, 0.0),
            "dog.jpg": "cannot identify image file 'dog.jpg'",
        }
        return [vectors.get(path.name) for path in paths]


class SemanticIndexTests(unittest.TestCase):
    def test_optional_index_builds_incrementally_and_ranks_text_queries(self):
        from photo_index import scan_library
        from semantic_index import build_index, search, status

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "photos"
            library.mkdir()
            for name, color in (
                ("beach.jpg", "blue"), ("document.jpg", "white"), ("dog.jpg", "brown")
            ):
                Image.new("RGB", (12, 8), color).save(library / name)
            database = root / "library.sqlite3"
            self.assertEqual(scan_library(library, database), 0)

            progress = []
            built = build_index(
                database, FakeEncoder(), batch_size=2, progress=progress.append
            )
            self.assertEqual(built["indexed"], 3)
            self.assertEqual(built["errors"], 0)
            self.assertEqual(status(database)["remaining"], 0)
            self.assertEqual(build_index(database, FakeEncoder())["indexed"], 0)

            ocean = search(database, "ocean vacation", encoder=FakeEncoder())
            receipt = search(database, "paper receipt", encoder=FakeEncoder())
            con = sqlite3.connect(database)
            ids = dict(con.execute("SELECT filename, id FROM assets"))
            con.close()
            self.assertEqual(ocean[0][0], ids["beach.jpg"])
            self.assertEqual(receipt[0][0], ids["document.jpg"])
            self.assertGreater(ocean[0][1], ocean[1][1])


    def test_numpy_and_python_paths_return_the_same_ranking(self):
        import semantic_index
        from photo_index import scan_library
        from semantic_index import build_index, clear_matrix_cache, search

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "photos"
            library.mkdir()
            for name, color in (
                ("beach.jpg", "blue"), ("document.jpg", "white"), ("dog.jpg", "brown")
            ):
                Image.new("RGB", (12, 8), color).save(library / name)
            database = root / "library.sqlite3"
            self.assertEqual(scan_library(library, database), 0)
            build_index(database, FakeEncoder())

            with_numpy = search(database, "ocean vacation", encoder=FakeEncoder())
            original = semantic_index.np
            try:
                semantic_index.np = None
                clear_matrix_cache(database)
                without_numpy = search(database, "ocean vacation", encoder=FakeEncoder())
            finally:
                semantic_index.np = original
                clear_matrix_cache(database)

            self.assertEqual(
                [asset_id for asset_id, _ in with_numpy],
                [asset_id for asset_id, _ in without_numpy],
            )
            for (_, numpy_score), (_, python_score) in zip(with_numpy, without_numpy):
                self.assertAlmostEqual(numpy_score, python_score, places=5)

    def test_cached_matrix_reloads_after_the_table_changes(self):
        from photo_index import scan_library
        from semantic_index import build_index, search

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "photos"
            library.mkdir()
            for name, color in (
                ("beach.jpg", "blue"), ("document.jpg", "white"), ("dog.jpg", "brown")
            ):
                Image.new("RGB", (12, 8), color).save(library / name)
            database = root / "library.sqlite3"
            self.assertEqual(scan_library(library, database), 0)
            build_index(database, FakeEncoder())

            # Populate the in-memory cache, then change the table out from under it.
            self.assertEqual(len(search(database, "ocean vacation", encoder=FakeEncoder())), 3)
            con = sqlite3.connect(database)
            con.execute(
                "DELETE FROM semantic_embeddings WHERE asset_id IN "
                "(SELECT id FROM assets WHERE filename='dog.jpg')"
            )
            con.commit()
            con.close()
            self.assertEqual(len(search(database, "ocean vacation", encoder=FakeEncoder())), 2)

    def test_encode_errors_store_actual_reason(self):
        from photo_index import connect, scan_library
        from semantic_index import build_index

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "photos"
            library.mkdir()
            for name, color in (
                ("beach.jpg", "blue"), ("document.jpg", "white"), ("dog.jpg", "brown")
            ):
                Image.new("RGB", (12, 8), color).save(library / name)
            database = root / "library.sqlite3"
            self.assertEqual(scan_library(library, database), 0)

            built = build_index(database, FakeEncoderWithErrors())
            self.assertEqual(built["indexed"], 2)
            self.assertEqual(built["errors"], 1)
            with connect(database) as con:
                row = con.execute(
                    "SELECT semantic_error FROM assets WHERE relative_path LIKE '%dog.jpg'"
                ).fetchone()
            self.assertIn("cannot identify image file", row["semantic_error"])


class FaceAppearanceEncoder:
    """Whole photos are told apart by file name and face crops by brightness:
    a dark crop reads as 'beard', a light one as 'clean-shaven'."""

    identity = "ViT-B-32/openai"

    def __init__(self):
        self.crop_sizes = []

    def encode_images(self, paths):
        vectors = {"dark.jpg": (0.0, 0.0, 1.0), "light.jpg": (0.3, 0.0, 0.954), "group.jpg": (0.0, 0.0, 1.0)}
        return [vectors.get(path.name) for path in paths]

    def encode_crops(self, crops):
        result = []
        for crop in crops:
            self.crop_sizes.append(crop.size)
            brightness = sum(crop.resize((1, 1)).getpixel((0, 0))) / 3
            result.append((1.0, 0.0, 0.0) if brightness < 128 else (0.0, 1.0, 0.0))
        return result

    def encode_text(self, text):
        return {"beard": (1.0, 0.0, 0.0), "clean shaven": (0.0, 1.0, 0.0)}[text]


class FaceAppearanceTests(unittest.TestCase):
    def build_library(self, root):
        from photo_index import connect, scan_library

        library = root / "photos"
        library.mkdir()
        Image.new("RGB", (200, 200), (20, 20, 20)).save(library / "dark.jpg")
        Image.new("RGB", (200, 200), (230, 230, 230)).save(library / "light.jpg")
        Image.new("RGB", (200, 100), (20, 20, 20)).save(library / "group.jpg")
        database = root / "library.sqlite3"
        self.assertEqual(scan_library(library, database), 0)
        faces = {}
        with connect(database) as con:
            person = con.execute("INSERT INTO people(name) VALUES ('Sample Person')").lastrowid
            other = con.execute("INSERT INTO people(name) VALUES ('Other Person')").lastrowid
            ids = dict(con.execute("SELECT filename, id FROM assets"))
            for number, (name, who, box) in enumerate((
                ("dark.jpg", person, (0.35, 0.35, 0.65, 0.65)),
                ("light.jpg", person, (0.35, 0.35, 0.65, 0.65)),
                ("group.jpg", other, (0.1, 0.1, 0.3, 0.5)),
            ), start=1):
                face_id = con.execute(
                    """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,
                           dimensions,embedding_f32,box_left,box_top,box_right,box_bottom)
                       VALUES ('test',?,?,?,1,x'00000000',?,?,?,?)""",
                    (number, ids[name], name, *box),
                ).lastrowid
                con.execute(
                    """INSERT INTO asset_people(asset_id,person_id,state,face_id,source,updated_at)
                       VALUES (?,?,'confirmed',?,'manual','2024-01-01')""",
                    (ids[name], who, face_id),
                )
                faces[name] = face_id
        return database, person, other, ids, faces

    def test_face_crop_is_a_square_inside_the_photo(self):
        from semantic_index import face_crop

        image = Image.new("RGB", (400, 200), "white")
        crop = face_crop(image, 0.0, 0.0, 0.1, 0.2)
        self.assertEqual(crop.size[0], crop.size[1])
        self.assertLessEqual(crop.size[0], 200)
        self.assertIsNone(face_crop(image, 0.5, 0.5, 0.5, 0.5))

    def test_face_index_is_incremental_and_skips_ignored_faces(self):
        from photo_index import connect
        from semantic_index import build_face_index, face_crop_pending

        with tempfile.TemporaryDirectory() as temporary:
            database, _person, _other, _ids, faces = self.build_library(Path(temporary))
            with connect(database) as con:
                con.execute("UPDATE face_embeddings SET ignored_at='2024-01-01' WHERE id=?", (faces["group.jpg"],))
            self.assertEqual(face_crop_pending(database), 2)
            encoder = FaceAppearanceEncoder()
            built = build_face_index(database, encoder, batch_size=1)
            self.assertEqual((built["indexed"], built["errors"]), (2, 0))
            self.assertEqual(face_crop_pending(database), 0)
            self.assertEqual(build_face_index(database, encoder)["indexed"], 0)
            self.assertTrue(all(width == height for width, height in encoder.crop_sizes))

    def test_a_person_with_a_beard_ranks_above_a_better_whole_photo_match(self):
        from semantic_index import blend_person_faces, build_face_index, build_index, search

        with tempfile.TemporaryDirectory() as temporary:
            database, person, _other, ids, _faces = self.build_library(Path(temporary))
            encoder = FaceAppearanceEncoder()
            build_index(database, encoder)
            build_face_index(database, encoder)

            whole = [(asset_id, score) for asset_id, score in search(database, "beard", encoder=encoder)
                     if asset_id in (ids["dark.jpg"], ids["light.jpg"])]
            self.assertEqual(whole[0][0], ids["light.jpg"])
            blended = blend_person_faces(database, "beard", whole, [person], encoder=encoder)
            self.assertEqual(blended[0][0], ids["dark.jpg"])
            self.assertAlmostEqual(blended[0][1], 1.0, places=5)

    def test_other_peoples_faces_and_missing_crops_do_not_change_the_score(self):
        from semantic_index import blend_person_faces, build_face_index, build_index, search

        with tempfile.TemporaryDirectory() as temporary:
            database, person, other, ids, _faces = self.build_library(Path(temporary))
            encoder = FaceAppearanceEncoder()
            build_index(database, encoder)
            scored = search(database, "beard", encoder=encoder)
            self.assertEqual(blend_person_faces(database, "beard", scored, [person], encoder=encoder), scored)
            build_face_index(database, encoder)
            blended = dict(blend_person_faces(database, "beard", scored, [person], encoder=encoder))
            original = dict(scored)
            self.assertEqual(blended[ids["group.jpg"]], original[ids["group.jpg"]])
            self.assertEqual(blend_person_faces(database, "beard", scored, [], encoder=encoder), scored)
            self.assertGreater(
                dict(blend_person_faces(database, "beard", scored, [other], encoder=encoder))[ids["group.jpg"]],
                original[ids["group.jpg"]],
            )


if __name__ == "__main__":
    unittest.main()
