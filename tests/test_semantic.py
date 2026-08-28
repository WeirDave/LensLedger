from __future__ import annotations

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
            self.assertEqual(ocean[0][0], 1)
            self.assertEqual(receipt[0][0], 2)
            self.assertGreater(ocean[0][1], ocean[1][1])


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


if __name__ == "__main__":
    unittest.main()
