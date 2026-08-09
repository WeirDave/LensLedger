import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

import library_config
from metadata_reader import pixel_hash, read_embedded_metadata


class ServiceModuleTests(unittest.TestCase):
    def test_pixel_hash_ignores_metadata_and_reader_handles_unsupported_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plain = root / "plain.png"
            tagged = root / "tagged.png"
            image = Image.new("RGB", (8, 6), (12, 34, 56))
            image.save(plain)
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Comment", "private catalog note")
            image.save(tagged, pnginfo=metadata)

            self.assertEqual(pixel_hash(plain), pixel_hash(tagged))
            self.assertEqual(
                read_embedded_metadata(root / "clip.mp4"),
                {"descriptive": [], "capture": [], "description": ""},
            )

    def test_library_config_is_atomic_and_databases_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "settings" / "libraries.json"
            databases = root / "databases"
            default = root / "default-library"
            other = root / "Vacation 2026"
            default.mkdir()
            other.mkdir()
            databases.mkdir()

            with patch.object(library_config, "LIBRARY_STATE_PATH", state), \
                 patch.object(library_config, "LIBRARY_DATABASE_ROOT", databases), \
                 patch.object(library_config, "DEFAULT_LIBRARY_ROOT", default):
                self.assertEqual(library_config.library_db_path(default), databases / "default.sqlite3")
                other_database = library_config.library_db_path(other)
                self.assertEqual(other_database.parent, databases)
                self.assertNotEqual(other_database, databases / "default.sqlite3")
                library_config.save_library_state(other)
                self.assertEqual(library_config.load_library_state(), other.resolve())
                self.assertEqual(library_config.load_library_config()["libraries"], [str(other.resolve())])


if __name__ == "__main__":
    unittest.main()
