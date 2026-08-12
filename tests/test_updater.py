import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import lensledger_updater as updater
from photo_index import scan_library


def make_release_tree(root: Path, version: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in updater.REQUIRED_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    (root / "src" / "product.py").write_text(f'APP_VERSION = "{version}"\n', encoding="utf-8")
    return root


class UpdaterTests(unittest.TestCase):
    def test_latest_release_selects_versioned_zip_and_digest(self):
        payload = {
            "tag_name": "v0.20.0",
            "name": "LensLedger v0.20.0",
            "html_url": "https://example.test/releases/v0.20.0",
            "assets": [{
                "name": "LensLedger-v0.20.0.zip",
                "url": "https://api.example.test/assets/20",
                "size": 42,
                "digest": "sha256:" + "a" * 64,
            }],
        }

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response(json.dumps(payload).encode("utf-8"))

        with patch.object(updater, "_opener", return_value=Opener()):
            release = updater.latest_release(token="test")
        self.assertEqual(release.version, "0.20.0")
        self.assertEqual(release.asset_name, "LensLedger-v0.20.0.zip")
        self.assertEqual(release.digest, "sha256:" + "a" * 64)

    def test_version_check_reports_only_newer_semantic_versions(self):
        release = updater.ReleaseInfo(
            version="0.20.0", tag="v0.20.0", name="LensLedger v0.20.0",
            page_url="https://example.test/release", asset_api_url="https://example.test/asset",
            asset_name="LensLedger-v0.20.0.zip", asset_size=10, digest="sha256:" + "0" * 64,
        )
        with patch.object(updater, "latest_release", return_value=release):
            self.assertTrue(updater.check_for_update("0.19.0", token="test")["available"])
            self.assertFalse(updater.check_for_update("0.20.0", token="test")["available"])
        with self.assertRaises(updater.UpdateError):
            updater.version_tuple("0.20")

    def test_download_requires_matching_digest_and_size(self):
        content = b"verified release bytes"
        release = updater.ReleaseInfo(
            version="0.20.0", tag="v0.20.0", name="LensLedger v0.20.0",
            page_url="https://example.test/release", asset_api_url="https://example.test/asset",
            asset_name="LensLedger-v0.20.0.zip", asset_size=len(content),
            digest="sha256:" + hashlib.sha256(content).hexdigest(),
        )

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response(content)

        with tempfile.TemporaryDirectory() as directory, patch.object(updater, "_opener", return_value=Opener()):
            destination = Path(directory) / release.asset_name
            updater._download_release(release, destination, "token")
            self.assertEqual(destination.read_bytes(), content)
            bad = updater.ReleaseInfo(**{**release.__dict__, "digest": "sha256:" + "f" * 64})
            with self.assertRaises(updater.UpdateError):
                updater._download_release(bad, destination, "token")

    def test_zip_traversal_and_git_working_copies_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../outside.txt", "no")
            with self.assertRaises(updater.UpdateError):
                updater._extract_release(archive, root / "expanded")
            release = make_release_tree(root / "release", "0.20.0")
            (release / ".git").mkdir()
            with self.assertRaises(updater.UpdateError):
                updater.validate_release_tree(release)

    def test_managed_install_replaces_cleanly_and_keeps_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_release_tree(root / "release", "0.20.0")
            target = make_release_tree(root / "Programs" / "LensLedger", "0.19.0")
            (target / updater.MARKER_NAME).write_text(json.dumps({
                "managed_by": "LensLedger updater", "version": "0.19.0",
            }), encoding="utf-8")
            result = updater.install_tree(source, target)
            self.assertEqual(updater.read_tree_version(target), "0.20.0")
            self.assertTrue(updater.is_managed_install(target))
            rollback = Path(result["rollback_root"])
            self.assertTrue(rollback.is_dir())
            self.assertEqual(updater.read_tree_version(rollback), "0.19.0")

    def test_unmanaged_existing_target_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_release_tree(root / "release", "0.20.0")
            target = make_release_tree(root / "Programs" / "LensLedger", "0.19.0")
            sentinel = target / "user-file.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(updater.UpdateError):
                updater.install_tree(source, target)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertEqual(updater.read_tree_version(target), "0.19.0")

    def test_legacy_launcher_handoff_preserves_original_and_can_be_refreshed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            managed = make_release_tree(root / "Programs" / "LensLedger", "0.20.0")
            (managed / updater.MARKER_NAME).write_text(json.dumps({
                "managed_by": "LensLedger updater", "version": "0.20.0",
            }), encoding="utf-8")
            legacy = root / "legacy-install"
            legacy.mkdir()
            (legacy / "photo_search.py").write_text("legacy app\n", encoding="utf-8")
            original = "@echo off\npython photo_search.py\n"
            start = legacy / "Start LensLedger.cmd"
            start.write_text(original, encoding="utf-8")

            with patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(runtime)}):
                result = updater.handoff_legacy_launcher(legacy, managed)
                self.assertEqual(result["status"], "handed_off")
                self.assertEqual(
                    (legacy / "Start LensLedger.pre-managed.cmd").read_text(encoding="utf-8"),
                    original,
                )
                handoff = start.read_text(encoding="utf-8")
                self.assertIn(updater.LEGACY_LAUNCHER_MARKER, handoff)
                self.assertIn(str(managed.resolve() / "Start LensLedger.cmd"), handoff)
                self.assertNotIn("python photo_search.py", handoff)

                start.write_text(original, encoding="utf-8")
                refreshed = updater.refresh_legacy_launcher_handoffs(managed)
                self.assertEqual(refreshed[0]["status"], "handed_off")
                self.assertEqual(
                    (legacy / "Start LensLedger.pre-managed.cmd").read_text(encoding="utf-8"),
                    original,
                )
                self.assertIn(updater.LEGACY_LAUNCHER_MARKER, start.read_text(encoding="utf-8"))

    def test_legacy_catalog_is_backed_up_migrated_and_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            library = root / "photos"
            legacy = root / "legacy-install"
            library.mkdir()
            legacy.mkdir()
            Image.new("RGB", (8, 6), (10, 20, 30)).save(library / "one.jpg")
            Image.new("RGB", (8, 6), (40, 50, 60)).save(library / "two.jpg")
            database = legacy / "photo-index.sqlite3"
            self.assertEqual(scan_library(library, database), 0)
            face_data = legacy / "Face Data"
            face_data.mkdir()
            (face_data / "profile.bin").write_bytes(b"profile")
            review_bin = legacy / "Review Bin"
            review_bin.mkdir()
            shutil.copy2(library / "one.jpg", review_bin / "one.jpg")
            metadata_backups = legacy / "Metadata Backups"
            metadata_backups.mkdir()
            shutil.copy2(library / "two.jpg", metadata_backups / "two.jpg")
            with closing(sqlite3.connect(database)) as con:
                asset_id = con.execute(
                    "SELECT id FROM assets WHERE filename = ?", ("one.jpg",)
                ).fetchone()[0]
                con.execute(
                    "UPDATE assets SET path = ?, in_review_bin = 1 WHERE id = ?",
                    (str(review_bin / "one.jpg"), asset_id),
                )
                con.execute(
                    "INSERT INTO review_bin "
                    "(asset_id, original_path, original_relative_path, review_path, moved_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (asset_id, str(library / "one.jpg"), "one.jpg", str(review_bin / "one.jpg"), "2026-08-09T12:00:00Z"),
                )
                con.execute(
                    "INSERT INTO metadata_publications "
                    "(asset_id, relative_path, backup_path, before_json, after_json, published_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (asset_id, "one.jpg", str(metadata_backups / "two.jpg"), "{}", "{}", "2026-08-09T12:00:00Z"),
                )
                con.commit()
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            install_root = Path(__file__).resolve().parents[1]

            with patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(runtime)}):
                result = updater.migrate_legacy_data(legacy, install_root)

            migrated = Path(result["database"])
            self.assertTrue(migrated.is_file())
            con = sqlite3.connect(migrated)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 2)
            self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
            migrated_asset_path = Path(con.execute(
                "SELECT path FROM assets WHERE in_review_bin = 1"
            ).fetchone()[0])
            migrated_review_path = Path(con.execute(
                "SELECT review_path FROM review_bin WHERE asset_id = ?", (asset_id,)
            ).fetchone()[0])
            migrated_backup_path = Path(con.execute(
                "SELECT backup_path FROM metadata_publications WHERE asset_id = ?", (asset_id,)
            ).fetchone()[0])
            con.close()
            state = json.loads((runtime / "library-state.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(state["current_root"]), library.resolve())
            self.assertEqual((runtime / "Face Data" / "profile.bin").read_bytes(), b"profile")
            self.assertTrue(os.path.samefile(migrated_asset_path, runtime / "Review Bin" / "one.jpg"))
            self.assertTrue(os.path.samefile(migrated_review_path, runtime / "Review Bin" / "one.jpg"))
            self.assertTrue(os.path.samefile(migrated_backup_path, runtime / "Metadata Backups" / "two.jpg"))
            self.assertTrue(migrated_asset_path.is_file())
            self.assertTrue(migrated_backup_path.is_file())
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_restart_source_waits_for_the_old_process_then_relaunches_with_no_file_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []
            with patch.object(updater, "wait_for_process", lambda pid: calls.append(("wait", pid))), \
                 patch.object(
                     updater, "launch_lensledger",
                     lambda install_root, old_window_pid=None: calls.append(("launch", install_root, old_window_pid)),
                 ), \
                 patch(
                     "sys.argv",
                     ["lensledger_updater.py", "restart-source", "--current-root", str(root),
                      "--wait-pid", "4242", "--old-window-pid", "777"],
                 ):
                self.assertEqual(updater.main(), 0)
            self.assertEqual(calls, [("wait", 4242), ("launch", root.resolve(), 777)])

    def test_close_old_launcher_window_only_shells_out_for_a_positive_pid_on_windows(self):
        with patch.object(updater.os, "name", "nt"), patch.object(updater.subprocess, "run") as mocked_run:
            updater.close_old_launcher_window(0)
            updater.close_old_launcher_window(-5)
            mocked_run.assert_not_called()
        with patch.object(updater.os, "name", "posix"), patch.object(updater.subprocess, "run") as mocked_run:
            updater.close_old_launcher_window(4242)
            mocked_run.assert_not_called()

    def test_close_old_launcher_window_targets_the_exact_pid_and_launcher_script(self):
        with patch.object(updater.os, "name", "nt"), patch.object(updater.subprocess, "run") as mocked_run:
            updater.close_old_launcher_window(4242)
        mocked_run.assert_called_once()
        command = mocked_run.call_args.args[0]
        script = command[command.index("-Command") + 1]
        self.assertIn("4242", script)
        self.assertIn("Start LensLedger.cmd", script)
        self.assertIn("cmd.exe", script)

    def test_launch_lensledger_starts_the_new_copy_before_closing_the_old_window(self):
        order = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(updater.os, "name", "nt"), \
                 patch.object(updater.subprocess, "Popen", lambda *a, **k: order.append("launch")), \
                 patch.object(updater, "close_old_launcher_window", lambda pid: order.append(("close", pid))):
                updater.launch_lensledger(root, old_window_pid=777)
        self.assertEqual(order, ["launch", ("close", 777)])

    def test_launch_lensledger_skips_closing_when_no_old_window_pid_is_given(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(updater.os, "name", "nt"), \
                 patch.object(updater.subprocess, "Popen") as mocked_popen, \
                 patch.object(updater, "close_old_launcher_window") as mocked_close:
                updater.launch_lensledger(root)
        mocked_popen.assert_called_once()
        mocked_close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
