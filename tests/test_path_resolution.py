from __future__ import annotations

import ast
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parent.parent / "src"


class TestNoImportTimePathBinding(unittest.TestCase):
    """Data-folder paths must be resolved when used, not when a module loads.

    Holding one in a module constant freezes whatever LENSLEDGER_DATA_DIR said
    at import. Anything that changed it afterwards still read and wrote the
    original location -- which is how a test run overwrote real settings and
    how a test server can reach a real library.
    """

    # Paths derived from where the code lives, not from the data folder. These
    # are fixed for the life of an install and are fine as constants.
    CODE_RELATIVE = {"WEB_ROOT", "EXIFTOOL_PATH", "HERE"}

    def data_folder_resolvers(self) -> set[str]:
        import app_paths

        return {
            name for name in dir(app_paths)
            if not name.startswith("_")
            and callable(getattr(app_paths, name))
            and name != "Path"
        }

    def test_no_module_constant_is_built_from_the_data_folder(self):
        resolvers = self.data_folder_resolvers()
        offenders: list[str] = []

        for path in sorted(SOURCE_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                if node.value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [getattr(t, "id", "") for t in targets]
                if any(name in self.CODE_RELATIVE for name in names):
                    continue
                for call in ast.walk(node.value):
                    if isinstance(call, ast.Call) and getattr(call.func, "id", "") in resolvers:
                        offenders.append(
                            f"{path.name}:{node.lineno} {', '.join(names)} = "
                            f"{call.func.id}() at import time"
                        )

        self.assertEqual(
            offenders, [],
            "resolve these inside a function instead, so they follow the data "
            "folder rather than freezing it: " + "; ".join(offenders),
        )


class TestPathsFollowTheDataFolder(unittest.TestCase):
    """Set the data folder after import; everything must follow it."""

    def assert_follows(self, resolver, temporary: str):
        resolved = str(resolver())
        self.assertTrue(
            resolved.startswith(temporary),
            f"{resolver.__module__}.{resolver.__name__}() returned {resolved}, "
            f"which is outside the configured data folder {temporary}",
        )

    def test_every_data_path_follows_a_data_folder_set_after_import(self):
        import app_paths
        import ingest_pipeline
        import library_config
        import photo_search
        import settings_config

        with tempfile.TemporaryDirectory() as directory:
            with unittest.mock.patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": directory}):
                resolved = str(Path(directory).resolve())
                for resolver in (
                    app_paths.data_root,
                    app_paths.settings_path,
                    app_paths.libraries_root,
                    app_paths.backup_root,
                    app_paths.review_bin_root,
                    app_paths.face_data_root,
                    app_paths.database_backup_root,
                    app_paths.log_dir,
                    settings_config.settings_file,
                    library_config.library_state_file,
                    library_config.library_database_root,
                    photo_search.metadata_backup_root,
                    ingest_pipeline.ingest_log_path,
                ):
                    with self.subTest(resolver=resolver.__name__):
                        self.assert_follows(resolver, resolved)

    def test_saving_settings_writes_inside_the_configured_folder(self):
        import settings_config

        with tempfile.TemporaryDirectory() as directory:
            with unittest.mock.patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": directory}):
                values = settings_config.load_settings()
                values["publish"]["write_mode"] = "sidecar"
                settings_config.save_settings(values)

                written = Path(directory) / "settings.json"
                self.assertTrue(written.is_file(), "settings went somewhere else entirely")
                stored = json.loads(written.read_text(encoding="utf-8"))
                self.assertEqual(stored["publish"]["write_mode"], "sidecar")

    def test_library_mappings_are_written_inside_the_configured_folder(self):
        import library_config

        with tempfile.TemporaryDirectory() as directory:
            with unittest.mock.patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": directory}):
                root = Path(directory) / "a-library"
                root.mkdir()
                database = root / "library.sqlite3"
                library_config.associate_db_path(root, database)

                state = Path(directory) / "library-state.json"
                self.assertTrue(state.is_file(), "library state went somewhere else entirely")
                stored = json.loads(state.read_text(encoding="utf-8"))
                self.assertIn(str(root.resolve()).casefold(), stored["db_mappings"])


class TestWritingStateSurvivesALockedFile(unittest.TestCase):
    """A momentarily locked file must not become a hard failure."""

    def test_the_write_retries_before_giving_up(self):
        from app_paths import write_json_atomically

        attempts = {"count": 0}
        real_replace = Path.replace

        def flaky_replace(self, target):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise PermissionError("[WinError 5] Access is denied")
            return real_replace(self, target)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            with unittest.mock.patch.object(Path, "replace", flaky_replace):
                write_json_atomically(target, {"current_root": "somewhere"})

        self.assertEqual(attempts["count"], 3, "it should have retried, not failed at once")

    def test_it_gives_up_cleanly_rather_than_leaving_a_temp_file(self):
        from app_paths import write_json_atomically

        def always_locked(self, target):
            raise PermissionError("[WinError 5] Access is denied")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            with unittest.mock.patch.object(Path, "replace", always_locked):
                with self.assertRaises(OSError):
                    write_json_atomically(target, {"a": 1}, attempts=2)
            leftovers = list(Path(directory).glob("*.tmp"))
            self.assertEqual(leftovers, [], "a failed write must not leave a temp file behind")

    def test_a_scan_is_not_discarded_when_the_state_write_fails(self):
        """Run a real scan with the state write jammed; the scan must still stand."""
        import photo_search
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "photos"
            library.mkdir()
            for index in range(2):
                Image.new("RGB", (16, 16), (40 * index, 80, 120)).save(
                    library / f"2026-0{index + 1}-0{index + 1} scene.jpg")

            class Handler:
                library_lock = threading.Lock()
                library_job = {"state": "idle", "message": ""}
                library_cancel = threading.Event()
                current_library = (library, root / "library.sqlite3")

            logged: list[str] = []
            with unittest.mock.patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(root / "data")}),                  unittest.mock.patch.object(photo_search, "console_log", side_effect=logged.append),                  unittest.mock.patch.object(
                     photo_search, "save_library_state",
                     side_effect=PermissionError("[WinError 5] Access is denied")):
                photo_search._run_library_scan_job(
                    Handler, library, root / "library.sqlite3", "2026-01-01T00:00:00Z")

        self.assertEqual(
            Handler.library_job.get("state"), "complete",
            "the photos were indexed; a failed bookkeeping write must not fail the scan",
        )
        self.assertTrue(
            any("could not be saved" in line for line in logged),
            "the failure to save the library note should still be reported",
        )


if __name__ == "__main__":
    unittest.main()
