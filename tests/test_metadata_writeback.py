from __future__ import annotations

import array
import json
import math
import os
import sqlite3
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image


class TestXmpSidecar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.photo = Path(self.tmp.name) / "test.jpg"
        Image.new("RGB", (32, 24), (10, 20, 30)).save(self.photo, quality=92)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_sidecar_creates_xmp_file(self):
        from xmp_sidecar import write_sidecar, sidecar_exists

        self.assertFalse(sidecar_exists(self.photo))
        path = write_sidecar(
            self.photo,
            title="Test Photo",
            description="A test description",
            keywords=["landscape", "nature", "forest"],
            people=["Alice", "Bob"],
        )
        self.assertTrue(path.exists())
        self.assertEqual(path.suffix, ".xmp")
        self.assertTrue(sidecar_exists(self.photo))

        content = path.read_text(encoding="utf-8")
        self.assertIn("Test Photo", content)
        self.assertIn("A test description", content)
        self.assertIn("landscape", content)
        self.assertIn("nature", content)
        self.assertIn("forest", content)
        self.assertIn("Alice", content)
        self.assertIn("Bob", content)
        self.assertIn("dc:subject", content)
        self.assertIn("Iptc4xmpExt:PersonInImage", content)

    def test_build_xmp_escapes_special_characters(self):
        from xmp_sidecar import build_xmp

        xmp = build_xmp(title='Photo & "Friends"', description="<test>")
        self.assertIn("&amp;", xmp)
        self.assertIn("&lt;test&gt;", xmp)
        self.assertIn("&quot;", xmp)
        self.assertNotIn("&Friends", xmp)

    def test_build_xmp_with_empty_fields(self):
        from xmp_sidecar import build_xmp

        xmp = build_xmp()
        self.assertIn("rdf:RDF", xmp)
        self.assertNotIn("dc:title", xmp)
        self.assertNotIn("dc:description", xmp)
        self.assertNotIn("dc:subject", xmp)
        self.assertNotIn("PersonInImage", xmp)

    def test_sidecar_overwrites_existing(self):
        from xmp_sidecar import write_sidecar

        write_sidecar(self.photo, keywords=["old"])
        xmp_path = self.photo.with_suffix(".xmp")
        self.assertIn("old", xmp_path.read_text(encoding="utf-8"))

        write_sidecar(self.photo, keywords=["new"])
        content = xmp_path.read_text(encoding="utf-8")
        self.assertIn("new", content)
        self.assertNotIn("old", content)


class TestSemanticClassify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        os.environ["LENSLEDGER_DATA_DIR"] = self.tmp.name
        from photo_index import connect
        with connect(self.db_path) as con:
            pass

    def tearDown(self):
        self.tmp.cleanup()

    def _make_encoder(self, vocab_scores: dict[str, float]):
        """Create a mock encoder that returns predetermined scores."""
        encoder = MagicMock()
        encoder.identity = "test-model"

        def encode_text(prompt):
            for term, score in vocab_scores.items():
                if term in prompt:
                    vec = [score] + [0.0] * 511
                    length = math.sqrt(sum(v * v for v in vec))
                    return tuple(v / length for v in vec)
            return tuple([0.01] * 512)

        encoder.encode_text = encode_text
        return encoder

    def _make_embedding(self, value: float = 1.0) -> bytes:
        vec = [value] + [0.0] * 511
        length = math.sqrt(sum(v * v for v in vec))
        normalized = [v / length for v in vec]
        return array.array("f", normalized).tobytes()

    def test_classify_asset_returns_top_matches(self):
        from semantic_classify import classify_asset, _vocab_cache
        _vocab_cache.clear()

        encoder = self._make_encoder({
            "landscape": 0.9,
            "portrait": 0.1,
            "food": 0.05,
        })
        embedding = self._make_embedding(1.0)

        results = classify_asset(embedding, encoder, threshold=0.0, top_n=3)
        self.assertTrue(len(results) > 0)
        terms = [term for term, score in results]
        self.assertIn("landscape", terms)

    def test_classify_library_stores_tags(self):
        from photo_index import connect
        from semantic_classify import classify_library, _vocab_cache
        _vocab_cache.clear()

        embedding = self._make_embedding(1.0)
        with connect(self.db_path) as con:
            con.execute(
                "INSERT INTO assets(path, relative_path, folder, filename, extension, media_type, "
                "size_bytes, mtime_ns, metadata_scanned, location_scanned, face_scanned, in_review_bin, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0, datetime('now'))",
                ("/test/photo.jpg", "photo.jpg", "test", "photo.jpg", ".jpg", "image", 1000, 0),
            )
            asset_id = con.execute("SELECT id FROM assets").fetchone()[0]
            con.execute(
                "INSERT INTO semantic_embeddings(asset_id, model, dimensions, embedding_f32, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (asset_id, "test-model", 512, embedding),
            )

        encoder = self._make_encoder({"landscape": 0.9})
        result = classify_library(self.db_path, encoder=encoder, threshold=0.0, top_n=5)
        self.assertEqual(result["classified"], 1)
        self.assertGreater(result["tags_added"], 0)

        with connect(self.db_path) as con:
            auto_tags = con.execute(
                "SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id "
                "WHERE at.asset_id=? AND at.source='semantic_auto'", (asset_id,)
            ).fetchall()
        self.assertGreater(len(auto_tags), 0)

    def test_clear_classifications(self):
        from photo_index import connect
        from semantic_classify import clear_classifications

        with connect(self.db_path) as con:
            con.execute(
                "INSERT INTO assets(path, relative_path, folder, filename, extension, media_type, "
                "size_bytes, mtime_ns, metadata_scanned, location_scanned, face_scanned, in_review_bin, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0, datetime('now'))",
                ("/test/photo.jpg", "photo.jpg", "test", "photo.jpg", ".jpg", "image", 1000, 0),
            )
            asset_id = con.execute("SELECT id FROM assets").fetchone()[0]
            tag_id = con.execute("INSERT INTO tags(name) VALUES ('test_tag') RETURNING id").fetchone()[0]
            con.execute(
                "INSERT INTO asset_tags(asset_id, tag_id, source, confidence) VALUES (?, ?, 'semantic_auto', 0.5)",
                (asset_id, tag_id),
            )

        removed = clear_classifications(self.db_path)
        self.assertEqual(removed, 1)

        with connect(self.db_path) as con:
            remaining = con.execute(
                "SELECT COUNT(*) FROM asset_tags WHERE source='semantic_auto'"
            ).fetchone()[0]
        self.assertEqual(remaining, 0)


class TestPublishSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LENSLEDGER_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_defaults_in_settings(self):
        from settings_config import load_settings

        settings = load_settings()
        publish = settings.get("publish", {})
        self.assertEqual(publish["write_mode"], "embedded")
        self.assertFalse(publish["auto_classify"])
        self.assertAlmostEqual(publish["classify_threshold"], 0.22)
        self.assertEqual(publish["classify_top_n"], 5)

    def test_save_and_load_publish_settings(self):
        from settings_config import load_settings, save_settings

        settings = load_settings()
        settings["publish"]["write_mode"] = "sidecar"
        settings["publish"]["auto_classify"] = True
        save_settings(settings)

        reloaded = load_settings()
        self.assertEqual(reloaded["publish"]["write_mode"], "sidecar")
        self.assertTrue(reloaded["publish"]["auto_classify"])


class TestWriteTagsEndpoint(unittest.TestCase):
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

        import library_config
        import photo_search
        from photo_index import scan_library

        self.photo_search = photo_search
        self._patched_state = patch.object(library_config, "LIBRARY_STATE_PATH", self.data / "library-state.json")
        self._patched_db_root = patch.object(library_config, "LIBRARY_DATABASE_ROOT", self.data / "Libraries")
        self._patched_state.start()
        self._patched_db_root.start()
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
        photo_search.SearchHandler.classify_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.classify_cancel.clear()
        photo_search.SearchHandler.write_tags_job = {"state": "idle", "message": ""}
        photo_search.SearchHandler.write_tags_cancel.clear()
        photo_search.SearchHandler.people_merge_lock = threading.Lock()
        photo_search.SearchHandler.update_job = {"state": "idle", "message": ""}
        from http.server import ThreadingHTTPServer
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
        self._patched_db_root.stop()
        self._patched_state.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def post(self, path, body, *, csrf="test-csrf"):
        import urllib.request
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

    def test_write_tags_sidecar_mode(self):
        from photo_index import connect
        con = sqlite3.connect(self.database)
        tag_id = int(con.execute("INSERT INTO tags(name) VALUES ('sunset')").lastrowid)
        con.execute(
            "INSERT INTO asset_tags(asset_id, tag_id, source, confidence) VALUES (?, ?, 'semantic_auto', 0.5)",
            (self.asset_id, tag_id),
        )
        con.commit()
        con.close()

        result = self.json_response(self.post(
            "/api/write-tags",
            {"id": self.asset_id, "write_mode": "sidecar"},
        ))
        self.assertTrue(result["ok"])
        self.assertTrue(result["wrote_sidecar"])
        self.assertFalse(result["wrote_embedded"])

        xmp_path = self.photo.with_suffix(".xmp")
        self.assertTrue(xmp_path.exists())
        content = xmp_path.read_text(encoding="utf-8")
        self.assertIn("sunset", content)

    def _seed_every_category(self):
        """Give the photo one of each thing "Write all tags" promises to write."""
        con = sqlite3.connect(self.database)
        tag_id = int(con.execute("INSERT INTO tags(name) VALUES ('sunset')").lastrowid)
        con.execute(
            "INSERT INTO asset_tags(asset_id, tag_id, source, confidence) VALUES (?, ?, 'semantic_auto', 0.5)",
            (self.asset_id, tag_id),
        )
        person_id = int(con.execute("INSERT INTO people(name) VALUES ('Marta Quill')").lastrowid)
        con.execute(
            "INSERT INTO asset_people(asset_id, person_id, state, source, updated_at) "
            "VALUES (?, ?, 'confirmed', 'test', datetime('now'))",
            (self.asset_id, person_id),
        )
        con.execute(
            "INSERT INTO text_data(asset_id, ocr_text, ocr_scanned) VALUES (?, ?, 1) "
            "ON CONFLICT(asset_id) DO UPDATE SET ocr_text=excluded.ocr_text, ocr_scanned=1",
            (self.asset_id, "PLATFORM 4 DEPARTURES"),
        )
        con.execute(
            "INSERT INTO asset_annotations(relative_path, subject, tags) VALUES (?, ?, '')",
            (self.photo.name, "Station steps"),
        )
        con.commit()
        con.close()
        return {
            "keywords": "sunset",
            "people": "Marta Quill",
            "description": "PLATFORM 4 DEPARTURES",
            "subject": "Station steps",
        }

    def _exiftool_or_skip(self):
        if not self.photo_search.EXIFTOOL_PATH.is_file():
            self.skipTest("bundled ExifTool is not present")
        try:
            return self.photo_search._exiftool_values(self.photo)
        except Exception as exc:
            self.skipTest(f"bundled ExifTool could not run: {exc}")

    def test_write_tags_embedded_writes_every_category(self):
        """Every category the button promises must reach the file itself.

        This is the regression guard: if any one of keywords, people,
        description or subject stops being embedded, the matching subTest
        fails and names the category that went missing.
        """
        self._exiftool_or_skip()
        expected = self._seed_every_category()

        result = self.json_response(self.post(
            "/api/write-tags",
            {"id": self.asset_id, "write_mode": "embedded"},
        ))
        self.assertTrue(result["ok"])
        self.assertTrue(result["wrote_embedded"])
        self.assertFalse(result["wrote_sidecar"])

        values = self.photo_search._exiftool_values(self.photo)
        category_fields = self.photo_search.SearchHandler.EMBEDDED_CATEGORY_FIELDS
        for category, needle in expected.items():
            with self.subTest(category=category):
                self.assertIn(
                    category, result["written"],
                    f"the write did not report {category} as written",
                )
                found = []
                for field in category_fields[category]:
                    found.extend(
                        self.photo_search.SearchHandler._metadata_values(values.get(field))
                    )
                self.assertTrue(
                    any(needle in value for value in found),
                    f"{category} never reached the photo — looked for {needle!r} in "
                    f"{', '.join(category_fields[category])}, found {found}",
                )

    def test_write_tags_embedded_keeps_a_restorable_backup(self):
        self._exiftool_or_skip()
        self._seed_every_category()
        before = self.photo.read_bytes()

        self.json_response(self.post(
            "/api/write-tags", {"id": self.asset_id, "write_mode": "embedded"},
        ))

        backups = list(self.photo_search.BACKUP_ROOT.rglob("*.before-write-tags-*.jpg"))
        self.assertEqual(len(backups), 1, "exactly one safety copy should exist")
        self.assertEqual(backups[0].read_bytes(), before,
                         "the safety copy must match the file as it was before the write")

        con = sqlite3.connect(self.database)
        recorded = con.execute(
            "SELECT backup_path, operation FROM metadata_publications WHERE asset_id=?",
            (self.asset_id,),
        ).fetchone()
        con.close()
        self.assertIsNotNone(recorded, "the write must be recorded so it can be undone")
        self.assertEqual(recorded[1], "write_tags")
        self.assertTrue(Path(recorded[0]).is_file())

    def test_write_tags_embedded_leaves_the_picture_untouched(self):
        self._exiftool_or_skip()
        self._seed_every_category()
        from metadata_reader import pixel_hash

        before = pixel_hash(self.photo)
        self.json_response(self.post(
            "/api/write-tags", {"id": self.asset_id, "write_mode": "embedded"},
        ))
        self.assertEqual(pixel_hash(self.photo), before)

    def test_write_tags_batch_reports_files_it_could_not_embed(self):
        """A file that cannot carry embedded tags is reported, not silently dropped."""
        self._exiftool_or_skip()
        self._seed_every_category()

        import time
        from photo_index import scan_library
        unsupported = self.library / "note.txt"
        unsupported.write_text("not a photo", encoding="utf-8")
        png = self.library / "2026-08-10 diagram.png"
        Image.new("RGB", (16, 16), (200, 40, 40)).save(png)
        scan_library(self.library, self.database)

        con = sqlite3.connect(self.database)
        png_id = int(con.execute(
            "SELECT id FROM assets WHERE filename=?", (png.name,)
        ).fetchone()[0])
        tag_id = int(con.execute("INSERT INTO tags(name) VALUES ('diagram')").lastrowid)
        con.execute(
            "INSERT INTO asset_tags(asset_id, tag_id, source, confidence) VALUES (?, ?, 'semantic_auto', 0.6)",
            (png_id, tag_id),
        )
        con.commit()
        con.close()

        self.json_response(self.post(
            "/api/write-tags/batch", {"scope": "all", "write_mode": "embedded"},
        ))

        deadline = time.monotonic() + 60
        job = {}
        while time.monotonic() < deadline:
            job = dict(self.photo_search.SearchHandler.write_tags_job)
            if job.get("state") in ("complete", "cancelled", "error"):
                break
            time.sleep(0.2)
        self.assertEqual(job.get("state"), "complete", f"batch did not finish: {job}")

        fell_back_paths = [item["path"] for item in job["fell_back"]]
        self.assertIn(png.name, fell_back_paths,
                      "a PNG cannot carry embedded tags, so the run must say so by name")
        for item in job["fell_back"]:
            self.assertTrue(item.get("error"), "every reported file needs a reason")

    def test_write_records_completion_so_an_interrupted_write_is_visible(self):
        self._exiftool_or_skip()
        self._seed_every_category()

        self.json_response(self.post(
            "/api/write-tags", {"id": self.asset_id, "write_mode": "embedded"},
        ))

        con = sqlite3.connect(self.database)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT completed_at FROM metadata_publications WHERE asset_id=?",
            (self.asset_id,),
        ).fetchone()
        con.close()
        self.assertIsNotNone(row["completed_at"],
                             "a finished write must record that it finished")

        self.assertEqual(
            self.photo_search.interrupted_publications(self.database), [],
            "a write that completed must not be reported as interrupted",
        )

    def test_a_write_that_never_completed_is_reported(self):
        con = sqlite3.connect(self.database)
        con.execute(
            """INSERT INTO metadata_publications
               (asset_id, relative_path, backup_path, before_json, after_json,
                operation, published_at, completed_at)
               VALUES (?, ?, ?, '{}', '{}', 'write_tags', datetime('now'), NULL)""",
            (self.asset_id, self.photo.name, str(self.photo)),
        )
        con.commit()
        con.close()

        interrupted = self.photo_search.interrupted_publications(self.database)
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0]["path"], self.photo.name)
        self.assertTrue(interrupted[0]["backup_exists"])

    def test_a_failed_write_leaves_nothing_recorded(self):
        """A rolled-back write must not linger as an interrupted one."""
        self._exiftool_or_skip()
        self._seed_every_category()

        original = self.photo.read_bytes()
        with patch.object(self.photo_search, "_run_exiftool",
                          side_effect=ValueError("exiftool exploded")):
            with self.assertRaises(Exception):
                self.json_response(self.post(
                    "/api/write-tags", {"id": self.asset_id, "write_mode": "embedded"},
                ))

        self.assertEqual(self.photo.read_bytes(), original,
                         "the photo must be put back exactly as it was")
        self.assertEqual(
            self.photo_search.interrupted_publications(self.database), [],
            "a write that was rolled back is not an interrupted write",
        )

    def test_backup_verification_uses_contents_not_size(self):
        """A same-size but different copy must be rejected."""
        self._exiftool_or_skip()
        self._seed_every_category()

        real_copy = shutil.copy2

        def corrupting_copy(source, destination, *args, **kwargs):
            result = real_copy(source, destination, *args, **kwargs)
            target = Path(destination)
            if ".before-write-tags-" in target.name:
                size = target.stat().st_size
                target.write_bytes(b"\0" * size)
            return result

        with patch.object(self.photo_search.shutil, "copy2", side_effect=corrupting_copy):
            with self.assertRaises(Exception):
                self.json_response(self.post(
                    "/api/write-tags", {"id": self.asset_id, "write_mode": "embedded"},
                ))

    def test_bulk_write_refuses_when_there_is_no_room_for_copies(self):
        self._seed_every_category()

        impossible = {"ok": False, "free_bytes": 1000, "required_bytes": 1 << 50,
                      "margin_bytes": 0, "shortfall_bytes": 1 << 50}
        with patch.object(self.photo_search.metadata_backups, "space_check",
                          return_value=impossible):
            import urllib.error
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post("/api/write-tags/batch", {"scope": "all", "write_mode": "embedded"})
            body = json.loads(caught.exception.read())

        self.assertIn("Not enough disk space", body["error"])
        self.assertIn("sidecar", body["error"],
                      "the refusal should point at the option that needs no copies")
        self.assertEqual(
            self.photo_search.SearchHandler.write_tags_job.get("state"), "idle",
            "nothing should have started",
        )

    def test_online_only_photos_are_refused_with_a_plain_reason(self):
        self._exiftool_or_skip()
        self._seed_every_category()

        with patch.object(self.photo_search, "is_cloud_placeholder", return_value=True):
            import urllib.error
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post("/api/write-tags", {"id": self.asset_id, "write_mode": "embedded"})
            body = json.loads(caught.exception.read())

        self.assertIn("online only", body["error"])

    def test_asset_detail_includes_auto_tags_and_write_mode(self):
        import urllib.request
        con = sqlite3.connect(self.database)
        tag_id = int(con.execute("INSERT INTO tags(name) VALUES ('mountain')").lastrowid)
        con.execute(
            "INSERT INTO asset_tags(asset_id, tag_id, source, confidence) VALUES (?, ?, 'semantic_auto', 0.85)",
            (self.asset_id, tag_id),
        )
        con.commit()
        con.close()

        response = urllib.request.urlopen(
            f"{self.base_url}/api/asset?id={self.asset_id}", timeout=10
        )
        detail = self.json_response(response)
        self.assertIn("auto_tags", detail)
        self.assertEqual(len(detail["auto_tags"]), 1)
        self.assertEqual(detail["auto_tags"][0]["name"], "mountain")
        self.assertAlmostEqual(detail["auto_tags"][0]["confidence"], 0.85, places=2)
        self.assertIn("write_mode", detail)

    def test_classify_status_endpoint(self):
        import urllib.request
        response = urllib.request.urlopen(
            f"{self.base_url}/api/classify/status", timeout=10
        )
        status = self.json_response(response)
        self.assertEqual(status["state"], "idle")
