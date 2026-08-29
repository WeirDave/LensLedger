from __future__ import annotations

import array
import html
import json
import os
import sqlite3
import subprocess
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
        photo_search.SearchHandler.current_library = (self.library.resolve(), self.database)
        photo_search.SearchHandler.csrf_token = "test-csrf"
        photo_search.SearchHandler.library_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.library_cancel.clear()
        photo_search.SearchHandler.ocr_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.ocr_cancel.clear()
        photo_search.SearchHandler.semantic_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.semantic_cancel.clear()
        photo_search.SearchHandler.semantic_install_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.face_scan_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.face_scan_cancel.clear()
        photo_search.SearchHandler.face_install_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.scan_all_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.scan_all_cancel.clear()
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
        import console_log
        console_log.shutdown()
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
        self.assertIn("separate each alternate name with a comma", people_page)
        self.assertIn("/web/js/viewer.js", people_page)
        with self.get("/web/js/viewer.js") as response:
            viewer_script = response.read().decode("utf-8")
        self.assertIn("separate each name with a comma", viewer_script)

    def test_custom_date_picker_renders_hidden_field_and_trigger_label(self):
        # The toolbar's date filter used to be a native <input type="date">,
        # which renders a different picker UI per browser. It's now a hand
        # -built dropdown; the hidden field it drives must still carry the
        # real ?date= value so form submission and changeDay() keep working.
        with self.get("/") as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="dateTrigger"', page)
        self.assertIn(">Any date<", page)
        self.assertIn('type="hidden" name="date" id="datePicker" value=""', page)
        self.assertNotIn('type="date"', page)

        with self.get("/?date=2019-03-15") as response:
            dated_page = response.read().decode("utf-8")
        self.assertIn('id="datePicker" value="2019-03-15"', dated_page)
        self.assertIn(">2019-03-15<", dated_page)

        with self.get("/web/js/viewer.js") as response:
            script = response.read().decode("utf-8")
        self.assertIn("function openCalendar", script)
        self.assertIn("function chooseDate", script)

    def test_person_picker_replaces_native_autofill_prone_inputs_everywhere(self):
        # A plain <input list=datalist> with autocomplete="off" still let
        # Firefox's own address-autofill pop suggestions over the field --
        # autocomplete="off" is largely ignored by Firefox's Form Autofill.
        # Every person-name field is now a <button> (never an autofill
        # target) that opens a custom dropdown built from person-picker.js.
        with self.get("/") as response:
            viewer_page = response.read().decode("utf-8")
        self.assertIn('id="personPickerContainer"', viewer_page)
        self.assertIn("js/person-picker.js", viewer_page)
        self.assertIn("css/person-picker.css", viewer_page)
        self.assertNotIn('id="newPerson"', viewer_page)
        self.assertNotIn('id="addPerson"', viewer_page)
        self.assertNotIn('id="peopleOptions"', viewer_page)

        with self.get("/faces-review") as response:
            faces_page = response.read().decode("utf-8")
        self.assertIn("js/person-picker.js", faces_page)
        self.assertIn("css/person-picker.css", faces_page)
        self.assertNotIn('id="peopleOptions"', faces_page)
        self.assertNotIn('<input list="peopleOptions"', faces_page)

        with self.get("/people-review") as response:
            people_review_page = response.read().decode("utf-8")
        self.assertIn("js/person-picker.js", people_review_page)
        self.assertIn("css/person-picker.css", people_review_page)
        self.assertNotIn('id="peopleOptions"', people_review_page)

        with self.get("/web/js/person-picker.js") as response:
            script = response.read().decode("utf-8")
        self.assertIn("function createPersonPicker", script)
        self.assertIn("+ New person", script)

    def test_onboarding_page_shown_for_true_first_run(self):
        from photo_index import connect
        from library_config import LIBRARY_STATE_PATH

        empty_database = self.root / "empty.sqlite3"
        connect(empty_database).close()
        self.photo_search.SearchHandler.current_library = (self.library.resolve(), empty_database)
        if LIBRARY_STATE_PATH.is_file():
            LIBRARY_STATE_PATH.unlink()
        try:
            with self.get("/") as response:
                page = response.read().decode("utf-8")
        finally:
            self.photo_search.SearchHandler.current_library = (self.library.resolve(), self.database)
        self.assertIn("Let’s find your photo library", page)
        self.assertIn(".LensLedger", page)
        self.assertIn("startOcr", page)

    def test_reconnection_page_shown_when_library_known_but_database_empty(self):
        from library_config import save_library_state
        from photo_index import connect

        save_library_state(self.library.resolve())
        empty_database = self.root / "empty.sqlite3"
        connect(empty_database).close()
        self.photo_search.SearchHandler.current_library = (self.library.resolve(), empty_database)
        try:
            with self.get("/") as response:
                page = response.read().decode("utf-8")
        finally:
            self.photo_search.SearchHandler.current_library = (self.library.resolve(), self.database)
        self.assertIn("Welcome back", page)
        self.assertIn("Your library couldn", page)
        self.assertIn("reconnect.js", page)

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

    def test_install_update_is_refused_for_an_unmanaged_source_checkout(self):
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self.post("/api/update/install", {})
        self.assertEqual(rejected.exception.code, 400)
        body = json.loads(rejected.exception.read().decode("utf-8"))
        self.assertIn("not a managed installation", body["error"])
        rejected.exception.close()

    def test_update_status_reports_restart_ready_when_running_process_is_stale(self):
        # The test suite itself runs from this repo's real checkout, so
        # `on_disk_version` always reflects the real product.py. Patching only
        # APP_VERSION (the value baked into this running process) simulates
        # `git pull` having moved the on-disk code past what's loaded in memory.
        release = {
            "version": "0.0.0-test-stale", "tag": "v0.0.0-test-stale", "name": "Stale",
            "page_url": "https://example.test/release", "asset_api_url": "https://example.test/asset",
            "asset_name": "LensLedger-stale.zip", "asset_size": 10, "digest": "sha256:" + "0" * 64,
        }
        with patch.object(
            self.photo_search, "check_for_update",
            return_value={"current_version": "0.0.0-test-stale", "available": False, "release": release},
        ), patch.object(self.photo_search, "APP_VERSION", "0.0.0-test-stale"):
            status = self.json_response(self.get("/api/update/status"))
        self.assertTrue(status["is_source_checkout"])
        self.assertEqual(status["current_version"], "0.0.0-test-stale")
        self.assertIsNotNone(status["on_disk_version"])
        self.assertNotEqual(status["on_disk_version"], "0.0.0-test-stale")
        self.assertTrue(status["restart_ready"])

    def test_restart_source_marks_state_restarting_without_actually_spawning_a_process(self):
        with patch.object(self.photo_search.SearchHandler, "_spawn_updater_helper", lambda self, extra_args: None), \
             patch.object(self.photo_search.SearchHandler, "_schedule_shutdown", lambda self: None):
            result = self.json_response(self.post("/api/update/restart-source", {}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "restarting")
        self.assertEqual(self.photo_search.SearchHandler.update_job["state"], "restarting")

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
        self.assertIn("/web/js/people-review.js", page)
        with self.get("/web/js/people-review.js") as response:
            script = response.read().decode("utf-8")
        self.assertIn("Face being checked", script)
        self.assertIn("ResizeObserver", script)

    def test_people_review_decision_not_a_person_marks_the_face_and_undoes_cleanly(self):
        from photo_index import utc_now

        con = sqlite3.connect(self.database)
        face_id = int(con.execute(
            """INSERT INTO face_embeddings(
                   source,source_face_id,asset_id,relative_path,dimensions,embedding_f32
               ) VALUES ('test',1,?,?,2,?)""",
            (self.asset_id, self.photo.name, b"12345678"),
        ).lastrowid)
        person_id = int(con.execute("INSERT INTO people(name) VALUES ('Maybe Someone')").lastrowid)
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
               VALUES (?,?,'suggested',0.8,?,'test',?)""",
            (self.asset_id, person_id, face_id, utc_now()),
        )
        con.commit()
        con.close()

        result = self.json_response(self.post(
            "/api/people/review/decision",
            {"asset_id": self.asset_id, "person_id": person_id, "action": "not_a_person"},
        ))
        self.assertTrue(result["ok"])
        action_id = result["action_id"]

        con = sqlite3.connect(self.database)
        state, = con.execute(
            "SELECT state FROM asset_people WHERE asset_id=? AND person_id=?", (self.asset_id, person_id)
        ).fetchone()
        self.assertEqual(state, "rejected")
        ignored_at, unknown_at = con.execute(
            "SELECT ignored_at, unknown_at FROM face_embeddings WHERE id=?", (face_id,)
        ).fetchone()
        self.assertIsNotNone(ignored_at)
        self.assertIsNone(unknown_at)
        con.close()

        undone = self.json_response(self.post("/api/people/review/undo", {"action_id": action_id}))
        self.assertTrue(undone["ok"])
        con = sqlite3.connect(self.database)
        state, = con.execute(
            "SELECT state FROM asset_people WHERE asset_id=? AND person_id=?", (self.asset_id, person_id)
        ).fetchone()
        self.assertEqual(state, "suggested")
        ignored_at, = con.execute("SELECT ignored_at FROM face_embeddings WHERE id=?", (face_id,)).fetchone()
        self.assertIsNone(ignored_at)
        con.close()

    def test_people_review_batch_decision_unknown_person_marks_the_face_and_publishes(self):
        from photo_index import utc_now

        con = sqlite3.connect(self.database)
        face_id = int(con.execute(
            """INSERT INTO face_embeddings(
                   source,source_face_id,asset_id,relative_path,dimensions,embedding_f32
               ) VALUES ('test',1,?,?,2,?)""",
            (self.asset_id, self.photo.name, b"12345678"),
        ).lastrowid)
        person_id = int(con.execute("INSERT INTO people(name) VALUES ('Batch Someone')").lastrowid)
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
               VALUES (?,?,'suggested',0.8,?,'test',?)""",
            (self.asset_id, person_id, face_id, utc_now()),
        )
        con.commit()
        con.close()

        result = self.json_response(self.post(
            "/api/people/review/batch",
            {"person_id": person_id, "decisions": [{"asset_id": self.asset_id, "action": "unknown_person"}]},
        ))
        self.assertTrue(result["ok"])
        self.assertEqual(result["published"], 1)

        con = sqlite3.connect(self.database)
        state, = con.execute(
            "SELECT state FROM asset_people WHERE asset_id=? AND person_id=?", (self.asset_id, person_id)
        ).fetchone()
        self.assertEqual(state, "rejected")
        unknown_at, ignored_at = con.execute(
            "SELECT unknown_at, ignored_at FROM face_embeddings WHERE id=?", (face_id,)
        ).fetchone()
        self.assertIsNotNone(unknown_at)
        self.assertIsNone(ignored_at)
        con.close()

    def test_naming_a_face_returns_similar_unidentified_faces_as_matches(self):
        from face_learning import SUGGESTION_THRESHOLD
        from photo_index import scan_library, utc_now

        second_photo = self.library / "2026-08-10 second.jpg"
        Image.new("RGB", (32, 24), (60, 120, 180)).save(second_photo, quality=92)
        third_photo = self.library / "2026-08-11 third.jpg"
        Image.new("RGB", (32, 24), (10, 10, 10)).save(third_photo, quality=92)
        self.assertEqual(scan_library(self.library, self.database), 0)
        con = sqlite3.connect(self.database)
        second_asset_id = int(con.execute(
            "SELECT id FROM assets WHERE relative_path=?", (second_photo.name,)
        ).fetchone()[0])
        third_asset_id = int(con.execute(
            "SELECT id FROM assets WHERE relative_path=?", (third_photo.name,)
        ).fetchone()[0])

        def insert_face(source_face_id, asset_id, filename, vector):
            return int(con.execute(
                """INSERT INTO face_embeddings(
                       source,source_face_id,asset_id,relative_path,dimensions,embedding_f32,
                       box_left,box_top,box_right,box_bottom,localized_at
                   ) VALUES ('test',?,?,?,?,?,0.1,0.2,0.4,0.6,?)""",
                (source_face_id, asset_id, filename, 2, vector, utc_now()),
            ).lastrowid)

        named_face_id = insert_face(1, self.asset_id, self.photo.name, array.array("f", [1.0, 0.0]).tobytes())
        similar_face_id = insert_face(2, second_asset_id, second_photo.name, array.array("f", [0.99, 0.14]).tobytes())
        different_face_id = insert_face(3, third_asset_id, third_photo.name, array.array("f", [0.0, 1.0]).tobytes())
        con.commit()
        con.close()

        result = self.json_response(self.post("/api/faces/name", {"face_id": named_face_id, "name": "Match Person"}))
        self.assertTrue(result["ok"])
        match_ids = {match["face_id"]: match["score"] for match in result["matches"]}
        self.assertIn(similar_face_id, match_ids)
        self.assertGreaterEqual(match_ids[similar_face_id], SUGGESTION_THRESHOLD)
        self.assertNotIn(different_face_id, match_ids)
        self.assertNotIn(named_face_id, match_ids)

        unidentified = self.json_response(self.get("/api/faces/unidentified"))
        remaining_ids = {face["face_id"] for face in unidentified["faces"]}
        self.assertNotIn(named_face_id, remaining_ids)
        self.assertIn(similar_face_id, remaining_ids)
        by_id = {face["face_id"]: face for face in unidentified["faces"]}
        self.assertEqual(
            (by_id[similar_face_id]["box_left"], by_id[similar_face_id]["box_top"],
             by_id[similar_face_id]["box_right"], by_id[similar_face_id]["box_bottom"]),
            (0.1, 0.2, 0.4, 0.6),
        )

    def test_faces_unknown_marks_the_face_and_removes_it_from_the_queue(self):
        con = sqlite3.connect(self.database)
        face_id = int(con.execute(
            """INSERT INTO face_embeddings(
                   source,source_face_id,asset_id,relative_path,dimensions,embedding_f32,
                   box_left,box_top,box_right,box_bottom
               ) VALUES ('test',1,?,?,2,?,0.1,0.2,0.4,0.6)""",
            (self.asset_id, self.photo.name, b"12345678"),
        ).lastrowid)
        con.commit()
        con.close()

        before = self.json_response(self.get("/api/faces/unidentified"))
        self.assertIn(face_id, {face["face_id"] for face in before["faces"]})

        result = self.json_response(self.post("/api/faces/unknown", {"face_id": face_id}))
        self.assertTrue(result["ok"])

        con = sqlite3.connect(self.database)
        unknown_at, ignored_at = con.execute(
            "SELECT unknown_at, ignored_at FROM face_embeddings WHERE id=?", (face_id,)
        ).fetchone()
        self.assertIsNotNone(unknown_at)
        self.assertIsNone(ignored_at)
        con.close()

        after = self.json_response(self.get("/api/faces/unidentified"))
        self.assertNotIn(face_id, {face["face_id"] for face in after["faces"]})

        with self.assertRaises(urllib.error.HTTPError) as repeat:
            self.post("/api/faces/unknown", {"face_id": face_id})
        self.assertEqual(repeat.exception.code, 400)
        repeat.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as also_ignore:
            self.post("/api/faces/ignore", {"face_id": face_id})
        self.assertEqual(also_ignore.exception.code, 400)
        also_ignore.exception.close()

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
                body = json.loads(error.read().decode("utf-8"))
                self.assertTrue(body["busy"])
                self.assertIn("catalog", body["error"])
            finally:
                error.close()
        finally:
            lock.rollback()
            lock.close()

    def test_catalog_busy_message_is_not_specific_to_merge_on_other_routes(self):
        # The 409 handler in do_POST is a global catch-all for every route,
        # not just /api/person/merge -- naming a face while a scan holds the
        # write lock used to surface a message about "names being merged"
        # even though no merge was involved. Confirms the fix reads the same
        # regardless of which route hit it.
        import photo_index

        con = sqlite3.connect(self.database)
        face_id = int(con.execute(
            """INSERT INTO face_embeddings(
                   source,source_face_id,asset_id,relative_path,dimensions,embedding_f32
               ) VALUES ('test',1,?,?,2,?)""",
            (self.asset_id, self.photo.name, b"12345678"),
        ).lastrowid)
        con.commit()
        con.close()

        lock = sqlite3.connect(self.database, timeout=1)
        lock.execute("BEGIN EXCLUSIVE")
        try:
            with patch.object(photo_index, "SQLITE_BUSY_TIMEOUT_MS", 50):
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    self.post("/api/faces/name", {"face_id": face_id, "name": "Someone"})
            error = blocked.exception
            try:
                self.assertEqual(error.code, 409)
                body = json.loads(error.read().decode("utf-8"))
                self.assertTrue(body["busy"])
                self.assertNotIn("merged", body["error"])
            finally:
                error.close()
        finally:
            lock.rollback()
            lock.close()

    def test_learn_people_publishes_auto_confirmed_matches_to_the_jpeg(self):
        # face_learning.learn() writes near-certain matches straight to
        # 'confirmed' (see AUTO_CONFIRM_THRESHOLD); this exercises the
        # photo_search.py side of that: does /api/people/learn actually
        # publish those to the JPEG's real XMP metadata, same as a human
        # confirming would, with a genuine safety backup.
        from photo_index import utc_now

        con = sqlite3.connect(self.database)
        person_id = int(con.execute(
            "INSERT INTO people(name) VALUES ('Auto Confirmed Person')"
        ).lastrowid)
        con.execute(
            """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
               VALUES (?,?,'confirmed',0.95,'learned_face_auto',?)""",
            (self.asset_id, person_id, utc_now()),
        )
        con.commit()
        con.close()

        fake_learn_result = {
            "profiles": 1, "eligible_profiles": 1, "suggestions": 0,
            "auto_confirmed": [{
                "asset_id": self.asset_id, "person_id": person_id,
                "name": "Auto Confirmed Person", "confidence": 0.95,
            }],
        }
        with patch.object(self.photo_search, "learn_faces", return_value=fake_learn_result):
            result = self.json_response(self.post("/api/people/learn", {}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["auto_confirmed"], 1)

        con = sqlite3.connect(self.database)
        con.row_factory = sqlite3.Row
        publication = con.execute(
            "SELECT operation, backup_path FROM metadata_publications WHERE asset_id=?", (self.asset_id,)
        ).fetchone()
        con.close()
        self.assertIsNotNone(publication)
        self.assertEqual(publication["operation"], "people-auto-confirm")
        self.assertTrue(Path(publication["backup_path"]).is_file())

        detail = self.json_response(self.get(f"/api/asset?id={self.asset_id}"))
        self.assertTrue(detail["can_restore_publish"])
        self.assertIn("Auto Confirmed Person", [p["name"] for p in detail["confirmed_people"]])

    def test_near_filter_shows_only_photos_at_that_rounded_location(self):
        from photo_index import scan_library

        second = self.library / "2026-08-10 other place.jpg"
        Image.new("RGB", (32, 24), (200, 100, 50)).save(second, quality=92)
        self.assertEqual(scan_library(self.library, self.database), 0)
        con = sqlite3.connect(self.database)
        other_id = int(con.execute("SELECT id FROM assets WHERE filename=?", (second.name,)).fetchone()[0])
        con.execute("UPDATE assets SET gps_latitude=?, gps_longitude=? WHERE id=?", (33.7, -117.8, self.asset_id))
        con.execute("UPDATE assets SET gps_latitude=?, gps_longitude=? WHERE id=?", (51.5, -0.1, other_id))
        con.commit()
        con.close()

        with self.get("/?near=33.7,-117.8&scope=all") as response:
            page = response.read().decode("utf-8")
        self.assertIn(self.photo.name, page)
        self.assertNotIn(second.name, page)
        self.assertIn("Photos near this location", page)

        items = self.json_response(self.get("/api/library/items?near=33.7,-117.8&scope=all"))
        self.assertEqual([item["filename"] for item in items["items"]], [self.photo.name])

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

    def test_scan_photos_page_renders_all_job_cards(self):
        with self.get("/scan-photos") as response:
            page = response.read().decode("utf-8")
        self.assertIn("Scan your photos", page)
        self.assertIn("Run all scans", page)
        self.assertIn("Photo locations (GPS)", page)
        self.assertIn("Local text recognition (OCR)", page)
        self.assertIn("Meaning search (optional)", page)
        self.assertIn("Face detection (optional)", page)
        self.assertIn("/web/js/scan-photos.js", page)
        with self.get("/web/css/scan-photos.css") as response:
            self.assertEqual(response.headers.get_content_type(), "text/css")
        with self.get("/web/js/scan-photos.js") as response:
            script = response.read().decode("utf-8")
        self.assertIn("startLocation", script)
        with self.get("/") as response:
            page = response.read().decode("utf-8")
        self.assertIn('href="/scan-photos"', page)

    def test_semantic_install_refuses_when_already_installed(self):
        with patch.object(self.photo_search, "semantic_is_available", return_value=True):
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                self.post("/api/semantic/install", {})
        self.assertEqual(rejected.exception.code, 400)
        body = json.loads(rejected.exception.read().decode("utf-8"))
        self.assertIn("already installed", body["error"])
        rejected.exception.close()

    def test_semantic_install_runs_and_reports_completion(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.object(self.photo_search, "semantic_is_available", return_value=False), \
             patch.object(self.photo_search.subprocess, "run", return_value=completed):
            started = self.json_response(self.post("/api/semantic/install", {}))
            self.assertEqual(started["state"], "installing")
            for _ in range(100):
                job = self.json_response(self.get("/api/semantic/status"))
                if job["install"]["state"] != "installing":
                    break
                time.sleep(0.02)
        self.assertEqual(job["install"]["state"], "complete")

    def test_face_scan_is_refused_when_not_installed(self):
        with patch.object(self.photo_search, "face_is_available", return_value=False):
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                self.post("/api/faces/start", {})
        self.assertEqual(rejected.exception.code, 400)
        body = json.loads(rejected.exception.read().decode("utf-8"))
        self.assertIn("not set up yet", body["error"])
        rejected.exception.close()

    def test_face_install_refuses_when_already_installed(self):
        with patch.object(self.photo_search, "face_is_available", return_value=True):
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                self.post("/api/faces/install", {})
        self.assertEqual(rejected.exception.code, 400)
        body = json.loads(rejected.exception.read().decode("utf-8"))
        self.assertIn("already installed", body["error"])
        rejected.exception.close()

    def test_face_install_runs_and_reports_completion(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.object(self.photo_search, "face_is_available", return_value=False), \
             patch.object(self.photo_search.subprocess, "run", return_value=completed):
            started = self.json_response(self.post("/api/faces/install", {}))
            self.assertEqual(started["state"], "installing")
            for _ in range(100):
                job = self.json_response(self.get("/api/faces/status"))
                if job["install"]["state"] != "installing":
                    break
                time.sleep(0.02)
        self.assertEqual(job["install"]["state"], "complete")

    def test_face_scan_runs_and_reports_completion(self):
        def fake_scan(_database, _library, **kwargs):
            counts = {"total": 1, "processed": 1, "faces_found": 3, "errors": 0, "cancelled": False}
            kwargs["progress"](counts)
            return counts

        with patch.object(self.photo_search, "face_is_available", return_value=True), \
             patch.object(self.photo_search, "scan_for_faces", side_effect=fake_scan):
            started = self.json_response(self.post("/api/faces/start", {}))
            self.assertEqual(started["state"], "running")
            for _ in range(100):
                job = self.json_response(self.get("/api/faces/status"))
                if job["state"] != "running":
                    break
                time.sleep(0.02)
        self.assertEqual(job["state"], "complete")
        self.assertEqual(job["faces_found"], 3)

    def test_scan_all_chains_location_then_ocr_when_optional_scans_not_set_up(self):
        with patch.object(self.photo_search, "semantic_is_available", return_value=False), \
             patch.object(self.photo_search, "face_is_available", return_value=False), \
             patch("photo_index.run_windows_ocr", return_value=(str(self.photo), "sample recognized text", None)):
            started = self.json_response(self.post("/api/scan-all/start", {}))
            self.assertEqual(started["state"], "running")
            for _ in range(200):
                job = self.json_response(self.get("/api/scan-all/status"))
                if job["state"] != "running":
                    break
                time.sleep(0.02)
        self.assertEqual(job["state"], "complete")
        ocr_job = self.json_response(self.get("/api/ocr/status"))
        self.assertEqual(ocr_job["state"], "complete")
        self.assertEqual(ocr_job["attempted"], 1)
        library_job = self.json_response(self.get("/api/library/status"))
        self.assertEqual(library_job["state"], "complete")

    def test_scan_all_chains_all_four_steps_when_optional_scans_are_installed(self):
        order = []

        def fake_semantic(_database, **kwargs):
            order.append("semantic")
            counts = {"total": 1, "indexed": 1, "errors": 0, "cancelled": False}
            kwargs["progress"](counts)
            return counts

        def fake_faces(_database, _library, **kwargs):
            order.append("face")
            counts = {"total": 1, "processed": 1, "faces_found": 2, "errors": 0, "cancelled": False}
            kwargs["progress"](counts)
            return counts

        with patch.object(self.photo_search, "semantic_is_available", return_value=True), \
             patch.object(self.photo_search, "face_is_available", return_value=True), \
             patch.object(self.photo_search, "build_semantic_index", side_effect=fake_semantic), \
             patch.object(self.photo_search, "scan_for_faces", side_effect=fake_faces), \
             patch("photo_index.run_windows_ocr", return_value=(str(self.photo), "sample recognized text", None)):
            started = self.json_response(self.post("/api/scan-all/start", {}))
            self.assertEqual(started["state"], "running")
            for _ in range(200):
                job = self.json_response(self.get("/api/scan-all/status"))
                if job["state"] != "running":
                    break
                time.sleep(0.02)
        self.assertEqual(job["state"], "complete")
        self.assertEqual(order, ["semantic", "face"])
        semantic_job = self.json_response(self.get("/api/semantic/status"))
        self.assertEqual(semantic_job["state"], "complete")
        face_job = self.json_response(self.get("/api/faces/status"))
        self.assertEqual(face_job["state"], "complete")
        self.assertEqual(face_job["faces_found"], 2)

    def test_scan_all_can_be_stopped_after_the_current_step(self):
        def slow_scan_library(_root, _database, **kwargs):
            should_cancel = kwargs.get("should_cancel")
            for _ in range(200):
                if should_cancel and should_cancel():
                    return 3
                time.sleep(0.02)
            return 0

        with patch.object(self.photo_search, "scan_library", side_effect=slow_scan_library):
            started = self.json_response(self.post("/api/scan-all/start", {}))
            self.assertEqual(started["state"], "running")
            for _ in range(50):
                job = self.json_response(self.get("/api/scan-all/status"))
                if job.get("step") == "location":
                    break
                time.sleep(0.02)
            cancelled = self.json_response(self.post("/api/scan-all/cancel", {}))
            self.assertEqual(cancelled["state"], "cancelling")
            for _ in range(200):
                job = self.json_response(self.get("/api/scan-all/status"))
                if job["state"] != "running":
                    break
                time.sleep(0.02)
        self.assertEqual(job["state"], "cancelled")

    def test_scan_all_is_refused_while_already_running(self):
        def slow_scan_library(_root, _database, **kwargs):
            should_cancel = kwargs.get("should_cancel")
            for _ in range(200):
                if should_cancel and should_cancel():
                    return 3
                time.sleep(0.02)
            return 0

        with patch.object(self.photo_search, "scan_library", side_effect=slow_scan_library):
            self.json_response(self.post("/api/scan-all/start", {}))
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                self.post("/api/scan-all/start", {})
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                self.post("/api/ocr/start", {})
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()
            self.post("/api/scan-all/cancel", {})
            for _ in range(200):
                job = self.json_response(self.get("/api/scan-all/status"))
                if job["state"] != "running":
                    break
                time.sleep(0.02)

    def test_everything_scope_search_ranks_by_relevance_without_crashing(self):
        # Regression test: scope='all' combined FTS's MATCH with a
        # confirmed-person-name OR inside one WHERE clause, which SQLite's
        # FTS5 refuses to evaluate at all ("unable to use function MATCH in
        # the requested context") -- every scope='all' search with a real
        # query silently returned zero results and an error banner.
        from photo_index import scan_library, set_source_tags, rebuild_search_row, utc_now

        strong = self.library / "2026-08-11 beach strong.jpg"
        weak = self.library / "2026-08-12 beach weak.jpg"
        unrelated = self.library / "2026-08-13 birthday.jpg"
        for path in (strong, weak, unrelated):
            Image.new("RGB", (32, 24), (10, 10, 10)).save(path, quality=92)
        self.assertEqual(scan_library(self.library, self.database), 0)

        con = sqlite3.connect(self.database)
        con.row_factory = sqlite3.Row
        ids = {row["relative_path"]: int(row["id"]) for row in con.execute("SELECT id, relative_path FROM assets")}
        set_source_tags(con, ids[strong.name], "asset_rule", ["beach", "sunset", "beach party"])
        con.execute("UPDATE text_data SET ocr_text=? WHERE asset_id=?", ("a faint mention of beach", ids[weak.name]))
        person_id = int(con.execute("INSERT INTO people(name) VALUES ('Beach Bob')").lastrowid)
        con.execute(
            "INSERT INTO asset_people(asset_id,person_id,state,source,updated_at) VALUES (?,?,'confirmed','manual',?)",
            (ids[unrelated.name], person_id, utc_now()),
        )
        for asset_id in ids.values():
            rebuild_search_row(con, asset_id)
        con.commit()
        con.close()

        page = self.json_response(self.get("/api/library/items?q=beach&scope=all&sort=relevance"))
        self.assertEqual(page["total"], 3)
        filenames = [item["filename"] for item in page["items"]]
        self.assertEqual(filenames[0], strong.name)
        self.assertLess(filenames.index(weak.name), filenames.index(unrelated.name))

        with self.get("/?q=beach&scope=all") as response:
            html_page = response.read().decode("utf-8")
        self.assertNotIn("unable to use function MATCH", html_page)
        self.assertIn("Best match", html_page)


if __name__ == "__main__":
    unittest.main()
