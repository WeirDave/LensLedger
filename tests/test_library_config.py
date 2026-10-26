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
        self.patched_state_path = patch.object(library_config, "library_state_file", return_value=self.state_path)
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

        self.assertEqual(resolved, self.library_config.default_library().resolve())

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
        self.patched_state_path = patch.object(library_config, "library_state_file", return_value=self.state_path)
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


class LibraryStateRewriteTests(unittest.TestCase):
    """Saving the current library must not forget anything else the file records."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        import library_config

        self.library_config = library_config
        self.state_path = self.root / "library-state.json"
        self.patched_state_path = patch.object(library_config, "library_state_file", return_value=self.state_path)
        self.patched_state_path.start()
        self.here = self.root / "here"
        self.here.mkdir()
        self.other = self.root / "other"
        self.other.mkdir()
        self.unplugged = self.root / "unplugged drive" / "photos"

    def tearDown(self):
        self.patched_state_path.stop()
        self.temporary.cleanup()

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, **extra):
        self.state_path.write_text(json.dumps({
            "current_root": str(self.here),
            "libraries": [str(self.here), str(self.unplugged), str(self.other)],
            **extra,
        }), encoding="utf-8")

    def test_saving_the_current_library_keeps_where_each_database_lives(self):
        (self.root / "appdata.sqlite3").write_bytes(b"")
        self.library_config.associate_db_path(self.here, self.root / "appdata.sqlite3")

        self.library_config.save_library_state(self.here)

        self.assertEqual(
            self.library_config.library_db_path(self.here), (self.root / "appdata.sqlite3").resolve(),
            "a library whose database lives in the app data folder must still find it",
        )

    def test_saving_the_current_library_keeps_libraries_that_are_offline(self):
        self.write_state()

        self.library_config.save_library_state(self.other)

        state = self.state()
        self.assertEqual(state["current_root"], str(self.other.resolve()))
        self.assertEqual(state["libraries"][0], str(self.other.resolve()))
        self.assertIn(str(self.unplugged.resolve()), state["libraries"])
        self.assertIn(str(self.here.resolve()), state["libraries"])
        self.assertEqual(len(state["libraries"]), 3)

    def test_the_offline_library_still_offers_itself_for_reconnecting(self):
        self.write_state()
        self.library_config.save_library_state(self.here)

        known = {item["path"]: item["accessible"] for item in self.library_config.load_all_known_libraries()}

        self.assertIs(known[str(self.unplugged.resolve())], False)

    def test_forgetting_a_library_removes_only_that_one(self):
        self.write_state(db_mappings={"x": "y"})

        self.library_config.forget_library(self.other)

        state = self.state()
        self.assertEqual(state["libraries"], [str(self.here.resolve()), str(self.unplugged.resolve())])
        self.assertEqual(state["db_mappings"], {"x": "y"})
        self.assertEqual(state["current_root"], str(self.here))

    def test_moving_a_library_keeps_the_rest_of_the_state(self):
        self.write_state(db_mappings={"x": "y"})
        moved = self.root / "moved"
        moved.mkdir()

        self.library_config.move_library_record(self.here, moved)

        state = self.state()
        self.assertEqual(state["current_root"], str(moved.resolve()))
        self.assertEqual(state["libraries"], [str(moved.resolve()), str(self.unplugged.resolve()), str(self.other.resolve())])
        self.assertEqual(state["db_mappings"], {"x": "y"})

    def test_saving_with_no_state_file_creates_one(self):
        self.library_config.save_library_state(self.here)

        self.assertEqual(self.state()["libraries"], [str(self.here.resolve())])
