from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image


class ServerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.library = self.root / "photos"
        self.library.mkdir()
        self.photo = self.library / "2026-08-09 sample.jpg"
        Image.new("RGB", (32, 24), (24, 80, 140)).save(self.photo, quality=92)
        self.environment = patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(self.data)})
        self.environment.start()

        import photo_search
        from photo_index import scan_library

        self.photo_search = photo_search
        self.database = self.root / "library.sqlite3"
        self.assertEqual(scan_library(self.library, self.database), 0)
        photo_search.BACKUP_ROOT = self.data / "Metadata Backups"
        photo_search.SearchHandler.db_path = self.database
        photo_search.SearchHandler.library_root = self.library.resolve()
        photo_search.SearchHandler.csrf_token = "test-csrf"
        photo_search.SearchHandler.library_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.library_cancel.clear()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), photo_search.SearchHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        con = sqlite3.connect(self.database)
        self.asset_id = int(con.execute("SELECT id FROM assets").fetchone()[0])
        con.close()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.environment.stop()
        self.temporary.cleanup()

    def get(self, path: str):
        return urllib.request.urlopen(self.base_url + path, timeout=10)

    def post(self, path: str, body: dict, *, csrf: str = "test-csrf"):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps({**body, "csrf": csrf}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=30)

    def json_response(self, response):
        with response:
            return json.loads(response.read())

    def test_viewer_map_and_asset_endpoints(self):
        with self.get("/") as response:
            page = response.read().decode("utf-8")
        self.assertIn("Search scope", page)
        self.assertIn('href="/map"', page)

        detail = self.json_response(self.get(f"/api/asset?id={self.asset_id}"))
        self.assertEqual(detail["filename"], self.photo.name)
        with self.get(f"/media?id={self.asset_id}") as response:
            self.assertEqual(response.headers.get_content_type(), "image/jpeg")
            self.assertGreater(len(response.read()), 100)
        points = self.json_response(self.get("/api/map/points"))
        self.assertEqual(points["located"], 0)
        with self.get("/map") as response:
            self.assertIn("Photo map", response.read().decode("utf-8"))

    def test_csrf_metadata_publish_restore_and_review_bin(self):
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self.post("/api/subject", {"id": self.asset_id, "subject": "Blue test image"}, csrf="wrong")
        self.assertEqual(rejected.exception.code, 403)
        rejected.exception.close()

        self.json_response(self.post(
            "/api/subject", {"id": self.asset_id, "subject": "Blue test image"}
        ))
        preview = self.json_response(self.post(
            "/api/publish/preview", {"id": self.asset_id, "description": "Safe test description"}
        ))
        published = self.json_response(self.post(
            "/api/publish",
            {
                "id": self.asset_id,
                "description": "Safe test description",
                "expected_after": preview["after"],
            },
        ))
        self.assertTrue(published["ok"])
        self.assertTrue(self.photo.is_file())
        restored = self.json_response(self.post("/api/publish/restore", {"id": self.asset_id}))
        self.assertTrue(restored["ok"])

        moved = self.json_response(self.post("/api/review-bin", {"id": self.asset_id}))
        self.assertFalse(self.photo.exists())
        restored_bin = self.json_response(self.post(
            "/api/review-bin/restore", {"review_id": moved["review_id"]}
        ))
        self.assertTrue(restored_bin["ok"])
        self.assertTrue(self.photo.is_file())

    def test_media_path_outside_library_is_refused(self):
        outside = self.root / "outside.jpg"
        Image.new("RGB", (8, 8), "red").save(outside)
        con = sqlite3.connect(self.database)
        con.execute("UPDATE assets SET path=? WHERE id=?", (str(outside), self.asset_id))
        con.commit()
        con.close()
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self.get(f"/media?id={self.asset_id}")
        self.assertEqual(rejected.exception.code, 403)
        rejected.exception.close()


if __name__ == "__main__":
    unittest.main()
