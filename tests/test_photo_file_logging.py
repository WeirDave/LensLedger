from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image


class TestPhotoFileActionsAreRecorded(unittest.TestCase):
    """Move and delete real photos through the real API, and read the log.

    Emptying the Review Bin removes the file and its database row together, so
    the log is the only record left of what went. These tests drive the actual
    endpoints against real files on disk rather than inspecting the source.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.library = self.root / "photos"
        (self.library / "Holidays").mkdir(parents=True)

        self.photos = []
        for index in range(1, 4):
            path = self.library / "Holidays" / f"2026-0{index}-1{index} scene-{index}.jpg"
            Image.new("RGB", (24, 18), (30 * index, 90, 140)).save(path, quality=88)
            self.photos.append(path)

        self.environment = patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(self.data)})
        self.environment.start()

        import library_config
        import photo_search
        from photo_index import scan_library

        self.photo_search = photo_search
        self._patched_state = patch.object(
            library_config, "library_state_file",
            return_value=self.data / "library-state.json")
        self._patched_db_root = patch.object(
            library_config, "library_database_root", return_value=self.data / "Libraries")
        self._patched_state.start()
        self._patched_db_root.start()

        self.database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(self.library, self.database), 0)

        # Two routes reach the log and both have to be captured: the Action
        # helper calls console_log.log by name at call time, while the handlers
        # hold their own reference to the original function from import.
        import console_log

        self.logged: list[str] = []
        self._patched_logs = [
            patch.object(console_log, "log", side_effect=self.logged.append),
            patch.object(photo_search, "console_log", side_effect=self.logged.append),
        ]
        for item in self._patched_logs:
            item.start()

        photo_search.SearchHandler.current_library = (self.library.resolve(), self.database)
        photo_search.SearchHandler.csrf_token = "test-csrf"
        photo_search.SearchHandler.library_job = {"state": "idle", "message": ""}

        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), photo_search.SearchHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        con = sqlite3.connect(self.database)
        self.asset_ids = [int(row[0]) for row in
                          con.execute("SELECT id FROM assets ORDER BY id")]
        con.close()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for item in self._patched_logs:
            item.stop()
        import console_log
        console_log.shutdown()
        self._patched_db_root.stop()
        self._patched_state.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def post(self, path, body=None):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps({**(body or {}), "csrf": "test-csrf"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    @property
    def log(self) -> str:
        return "\n".join(self.logged)

    def test_moving_photos_to_the_bin_names_each_one(self):
        result = self.post("/api/review-bin/batch", {"ids": self.asset_ids[:2]})
        self.assertEqual(result["moved"], 2)

        for photo in self.photos[:2]:
            self.assertFalse(photo.exists(), "the file should have left the library")
            self.assertIn(photo.name, self.log,
                          f"{photo.name} was moved but never named in the log")
        self.assertIn("2 photos moved to the Review Bin", self.log)

    def test_emptying_the_bin_names_every_photo_it_deletes(self):
        self.post("/api/review-bin/batch", {"ids": self.asset_ids})
        self.logged.clear()

        result = self.post("/api/review-bin/empty")
        self.assertEqual(result["deleted"], 3)

        for photo in self.photos:
            self.assertIn(photo.name, self.log,
                          f"{photo.name} was deleted permanently with no record of it")
        self.assertIn("deleted permanently", self.log)
        self.assertIn("3 photos deleted permanently", self.log)

    def test_the_photos_really_are_gone_and_the_log_is_the_only_record(self):
        self.post("/api/review-bin/batch", {"ids": self.asset_ids})
        self.post("/api/review-bin/empty")

        for photo in self.photos:
            self.assertFalse(photo.exists())
        con = sqlite3.connect(self.database)
        remaining = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        bin_rows = con.execute("SELECT COUNT(*) FROM review_bin").fetchone()[0]
        con.close()
        self.assertEqual(remaining, 0)
        self.assertEqual(bin_rows, 0, "nothing in the database remembers these photos")
        for photo in self.photos:
            self.assertIn(photo.name, self.log)

    def test_restoring_a_photo_is_recorded(self):
        moved = self.post("/api/review-bin/batch", {"ids": self.asset_ids[:1]})
        self.logged.clear()

        self.post("/api/review-bin/restore", {"review_id": moved["review_ids"][0]})

        self.assertTrue(self.photos[0].exists(), "the photo should be back")
        self.assertIn("put back", self.log)
        self.assertIn(self.photos[0].name, self.log)

    def test_deleting_one_photo_is_recorded(self):
        moved = self.post("/api/review-bin/batch", {"ids": self.asset_ids[:1]})
        self.logged.clear()

        self.post("/api/review-bin/delete", {"review_id": moved["review_ids"][0]})

        self.assertFalse(self.photos[0].exists())
        self.assertIn("deleted permanently", self.log)
        self.assertIn(self.photos[0].name, self.log)

    def test_an_empty_bin_still_reports_an_outcome(self):
        result = self.post("/api/review-bin/empty")

        self.assertEqual(result["deleted"], 0)
        self.assertIn("already empty", self.log,
                      "even doing nothing must leave a record that it ran")


if __name__ == "__main__":
    unittest.main()
