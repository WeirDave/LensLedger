from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LoadLibraryStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        import library_config

        self.library_config = library_config
        self.state_path = self.root / "library-state.json"
        self.patched_state_path = patch.object(library_config, "LIBRARY_STATE_PATH", self.state_path)
        self.patched_state_path.start()

    def tearDown(self):
        self.patched_state_path.stop()
        self.temporary.cleanup()

    def write_state(self, current_root, libraries):
        self.state_path.write_text(
            json.dumps({"current_root": current_root, "libraries": libraries}), encoding="utf-8"
        )

    def test_falls_back_to_a_known_library_when_current_root_no_longer_exists(self):
        real_library = self.root / "real-photos"
        real_library.mkdir()
        self.write_state(str(self.root / "deleted-temp-folder"), [str(self.root / "deleted-temp-folder"), str(real_library)])

        resolved = self.library_config.load_library_state()

        self.assertEqual(resolved, real_library.resolve())

    def test_falls_back_to_default_when_nothing_in_state_still_exists(self):
        self.write_state(str(self.root / "gone-a"), [str(self.root / "gone-a"), str(self.root / "gone-b")])

        resolved = self.library_config.load_library_state()

        self.assertEqual(resolved, self.library_config.DEFAULT_LIBRARY_ROOT.resolve())

    def test_uses_current_root_directly_when_it_still_exists(self):
        real_library = self.root / "real-photos"
        real_library.mkdir()
        self.write_state(str(real_library), [str(real_library)])

        resolved = self.library_config.load_library_state()

        self.assertEqual(resolved, real_library.resolve())


class LoadAllKnownLibrariesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        import library_config

        self.library_config = library_config
        self.state_path = self.root / "library-state.json"
        self.patched_state_path = patch.object(library_config, "LIBRARY_STATE_PATH", self.state_path)
        self.patched_state_path.start()

    def tearDown(self):
        self.patched_state_path.stop()
        self.temporary.cleanup()

    def write_state(self, current_root, libraries):
        self.state_path.write_text(
            json.dumps({"current_root": current_root, "libraries": libraries}), encoding="utf-8"
        )

    def test_returns_empty_list_when_no_state_file_exists(self):
        result = self.library_config.load_all_known_libraries()

        self.assertEqual(result, [])

    def test_includes_inaccessible_libraries(self):
        gone = str(self.root / "gone-folder")
        self.write_state(gone, [gone])

        result = self.library_config.load_all_known_libraries()

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["accessible"])
        self.assertTrue(result[0]["is_current"])

    def test_marks_accessible_libraries(self):
        real = self.root / "real-photos"
        real.mkdir()
        self.write_state(str(real), [str(real)])

        result = self.library_config.load_all_known_libraries()

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["accessible"])

    def test_includes_both_accessible_and_inaccessible(self):
        real = self.root / "real-photos"
        real.mkdir()
        gone = str(self.root / "gone-folder")
        self.write_state(gone, [gone, str(real)])

        result = self.library_config.load_all_known_libraries()

        self.assertEqual(len(result), 2)
        self.assertFalse(result[0]["accessible"])
        self.assertTrue(result[0]["is_current"])
        self.assertTrue(result[1]["accessible"])
        self.assertFalse(result[1]["is_current"])

    def test_deduplicates_libraries(self):
        real = self.root / "real-photos"
        real.mkdir()
        self.write_state(str(real), [str(real), str(real)])

        result = self.library_config.load_all_known_libraries()

        self.assertEqual(len(result), 1)

    def test_labels_use_folder_name(self):
        real = self.root / "MyPhotos"
        real.mkdir()
        self.write_state(str(real), [str(real)])

        result = self.library_config.load_all_known_libraries()

        self.assertEqual(result[0]["label"], "MyPhotos")


if __name__ == "__main__":
    unittest.main()
