from __future__ import annotations

import ast
import json
import os
import tempfile
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


if __name__ == "__main__":
    unittest.main()
