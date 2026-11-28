from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from PIL import Image


class FolderNameTests(unittest.TestCase):
    def test_a_name_is_split_into_the_date_and_the_description(self):
        from imported_folders import split_folder_name

        for name, expected in (
            ("2026_03_14", ("2026_03_14", "")),
            ("2026-03-14", ("2026-03-14", "")),
            ("2026_03_14 - Beach day", ("2026_03_14", "Beach day")),
            ("2026_03_14  - Beach day", ("2026_03_14", "Beach day")),
            ("2026_03_14 Beach day", ("2026_03_14", "Beach day")),
            ("2026_03_14 - Kite show - North Field, CA", ("2026_03_14", "Kite show - North Field, CA")),
            ("Screenshots", ("Screenshots", "")),
        ):
            with self.subTest(name=name):
                self.assertEqual(split_folder_name(name), expected)

    def test_the_logged_template_name_decides_where_the_description_starts(self):
        from imported_folders import split_folder_name

        self.assertEqual(split_folder_name("Phone - Screens", "Phone - Screens"), ("Phone - Screens", ""))
        self.assertEqual(split_folder_name("03 - Beach day", "03"), ("03", "Beach day"))

    def test_a_description_is_tidied_and_checked(self):
        from imported_folders import clean_description

        self.assertEqual(clean_description("  - Beach   day. "), "Beach day")
        self.assertEqual(clean_description("   "), "")
        for bad in ("a/b", "a\\b", "what?", 'say "hi"', "x" * 101):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clean_description(bad)


class ImportedFolderCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.data = base / "data"
        self.source = base / "incoming"
        self.library = base / "photos"
        self.source.mkdir()
        self.library.mkdir()
        self.database = base / "library.sqlite3"
        environment = mock.patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(self.data)})
        environment.start()
        self.addCleanup(environment.stop)
        stability = mock.patch("ingest_pipeline.STABILITY_SECONDS", 0)
        stability.start()
        self.addCleanup(stability.stop)
        import console_log

        self.addCleanup(console_log.shutdown)
        from ingest_pipeline import IngestPipeline

        self.pipeline = IngestPipeline(source_folder=str(self.source), destination_folder=str(self.library))
        self.shade = 0

    def add_photos(self, *names):
        for name in names:
            self.shade += 40
            Image.new("RGB", (32, 24), (self.shade, 80, 140)).save(self.source / name)
        self.pipeline.run_once()

    def listed(self):
        from imported_folders import list_folders, read_log
        from ingest_pipeline import ingest_log_path

        return list_folders(read_log(ingest_log_path()))

    def scan(self):
        from photo_index import scan_library

        self.assertEqual(scan_library(self.library, self.database, quiet=True), 0)

    def query(self, sql, values=()):
        from photo_index import connect

        with connect(self.database) as con:
            return [tuple(row) for row in con.execute(sql, values)]

    def execute(self, sql, values=()):
        from photo_index import connect

        with connect(self.database) as con:
            con.execute(sql, values)
            con.commit()


class ListingTests(ImportedFolderCase):
    def test_imported_folders_are_listed_with_their_item_counts(self):
        self.add_photos("2026-03-14 one.jpg", "2026-03-14 two.jpg", "2026-03-15 three.jpg")

        listed = {item["display"]: item for item in self.listed()}

        self.assertEqual(set(listed), {"2026/2026_03_14", "2026/2026_03_15"})
        self.assertEqual(listed["2026/2026_03_14"]["items"], 2)
        self.assertEqual(listed["2026/2026_03_14"]["base"], "2026_03_14")
        self.assertEqual(listed["2026/2026_03_14"]["description"], "")

    def test_a_folder_renamed_outside_the_app_is_still_listed(self):
        self.add_photos("2026-03-14 one.jpg")
        year = self.library / "2026"
        (year / "2026_03_14").rename(year / "2026_03_14 - Beach day")

        (item,) = self.listed()

        self.assertEqual(item["folder"], year / "2026_03_14 - Beach day")
        self.assertEqual(item["description"], "Beach day")

    def test_a_removed_or_emptied_folder_is_not_listed(self):
        self.add_photos("2026-03-14 one.jpg", "2026-03-15 two.jpg")
        (self.library / "2026" / "2026_03_14" / "2026-03-14 one.jpg").unlink()
        removed = self.library / "2026" / "2026_03_15"
        (removed / "2026-03-15 two.jpg").unlink()
        removed.rmdir()

        self.assertEqual(self.listed(), [])

    def test_the_destination_folder_itself_is_never_listed(self):
        self.pipeline.update_config(
            source_folder=str(self.source), destination_folder=str(self.library),
            rules=[{"match": "flat", "destination": "."}],
        )
        self.add_photos("flat 2026-03-14.jpg")

        self.assertEqual(self.listed(), [])


class RenameTests(ImportedFolderCase):
    def setUp(self):
        super().setUp()
        self.add_photos("2026-03-14 one.jpg", "2026-03-14 two.jpg")
        self.scan()
        self.old = self.library / "2026" / "2026_03_14"
        self.old_rel = "2026/2026_03_14/2026-03-14 one.jpg"
        self.new_rel = "2026/2026_03_14 - Beach day/2026-03-14 one.jpg"

    def rename(self, folder, name):
        from imported_folders import rename_folder

        return rename_folder(folder, name, [(self.library, self.database)])

    def test_everything_recorded_about_the_photos_moves_with_the_folder(self):
        (asset_id,), = self.query("SELECT id FROM assets WHERE relative_path=?", (self.old_rel,))
        self.execute("INSERT INTO asset_ratings VALUES (?, 4, 'now')", (self.old_rel,))
        self.execute("INSERT INTO asset_annotations VALUES (?, 'Sandcastle', 'summer')", (self.old_rel,))
        self.execute("INSERT INTO asset_tag_exclusions VALUES (?, 'hidden')", (self.old_rel,))
        self.execute("INSERT INTO folder_tags VALUES ('2026/2026_03_14', 'Added by hand')")

        target = self.rename(self.old, "2026_03_14 - Beach day")

        self.assertTrue((target / "2026-03-14 one.jpg").is_file())
        self.assertFalse(self.old.exists())
        self.assertEqual(
            self.query("SELECT relative_path, folder, path FROM assets WHERE id=?", (asset_id,)),
            [(self.new_rel, "2026/2026_03_14 - Beach day", str(self.library / self.new_rel))],
        )
        for table in ("asset_ratings", "asset_annotations", "asset_tag_exclusions"):
            with self.subTest(table=table):
                self.assertEqual(self.query(f"SELECT relative_path FROM {table}"), [(self.new_rel,)])
        names = {row[0] for row in self.query(
            "SELECT tag FROM folder_tags WHERE folder='2026/2026_03_14 - Beach day'")}
        self.assertIn("Added by hand", names)
        self.assertIn("Beach", names)
        self.assertNotIn("2026_03_14", names)
        self.assertEqual(self.query("SELECT 1 FROM folder_tags WHERE folder='2026/2026_03_14'"), [])
        tags = {row[0] for row in self.query(
            "SELECT t.name FROM asset_tags a JOIN tags t ON t.id=a.tag_id WHERE a.asset_id=?", (asset_id,))}
        self.assertTrue({"Added by hand", "Beach"} <= tags)
        self.assertNotIn("2026_03_14", tags)
        self.assertEqual(
            self.query("SELECT folder FROM search_fts WHERE asset_id=?", (asset_id,)),
            [("2026/2026_03_14 - Beach day",)],
        )

    def test_the_next_scan_finds_nothing_added_or_removed(self):
        before = self.query("SELECT id FROM assets ORDER BY id")

        self.rename(self.old, "2026_03_14 - Beach day")
        self.scan()

        self.assertEqual(self.query("SELECT id FROM assets ORDER BY id"), before)
        self.assertEqual(self.query("SELECT changed, removed FROM runs ORDER BY id DESC LIMIT 1"), [(0, 0)])

    def test_only_the_case_of_a_name_can_change(self):
        first = self.rename(self.old, "2026_03_14 - beach day")

        second = self.rename(first, "2026_03_14 - Beach Day")

        self.assertEqual([item.name for item in (self.library / "2026").iterdir()], [second.name])
        self.assertEqual(
            self.query("SELECT DISTINCT folder FROM assets"), [("2026/2026_03_14 - Beach Day",)])

    def test_later_imports_for_the_date_join_the_renamed_folder(self):
        target = self.rename(self.old, "2026_03_14 - Beach day")

        self.add_photos("2026-03-14 later.jpg")

        self.assertTrue((target / "2026-03-14 later.jpg").is_file())
        self.assertFalse(self.old.exists())

    def test_an_existing_folder_is_not_overwritten(self):
        (self.library / "2026" / "2026_03_14 - Beach day").mkdir()

        with self.assertRaises(ValueError):
            self.rename(self.old, "2026_03_14 - Beach day")

        self.assertTrue(self.old.is_dir())

    def test_leftover_records_under_the_new_name_stop_the_rename(self):
        self.execute(
            "INSERT INTO assets(path, relative_path, folder, filename, extension, media_type, "
            "size_bytes, mtime_ns, indexed_at) VALUES ('gone', ?, 'x', 'x', '.jpg', 'image', 1, 1, 'now')",
            (self.new_rel,),
        )

        with self.assertRaises(ValueError):
            self.rename(self.old, "2026_03_14 - Beach day")

        self.assertTrue(self.old.is_dir())

    def test_a_folder_windows_will_not_rename_leaves_the_records_alone(self):
        before = self.query("SELECT relative_path, path FROM assets ORDER BY id")

        with mock.patch("imported_folders.os.rename", side_effect=PermissionError(13, "in use")):
            with self.assertRaises(ValueError):
                self.rename(self.old, "2026_03_14 - Beach day")

        self.assertEqual(self.query("SELECT relative_path, path FROM assets ORDER BY id"), before)


class RenameRequestTests(ImportedFolderCase):
    def setUp(self):
        super().setUp()
        import library_config
        import photo_search

        for name, value in (("library_state_file", self.data / "library-state.json"),
                            ("library_database_root", self.data / "Libraries")):
            patcher = mock.patch.object(library_config, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.add_photos("2026-03-14 one.jpg", "2026-03-14 two.jpg")
        self.scan()
        handler = photo_search.SearchHandler
        for name, value in (
            ("current_library", (self.library, self.database)), ("csrf_token", "test-csrf"),
            ("ingest_pipeline", self.pipeline),
            *((job, {"state": "idle", "message": ""}) for job in (
                "library_job", "ocr_job", "semantic_job", "face_scan_job", "write_tags_job",
                "classify_job", "scan_all_job")),
        ):
            patcher = mock.patch.object(handler, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.handler = handler
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base_url = f"http://127.0.0.1:{server.server_port}"

    def get_folders(self):
        with urllib.request.urlopen(self.base_url + "/api/ingest/folders", timeout=10) as response:
            return json.loads(response.read())

    def post(self, route, body):
        request = urllib.request.Request(
            self.base_url + route, data=json.dumps({**body, "csrf": "test-csrf"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def rename(self, path, description):
        return self.post("/api/ingest/rename-folder", {"path": path, "description": description})

    def test_the_page_lists_folders_with_previews(self):
        (folder,) = self.get_folders()["folders"]

        self.assertEqual(folder["display"], "2026/2026_03_14")
        self.assertEqual(folder["library_folder"], "2026/2026_03_14")
        self.assertEqual(len(folder["thumbnails"]), 2)
        with urllib.request.urlopen(self.base_url + "/auto-import", timeout=10) as response:
            self.assertIn('id="folderList"', response.read().decode("utf-8"))

    def test_a_folder_is_named_renamed_and_cleared(self):
        (folder,) = self.get_folders()["folders"]

        for description, expected in (
            ("Beach day", "2026_03_14 - Beach day"),
            ("Kite show", "2026_03_14 - Kite show"),
            ("", "2026_03_14"),
        ):
            with self.subTest(description=description):
                status, reply = self.rename(folder["path"], description)
                self.assertEqual((status, reply.get("name")), (200, expected))
                (folder,) = self.get_folders()["folders"]
                self.assertEqual(folder["display"], f"2026/{expected}")
                self.assertEqual(len(folder["thumbnails"]), 2)

    def test_a_trashed_photo_is_restored_into_the_renamed_folder(self):
        (folder,) = self.get_folders()["folders"]
        _status, binned = self.post("/api/review-bin", {"id": folder["thumbnails"][0]})

        self.rename(folder["path"], "Beach day")
        status, _reply = self.post("/api/review-bin/restore", {"review_id": binned["review_id"]})

        self.assertEqual(status, 200)
        self.assertEqual(len(list((self.library / "2026" / "2026_03_14 - Beach day").glob("*.jpg"))), 2)
        self.assertFalse((self.library / "2026" / "2026_03_14").exists())

    def test_renaming_is_refused_while_a_scan_is_running(self):
        (folder,) = self.get_folders()["folders"]
        self.handler.ocr_job = {"state": "running"}

        self.assertEqual(self.get_folders()["busy"], "text recognition")
        status, reply = self.rename(folder["path"], "Beach day")

        self.assertEqual(status, 400)
        self.assertIn("text recognition", reply["error"])
        self.assertTrue((self.library / "2026" / "2026_03_14").is_dir())

    def test_only_a_listed_folder_can_be_renamed(self):
        other = self.library / "Keep this"
        other.mkdir()

        status, _reply = self.rename(str(other), "Changed")

        self.assertEqual(status, 400)
        self.assertTrue(other.is_dir())

    def test_a_folder_that_is_a_library_keeps_its_name(self):
        (folder,) = self.get_folders()["folders"]
        self.handler.current_library = (Path(folder["path"]), self.database)

        status, reply = self.rename(folder["path"], "Beach day")

        self.assertEqual(status, 400)
        self.assertIn("library", reply["error"])


if __name__ == "__main__":
    unittest.main()
