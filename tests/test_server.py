from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
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
        photo_search.SearchHandler.ocr_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.ocr_cancel.clear()
        photo_search.SearchHandler.semantic_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.semantic_cancel.clear()
        photo_search.SearchHandler.people_merge_lock = threading.Lock()
        photo_search.SearchHandler.update_job = {"state": "idle", "message": ""}
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
        with self.get("/?scope=people") as response:
            people_page = response.read().decode("utf-8")
        self.assertIn("Merge people", people_page)
        self.assertIn("separate each name with a comma", people_page)

    def test_update_status_runs_in_background_and_reports_current_release(self):
        release = {
            "version": self.photo_search.APP_VERSION,
            "tag": "v" + self.photo_search.APP_VERSION,
            "name": "Current release",
            "page_url": "https://example.test/release",
            "asset_api_url": "https://example.test/asset",
            "asset_name": "LensLedger-current.zip",
            "asset_size": 10,
            "digest": "sha256:" + "0" * 64,
        }
        with patch.object(
            self.photo_search, "check_for_update",
            return_value={"current_version": self.photo_search.APP_VERSION, "available": False, "release": release},
        ):
            for _ in range(100):
                status = self.json_response(self.get("/api/update/status"))
                if status["state"] != "checking":
                    break
                time.sleep(0.01)
        self.assertEqual(status["state"], "current")
        self.assertFalse(status["available"])
        self.assertIn("managed_install_root", status)

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

    def test_diagnostics_verified_backup_and_background_ocr(self):
        diagnostics = self.json_response(self.get("/api/diagnostics"))
        self.assertEqual(diagnostics["integrity"], "ok")
        self.assertEqual(diagnostics["schema_version"], diagnostics["current_schema"])
        self.assertEqual(diagnostics["counts"]["ocr_pending"], 1)

        with patch(
            "photo_index.run_windows_ocr",
            return_value=(str(self.photo), "sample recognized text", None),
        ):
            started = self.json_response(self.post("/api/ocr/start", {"workers": 1}))
            self.assertEqual(started["state"], "running")
            for _ in range(100):
                status = self.json_response(self.get("/api/ocr/status"))
                if status["state"] != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(status["state"], "complete")
            self.assertEqual(status["attempted"], 1)

        backup = self.json_response(self.post("/api/database/backup", {}))
        backup_path = Path(backup["path"])
        self.assertTrue(backup_path.is_file())
        # Hosted Windows runners may report the temp root once via its 8.3 alias
        # and once via its expanded user name; compare canonical paths.
        self.assertTrue(
            backup_path.resolve().is_relative_to((self.data / "Database Backups").resolve())
        )
        con = sqlite3.connect(backup_path)
        self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
        con.close()

    def test_people_queue_can_defer_a_person_across_sessions(self):
        from photo_index import utc_now

        con = sqlite3.connect(self.database)
        person_id = int(con.execute("INSERT INTO people(name) VALUES ('Test Person')").lastrowid)
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
               VALUES (?,?,'suggested',0.91,'test',?)""",
            (self.asset_id, person_id, utc_now()),
        )
        con.commit()
        con.close()

        queue = self.json_response(self.get("/api/people/review/queue"))
        self.assertEqual(queue["person"]["id"], person_id)
        deferred = self.json_response(self.post(
            "/api/people/review/defer", {"person_id": person_id, "days": 7}
        ))
        self.assertEqual(deferred["person"], "Test Person")
        queue = self.json_response(self.get("/api/people/review/queue"))
        self.assertIsNone(queue["person"])
        self.assertEqual(queue["deferred_people"], 1)

    def test_people_queue_returns_the_exact_face_bounds(self):
        from photo_index import utc_now

        con = sqlite3.connect(self.database)
        face_id = int(con.execute(
            """INSERT INTO face_embeddings(
                   source,source_face_id,asset_id,relative_path,gender_marker,dimensions,embedding_f32,
                   box_left,box_top,box_right,box_bottom,localization_similarity,localized_at
               ) VALUES ('test',1,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.asset_id, self.photo.name, "F", 2, b"12345678", 0.1, 0.2, 0.4, 0.6, 0.99, utc_now()),
        ).lastrowid)
        person_id = int(con.execute("INSERT INTO people(name) VALUES ('Bounded Person')").lastrowid)
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
               VALUES (?,?,'suggested',0.93,?,'test',?)""",
            (self.asset_id, person_id, face_id, utc_now()),
        )
        con.commit()
        con.close()

        queue = self.json_response(self.get("/api/people/review/queue"))
        suggestion = queue["suggestions"][0]
        self.assertEqual(suggestion["face_id"], face_id)
        self.assertEqual(
            [suggestion[key] for key in ("box_left", "box_top", "box_right", "box_bottom")],
            [0.1, 0.2, 0.4, 0.6],
        )
        with self.get("/people-review") as response:
            page = response.read().decode("utf-8")
        self.assertIn("Face being checked", page)
        self.assertIn("ResizeObserver", page)

    def test_person_view_exposes_pending_matches_and_the_focused_face(self):
        from photo_index import scan_library, utc_now

        second_photo = self.library / "2026-08-10 second.jpg"
        Image.new("RGB", (32, 24), (60, 120, 180)).save(second_photo, quality=92)
        self.assertEqual(scan_library(self.library, self.database), 0)
        con = sqlite3.connect(self.database)
        second_asset_id = int(con.execute(
            "SELECT id FROM assets WHERE relative_path=?", (second_photo.name,)
        ).fetchone()[0])
        face_id = int(con.execute(
            """INSERT INTO face_embeddings(
                   source,source_face_id,asset_id,relative_path,gender_marker,dimensions,embedding_f32,
                   box_left,box_top,box_right,box_bottom,localization_similarity,localized_at
               ) VALUES ('test',1,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.asset_id, self.photo.name, "F", 2, b"12345678", 0.1, 0.2, 0.4, 0.6, 0.99, utc_now()),
        ).lastrowid)
        person_id = int(con.execute("INSERT INTO people(name) VALUES ('Focused Person')").lastrowid)
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
               VALUES (?,?,'confirmed',0.93,?,'test',?)""",
            (self.asset_id, person_id, face_id, utc_now()),
        )
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
               VALUES (?,?,'suggested',0.82,'test',?)""",
            (second_asset_id, person_id, utc_now()),
        )
        con.commit()
        con.close()

        with self.get(f"/?scope=people&person={person_id}") as response:
            page = response.read().decode("utf-8")
        self.assertIn("1 confirmed photo", page)
        self.assertIn("1 exact face box", page)
        self.assertIn(f'/people-review?person={person_id}', page)
        self.assertIn("Review 1 possible match", page)
        detail = self.json_response(self.get(
            f"/api/asset?id={self.asset_id}&person_id={person_id}"
        ))
        focused = detail["focused_person_face"]
        self.assertEqual(focused["name"], "Focused Person")
        self.assertEqual(
            [focused[key] for key in ("box_left", "box_top", "box_right", "box_bottom")],
            [0.1, 0.2, 0.4, 0.6],
        )

    def test_merge_people_keeps_aliases_and_strongest_photo_decision(self):
        from photo_index import utc_now

        con = sqlite3.connect(self.database)
        target_id = int(con.execute("INSERT INTO people(name) VALUES ('R David Paine III')").lastrowid)
        source_id = int(con.execute("INSERT INTO people(name) VALUES ('R. David Paine III')").lastrowid)
        con.execute(
            "INSERT INTO person_aliases(person_id,alias) VALUES (?,?)",
            (source_id, "Dave Paine"),
        )
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
               VALUES (?,?,'suggested',0.80,'test',?)""",
            (self.asset_id, target_id, utc_now()),
        )
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
               VALUES (?,?,'confirmed',0.70,'manual',?)""",
            (self.asset_id, source_id, utc_now()),
        )
        action_id = int(con.execute(
            """INSERT INTO people_review_actions(asset_id,person_id,action,previous_json,created_at)
               VALUES (?,?, 'confirmed', '{}', ?)""",
            (self.asset_id, source_id, utc_now()),
        ).lastrowid)
        con.commit()
        con.close()

        with patch.object(
            self.photo_search.SearchHandler, "_publish_people_metadata",
            return_value={"path": self.photo, "backup": self.photo, "filename": self.photo.name},
        ), patch.object(
            self.photo_search, "learn_faces", return_value={"suggestions": 0},
        ):
            result = self.json_response(self.post(
                "/api/person/merge",
                {"target_person_id": target_id, "source_person_ids": [source_id]},
            ))

        self.assertTrue(result["ok"])
        self.assertEqual(result["person"], "R David Paine III")
        self.assertEqual(result["merged_names"], ["R. David Paine III"])
        self.assertEqual(result["published"], 1)
        self.assertTrue(Path(result["database_backup"]).is_file())
        con = sqlite3.connect(self.database)
        try:
            self.assertIsNone(con.execute("SELECT 1 FROM people WHERE id=?", (source_id,)).fetchone())
            aliases = [row[0] for row in con.execute(
                "SELECT alias FROM person_aliases WHERE person_id=? ORDER BY alias", (target_id,)
            )]
            self.assertEqual(aliases, ["Dave Paine", "R. David Paine III"])
            association = con.execute(
                "SELECT person_id,state,source FROM asset_people WHERE asset_id=?", (self.asset_id,)
            ).fetchone()
            self.assertEqual(association, (target_id, "confirmed", "person-merge"))
            action = con.execute(
                "SELECT person_id,undone_at FROM people_review_actions WHERE id=?", (action_id,)
            ).fetchone()
            self.assertEqual(action[0], target_id)
            self.assertIsNotNone(action[1])
        finally:
            con.close()

    def test_merge_people_reports_a_safe_conflict_when_catalog_is_busy(self):
        import photo_index

        lock = sqlite3.connect(self.database, timeout=1)
        lock.execute("BEGIN EXCLUSIVE")
        try:
            with patch.object(photo_index, "SQLITE_BUSY_TIMEOUT_MS", 50):
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    self.post(
                        "/api/person/merge",
                        {"target_person_id": 1, "source_person_ids": [2]},
                    )
            error = blocked.exception
            try:
                self.assertEqual(error.code, 409)
                message = json.loads(error.read().decode("utf-8"))["error"]
                self.assertIn("No names were merged", message)
            finally:
                error.close()
        finally:
            lock.rollback()
            lock.close()

    def test_optional_semantic_job_and_viewer_scope(self):
        def fake_build(_database, **kwargs):
            counts = {"total": 1, "indexed": 1, "errors": 0, "cancelled": False}
            kwargs["progress"](counts)
            return counts

        with patch.object(self.photo_search, "build_semantic_index", side_effect=fake_build):
            started = self.json_response(self.post("/api/semantic/start", {"batch_size": 1}))
            self.assertEqual(started["state"], "running")
            for _ in range(100):
                job = self.json_response(self.get("/api/semantic/status"))
                if job["state"] != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(job["state"], "complete")
            self.assertEqual(job["indexed_this_pass"], 1)

        with patch.object(self.photo_search, "semantic_search", return_value=[(self.asset_id, 0.9)]), patch.object(
            self.photo_search, "semantic_status", return_value={
                "indexed": 1, "eligible": 1, "remaining": 0, "model": "test"
            }
        ):
            with self.get("/?scope=semantic&q=blue+scene") as response:
                page = response.read().decode("utf-8")
        self.assertIn("Meaning (optional)", page)
        self.assertIn("Describe a scene, object, or idea", page)
        self.assertIn(self.photo.name, page)


if __name__ == "__main__":
    unittest.main()
