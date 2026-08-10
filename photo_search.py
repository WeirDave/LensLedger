#!/usr/bin/env python3
"""LensLedger localhost-only photo review and metadata staging interface."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app_paths import (
    backup_root, database_backup_root, review_bin_root,
)
from face_learning import learn as learn_faces
from library_config import (
    choose_library_folder, library_db_path, load_library_config, load_library_state,
    save_library_state, suggested_library_roots,
)
from lensledger_updater import check_for_update, is_managed_install, managed_install_root, updates_root
from metadata_reader import pixel_hash as _pixel_hash, read_embedded_metadata
from photo_index import (
    SCHEMA_VERSION, SQLITE_BUSY_TIMEOUT_MS, connect, extract_xmp_keywords, ocr_assets, rebuild_search_row, scan_library,
    set_source_tags, sync_person_tags, utc_now,
)
from product import APP_NAME, APP_TAGLINE, APP_VERSION
from semantic_index import (
    build_index as build_semantic_index,
    search as semantic_search,
    status as semantic_status,
)


TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
LIKE_ESCAPE_RE = re.compile(r"([\\%_])")
PAGE_SIZE = 250
PUBLISHABLE_EXTENSIONS = {".jpg", ".jpeg"}
STATIC_ROOT = Path(__file__).parent / "static"
STATIC_NAME_RE = re.compile(r"[a-z][a-z0-9-]*\.(?:css|js)")
STATIC_CONTENT_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


def static_url(name: str) -> str:
    """A static asset URL that changes on every release, so the browser cache
    is safely bypassed after an update without needing any other cache-busting."""
    return f"/static/{name}?v={APP_VERSION}"


def bootstrap_attr(values: dict[str, object]) -> str:
    """A data-ll="..." <body> attribute that hands page data to an externally-
    served JS file, which reads it with JSON.parse(document.body.dataset.ll).

    Kept to one JSON.stringify per page instead of interpolating individual
    values into hand-written JS, so there is exactly one place per page where
    server data crosses the HTML boundary -- and one place to escape it. Using
    an attribute rather than an inline <script> means no page needs inline
    script content, so the CSP below can omit 'unsafe-inline' for scripts.
    """
    return f'data-ll="{html.escape(json.dumps(values), quote=True)}"'


EXIFTOOL_PATH = Path(__file__).parent / "tools" / "ExifTool" / "ExifTool.exe"
BACKUP_ROOT = backup_root()


def clean_tag(value: str) -> str:
    return " ".join(value.strip().split())[:120]


def like_pattern(value: str) -> str:
    """Build a SQL LIKE '%...%' pattern that matches a value literally.

    Without escaping, a user typing "%" or "_" gets surprisingly broad
    matches instead of searching for those literal characters. Pair with
    ``LIKE ? ESCAPE '\\'`` in the query.
    """
    return f"%{LIKE_ESCAPE_RE.sub(r'\\\1', value)}%"


def search_everything_scope(
    con: sqlite3.Connection, base_where: str, base_values: list[object],
    tokens: list[str], query: str, sort: str, order: str, page_number: int,
) -> tuple[list[dict], int]:
    """Fetch one page of scope='all' search results (base_where has no
    leading WHERE and applies only the non-search filters: review-bin state,
    selected date).

    A photo can match two independent ways here: its indexed text/tags, or a
    confirmed person's name/alias. FTS5 refuses to use MATCH at all -- not
    even to just list ids, let alone rank them -- once it sits inside an OR
    with anything else in the same WHERE clause. The previous version of
    this query did exactly that (`search_fts MATCH ? OR EXISTS(...)`), which
    meant every scope='all' search with a real query raised
    "unable to use function MATCH in the requested context" and silently
    returned zero results. Querying each way independently and combining
    with UNION sidesteps the restriction entirely, and as a side benefit
    gives the FTS branch a real bm25 rank to sort by.
    """
    match_expr = " AND ".join(f'"{token}"' for token in tokens)
    person_pattern = like_pattern(query)
    ids_sql = f"""
        SELECT a.id, a.capture_date FROM search_fts JOIN assets a ON a.id=search_fts.asset_id
        WHERE search_fts MATCH ? AND {base_where}
        UNION
        SELECT a.id, a.capture_date FROM assets a
        WHERE {base_where} AND EXISTS (
            SELECT 1 FROM asset_people ap JOIN people p ON p.id=ap.person_id
            LEFT JOIN person_aliases pa ON pa.person_id=p.id
            WHERE ap.asset_id=a.id AND ap.state='confirmed'
              AND (p.name LIKE ? ESCAPE '\\' OR pa.alias LIKE ? ESCAPE '\\')
        )
    """
    ids_values = [match_expr, *base_values, *base_values, person_pattern, person_pattern]
    matches = [(int(row[0]), row[1]) for row in con.execute(ids_sql, ids_values)]
    total = len(matches)
    if not matches:
        return [], 0

    if sort != "relevance":
        matching_ids = [asset_id for asset_id, _capture_date in matches]
        placeholders = ",".join("?" for _ in matching_ids)
        rows = con.execute(
            f"""SELECT id,filename,folder,capture_date,media_type FROM assets AS a
                WHERE id IN ({placeholders}) ORDER BY {order} LIMIT ? OFFSET ?""",
            [*matching_ids, PAGE_SIZE, (page_number - 1) * PAGE_SIZE],
        ).fetchall()
        return [dict(row) for row in rows], total

    dates_by_id = dict(matches)
    scores: dict[int, float] = {}
    placeholders = ",".join("?" for _ in matches)
    for asset_id, rank in con.execute(
        f"SELECT search_fts.asset_id, search_fts.rank FROM search_fts "
        f"WHERE search_fts MATCH ? AND search_fts.asset_id IN ({placeholders})",
        [match_expr, *dates_by_id],
    ):
        scores[int(asset_id)] = float(rank)
    # Best FTS matches first; ids that matched only via the person-name
    # clause (no FTS hit at all) fall back to newest-first.
    ranked_ids = sorted((aid for aid in dates_by_id if aid in scores), key=scores.__getitem__)
    unranked_ids = sorted(
        (aid for aid in dates_by_id if aid not in scores),
        key=lambda aid: dates_by_id[aid] or "",
        reverse=True,
    )
    page_ids = (ranked_ids + unranked_ids)[(page_number - 1) * PAGE_SIZE: page_number * PAGE_SIZE]
    if not page_ids:
        return [], total
    placeholders = ",".join("?" for _ in page_ids)
    found = {
        int(row["id"]): dict(row) for row in con.execute(
            f"SELECT id,filename,folder,capture_date,media_type FROM assets WHERE id IN ({placeholders})",
            page_ids,
        )
    }
    items = [found[aid] for aid in page_ids if aid in found]
    return items, total


def split_tags(value: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in value.split(";"):
        tag = clean_tag(part)
        if tag and tag.casefold() not in seen:
            result.append(tag)
            seen.add(tag.casefold())
    return result


def _run_exiftool(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    if not EXIFTOOL_PATH.is_file():
        raise ValueError("The metadata publishing tool is not installed")
    result = subprocess.run(
        [str(EXIFTOOL_PATH), *arguments], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, check=False,
    )
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or "Metadata publishing failed").strip())
    return result


def _exiftool_values(path: Path) -> dict[str, object]:
    result = _run_exiftool([
        "-j", "-G1", "-struct", "-EXIF:ImageDescription", "-XMP-dc:Description",
        "-IPTC:Caption-Abstract", "-XMP-dc:Title", "-IPTC:ObjectName",
        "-XMP-photoshop:Headline", "-XMP-dc:Subject", "-IPTC:Keywords",
        "-XMP-microsoft:LastKeywordXMP", "-XMP-iptcExt:PersonInImage", str(path),
    ])
    rows = json.loads(result.stdout)
    return rows[0] if rows else {}


def create_verified_database_backup(db_path: Path) -> Path:
    """Create an SQLite online backup and refuse to keep an invalid copy."""
    source_path = db_path.resolve()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = (database_backup_root() / f"{source_path.stem}-{stamp}.sqlite3").resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.relative_to(database_backup_root().resolve())
    source = sqlite3.connect(source_path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000)
    target = sqlite3.connect(destination, timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000)
    try:
        source.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        target.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        source.backup(target)
    finally:
        target.close()
        source.close()
    check = sqlite3.connect(destination, timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000)
    try:
        check.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        integrity = str(check.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        check.close()
    if integrity != "ok":
        destination.unlink(missing_ok=True)
        raise ValueError("database backup verification failed")
    return destination


class _LibraryAttr:
    """Read library_root/db_path from one atomically-swapped pair.

    A request reads both as a pair (opening the catalog, then checking a
    path against the library root it came from). Storing them separately let
    a library switch update one and then the other, so a concurrent request
    could briefly observe a new root paired with the old database, or vice
    versa. Reading through this descriptor from a single `current_library`
    tuple means every reader sees either the fully-old pair or the fully-new
    one, whether accessed as `self.library_root` or `SearchHandler.db_path`.
    """

    def __init__(self, index: int):
        self.index = index

    def __get__(self, obj, objtype=None) -> Path:
        owner = objtype if obj is None else type(obj)
        return owner.current_library[self.index]


class SearchHandler(BaseHTTPRequestHandler):
    current_library: tuple[Path, Path]
    library_root = _LibraryAttr(0)
    db_path = _LibraryAttr(1)
    csrf_token: str
    library_lock = threading.Lock()
    library_job: dict[str, object] = {"state": "idle", "message": ""}
    library_cancel = threading.Event()
    ocr_lock = threading.Lock()
    ocr_job: dict[str, object] = {"state": "idle", "message": ""}
    ocr_cancel = threading.Event()
    semantic_lock = threading.Lock()
    semantic_job: dict[str, object] = {"state": "idle", "message": ""}
    semantic_cancel = threading.Event()
    people_merge_lock = threading.Lock()
    update_lock = threading.Lock()
    update_job: dict[str, object] = {"state": "idle", "message": "Checking has not started."}

    def db(self):
        return connect(self.db_path)

    def send_bytes(self, data: bytes, content_type: str, status: int = 200, cache: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        # No inline <script> or <style> anywhere in the app -- page data crosses
        # into JS through a data-ll="..." attribute instead -- so this omits
        # 'unsafe-inline' entirely rather than weakening it for convenience.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, body: str, status: int = 200):
        self.send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status)

    def send_json(self, value, status: int = 200):
        self.send_bytes(json.dumps(value).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(url.query)
        if url.path.startswith("/static/"):
            return self.serve_static(url.path.removeprefix("/static/"))
        if url.path == "/logo.png":
            return self.serve_logo()
        if url.path == "/world-map.svg":
            return self.serve_world_map()
        if url.path == "/media":
            return self.serve_media(params)
        if url.path == "/api/asset":
            return self.asset_detail(params)
        if url.path == "/api/trash":
            return self.trash_history()
        if url.path == "/api/library/status":
            return self.library_status()
        if url.path == "/api/library/options":
            return self.library_options()
        if url.path == "/api/library/items":
            return self.library_items_api(params)
        if url.path == "/api/map/points":
            return self.map_points()
        if url.path == "/api/diagnostics":
            return self.diagnostics()
        if url.path == "/api/ocr/status":
            return self.ocr_status()
        if url.path == "/api/semantic/status":
            return self.semantic_job_status()
        if url.path == "/api/update/status":
            return self.update_status()
        if url.path == "/api/people/review/queue":
            return self.people_review_queue(params)
        if url.path == "/people-review":
            return self.people_review_page(params)
        if url.path == "/map":
            return self.map_page()
        if url.path != "/":
            return self.send_error(404)
        return self.viewer_page(params)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 100_000:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not secrets.compare_digest(str(body.get("csrf", "")), self.csrf_token):
                return self.send_json({"error": "invalid request token"}, 403)
            route = urllib.parse.urlparse(self.path).path
            if route == "/api/subject":
                return self.update_subject(body)
            if route == "/api/tag/add":
                return self.add_tag(body)
            if route == "/api/folder-tag/add":
                return self.add_folder_tag(body)
            if route == "/api/tag/remove":
                return self.remove_tag(body)
            if route == "/api/tag/restore":
                return self.restore_tag(body)
            if route == "/api/person/add":
                return self.add_person(body)
            if route == "/api/person/state":
                return self.set_person_state(body)
            if route == "/api/person/aliases":
                return self.set_person_aliases(body)
            if route == "/api/person/names":
                return self.set_person_names(body)
            if route == "/api/person/merge":
                return self.merge_people(body)
            if route == "/api/people/review/decision":
                return self.people_review_decision(body)
            if route == "/api/people/review/batch":
                return self.people_review_batch_decision(body)
            if route == "/api/people/review/undo":
                return self.undo_people_review(body)
            if route == "/api/people/review/batch-undo":
                return self.undo_people_review_batch(body)
            if route == "/api/people/learn":
                return self.learn_people(body)
            if route == "/api/people/review/defer":
                return self.defer_people_review(body)
            if route == "/api/review-bin":
                return self.move_to_review_bin(body)
            if route == "/api/review-bin/restore":
                return self.restore_from_review_bin(body)
            if route == "/api/publish/preview":
                return self.preview_publish(body)
            if route == "/api/publish":
                return self.publish_metadata(body)
            if route == "/api/publish/restore":
                return self.restore_published_metadata(body)
            if route == "/api/library/browse":
                return self.browse_library(body)
            if route == "/api/library/open":
                return self.open_library(body)
            if route == "/api/library/cancel":
                return self.cancel_library_scan(body)
            if route == "/api/ocr/start":
                return self.start_ocr(body)
            if route == "/api/ocr/cancel":
                return self.cancel_ocr(body)
            if route == "/api/database/backup":
                return self.backup_database(body)
            if route == "/api/semantic/start":
                return self.start_semantic_index(body)
            if route == "/api/semantic/cancel":
                return self.cancel_semantic_index(body)
            if route == "/api/update/check":
                return self.check_update(body)
            if route == "/api/update/install":
                return self.install_update(body)
            return self.send_json({"error": "not found"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                return self.send_json({
                    "error": "The catalog is busy with another LensLedger change. No names were merged; wait a moment and try again.",
                }, 409)
            return self.send_json({"error": str(exc)}, 500)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, 500)

    def get_active_asset(self, con: sqlite3.Connection, asset_id: int):
        row = con.execute(
            "SELECT * FROM assets WHERE id=? AND in_review_bin=0", (asset_id,)
        ).fetchone()
        if not row:
            raise ValueError("photo is no longer available")
        return row

    def onboarding_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Set up {APP_NAME}</title><link rel="icon" href="/logo.png"><link rel="stylesheet" href="{static_url('onboarding.css')}">
</head><body {bootstrap_attr({"csrf": self.csrf_token})}><main class="shell"><header class="brand"><img src="/logo.png" alt=""><div><h1>{APP_NAME}</h1><p>{APP_TAGLINE}</p></div><span class="version">v{APP_VERSION}</span></header><section class="card">
<div class="intro"><h2>Let’s find your photo library</h2><p>Choose a folder that contains photos or videos. LensLedger will build a private, searchable inventory without moving, renaming, uploading, or changing your files.</p></div>
<div class="steps"><div class="step"><strong>1 · Discover</strong><span>Record file locations, types, dates, and locally available metadata.</span></div><div class="step"><strong>2 · Review</strong><span>See exactly what was found, including cloud files that are not downloaded.</span></div><div class="step"><strong>3 · Enrich</strong><span>Add subjects, people, OCR, and approved metadata at your pace.</span></div></div>
<section class="chooser"><h3>Choose your first library</h3><p>You can add and switch between more libraries later. Start with the folder that best represents one photo collection.</p><div class="suggestions" id="suggestions"></div><div class="path-row"><input id="libraryPath" aria-label="Photo library folder" placeholder="C:\\Users\\you\\Pictures"><button type="button" class="secondary" id="browse">Browse…</button></div><div class="actions"><span class="privacy">🔒 The index stays on this computer. Cloud placeholders are counted without forcing a download.</span><span class="spacer"></span><button type="button" id="start">Build my library</button></div></section>
<section class="progress-panel" id="progressPanel" aria-live="polite"><div class="progress-head"><div><h3 id="progressTitle">Building your library</h3><p id="progressMessage">Preparing scan…</p></div><span class="spacer"></span><button type="button" class="danger" id="cancel">Pause scan</button></div><div class="bar"><span></span></div><div class="metrics"><div class="metric"><strong id="scanned">0</strong><span>discovered</span></div><div class="metric"><strong id="changed">0</strong><span>indexed</span></div><div class="metric"><strong id="unchanged">0</strong><span>unchanged</span></div><div class="metric"><strong id="placeholders">0</strong><span>cloud-only</span></div><div class="metric"><strong id="errors">0</strong><span>errors</span></div></div><div class="complete-grid" id="completeGrid"></div><div class="completion-actions"><button type="button" id="enterLibrary">Open my library</button></div></section>
</section></main><script src="{static_url('onboarding.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def map_points(self):
        with self.db() as con:
            located = int(con.execute(
                """SELECT COUNT(*) FROM assets WHERE in_review_bin=0
                   AND gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL"""
            ).fetchone()[0])
            pending = int(con.execute(
                "SELECT COUNT(*) FROM assets WHERE in_review_bin=0 AND location_scanned=0"
            ).fetchone()[0])
            rows = con.execute(
                """SELECT MIN(id) AS asset_id, AVG(gps_latitude) AS latitude,
                          AVG(gps_longitude) AS longitude, COUNT(*) AS photo_count,
                          MIN(capture_date) AS first_date, MAX(capture_date) AS last_date,
                          MIN(filename) AS filename
                   FROM assets
                   WHERE in_review_bin=0 AND gps_latitude IS NOT NULL
                         AND gps_longitude IS NOT NULL
                   GROUP BY ROUND(gps_latitude, 1), ROUND(gps_longitude, 1)
                   ORDER BY photo_count DESC, first_date DESC
                   LIMIT 5000"""
            ).fetchall()
        self.send_json({
            "located": located,
            "pending": pending,
            "clusters": [dict(row) for row in rows],
        })

    def map_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Photo map — {APP_NAME}</title><link rel="icon" href="/logo.png"><link rel="stylesheet" href="{static_url('map.css')}">
</head><body><header><img src="/logo.png" alt=""><div><h1>Photo map</h1><p>Embedded locations from the current library · read-only and kept local</p></div><span class="spacer"></span><span class="count" id="count">Loading locations…</span><a class="button" href="/">Back to library</a></header>
<main class="map-shell" id="viewport"><div id="world"></div><div class="controls"><button type="button" id="zoomIn" aria-label="Zoom in">+</button><button type="button" id="zoomOut" aria-label="Zoom out">−</button><button type="button" id="reset" aria-label="Reset map">⌂</button></div><div class="legend"><strong>Photo locations</strong>Scroll to zoom and drag to pan. Nearby coordinates are grouped; select a marker to open its representative photo.</div><aside class="details" id="details"><img id="preview" alt="Representative photo from this location"><div class="details-body"><h2 id="placeTitle"></h2><p id="placeDates"></p><p id="placeCoords"></p><div class="details-actions"><a class="button" id="openPhoto">Open photo</a><button type="button" id="closeDetails">Close</button></div></div></aside><section class="empty" id="empty"><div><h2>No mapped photos yet</h2><p id="emptyText">Run an incremental library scan to collect embedded GPS coordinates. LensLedger reads them locally and never writes location data back to your files.</p><a class="button" href="/">Return to library</a></div></section></main>
<script src="{static_url('map.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def parse_photo_query(self, params):
        """Parse q/date/scope/person/sort/page params into validated values,
        applying the same defaults the full page and the pagination API
        both need."""
        query = params.get("q", [""])[0].strip()
        selected_date = params.get("date", [""])[0]
        scope = params.get("scope", ["all"])[0]
        requested_person = params.get("person", [""])[0]
        person_id = int(requested_person) if requested_person.isdigit() else None
        if scope not in {"image", "context", "people", "semantic", "all"}:
            scope = "image"
        default_sort = "relevance" if (query and scope == "all") else ("oldest" if selected_date else "newest")
        sort = params.get("sort", [default_sort])[0]
        if sort not in {"newest", "oldest", "name", "relevance"}:
            sort = default_sort
        try:
            page_number = max(1, int(params.get("page", ["1"])[0]))
        except ValueError:
            page_number = 1
        return query, selected_date, scope, person_id, sort, page_number

    def fetch_matching_photos(self, con, query, selected_date, scope, person_id, sort, page_number):
        """Fetch one page of matching photos for the image/context/all/
        semantic scopes, or a specific person's confirmed photos.

        Shared by the full page render and the JSON pagination endpoint the
        filmstrip calls while scrolling, so paging can never silently drift
        out of sync with what the initial page showed. (The people-gallery
        listing -- scope='people' with no person selected -- returns person
        cards rather than photos and is handled separately by the caller.)

        Returns (items, total, corrected_selected_date): an unparsable date
        is dropped rather than rejected outright, same as the full page.
        """
        clauses = ["a.in_review_bin=0"]
        values: list[object] = []
        if scope == "people" and person_id:
            clauses.append("""EXISTS (
                SELECT 1 FROM asset_people ap
                WHERE ap.asset_id=a.id AND ap.person_id=? AND ap.state='confirmed'
            )""")
            values.append(person_id)
        if selected_date:
            try:
                dt.date.fromisoformat(selected_date)
                clauses.append("a.capture_date=?")
                values.append(selected_date)
            except ValueError:
                selected_date = ""
        tokens = TOKEN_RE.findall(query)
        # scope=='all' with a query is handled separately by
        # search_everything_scope() below: FTS5 refuses to compute rank (or
        # even just evaluate MATCH) once it sits inside an OR with anything
        # else in the same WHERE, which a naive `search_fts MATCH ? OR
        # EXISTS(person match)` clause here would do. `clauses`/`values`
        # stay as only the base filters (review-bin state, selected date)
        # for that case, and search_everything_scope builds its own
        # MATCH/EXISTS queries independently.
        if tokens and scope not in {"all", "people", "semantic"}:
            sources = "('subject','asset_rule','embedded_xmp','person')" if scope == "image" else "('folder_rule')"
            for token in tokens:
                pattern = like_pattern(token)
                tag_clause = f"""EXISTS (
                    SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                    WHERE at.asset_id=a.id AND at.source IN {sources} AND t.name LIKE ? ESCAPE '\\'
                      AND NOT EXISTS (
                          SELECT 1 FROM asset_tag_exclusions e
                          WHERE e.relative_path=a.relative_path AND e.tag=t.name
                      )
                )"""
                if scope == "image":
                    clauses.append(f"({tag_clause} OR EXISTS (SELECT 1 FROM text_data x WHERE x.asset_id=a.id AND (x.ocr_text LIKE ? ESCAPE '\\' OR x.caption LIKE ? ESCAPE '\\')))")
                    values.extend([pattern, pattern, pattern])
                else:
                    clauses.append(f"({tag_clause} OR a.folder LIKE ? ESCAPE '\\')")
                    values.extend([pattern, pattern])

        base_where = " AND ".join(clauses)
        where = "WHERE " + base_where
        order = {
            "newest": "a.capture_date DESC, a.relative_path DESC",
            "oldest": "a.capture_date ASC, a.relative_path ASC",
            "name": "a.filename COLLATE NOCASE ASC, a.relative_path ASC",
        }.get(sort, "a.capture_date DESC, a.relative_path DESC")

        if scope == "semantic":
            if not query:
                return [], 0, selected_date
            ranked = semantic_search(type(self).db_path, query, PAGE_SIZE, (page_number - 1) * PAGE_SIZE)
            ranked_ids = [asset_id for asset_id, _score in ranked]
            total = int(semantic_status(type(self).db_path)["indexed"])
            items: list[dict] = []
            if ranked_ids:
                placeholders = ",".join("?" for _ in ranked_ids)
                found = con.execute(
                    f"""SELECT id,filename,folder,capture_date,media_type FROM assets
                        WHERE in_review_bin=0 AND id IN ({placeholders})""",
                    ranked_ids,
                ).fetchall()
                by_id = {int(row["id"]): dict(row) for row in found}
                items = [by_id[asset_id] for asset_id in ranked_ids if asset_id in by_id]
            return items, total, selected_date

        if scope == "all" and tokens:
            items, total = search_everything_scope(con, base_where, values, tokens, query, sort, order, page_number)
            return items, total, selected_date

        total = int(con.execute(
            f"SELECT COUNT(*) FROM search_fts JOIN assets a ON a.id=search_fts.asset_id {where}", values
        ).fetchone()[0])
        rows = con.execute(
            f"""SELECT a.id,a.filename,a.folder,a.capture_date,a.media_type
                FROM search_fts JOIN assets a ON a.id=search_fts.asset_id
                {where} ORDER BY {order} LIMIT ? OFFSET ?""",
            values + [PAGE_SIZE, (page_number - 1) * PAGE_SIZE],
        ).fetchall()
        return [dict(row) for row in rows], total, selected_date

    def library_items_api(self, params):
        """JSON pagination endpoint: the filmstrip calls this while
        scrolling to fetch additional pages after the first, which is
        rendered directly into the full page."""
        query, selected_date, scope, person_id, sort, page_number = self.parse_photo_query(params)
        if scope == "people" and not person_id:
            return self.send_json({"error": "this scope has no photo pages to fetch"}, 400)
        try:
            with self.db() as con:
                if scope == "people" and person_id:
                    exists = con.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone()
                    if not exists:
                        raise ValueError("That person is no longer available")
                items, total, selected_date = self.fetch_matching_photos(
                    con, query, selected_date, scope, person_id, sort, page_number,
                )
        except (sqlite3.Error, RuntimeError, ValueError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        has_more = page_number * PAGE_SIZE < total
        self.send_json({"items": items, "total": total, "page": page_number, "has_more": has_more})

    def viewer_page(self, params):
        with self.db() as con:
            if int(con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0]) == 0:
                return self.onboarding_page()
        query, selected_date, scope, person_id, sort, page_number = self.parse_photo_query(params)

        error = ""
        rows: list[dict] = []
        people_cards = []
        people_directory = []
        selected_person_name = ""
        selected_person_stats: dict[str, int] = {}
        total = 0
        trash_count = 0
        review_count = 0
        try:
            with self.db() as con:
                trash_count = int(con.execute(
                    "SELECT COUNT(*) FROM review_bin WHERE restored_at IS NULL"
                ).fetchone()[0])
                review_count = int(con.execute(
                    "SELECT COUNT(*) FROM asset_people WHERE state='suggested'"
                ).fetchone()[0])
                if scope == "people" and not person_id:
                    people_query = like_pattern(query)
                    people_rows = con.execute(
                        """SELECT p.id,p.name,
                               (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0) confirmed_count,
                               (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0) suggested_count,
                               COALESCE(
                                 (SELECT ap.asset_id FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                  WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0 AND a.media_type='image'
                                  ORDER BY a.capture_date DESC,a.id DESC LIMIT 1),
                                 (SELECT ap.asset_id FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                  WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0 AND a.media_type='image'
                                  ORDER BY ap.confidence DESC,a.id DESC LIMIT 1)
                               ) representative_id
                            FROM people p
                            WHERE ?='' OR p.name LIKE ? ESCAPE '\\' OR EXISTS (
                                SELECT 1 FROM person_aliases pa WHERE pa.person_id=p.id AND pa.alias LIKE ? ESCAPE '\\'
                            ) ORDER BY p.name COLLATE NOCASE""",
                        (query, people_query, people_query),
                    ).fetchall()
                    for person in people_rows:
                        aliases = [row[0] for row in con.execute(
                            "SELECT alias FROM person_aliases WHERE person_id=? ORDER BY alias COLLATE NOCASE",
                            (person["id"],),
                        )]
                        people_cards.append(dict(person) | {"aliases": aliases})
                    directory_rows = con.execute(
                        """SELECT p.id,p.name,
                               (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0) confirmed_count,
                               (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0) suggested_count
                            FROM people p ORDER BY p.name COLLATE NOCASE"""
                    ).fetchall()
                    people_directory = [dict(row) for row in directory_rows]
                    total = len(people_cards)
                else:
                    if scope == "people" and person_id:
                        person_row = con.execute(
                            """SELECT p.name,
                                      (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                       WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0) confirmed_count,
                                      (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                       WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0) suggested_count,
                                      (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                       JOIN face_embeddings f ON f.id=ap.face_id
                                       WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0
                                         AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                                         AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL) localized_count
                               FROM people p WHERE p.id=?""",
                            (person_id,),
                        ).fetchone()
                        if not person_row:
                            person_id = None
                            raise ValueError("That person is no longer available")
                        selected_person_name = str(person_row["name"])
                        selected_person_stats = {
                            key: int(person_row[key]) for key in (
                                "confirmed_count", "suggested_count", "localized_count"
                            )
                        }
                    rows, total, selected_date = self.fetch_matching_photos(
                        con, query, selected_date, scope, person_id, sort, page_number,
                    )
        except (sqlite3.Error, RuntimeError, ValueError) as exc:
            error = str(exc)

        items = rows
        requested_id = params.get("selected", [""])[0]
        selected_id = int(requested_id) if requested_id.isdigit() and any(int(x["id"]) == int(requested_id) for x in items) else (int(items[0]["id"]) if items else None)
        first = (page_number - 1) * PAGE_SIZE + 1 if total else 0
        last = min(page_number * PAGE_SIZE, total)
        if scope == "people" and not person_id:
            view_label = "People"
        elif scope == "people" and selected_person_name:
            view_label = f"Photos of {selected_person_name}"
        elif scope == "semantic":
            view_label = f'Meaning: “{query}”' if query else "Meaning search"
        elif query:
            view_label = f'Search: “{query}”'
        elif selected_date:
            view_label = selected_date
        else:
            view_label = {
                "newest": "Newest photos",
                "oldest": "Oldest photos",
                "name": "Filename order",
            }.get(sort, "Photos")
        summary = f"{view_label} • {total:,}" if scope == "people" and not person_id else f"{view_label} • {first:,}–{last:,} of {total:,}"
        has_more = last < total

        scope_options = "".join(
            f'<option value="{value}"{" selected" if scope == value else ""}>{label}</option>'
            for value, label in (("image", "Visible image tags"), ("context", "Day/event context"), ("people", "People"), ("semantic", "Meaning (optional)"), ("all", "Everything"))
        )
        sort_options = "".join(
            f'<option value="{value}"{" selected" if sort == value else ""}>{label}</option>'
            for value, label in (
                ("relevance", "Best match"), ("oldest", "Oldest first"),
                ("newest", "Newest first"), ("name", "Filename A–Z"),
            )
            if value != "relevance" or (query and scope == "all")
        )
        gallery_mode = scope == "people" and not person_id
        gallery_cards = []
        for person in people_cards:
            aliases = person["aliases"]
            alias_text = ", ".join(aliases) if aliases else "No alternate names yet"
            picture = (
                f'<img src="/media?id={int(person["representative_id"])}" alt="Representative photo of {html.escape(person["name"], quote=True)}">'
                if person["representative_id"] else '<div class="person-placeholder">👤</div>'
            )
            person_url = "/?" + urllib.parse.urlencode({"scope": "people", "person": person["id"], "sort": "newest"})
            gallery_cards.append(
                f'<article class="person-card"><a href="{person_url}">{picture}'
                f'<div class="person-card-info"><strong>{html.escape(person["name"])}</strong>'
                f'<small>{int(person["confirmed_count"]):,} confirmed photo{"s" if int(person["confirmed_count"]) != 1 else ""}'
                f' · {int(person["suggested_count"]):,} to review</small>'
                f'<span>{html.escape(alias_text)}</span></div></a>'
                f'<button type="button" class="edit-aliases" data-person-id="{int(person["id"])}" '
                f'data-person-name="{html.escape(person["name"], quote=True)}" '
                f'data-aliases="{html.escape(json.dumps(aliases), quote=True)}">Edit names</button></article>'
            )
        people_gallery_html = (
            '<main class="people-browser"><div class="people-browser-head"><div><h2>People</h2>'
            '<p>Choose a person to see confirmed photos. Edit names to add nicknames, maiden names, or other aliases; separate each alternate name with a comma.</p></div>'
            f'<div class="people-head-actions"><span>{len(people_cards):,} {"person" if len(people_cards) == 1 else "people"}</span>'
            f'<button type="button" class="secondary" id="mergePeopleGallery"'
            f'{" disabled" if len(people_directory) < 2 else ""}>Merge people</button>'
            f'<button type="button" id="reviewPeopleGallery">Review people ({review_count:,})</button></div></div><section class="people-grid">'
            + ("".join(gallery_cards) if gallery_cards else '<p class="people-empty">No people match that name.</p>')
            + '</section></main>'
        ) if gallery_mode else ""
        people_result_bar = ""
        if scope == "people" and person_id:
            confirmed = selected_person_stats.get("confirmed_count", 0)
            suggested = selected_person_stats.get("suggested_count", 0)
            localized = selected_person_stats.get("localized_count", 0)
            people_result_bar = (
                f'<div class="people-result-bar"><a href="/?scope=people">← All people</a>'
                f'<strong>{html.escape(selected_person_name)}</strong>'
                f'<span>{confirmed:,} confirmed photo{"s" if confirmed != 1 else ""}</span>'
                + (f'<span>{localized:,} exact face box{"es" if localized != 1 else ""}</span>' if localized else "")
                + (f'<a class="button secondary" href="/people-review?person={person_id}">Review {suggested:,} possible match{"es" if suggested != 1 else ""}</a>' if suggested else "")
                + '</div>'
            )
        person_hidden = f'<input type="hidden" name="person" value="{person_id}">' if person_id else ""
        body_class = "people-gallery-mode" if gallery_mode else ""
        search_placeholder = (
            "Filter people by name or alias" if gallery_mode else
            "Describe a scene, object, or idea" if scope == "semantic" else
            "Subject, person, object, or visible text"
        )
        viewer_hidden_class = " viewer-hidden" if gallery_mode else ""
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{APP_NAME} — {APP_TAGLINE}</title><link rel="icon" href="/logo.png"><link rel="stylesheet" href="{static_url('viewer.css')}"></head><body class="{body_class}" {bootstrap_attr({
            "items": items, "personDirectory": people_directory, "csrf": self.csrf_token,
            "currentLibrary": str(self.library_root), "viewedPersonId": person_id,
            "selectedId": selected_id, "appVersion": APP_VERSION, "appTagline": APP_TAGLINE,
            "query": {"q": query, "date": selected_date, "scope": scope, "sort": sort, "person": person_id},
            "page": page_number, "hasMore": has_more,
        })}>
<header><div class="top"><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png" alt=""><div class="identity"><h1>{APP_NAME}</h1><div class="tagline">{APP_TAGLINE}</div></div><span class="version">v{APP_VERSION}</span><span class="summary">{html.escape(summary)} <span class="error-inline">{html.escape(error)}</span></span><button type="button" class="danger" id="moveToTrash">🗑 Move to Trash</button></div>
<form class="toolbar">{person_hidden}<label class="search-field">Search<input name="q" value="{html.escape(query, quote=True)}" placeholder="{search_placeholder}"></label><label class="scope-field">Search scope<select name="scope" id="scopePicker">{scope_options}</select></label><button type="button" class="secondary" id="previousDay">◀ Day</button><label class="date-field">Date<input type="date" name="date" id="datePicker" value="{html.escape(selected_date, quote=True)}"></label><button type="button" class="secondary" id="nextDay">Day ▶</button><label class="sort-field optional">Sort<select name="sort">{sort_options}</select></label><button>View</button></form></header>
<nav class="menu-panel" id="menuPanel"><a href="/people-review">👥 Review people ({review_count:,})</a><button type="button" data-panel="library">📁 Open photo library</button><a href="/map">🌍 Photo map</a><button type="button" data-panel="diagnostics">🩺 Library health &amp; OCR</button><button type="button" id="updateMenu" data-panel="update">⬆ Check for updates</button><button type="button" data-panel="trash">Trash &amp; restore ({trash_count})</button><button type="button" data-panel="guide">Quick guide</button><button type="button" data-panel="about">About LensLedger</button></nav>
{people_gallery_html}{people_result_bar}<main class="viewer{viewer_hidden_class}"><section class="upper"><div class="stage" id="stage"><div class="empty">Choose a photo from the filmstrip</div><button class="stage-nav" id="previousPhoto">‹</button><button class="stage-nav" id="nextPhoto">›</button></div><aside class="sidebar">
<div class="file-date" id="assetDate"></div><div class="file-name" id="assetName"></div><div class="folder" id="assetFolder"></div>
<div class="editor-compact"><strong>Metadata for this photo</strong><button type="button" class="info-button" data-help="editorHelp" aria-label="About metadata editing">ⓘ</button><div class="help-popover" id="editorHelp">Your edits stay in LensLedger until you click Preview &amp; publish for this photo. Nothing is written automatically.</div></div>
<div class="section"><div class="section-title"><h2>1. Primary subject</h2><button type="button" class="info-button" data-help="subjectHelp" aria-label="About primary subjects">ⓘ</button></div><div class="help-popover" id="subjectHelp">One short phrase naming the main thing in this photo. Stored as IPTC/XMP Title/Headline metadata.</div><div class="chips" id="subjectChip"></div><div class="row subject-editor"><input id="subjectInput" placeholder="Example: Formula 1 race cars"><button id="saveSubject">Save subject</button></div></div>
<div class="section"><div class="section-title"><h2>2. Photo tags</h2><button type="button" class="info-button" data-help="photoTagHelp" aria-label="About photo tags">ⓘ</button></div><div class="help-popover" id="photoTagHelp">Searchable people, objects, places, or activities visible in this photo. Use lowercase for ordinary things and activities; capitalize people, places, brands, and acronyms normally. Search ignores capitalization. These are stored as IPTC/XMP Keywords. Enter one or several separated by commas.</div><div class="chips" id="imageTags"></div><div class="row row-spaced"><input id="newTag" placeholder="Formula 1, race car, Honda, McLaren"><button id="addTag">Add tags</button></div></div>
<div class="section"><div class="section-title"><h2>3. People in this photo</h2><button type="button" class="info-button" data-help="peopleHelp" aria-label="About people in this photo">ⓘ</button></div><div class="help-popover" id="peopleHelp">Confirmed people become searchable and publish to the standard XMP People Shown field and Keywords. Face matches are suggestions only until you approve them.</div><div class="chips" id="confirmedPeople"></div><div id="suggestionLabel" class="suggestion-label">Face recognition suggestions — confirm or reject</div><div class="chips" id="suggestedPeople"></div><div class="row row-spaced"><input id="newPerson" list="peopleOptions" placeholder="Person's name"><datalist id="peopleOptions"></datalist><button id="addPerson">Add person</button></div></div>
<div class="section"><div class="section-title"><h2>4. Event / folder tags</h2><button type="button" class="info-button" data-help="eventTagHelp" aria-label="About event and folder tags">ⓘ</button></div><div class="help-popover" id="eventTagHelp">Reusable context applied to every photo in this folder. These also map to Keywords; use Event name and Location below when a more precise standard field applies.</div><div class="chips" id="contextTags"></div><div class="row row-spaced"><input id="newContextTag" placeholder="Car show, family vacation, birthday"><button id="addContextTag">Add to event</button></div></div>
<section class="publish-section" id="publishSection"><div class="section-title"><h2>Publish to this photo</h2><button type="button" class="info-button" data-help="publishHelp" aria-label="About publishing">ⓘ</button></div><div class="help-popover" id="publishHelp">Writes the primary subject as Title/Headline, confirmed people as People Shown, all visible Photo, People, and Event tags as Keywords, and the description below into this JPEG. A safety backup is made first and the picture pixels are verified afterward.</div><label>Photo description<textarea id="publishDescription" placeholder="Describe what is actually in this photo"></textarea></label><div class="publish-actions"><button type="button" id="previewPublish">Preview &amp; publish</button><button type="button" class="secondary" id="restorePublish">Restore last publish</button></div><p class="publish-note" id="publishNote">Only this selected photo will be changed, and only after you approve the preview.</p></section>
<details class="metadata-details" id="embeddedMetadata"><summary>Information already in this photo</summary><p class="metadata-note">Read directly from the photo. Nothing here changes the file.</p><div class="metadata-readout" id="metadataReadout"></div></details>
<div class="section" id="hiddenSection"><div class="section-title"><h2>Hidden tags</h2><button type="button" class="info-button" data-help="hiddenTagHelp" aria-label="About hidden tags">ⓘ</button></div><div class="help-popover" id="hiddenTagHelp">These tags are ignored only for this photo. Click one to restore it.</div><div class="chips" id="hiddenTags"></div></div><div class="status" id="status"></div>
</aside></section><section class="filmstrip" id="filmstrip"></section></main><div class="toast" id="toast"></div>
<div class="modal-backdrop" id="modalBackdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle"><div class="modal-head"><h2 id="modalTitle"></h2><button type="button" class="modal-close" id="modalClose">Close</button></div><div id="modalBody"></div></section></div>
<script src="{static_url('viewer.js')}" defer></script></body></html>"""
        self.send_html(page)

    def people_review_page(self, params):
        requested = params.get("person", [""])[0]
        initial_person_id = int(requested) if requested.isdigit() else None
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review people — {APP_NAME}</title><link rel="icon" href="/logo.png"><link rel="stylesheet" href="{static_url('people-review.css')}">
</head><body {bootstrap_attr({"csrf": self.csrf_token, "initialPersonId": initial_person_id})}>
<header><div class="topbar"><a class="button secondary" href="/">← Photo library</a><img src="/logo.png" alt=""><div class="identity"><strong>{APP_NAME}</strong><small>People review</small></div><span class="version">v{APP_VERSION}</span><span class="top-spacer"></span><span class="progress" id="globalProgress">Loading suggestions…</span><button type="button" class="secondary" id="learnMore">Find more matches</button></div></header>
<main><section id="reviewArea"><div class="empty"><div><h2>Loading people…</h2><p>Preparing the next group of photos.</p></div></div></section></main>
<div class="actionbar" id="actionbar" hidden><div class="actions"><button type="button" class="secondary" id="skipBatch">Skip these for now</button><button type="button" class="secondary" id="nextPerson">Next person</button><button type="button" class="secondary" id="deferPerson">Defer person 7 days</button><button type="button" class="secondary" id="undoBatch" disabled>Undo last batch</button><span class="spacer"></span><span><span class="selection-summary" id="selectionSummary"></span><span class="status" id="status"></span></span><button type="button" class="primary-action" id="confirmBatch">Save &amp; publish this group</button></div></div>
<div class="lightbox" id="lightbox"><div class="lightbox-head"><button type="button" class="secondary" id="closeLightbox">Close</button></div><div class="lightbox-photo" id="largePhotoBox"><img id="largePhoto" alt="Enlarged photo"></div></div>
<datalist id="peopleOptions"></datalist>
<script src="{static_url('people-review.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def asset_detail(self, params):
        try:
            asset_id = int(params.get("id", [""])[0])
            requested_person = params.get("person_id", [""])[0]
            focused_person_id = int(requested_person) if requested_person.isdigit() else None
            with self.db() as con:
                asset = self.get_active_asset(con, asset_id)
                source_path = self.library_root / Path(asset["relative_path"])
                embedded_keywords = extract_xmp_keywords(source_path)
                stored_keywords = [row[0] for row in con.execute(
                    """SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                       WHERE at.asset_id=? AND at.source='embedded_xmp' ORDER BY t.name""", (asset_id,)
                )]
                if [value.casefold() for value in stored_keywords] != [value.casefold() for value in embedded_keywords]:
                    set_source_tags(con, asset_id, "embedded_xmp", embedded_keywords)
                    rebuild_search_row(con, asset_id)
                annotation = con.execute(
                    "SELECT subject FROM asset_annotations WHERE relative_path=?", (asset["relative_path"],)
                ).fetchone()
                excluded = {row[0].casefold() for row in con.execute(
                    "SELECT tag FROM asset_tag_exclusions WHERE relative_path=?", (asset["relative_path"],)
                )}
                tag_rows = con.execute(
                    """SELECT t.name,at.source FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                       WHERE at.asset_id=? ORDER BY t.name""", (asset_id,)
                ).fetchall()
                context_keys = {
                    row["name"].casefold() for row in tag_rows
                    if row["source"] == "folder_rule" and row["name"].casefold() not in excluded
                }
                seen: set[tuple[str, bool]] = set()
                image_tags = []
                context_tags = []
                for row in tag_rows:
                    key = row["name"].casefold()
                    if key in excluded or row["source"] in {"subject", "person"}:
                        continue
                    if row["source"] != "folder_rule" and key in context_keys:
                        continue
                    target = context_tags if row["source"] == "folder_rule" else image_tags
                    if (key, target is context_tags) not in seen:
                        target.append({"name": row["name"], "source": row["source"]})
                        seen.add((key, target is context_tags))
                confirmed_people = [dict(row) for row in con.execute(
                    """SELECT p.name,ap.source FROM asset_people ap JOIN people p ON p.id=ap.person_id
                       WHERE ap.asset_id=? AND ap.state='confirmed' ORDER BY p.name""", (asset_id,)
                )]
                suggested_people = [dict(row) for row in con.execute(
                    """SELECT p.name,ap.confidence FROM asset_people ap JOIN people p ON p.id=ap.person_id
                       WHERE ap.asset_id=? AND ap.state='suggested'
                       ORDER BY ap.confidence DESC,p.name""", (asset_id,)
                )]
                focused_person_face = None
                if focused_person_id:
                    focused = con.execute(
                        """SELECT p.name,ap.face_id,f.box_left,f.box_top,f.box_right,f.box_bottom
                           FROM asset_people ap JOIN people p ON p.id=ap.person_id
                           LEFT JOIN face_embeddings f ON f.id=ap.face_id
                           WHERE ap.asset_id=? AND ap.person_id=? AND ap.state='confirmed'""",
                        (asset_id, focused_person_id),
                    ).fetchone()
                    focused_person_face = dict(focused) if focused else None
                confirmed_keys = {person["name"].casefold() for person in confirmed_people}
                image_tags = [tag for tag in image_tags if tag["name"].casefold() not in confirmed_keys]
                return self.send_json({
                    "id": asset_id, "filename": asset["filename"], "folder": asset["folder"],
                    "capture_date": asset["capture_date"], "media_type": asset["media_type"],
                    "subject": annotation["subject"] if annotation else "",
                    "image_tags": image_tags, "context_tags": context_tags,
                    "confirmed_people": confirmed_people, "suggested_people": suggested_people,
                    "focused_person_face": focused_person_face,
                    "people_options": [row[0] for row in con.execute(
                        "SELECT name FROM people UNION SELECT alias FROM person_aliases ORDER BY 1 COLLATE NOCASE"
                    )],
                    "embedded_metadata": read_embedded_metadata(source_path),
                    "publishable": asset["media_type"] == "image" and source_path.suffix.lower() in PUBLISHABLE_EXTENSIONS,
                    "can_restore_publish": bool(con.execute(
                        """SELECT 1 FROM metadata_publications WHERE relative_path=? AND restored_at IS NULL
                           ORDER BY id DESC LIMIT 1""", (asset["relative_path"],)
                    ).fetchone()),
                    "hidden_tags": [row[0] for row in con.execute(
                        "SELECT tag FROM asset_tag_exclusions WHERE relative_path=? ORDER BY tag", (asset["relative_path"],)
                    )],
                })
        except (ValueError, sqlite3.Error) as exc:
            return self.send_json({"error": str(exc)}, 404)

    def _publication_values(self, con: sqlite3.Connection, asset_id: int, description: str):
        asset = self.get_active_asset(con, asset_id)
        path = (self.library_root / Path(asset["relative_path"])).resolve()
        path.relative_to(self.library_root)
        if path.suffix.lower() not in PUBLISHABLE_EXTENSIONS or not path.is_file():
            raise ValueError("Publishing is currently available for JPEG photos")
        annotation = con.execute(
            "SELECT subject FROM asset_annotations WHERE relative_path=?", (asset["relative_path"],)
        ).fetchone()
        excluded = {row[0].casefold() for row in con.execute(
            "SELECT tag FROM asset_tag_exclusions WHERE relative_path=?", (asset["relative_path"],)
        )}
        keywords: list[str] = []
        seen: set[str] = set()
        for row in con.execute(
            """SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
               WHERE at.asset_id=? AND at.source<>'subject' ORDER BY t.name""", (asset_id,)
        ):
            key = row["name"].casefold()
            if key not in excluded and key not in seen:
                keywords.append(row["name"]); seen.add(key)
        subject = annotation["subject"] if annotation else ""
        people = [row[0] for row in con.execute(
            """SELECT p.name FROM asset_people ap JOIN people p ON p.id=ap.person_id
               WHERE ap.asset_id=? AND ap.state='confirmed' ORDER BY p.name""", (asset_id,)
        )]
        before_fields = _exiftool_values(path)
        before_fields.pop("SourceFile", None)
        after_fields = {
            "IFD0:ImageDescription": description,
            "XMP-dc:Description": description,
            "IPTC:Caption-Abstract": description,
            "XMP-dc:Title": subject,
            "IPTC:ObjectName": subject,
            "XMP-photoshop:Headline": subject,
            "XMP-dc:Subject": keywords,
            "IPTC:Keywords": keywords,
            "XMP-microsoft:LastKeywordXMP": keywords,
            "XMP-iptcExt:PersonInImage": people,
        }
        return asset, path, {
            "before": before_fields,
            "after": after_fields,
            "summary": {"subject": subject, "description": description, "keywords": keywords, "people": people},
        }

    @staticmethod
    def _metadata_values(value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    def _publish_people_metadata(self, con: sqlite3.Connection, asset_id: int,
                                 remove_names=(), review_action_id=None,
                                 operation="people"):
        asset = self.get_active_asset(con, int(asset_id))
        path = (self.library_root / Path(asset["relative_path"])).resolve()
        path.relative_to(self.library_root)
        if path.suffix.lower() not in PUBLISHABLE_EXTENSIONS or not path.is_file():
            raise ValueError(f"People metadata publishing requires a JPEG: {asset['filename']}")

        before = _exiftool_values(path)
        before.pop("SourceFile", None)
        excluded = {row[0].casefold() for row in con.execute(
            "SELECT tag FROM asset_tag_exclusions WHERE relative_path=?", (asset["relative_path"],)
        )}
        removed = {clean_tag(str(name)).casefold() for name in remove_names if clean_tag(str(name))}
        blocked = excluded | removed

        keywords: list[str] = []
        keyword_keys: set[str] = set()
        for field in ("XMP-dc:Subject", "IPTC:Keywords", "XMP-microsoft:LastKeywordXMP"):
            for value in self._metadata_values(before.get(field)):
                key = value.casefold()
                if key not in blocked and key not in keyword_keys:
                    keywords.append(value); keyword_keys.add(key)

        people: list[str] = []
        people_keys: set[str] = set()
        for value in self._metadata_values(before.get("XMP-iptcExt:PersonInImage")):
            key = value.casefold()
            if key not in blocked and key not in people_keys:
                people.append(value); people_keys.add(key)
        for row in con.execute(
            """SELECT p.name FROM asset_people ap JOIN people p ON p.id=ap.person_id
               WHERE ap.asset_id=? AND ap.state='confirmed' ORDER BY p.name COLLATE NOCASE""",
            (asset_id,),
        ):
            key = row["name"].casefold()
            if key not in people_keys:
                people.append(row["name"]); people_keys.add(key)
            if key not in keyword_keys:
                keywords.append(row["name"]); keyword_keys.add(key)

        after = {
            "XMP-dc:Subject": keywords,
            "IPTC:Keywords": keywords,
            "XMP-microsoft:LastKeywordXMP": keywords,
            "XMP-iptcExt:PersonInImage": people,
        }
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        relative = Path(asset["relative_path"])
        backup = (BACKUP_ROOT / relative.parent /
                  f"{relative.stem}.before-people-{timestamp}{relative.suffix}").resolve()
        backup.relative_to(BACKUP_ROOT.resolve())
        backup.parent.mkdir(parents=True, exist_ok=True)
        before_pixels = _pixel_hash(path)
        shutil.copy2(path, backup)
        if backup.stat().st_size != path.stat().st_size:
            backup.unlink(missing_ok=True)
            raise ValueError(f"The safety backup could not be verified for {asset['filename']}")

        clear_arguments = [
            "-overwrite_original", "-charset", "iptc=UTF8",
            "-XMP-dc:Subject=", "-IPTC:Keywords=", "-XMP-microsoft:LastKeywordXMP=",
            "-XMP-iptcExt:PersonInImage=", str(path),
        ]
        add_arguments = ["-overwrite_original", "-charset", "iptc=UTF8"]
        for keyword in keywords:
            add_arguments.extend([
                f"-XMP-dc:Subject+={keyword}", f"-IPTC:Keywords+={keyword}",
                f"-XMP-microsoft:LastKeywordXMP+={keyword}",
            ])
        for person in people:
            add_arguments.append(f"-XMP-iptcExt:PersonInImage+={person}")
        add_arguments.append(str(path))
        try:
            _run_exiftool(clear_arguments)
            if keywords or people:
                _run_exiftool(add_arguments)
            if _pixel_hash(path) != before_pixels:
                raise ValueError(f"Pixel verification failed for {asset['filename']}")
        except Exception:
            shutil.copy2(backup, path)
            raise

        stat = path.stat()
        con.execute(
            """INSERT INTO metadata_publications
               (asset_id,relative_path,backup_path,before_json,after_json,
                operation,review_action_id,published_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (asset_id, asset["relative_path"], str(backup), json.dumps(before),
             json.dumps(after), operation, review_action_id, utc_now()),
        )
        con.execute(
            "UPDATE assets SET size_bytes=?,mtime_ns=?,metadata_scanned=1,indexed_at=? WHERE id=?",
            (stat.st_size, stat.st_mtime_ns, utc_now(), asset_id),
        )
        set_source_tags(con, asset_id, "embedded_xmp", keywords)
        rebuild_search_row(con, asset_id)
        return {"path": path, "backup": backup, "filename": asset["filename"]}

    @staticmethod
    def _restore_people_batch(published):
        for item in reversed(published):
            try:
                shutil.copy2(item["backup"], item["path"])
                item["backup"].unlink(missing_ok=True)
            except OSError:
                pass

    def preview_publish(self, body):
        asset_id = int(body["id"])
        description = str(body.get("description", "")).strip()[:2000]
        with self.db() as con:
            _asset, _path, preview = self._publication_values(con, asset_id, description)
        self.send_json(preview)

    def publish_metadata(self, body):
        asset_id = int(body["id"])
        description = str(body.get("description", "")).strip()[:2000]
        with self.db() as con:
            asset, path, preview = self._publication_values(con, asset_id, description)
            if body.get("expected_after") != preview["after"]:
                raise ValueError("The metadata changed after the preview. Please preview it again.")
            timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            relative = Path(asset["relative_path"])
            backup = (BACKUP_ROOT / relative.parent / f"{relative.stem}.before-{timestamp}{relative.suffix}").resolve()
            backup.relative_to(BACKUP_ROOT.resolve())
            backup.parent.mkdir(parents=True, exist_ok=True)
            before_pixels = _pixel_hash(path)
            shutil.copy2(path, backup)
            if backup.stat().st_size != path.stat().st_size:
                backup.unlink(missing_ok=True)
                raise ValueError("The safety backup could not be verified")
            after = preview["summary"]
            arguments = [
                "-overwrite_original", "-charset", "iptc=UTF8",
                f"-EXIF:ImageDescription={after['description']}",
                f"-XMP-dc:Description={after['description']}",
                f"-IPTC:Caption-Abstract={after['description']}",
                f"-XMP-dc:Title={after['subject']}", f"-IPTC:ObjectName={after['subject']}",
                f"-XMP-photoshop:Headline={after['subject']}",
                "-XMP-dc:Subject=", "-IPTC:Keywords=", "-XMP-microsoft:LastKeywordXMP=",
                "-XMP-iptcExt:PersonInImage=",
            ]
            keyword_arguments = ["-overwrite_original", "-charset", "iptc=UTF8"]
            for keyword in after["keywords"]:
                keyword_arguments.extend([
                    f"-XMP-dc:Subject+={keyword}", f"-IPTC:Keywords+={keyword}",
                    f"-XMP-microsoft:LastKeywordXMP+={keyword}",
                ])
            for person in after["people"]:
                keyword_arguments.append(f"-XMP-iptcExt:PersonInImage+={person}")
            try:
                _run_exiftool([*arguments, str(path)])
                if after["keywords"] or after["people"]:
                    _run_exiftool([*keyword_arguments, str(path)])
                if _pixel_hash(path) != before_pixels:
                    raise ValueError("Pixel verification failed")
            except Exception:
                shutil.copy2(backup, path)
                raise
            stat = path.stat()
            con.execute(
                """INSERT INTO metadata_publications
                   (asset_id,relative_path,backup_path,before_json,after_json,published_at)
                   VALUES (?,?,?,?,?,?)""",
                (asset_id, asset["relative_path"], str(backup), json.dumps(preview["before"]),
                 json.dumps(preview["after"]), utc_now()),
            )
            con.execute(
                "UPDATE assets SET size_bytes=?,mtime_ns=?,metadata_scanned=1,indexed_at=? WHERE id=?",
                (stat.st_size, stat.st_mtime_ns, utc_now(), asset_id),
            )
            set_source_tags(con, asset_id, "embedded_xmp", after["keywords"])
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True, "backup": str(backup), "message": "Metadata published and picture pixels verified"})

    def restore_published_metadata(self, body):
        asset_id = int(body["id"])
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            record = con.execute(
                """SELECT * FROM metadata_publications WHERE relative_path=? AND restored_at IS NULL
                   ORDER BY id DESC LIMIT 1""", (asset["relative_path"],)
            ).fetchone()
            if not record:
                raise ValueError("There is no published version to restore")
            source = (self.library_root / Path(asset["relative_path"])).resolve()
            source.relative_to(self.library_root)
            backup = Path(record["backup_path"]).resolve()
            backup.relative_to(BACKUP_ROOT.resolve())
            if not backup.is_file():
                raise ValueError("The safety backup is missing")
            shutil.copy2(backup, source)
            stat = source.stat()
            con.execute("UPDATE metadata_publications SET restored_at=? WHERE id=?", (utc_now(), record["id"]))
            con.execute(
                "UPDATE assets SET size_bytes=?,mtime_ns=?,metadata_scanned=1,indexed_at=? WHERE id=?",
                (stat.st_size, stat.st_mtime_ns, utc_now(), asset_id),
            )
            set_source_tags(con, asset_id, "embedded_xmp", extract_xmp_keywords(source))
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True, "message": "The photo was restored from its safety backup"})

    def trash_history(self):
        with self.db() as con:
            rows = con.execute(
                """SELECT id,original_relative_path,moved_at FROM review_bin
                   WHERE restored_at IS NULL ORDER BY moved_at DESC"""
            ).fetchall()
        self.send_json({"items": [
            {
                "id": int(row["id"]),
                "name": Path(row["original_relative_path"]).name,
                "path": row["original_relative_path"],
                "moved_at": row["moved_at"],
            }
            for row in rows
        ]})

    @staticmethod
    def resolve_or_create_person(con: sqlite3.Connection, name: str) -> int:
        row = con.execute(
            """SELECT id FROM people WHERE name=? COLLATE NOCASE
               UNION SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE LIMIT 1""",
            (name, name),
        ).fetchone()
        if row:
            return int(row[0])
        con.execute("INSERT INTO people(name) VALUES (?)", (name,))
        return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    def people_review_queue(self, params):
        requested = params.get("person_id", [""])[0]
        requested_id = int(requested) if requested.isdigit() else None
        advance = params.get("advance", [""])[0] == "1"
        with self.db() as con:
            con.execute("DELETE FROM person_review_deferrals WHERE deferred_until<=?", (utc_now(),))
            deferred_people = int(con.execute(
                "SELECT COUNT(*) FROM person_review_deferrals"
            ).fetchone()[0])
            remaining_total = int(con.execute(
                """SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                   WHERE ap.state='suggested' AND a.in_review_bin=0 AND ap.person_id NOT IN (
                       SELECT person_id FROM person_review_deferrals
                   )"""
            ).fetchone()[0])
            people_remaining = int(con.execute(
                """SELECT COUNT(DISTINCT ap.person_id) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                   WHERE ap.state='suggested' AND a.in_review_bin=0 AND ap.person_id NOT IN (
                       SELECT person_id FROM person_review_deferrals
                   )"""
            ).fetchone()[0])
            person = None
            if requested_id and not advance:
                person = con.execute(
                    """SELECT p.id,p.name FROM people p WHERE p.id=?
                       AND p.id NOT IN (SELECT person_id FROM person_review_deferrals) AND EXISTS (
                           SELECT 1 FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                           WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0
                       )""", (requested_id,)
                ).fetchone()
            if not person and remaining_total:
                previous_name = ""
                if requested_id:
                    row = con.execute("SELECT name FROM people WHERE id=?", (requested_id,)).fetchone()
                    previous_name = row[0] if row else ""
                person = con.execute(
                    """SELECT p.id,p.name FROM people p WHERE p.name>? COLLATE NOCASE
                       AND p.id NOT IN (SELECT person_id FROM person_review_deferrals) AND EXISTS (
                           SELECT 1 FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                           WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0
                       ) ORDER BY p.name COLLATE NOCASE LIMIT 1""", (previous_name,)
                ).fetchone()
                if not person:
                    person = con.execute(
                        """SELECT p.id,p.name FROM people p
                           WHERE p.id NOT IN (SELECT person_id FROM person_review_deferrals) AND EXISTS (
                               SELECT 1 FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                               WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0
                           ) ORDER BY p.name COLLATE NOCASE LIMIT 1"""
                    ).fetchone()
            suggestions = []
            if person:
                suggestions = [dict(row) for row in con.execute(
                    """SELECT a.id,a.filename,a.folder,a.capture_date,ap.confidence,ap.face_id,
                              f.box_left,f.box_top,f.box_right,f.box_bottom,
                              f.localization_similarity
                       FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                       LEFT JOIN face_embeddings f ON f.id=ap.face_id
                       WHERE ap.person_id=? AND ap.state='suggested' AND a.in_review_bin=0
                       ORDER BY ap.confidence DESC,a.capture_date,a.relative_path""",
                    (person["id"],),
                )]
            people_options = [row[0] for row in con.execute(
                "SELECT name FROM people UNION SELECT alias FROM person_aliases ORDER BY 1 COLLATE NOCASE"
            )]
        self.send_json({
            "person": dict(person) if person else None,
            "suggestions": suggestions,
            "remaining_total": remaining_total,
            "people_remaining": people_remaining,
            "deferred_people": deferred_people,
            "people_options": people_options,
        })

    def defer_people_review(self, body):
        person_id = int(body["person_id"])
        days = max(1, min(30, int(body.get("days", 7))))
        now = dt.datetime.now(dt.timezone.utc)
        deferred_until = (now + dt.timedelta(days=days)).isoformat()
        with self.db() as con:
            person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
            if not person:
                raise ValueError("person is no longer available")
            if not con.execute(
                "SELECT 1 FROM asset_people WHERE person_id=? AND state='suggested'", (person_id,)
            ).fetchone():
                raise ValueError("this person has no suggestions to defer")
            con.execute(
                """INSERT INTO person_review_deferrals(person_id,deferred_at,deferred_until)
                   VALUES (?,?,?) ON CONFLICT(person_id) DO UPDATE SET
                   deferred_at=excluded.deferred_at,deferred_until=excluded.deferred_until""",
                (person_id, now.isoformat(), deferred_until),
            )
        self.send_json({
            "ok": True, "person": person["name"], "days": days,
            "deferred_until": deferred_until,
        })

    def learn_people(self, body):
        result = learn_faces(self.db_path, apply=True)
        # learn_faces() already wrote near-certain matches into the catalog
        # as confirmed (see AUTO_CONFIRM_THRESHOLD in face_learning.py); the
        # same JPEG metadata publish a human confirmation triggers still has
        # to happen here, with the usual safety-backup-and-rollback pattern.
        published = []
        try:
            with self.db() as con:
                for entry in result["auto_confirmed"]:
                    published.append(self._publish_people_metadata(
                        con, entry["asset_id"], operation="people-auto-confirm",
                    ))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({
            "ok": True,
            "profiles": result["profiles"],
            "eligible_profiles": result["eligible_profiles"],
            "suggestions": result["suggestions"],
            "auto_confirmed": len(result["auto_confirmed"]),
        })

    def _apply_people_review_decision(self, con, asset_id, person_id, action, corrected_name=""):
        asset_id = int(asset_id); person_id = int(person_id); action = str(action)
        if action not in {"confirmed", "rejected", "corrected"}:
            raise ValueError("invalid review decision")
        corrected_name = clean_tag(str(corrected_name))
        if action == "corrected" and not corrected_name:
            raise ValueError("enter the correct person's name")
        self.get_active_asset(con, asset_id)
        original = con.execute(
            "SELECT state,confidence,face_id,source,updated_at FROM asset_people WHERE asset_id=? AND person_id=?",
            (asset_id, person_id),
        ).fetchone()
        if not original or original["state"] != "suggested":
            raise ValueError("that suggestion has already been reviewed")
        previous = dict(original)
        corrected_person_id = None
        corrected_previous = None
        final_action = action
        if action == "corrected":
            corrected_person_id = self.resolve_or_create_person(con, corrected_name)
            if corrected_person_id == person_id:
                final_action = "confirmed"
                corrected_person_id = None
            else:
                prior = con.execute(
                    "SELECT state,confidence,face_id,source,updated_at FROM asset_people WHERE asset_id=? AND person_id=?",
                    (asset_id, corrected_person_id),
                ).fetchone()
                corrected_previous = dict(prior) if prior else None
        new_state = "confirmed" if final_action == "confirmed" else "rejected"
        con.execute(
            "UPDATE asset_people SET state=?,updated_at=? WHERE asset_id=? AND person_id=?",
            (new_state, utc_now(), asset_id, person_id),
        )
        if corrected_person_id:
            con.execute(
                """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
                   VALUES (?,?,'confirmed',NULL,?,'manual-correction',?)
                   ON CONFLICT(asset_id,person_id) DO UPDATE SET
                       state='confirmed',face_id=excluded.face_id,
                       source='manual-correction',updated_at=excluded.updated_at""",
                (asset_id, corrected_person_id, original["face_id"], utc_now()),
            )
        cursor = con.execute(
            """INSERT INTO people_review_actions(
                   asset_id,person_id,action,previous_json,corrected_person_id,
                   corrected_previous_json,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (asset_id, person_id, "corrected" if corrected_person_id else final_action,
             json.dumps(previous), corrected_person_id,
             json.dumps(corrected_previous) if corrected_person_id else None, utc_now()),
        )
        sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)
        return int(cursor.lastrowid)

    def people_review_decision(self, body):
        published = []
        try:
            with self.db() as con:
                person = con.execute("SELECT name FROM people WHERE id=?", (int(body["person_id"]),)).fetchone()
                if not person:
                    raise ValueError("person is no longer available")
                action = body.get("action", "")
                action_id = self._apply_people_review_decision(
                    con, body["asset_id"], body["person_id"], action,
                    body.get("corrected_name", ""),
                )
                removed = [person["name"]] if action in {"rejected", "corrected"} else []
                published.append(self._publish_people_metadata(
                    con, int(body["asset_id"]), removed, action_id
                ))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({"ok": True, "action_id": action_id, "published": 1})

    def people_review_batch_decision(self, body):
        person_id = int(body["person_id"])
        decisions = body.get("decisions")
        if not isinstance(decisions, list) or not 1 <= len(decisions) <= 8:
            raise ValueError("choose between one and eight photos")
        asset_ids = [int(item["asset_id"]) for item in decisions]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("the same photo cannot appear twice")
        published = []
        try:
            with self.db() as con:
                person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
                if not person:
                    raise ValueError("person is no longer available")
                action_ids = []
                removals: dict[int, set[str]] = {}
                for item in decisions:
                    action = item.get("action", "")
                    action_ids.append(self._apply_people_review_decision(
                        con, item["asset_id"], person_id, action,
                        item.get("corrected_name", ""),
                    ))
                    if action in {"rejected", "corrected"}:
                        removals.setdefault(int(item["asset_id"]), set()).add(person["name"])
                actions_by_asset = dict(zip(asset_ids, action_ids))
                for asset_id in asset_ids:
                    published.append(self._publish_people_metadata(
                        con, asset_id, removals.get(asset_id, set()), actions_by_asset[asset_id]
                    ))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({"ok": True, "action_ids": action_ids, "published": len(published)})

    def _undo_people_review_action(self, con, action_id):
        action = con.execute(
            "SELECT * FROM people_review_actions WHERE id=? AND undone_at IS NULL", (int(action_id),)
        ).fetchone()
        if not action:
            raise ValueError("that review action can no longer be undone")
        previous = json.loads(action["previous_json"])
        con.execute(
                """UPDATE asset_people SET state=?,confidence=?,face_id=?,source=?,updated_at=?
                   WHERE asset_id=? AND person_id=?""",
                (previous["state"], previous["confidence"], previous["face_id"], previous["source"],
                 previous["updated_at"], action["asset_id"], action["person_id"]),
        )
        corrected_id = action["corrected_person_id"]
        if corrected_id:
            corrected_previous = json.loads(action["corrected_previous_json"])
            if corrected_previous:
                con.execute(
                        """UPDATE asset_people SET state=?,confidence=?,face_id=?,source=?,updated_at=?
                           WHERE asset_id=? AND person_id=?""",
                        (corrected_previous["state"], corrected_previous["confidence"],
                         corrected_previous["face_id"], corrected_previous["source"], corrected_previous["updated_at"],
                         action["asset_id"], corrected_id),
                )
            else:
                con.execute(
                    "DELETE FROM asset_people WHERE asset_id=? AND person_id=?",
                    (action["asset_id"], corrected_id),
                )
        con.execute("UPDATE people_review_actions SET undone_at=? WHERE id=?", (utc_now(), int(action_id)))
        asset_id = int(action["asset_id"])
        publication = con.execute(
            """SELECT * FROM metadata_publications
               WHERE review_action_id=? AND restored_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (int(action_id),),
        ).fetchone()
        if publication:
            backup = Path(publication["backup_path"]).resolve()
            backup.relative_to(BACKUP_ROOT.resolve())
            source = (self.library_root / Path(publication["relative_path"])).resolve()
            source.relative_to(self.library_root)
            if not backup.is_file():
                raise ValueError("The people-metadata safety backup is missing")
            expected_pixels = _pixel_hash(backup)
            shutil.copy2(backup, source)
            if _pixel_hash(source) != expected_pixels:
                raise ValueError("Pixel verification failed while undoing people metadata")
            stat = source.stat()
            con.execute(
                "UPDATE metadata_publications SET restored_at=? WHERE id=?",
                (utc_now(), publication["id"]),
            )
            con.execute(
                "UPDATE assets SET size_bytes=?,mtime_ns=?,metadata_scanned=1,indexed_at=? WHERE id=?",
                (stat.st_size, stat.st_mtime_ns, utc_now(), asset_id),
            )
            set_source_tags(con, asset_id, "embedded_xmp", extract_xmp_keywords(source))
        sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)

    def undo_people_review(self, body):
        with self.db() as con:
            self._undo_people_review_action(con, body["action_id"])
        self.send_json({"ok": True})

    def undo_people_review_batch(self, body):
        action_ids = body.get("action_ids")
        if not isinstance(action_ids, list) or not 1 <= len(action_ids) <= 8:
            raise ValueError("choose between one and eight review decisions")
        if len(action_ids) != len({int(value) for value in action_ids}):
            raise ValueError("the same review decision cannot appear twice")
        with self.db() as con:
            for action_id in reversed(action_ids):
                self._undo_people_review_action(con, action_id)
        self.send_json({"ok": True})

    def add_person(self, body):
        asset_id = int(body["id"]); name = clean_tag(str(body.get("name", "")))
        if not name:
            raise ValueError("enter a person's name")
        published = []
        try:
            with self.db() as con:
                self.get_active_asset(con, asset_id)
                person_id = self.resolve_or_create_person(con, name)
                con.execute(
                    """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
                       VALUES (?,?,'confirmed',NULL,'manual',?)
                       ON CONFLICT(asset_id,person_id) DO UPDATE SET
                           state='confirmed',source='manual',updated_at=excluded.updated_at""",
                    (asset_id, person_id, utc_now()),
                )
                sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)
                published.append(self._publish_people_metadata(con, asset_id))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({"ok": True, "published": 1})

    def set_person_aliases(self, body):
        person_id = int(body["person_id"])
        raw_aliases = body.get("aliases", [])
        if not isinstance(raw_aliases, list):
            raise ValueError("aliases must be a list")
        aliases: list[str] = []
        seen: set[str] = set()
        for value in raw_aliases:
            alias = clean_tag(str(value))
            key = alias.casefold()
            if alias and key not in seen:
                aliases.append(alias); seen.add(key)
        with self.db() as con:
            person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
            if not person:
                raise ValueError("person is no longer available")
            primary_key = person["name"].casefold()
            aliases = [alias for alias in aliases if alias.casefold() != primary_key]
            for alias in aliases:
                conflict = con.execute(
                    "SELECT id FROM people WHERE name=? COLLATE NOCASE AND id<>?", (alias, person_id)
                ).fetchone()
                alias_conflict = con.execute(
                    "SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE AND person_id<>?",
                    (alias, person_id),
                ).fetchone()
                if conflict or alias_conflict:
                    raise ValueError(f"“{alias}” already belongs to another person")
            con.execute("DELETE FROM person_aliases WHERE person_id=?", (person_id,))
            con.executemany(
                "INSERT INTO person_aliases(person_id,alias) VALUES (?,?)",
                [(person_id, alias) for alias in aliases],
            )
        self.send_json({"ok": True, "aliases": aliases})

    def set_person_names(self, body):
        person_id = int(body["person_id"])
        primary_name = clean_tag(str(body.get("name", "")))
        if not primary_name:
            raise ValueError("enter the person's primary name")
        raw_aliases = body.get("aliases", [])
        if not isinstance(raw_aliases, list):
            raise ValueError("alternate names must be a list")
        aliases: list[str] = []
        seen = {primary_name.casefold()}
        for value in raw_aliases:
            alias = clean_tag(str(value))
            key = alias.casefold()
            if alias and key not in seen:
                aliases.append(alias); seen.add(key)

        published = []
        try:
            with self.db() as con:
                person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
                if not person:
                    raise ValueError("person is no longer available")
                old_name = str(person["name"])
                primary_conflict = con.execute(
                    "SELECT id FROM people WHERE name=? COLLATE NOCASE AND id<>?",
                    (primary_name, person_id),
                ).fetchone()
                primary_alias_conflict = con.execute(
                    "SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE AND person_id<>?",
                    (primary_name, person_id),
                ).fetchone()
                if primary_conflict or primary_alias_conflict:
                    raise ValueError(f"“{primary_name}” already belongs to another person")
                for alias in aliases:
                    name_conflict = con.execute(
                        "SELECT id FROM people WHERE name=? COLLATE NOCASE AND id<>?", (alias, person_id)
                    ).fetchone()
                    alias_conflict = con.execute(
                        "SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE AND person_id<>?",
                        (alias, person_id),
                    ).fetchone()
                    if name_conflict or alias_conflict:
                        raise ValueError(f"“{alias}” already belongs to another person")

                affected = con.execute(
                    """SELECT DISTINCT a.id,a.relative_path FROM asset_people ap
                       JOIN assets a ON a.id=ap.asset_id
                       WHERE ap.person_id=? AND ap.state='confirmed'""",
                    (person_id,),
                ).fetchall()
                embedded_old_assets: set[int] = set()
                if old_name.casefold() != primary_name.casefold():
                    embedded_old_assets = {
                        int(row[0]) for row in con.execute(
                            """SELECT at.asset_id FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                               WHERE at.asset_id IN (
                                   SELECT asset_id FROM asset_people WHERE person_id=? AND state='confirmed'
                               ) AND at.source='embedded_xmp' AND t.name=? COLLATE NOCASE""",
                            (person_id, old_name),
                        )
                    }

                con.execute("DELETE FROM person_aliases WHERE person_id=?", (person_id,))
                con.execute("UPDATE people SET name=? WHERE id=?", (primary_name, person_id))
                con.executemany(
                    "INSERT INTO person_aliases(person_id,alias) VALUES (?,?)",
                    [(person_id, alias) for alias in aliases],
                )
                name_changed = old_name != primary_name
                for asset in affected:
                    asset_id = int(asset["id"])
                    if asset_id in embedded_old_assets:
                        con.execute(
                            "INSERT OR IGNORE INTO asset_tag_exclusions(relative_path,tag) VALUES (?,?)",
                            (asset["relative_path"], old_name),
                        )
                    sync_person_tags(con, asset_id)
                    rebuild_search_row(con, asset_id)
                    if name_changed:
                        published.append(self._publish_people_metadata(
                            con, asset_id, [old_name], operation="people-rename"
                        ))
                if old_name.casefold() != primary_name.casefold():
                    con.execute(
                        """DELETE FROM tags WHERE name=? COLLATE NOCASE AND NOT EXISTS (
                               SELECT 1 FROM asset_tags at WHERE at.tag_id=tags.id
                           )""",
                        (old_name,),
                    )
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({
            "ok": True, "name": primary_name, "aliases": aliases,
            "updated_photos": len(affected), "published": len(published),
        })

    @classmethod
    def _assert_catalog_idle_for_people_merge(cls) -> None:
        jobs = (
            (cls.library_lock, cls.library_job, {"scanning", "cancelling"}, "library scan"),
            (cls.ocr_lock, cls.ocr_job, {"running", "cancelling"}, "OCR"),
            (cls.semantic_lock, cls.semantic_job, {"running", "cancelling"}, "meaning indexing"),
        )
        for lock, job, active_states, label in jobs:
            with lock:
                if str(job.get("state", "")).casefold() in active_states:
                    raise ValueError(f"wait for {label} to finish before merging people")

    def merge_people(self, body):
        if not type(self).people_merge_lock.acquire(blocking=False):
            raise ValueError("another People merge is already in progress")
        try:
            return self._merge_people(body)
        finally:
            type(self).people_merge_lock.release()

    def _merge_people(self, body):
        """Merge duplicate person records into one chosen primary record."""
        try:
            target_id = int(body["target_person_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("choose the person to keep") from exc
        raw_source_ids = body.get("source_person_ids", [])
        if not isinstance(raw_source_ids, list):
            raise ValueError("choose one or more names to merge")
        source_ids: list[int] = []
        for value in raw_source_ids:
            try:
                person_id = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid person selection") from exc
            if person_id == target_id:
                raise ValueError("the person to keep cannot also be merged")
            if person_id > 0 and person_id not in source_ids:
                source_ids.append(person_id)
        if not source_ids:
            raise ValueError("choose at least one other name to merge")
        if len(source_ids) > 50:
            raise ValueError("merge at most 50 names at a time")
        type(self)._assert_catalog_idle_for_people_merge()

        all_ids = [target_id, *source_ids]
        placeholders = ",".join("?" for _ in all_ids)
        source_placeholders = ",".join("?" for _ in source_ids)
        published = []
        affected_asset_ids: list[int] = []
        target_name = ""
        source_names: list[str] = []
        database_backup = ""
        try:
            with self.db() as con:
                con.execute("BEGIN IMMEDIATE")
                rows = con.execute(
                    f"SELECT id,name FROM people WHERE id IN ({placeholders})", all_ids
                ).fetchall()
                people = {int(row["id"]): str(row["name"]) for row in rows}
                missing = [person_id for person_id in all_ids if person_id not in people]
                if missing:
                    raise ValueError("one of those people is no longer available")
                target_name = people[target_id]
                source_names = [people[person_id] for person_id in source_ids]

                target_aliases = [str(row[0]) for row in con.execute(
                    "SELECT alias FROM person_aliases WHERE person_id=?", (target_id,)
                )]
                source_aliases = [str(row[0]) for row in con.execute(
                    f"SELECT alias FROM person_aliases WHERE person_id IN ({source_placeholders})",
                    source_ids,
                )]
                retained_names: list[str] = []
                retained_keys = {target_name.casefold(), *(name.casefold() for name in target_aliases)}
                for name in [*source_names, *source_aliases]:
                    normalized = clean_tag(name)
                    if normalized and normalized.casefold() not in retained_keys:
                        retained_names.append(normalized)
                        retained_keys.add(normalized.casefold())
                for name in retained_names:
                    name_conflict = con.execute(
                        f"SELECT id FROM people WHERE name=? COLLATE NOCASE AND id NOT IN ({placeholders})",
                        (name, *all_ids),
                    ).fetchone()
                    alias_conflict = con.execute(
                        f"SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE "
                        f"AND person_id NOT IN ({placeholders})",
                        (name, *all_ids),
                    ).fetchone()
                    if name_conflict or alias_conflict:
                        raise ValueError(f"“{name}” already belongs to another person")

                database_backup = str(create_verified_database_backup(self.db_path))

                association_rows = con.execute(
                    f"""SELECT asset_id,person_id,state,confidence,face_id,source,updated_at
                           FROM asset_people WHERE person_id IN ({placeholders})""",
                    all_ids,
                ).fetchall()
                by_asset: dict[int, list[sqlite3.Row]] = {}
                for row in association_rows:
                    by_asset.setdefault(int(row["asset_id"]), []).append(row)
                state_rank = {"rejected": 1, "suggested": 2, "confirmed": 3}

                merged_associations = []
                for asset_id, candidates in by_asset.items():
                    winner = max(
                        candidates,
                        key=lambda row: (
                            state_rank[str(row["state"])],
                            row["face_id"] is not None,
                            float(row["confidence"]) if row["confidence"] is not None else -1.0,
                            str(row["updated_at"]),
                            int(row["person_id"]) == target_id,
                        ),
                    )
                    merged_associations.append((
                        asset_id,
                        str(winner["state"]),
                        winner["confidence"],
                        winner["face_id"],
                        str(winner["source"]) if int(winner["person_id"]) == target_id else "person-merge",
                        utc_now(),
                    ))
                affected_asset_ids = sorted(by_asset)
                con.execute(
                    f"DELETE FROM asset_people WHERE person_id IN ({placeholders})", all_ids
                )
                con.executemany(
                    """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    [(asset_id, target_id, state, confidence, face_id, source, updated_at)
                     for asset_id, state, confidence, face_id, source, updated_at in merged_associations],
                )

                timestamp = utc_now()
                con.execute(
                    f"""UPDATE people_review_actions SET undone_at=COALESCE(undone_at, ?)
                           WHERE person_id IN ({source_placeholders})
                              OR corrected_person_id IN ({source_placeholders})""",
                    (timestamp, *source_ids, *source_ids),
                )
                con.execute(
                    f"UPDATE people_review_actions SET person_id=? WHERE person_id IN ({source_placeholders})",
                    (target_id, *source_ids),
                )
                con.execute(
                    f"UPDATE people_review_actions SET corrected_person_id=? "
                    f"WHERE corrected_person_id IN ({source_placeholders})",
                    (target_id, *source_ids),
                )
                con.execute(
                    f"DELETE FROM person_review_deferrals WHERE person_id IN ({source_placeholders})",
                    source_ids,
                )
                con.execute(
                    f"DELETE FROM person_face_profiles WHERE person_id IN ({placeholders})", all_ids
                )
                con.execute(
                    f"DELETE FROM person_aliases WHERE person_id IN ({source_placeholders})", source_ids
                )
                con.executemany(
                    "INSERT INTO person_aliases(person_id,alias) VALUES (?,?)",
                    [(target_id, name) for name in retained_names],
                )
                con.execute(f"DELETE FROM people WHERE id IN ({source_placeholders})", source_ids)

                for asset_id in affected_asset_ids:
                    sync_person_tags(con, asset_id)
                    rebuild_search_row(con, asset_id)
                for name in source_names:
                    con.execute(
                        """DELETE FROM tags WHERE name=? COLLATE NOCASE AND NOT EXISTS (
                               SELECT 1 FROM asset_tags WHERE tag_id=tags.id
                           )""",
                        (name,),
                    )
                if affected_asset_ids:
                    active_confirmed = con.execute(
                        f"""SELECT ap.asset_id FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                               WHERE ap.person_id=? AND ap.state='confirmed' AND a.in_review_bin=0
                                 AND a.extension IN ('.jpg','.jpeg')
                                 AND ap.asset_id IN ({','.join('?' for _ in affected_asset_ids)})""",
                        (target_id, *affected_asset_ids),
                    ).fetchall()
                    for row in active_confirmed:
                        published.append(self._publish_people_metadata(
                            con, int(row["asset_id"]), source_names, operation="people-merge"
                        ))
        except Exception:
            self._restore_people_batch(published)
            raise

        learning_error = ""
        suggestions = None
        try:
            suggestions = int(learn_faces(self.db_path, apply=True)["suggestions"])
        except Exception as exc:  # A merge remains valid even if face rebuilding is unavailable.
            learning_error = str(exc)
        self.send_json({
            "ok": True,
            "person": target_name,
            "merged_names": source_names,
            "aliases": retained_names,
            "updated_photos": len(affected_asset_ids),
            "published": len(published),
            "database_backup": database_backup,
            "suggestions": suggestions,
            "learning_error": learning_error,
        })

    def set_person_state(self, body):
        asset_id = int(body["id"]); name = clean_tag(str(body.get("name", "")))
        state = str(body.get("state", ""))
        if state not in {"confirmed", "rejected"}:
            raise ValueError("invalid person state")
        published = []
        try:
            with self.db() as con:
                self.get_active_asset(con, asset_id)
                row = con.execute("SELECT id FROM people WHERE name=? COLLATE NOCASE", (name,)).fetchone()
                if not row:
                    raise ValueError("person is no longer available")
                changed = con.execute(
                    "UPDATE asset_people SET state=?,updated_at=? WHERE asset_id=? AND person_id=?",
                    (state, utc_now(), asset_id, int(row[0])),
                ).rowcount
                if not changed:
                    raise ValueError("person is no longer associated with this photo")
                sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)
                published.append(self._publish_people_metadata(
                    con, asset_id, [name] if state == "rejected" else []
                ))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({"ok": True, "published": 1})

    def diagnostics(self):
        semantic = semantic_status(type(self).db_path)
        with self.db() as con:
            integrity = str(con.execute("PRAGMA quick_check").fetchone()[0])
            schema_version = int(con.execute("PRAGMA user_version").fetchone()[0])
            counts = {
                "assets": int(con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0]),
                "metadata_ready": int(con.execute("SELECT COUNT(*) FROM assets WHERE metadata_scanned=1 AND in_review_bin=0").fetchone()[0]),
                "mapped": int(con.execute("SELECT COUNT(*) FROM assets WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL AND in_review_bin=0").fetchone()[0]),
                "location_pending": int(con.execute("SELECT COUNT(*) FROM assets WHERE location_scanned=0 AND in_review_bin=0").fetchone()[0]),
                "ocr_complete": int(con.execute("SELECT COUNT(*) FROM text_data WHERE ocr_scanned=1").fetchone()[0]),
                "ocr_with_text": int(con.execute("SELECT COUNT(*) FROM text_data WHERE ocr_text<>''").fetchone()[0]),
                "ocr_pending": int(con.execute("""SELECT COUNT(*) FROM text_data x JOIN assets a ON a.id=x.asset_id
                    WHERE a.media_type='image' AND a.metadata_scanned=1 AND a.in_review_bin=0 AND x.ocr_scanned=0""").fetchone()[0]),
                "ocr_errors": int(con.execute("SELECT COUNT(*) FROM text_data WHERE ocr_error<>''").fetchone()[0]),
                "people_pending": int(con.execute("SELECT COUNT(*) FROM asset_people WHERE state='suggested'").fetchone()[0]),
                "publications": int(con.execute("SELECT COUNT(*) FROM metadata_publications").fetchone()[0]),
                "review_bin": int(con.execute("SELECT COUNT(*) FROM review_bin WHERE restored_at IS NULL").fetchone()[0]),
                "semantic_indexed": int(semantic["indexed"]),
                "semantic_remaining": int(semantic["remaining"]),
            }
            latest = con.execute(
                """SELECT finished_at,scanned,changed,unchanged,removed,errors,cancelled
                   FROM runs ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        self.send_json({
            "app_version": APP_VERSION,
            "schema_version": schema_version,
            "current_schema": SCHEMA_VERSION,
            "integrity": integrity,
            "database": str(type(self).db_path),
            "database_bytes": type(self).db_path.stat().st_size if type(self).db_path.is_file() else 0,
            "library": str(type(self).library_root),
            "counts": counts,
            "last_scan": dict(latest) if latest else None,
        })

    @classmethod
    def _begin_update_check(cls, force: bool = False) -> bool:
        with cls.update_lock:
            if cls.update_job.get("state") == "checking":
                return False
            if not force and cls.update_job.get("state") not in {"idle", "error"}:
                if cls.update_job.get("state") == "available":
                    return False
                try:
                    checked = dt.datetime.fromisoformat(str(cls.update_job.get("checked_at", "")))
                    if dt.datetime.now(dt.timezone.utc) - checked < dt.timedelta(hours=6):
                        return False
                except ValueError:
                    return False
            cls.update_job = {
                "state": "checking",
                "message": "Checking GitHub for a verified LensLedger release…",
                "current_version": APP_VERSION,
            }

        def worker():
            try:
                result = check_for_update(APP_VERSION)
                release = result["release"]
                available = bool(result["available"])
                job = {
                    **result,
                    "state": "available" if available else "current",
                    "message": (
                        f"LensLedger {release['version']} is ready to install."
                        if available else f"LensLedger {APP_VERSION} is up to date."
                    ),
                    "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            except Exception as exc:
                job = {
                    "state": "error",
                    "message": str(exc),
                    "current_version": APP_VERSION,
                    "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            with cls.update_lock:
                cls.update_job = job

        threading.Thread(target=worker, name="LensLedger-update-check", daemon=True).start()
        return True

    def update_status(self):
        type(self)._begin_update_check()
        with type(self).update_lock:
            job = dict(type(self).update_job)
        install_root = Path(__file__).parent.resolve()
        job.update({
            "current_version": APP_VERSION,
            "managed_install": is_managed_install(install_root),
            "current_install_root": str(install_root),
            "managed_install_root": str(managed_install_root()),
        })
        self.send_json(job)

    def check_update(self, _body):
        started = type(self)._begin_update_check(force=True)
        self.send_json({"ok": True, "state": "checking", "started": started}, 202)

    def install_update(self, _body):
        with type(self).update_lock:
            if type(self).update_job.get("state") != "available":
                raise ValueError("No verified LensLedger update is ready to install")
            release = dict(type(self).update_job.get("release") or {})
            type(self).update_job = {
                "state": "restarting",
                "message": f"Installing LensLedger {release.get('version', '')} and restarting…",
                "current_version": APP_VERSION,
                "release": release,
            }

        helper_root = updates_root()
        helper_root.mkdir(parents=True, exist_ok=True)
        helper = helper_root / "lensledger-updater-helper.py"
        shutil.copy2(Path(__file__).parent / "lensledger_updater.py", helper)
        install_root = Path(__file__).parent.resolve()
        command = [
            sys.executable, str(helper), "install-latest",
            "--current-root", str(install_root),
            "--current", APP_VERSION,
            "--wait-pid", str(os.getpid()),
        ]
        if (install_root / "photo-index.sqlite3").is_file():
            command.extend(["--legacy-root", str(install_root)])
        log_path = helper_root / "last-update.log"
        log_stream = log_path.open("w", encoding="utf-8")
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            subprocess.Popen(
                command, cwd=helper_root, stdin=subprocess.DEVNULL,
                stdout=log_stream, stderr=subprocess.STDOUT,
                close_fds=True, creationflags=flags,
            )
        finally:
            log_stream.close()

        def stop_server():
            threading.Event().wait(1.0)
            self.server.shutdown()

        threading.Thread(target=stop_server, name="LensLedger-update-shutdown", daemon=True).start()
        self.send_json({
            "ok": True,
            "state": "restarting",
            "message": "The verified update is being installed. LensLedger will reopen automatically.",
        }, 202)

    def ocr_status(self):
        with type(self).ocr_lock:
            job = dict(type(self).ocr_job)
        self.send_json(job)

    def start_ocr(self, body):
        workers = max(1, min(8, int(body.get("workers", 4))))
        since = str(body.get("since", "")).strip() or None
        if since:
            dt.date.fromisoformat(since)
        with type(self).ocr_lock:
            if type(self).ocr_job.get("state") == "running":
                raise ValueError("OCR is already running")
            with type(self).library_lock:
                if type(self).library_job.get("state") == "scanning":
                    raise ValueError("wait for the library scan to finish before starting OCR")
            type(self).ocr_job = {
                "state": "running", "message": "Preparing local text recognition…",
                "total": 0, "attempted": 0, "with_text": 0, "errors": 0,
            }
            type(self).ocr_cancel.clear()
        handler_class = type(self)
        database = handler_class.db_path

        def worker():
            try:
                def update_progress(counts):
                    with handler_class.ocr_lock:
                        handler_class.ocr_job = {
                            "state": "running",
                            "message": f"Processed {int(counts['attempted']):,} of {int(counts['total']):,} images…",
                            **counts,
                        }

                result = ocr_assets(
                    database, since, workers, progress=update_progress,
                    should_cancel=handler_class.ocr_cancel.is_set,
                )
                with handler_class.ocr_lock:
                    current = dict(handler_class.ocr_job)
                    state = "cancelled" if result == 3 else "complete"
                    current.update({
                        "state": state,
                        "message": "OCR paused safely. Start it again to resume." if result == 3
                        else "OCR pass complete.",
                    })
                    handler_class.ocr_job = current
            except Exception as exc:
                with handler_class.ocr_lock:
                    handler_class.ocr_job = {"state": "error", "message": str(exc)}

        threading.Thread(target=worker, name="LensLedger-ocr", daemon=True).start()
        self.send_json({"ok": True, "state": "running"}, 202)

    def cancel_ocr(self, _body):
        with type(self).ocr_lock:
            if type(self).ocr_job.get("state") != "running":
                raise ValueError("OCR is not running")
            type(self).ocr_cancel.set()
            type(self).ocr_job["message"] = "Pausing after active images finish…"
        self.send_json({"ok": True, "state": "cancelling"}, 202)

    def semantic_job_status(self):
        coverage = semantic_status(type(self).db_path)
        with type(self).semantic_lock:
            job = dict(type(self).semantic_job)
        self.send_json({**coverage, **job})

    def start_semantic_index(self, body):
        batch_size = max(1, min(64, int(body.get("batch_size", 16))))
        with type(self).semantic_lock:
            if type(self).semantic_job.get("state") == "running":
                raise ValueError("meaning indexing is already running")
            with type(self).library_lock:
                if type(self).library_job.get("state") == "scanning":
                    raise ValueError("wait for the library scan to finish first")
            with type(self).ocr_lock:
                if type(self).ocr_job.get("state") == "running":
                    raise ValueError("pause OCR before starting meaning indexing")
            type(self).semantic_job = {
                "state": "running", "message": "Loading the optional local meaning model…",
                "total": 0, "indexed_this_pass": 0, "errors": 0,
            }
            type(self).semantic_cancel.clear()
        handler_class = type(self)
        database = handler_class.db_path

        def worker():
            try:
                def update_progress(counts):
                    with handler_class.semantic_lock:
                        handler_class.semantic_job = {
                            "state": "running",
                            "message": f"Indexed {int(counts['indexed']):,} of {int(counts['total']):,} images this pass…",
                            "total": int(counts["total"]),
                            "indexed_this_pass": int(counts["indexed"]),
                            "errors": int(counts["errors"]),
                        }

                result = build_semantic_index(
                    database, batch_size=batch_size, progress=update_progress,
                    should_cancel=handler_class.semantic_cancel.is_set,
                )
                with handler_class.semantic_lock:
                    handler_class.semantic_job = {
                        "state": "cancelled" if result["cancelled"] else "complete",
                        "message": "Meaning indexing paused safely." if result["cancelled"]
                        else "Meaning index is ready.",
                        "total": int(result["total"]),
                        "indexed_this_pass": int(result["indexed"]),
                        "errors": int(result["errors"]),
                    }
            except Exception as exc:
                with handler_class.semantic_lock:
                    handler_class.semantic_job = {"state": "error", "message": str(exc)}

        threading.Thread(target=worker, name="LensLedger-semantic", daemon=True).start()
        self.send_json({"ok": True, "state": "running"}, 202)

    def cancel_semantic_index(self, _body):
        with type(self).semantic_lock:
            if type(self).semantic_job.get("state") != "running":
                raise ValueError("meaning indexing is not running")
            type(self).semantic_cancel.set()
            type(self).semantic_job["message"] = "Pausing after the active image batch…"
        self.send_json({"ok": True, "state": "cancelling"}, 202)

    def backup_database(self, _body):
        destination = create_verified_database_backup(type(self).db_path)
        self.send_json({"ok": True, "path": str(destination), "bytes": destination.stat().st_size})

    def library_status(self):
        with type(self).library_lock:
            job = dict(type(self).library_job)
        job["current_root"] = str(type(self).library_root)
        self.send_json(job)

    def library_options(self):
        config = load_library_config()
        known = []
        for value in config.get("libraries", []):
            root = Path(str(value))
            if root.is_dir():
                known.append({"label": root.name or str(root), "path": str(root.resolve())})
        self.send_json({
            "suggestions": suggested_library_roots(),
            "known": known,
            "current_root": str(type(self).library_root),
        })

    def browse_library(self, _body):
        self.send_json({"path": choose_library_folder()})

    def open_library(self, body):
        value = str(body.get("path", "")).strip()
        if not value:
            raise ValueError("choose a photo library folder")
        root = Path(value).resolve()
        if not root.is_dir():
            raise ValueError("that photo library folder does not exist")
        database = library_db_path(root).resolve()
        with type(self).library_lock:
            if type(self).library_job.get("state") == "scanning":
                raise ValueError("another photo library is currently being indexed")
            type(self).library_job = {
                "state": "scanning", "message": "Discovering photos and videos…",
                "target_root": str(root), "scanned": 0, "changed": 0,
                "unchanged": 0, "removed": 0, "errors": 0, "placeholders": 0,
            }
            type(self).library_cancel.clear()
        handler_class = type(self)

        def worker():
            try:
                def update_progress(counts):
                    with handler_class.library_lock:
                        handler_class.library_job = {
                            "state": "scanning", "message": f"Discovered {int(counts['scanned']):,} media files…",
                            "target_root": str(root), **counts,
                        }

                result = scan_library(
                    root, database, progress=update_progress,
                    should_cancel=handler_class.library_cancel.is_set,
                )
                if result not in {0, 2, 3}:
                    raise ValueError("the library index did not complete")
                with handler_class.library_lock:
                    handler_class.current_library = (root, database)
                save_library_state(root)
                with connect(database) as con:
                    summary = {
                        "assets": int(con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0]),
                        "images": int(con.execute("SELECT COUNT(*) FROM assets WHERE media_type='image' AND in_review_bin=0").fetchone()[0]),
                        "videos": int(con.execute("SELECT COUNT(*) FROM assets WHERE media_type='video' AND in_review_bin=0").fetchone()[0]),
                        "raw_files": int(con.execute("SELECT COUNT(*) FROM assets WHERE media_type='raw' AND in_review_bin=0").fetchone()[0]),
                        "metadata_ready": int(con.execute("SELECT COUNT(*) FROM assets WHERE metadata_scanned=1 AND in_review_bin=0").fetchone()[0]),
                        "placeholders": int(con.execute("SELECT COUNT(*) FROM assets WHERE metadata_scanned=0 AND in_review_bin=0").fetchone()[0]),
                    }
                with handler_class.library_lock:
                    progress_counts = {key: handler_class.library_job.get(key, 0) for key in (
                        "scanned", "changed", "unchanged", "removed", "errors", "placeholders"
                    )}
                    state = "cancelled" if result == 3 else "complete"
                    handler_class.library_job = {
                        "state": state,
                        "message": "Scan paused. Run it again to resume." if result == 3 else f"Library ready: {root}",
                        "target_root": str(root), **progress_counts, "summary": summary,
                    }
            except Exception as exc:
                with handler_class.library_lock:
                    handler_class.library_job = {
                        "state": "error", "message": str(exc), "target_root": str(root),
                    }

        threading.Thread(target=worker, name="LensLedger-library-index", daemon=True).start()
        self.send_json({"ok": True, "state": "scanning", "path": str(root)}, 202)

    def cancel_library_scan(self, _body):
        with type(self).library_lock:
            if type(self).library_job.get("state") != "scanning":
                raise ValueError("there is no scan running")
            type(self).library_cancel.set()
            type(self).library_job["message"] = "Pausing after the current file…"
        self.send_json({"ok": True, "state": "cancelling"}, 202)

    def update_subject(self, body):
        asset_id = int(body["id"]); subject = clean_tag(str(body.get("subject", "")))
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            con.execute("""INSERT INTO asset_annotations(relative_path,subject,tags) VALUES (?,?,'')
                ON CONFLICT(relative_path) DO UPDATE SET subject=excluded.subject""", (asset["relative_path"], subject))
            set_source_tags(con, asset_id, "subject", [subject] if subject else [])
            if subject:
                con.execute("DELETE FROM asset_tag_exclusions WHERE relative_path=? AND tag=? COLLATE NOCASE", (asset["relative_path"], subject))
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True})

    def add_tag(self, body):
        asset_id = int(body["id"])
        incoming = [
            clean_tag(part) for part in re.split(r"[,;\n]+", str(body.get("tag", "")))
            if clean_tag(part)
        ]
        if not incoming: raise ValueError("enter at least one tag")
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            row = con.execute("SELECT subject,tags FROM asset_annotations WHERE relative_path=?", (asset["relative_path"],)).fetchone()
            names = split_tags(row["tags"] if row else "")
            known = {name.casefold() for name in names}
            for tag in incoming:
                if tag.casefold() not in known:
                    names.append(tag); known.add(tag.casefold())
            con.execute("""INSERT INTO asset_annotations(relative_path,subject,tags) VALUES (?,?,?)
                ON CONFLICT(relative_path) DO UPDATE SET tags=excluded.tags""", (asset["relative_path"], row["subject"] if row else "", ";".join(names)))
            for tag in incoming:
                con.execute("DELETE FROM asset_tag_exclusions WHERE relative_path=? AND tag=? COLLATE NOCASE", (asset["relative_path"], tag))
            set_source_tags(con, asset_id, "asset_rule", names); rebuild_search_row(con, asset_id)
        self.send_json({"ok": True, "added": len(incoming)})

    def add_folder_tag(self, body):
        asset_id = int(body["id"])
        incoming = [
            clean_tag(part) for part in re.split(r"[,;\n]+", str(body.get("tag", "")))
            if clean_tag(part)
        ]
        if not incoming:
            raise ValueError("enter at least one event tag")
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            folder = asset["folder"]
            existing = {
                row[0].casefold() for row in con.execute(
                    "SELECT tag FROM folder_tags WHERE folder=?", (folder,)
                )
            }
            added = 0
            for tag in incoming:
                if tag.casefold() not in existing:
                    con.execute("INSERT INTO folder_tags(folder,tag) VALUES (?,?)", (folder, tag))
                    existing.add(tag.casefold())
                    added += 1
            folder_names = [
                row[0] for row in con.execute(
                    "SELECT tag FROM folder_tags WHERE folder=? ORDER BY tag", (folder,)
                )
            ]
            folder_assets = con.execute(
                "SELECT id FROM assets WHERE folder=?", (folder,)
            ).fetchall()
            for row in folder_assets:
                set_source_tags(con, int(row["id"]), "folder_rule", folder_names)
                rebuild_search_row(con, int(row["id"]))
        self.send_json({"ok": True, "added": added, "assets": len(folder_assets)})

    def remove_tag(self, body):
        asset_id = int(body["id"]); tag = clean_tag(str(body.get("tag", "")))
        if not tag: raise ValueError("tag cannot be empty")
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            con.execute("INSERT OR IGNORE INTO asset_tag_exclusions(relative_path,tag) VALUES (?,?)", (asset["relative_path"], tag))
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True})

    def restore_tag(self, body):
        asset_id = int(body["id"]); tag = clean_tag(str(body.get("tag", "")))
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            con.execute("DELETE FROM asset_tag_exclusions WHERE relative_path=? AND tag=? COLLATE NOCASE", (asset["relative_path"], tag))
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True})

    def move_to_review_bin(self, body):
        asset_id = int(body["id"])
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            source = Path(asset["path"]).resolve(); source.relative_to(self.library_root)
            if any(part in {"!LensLedger", "_PhotoIndex", "_FaceData"} for part in source.relative_to(self.library_root).parts) or not source.is_file():
                raise ValueError("refusing to move this path")
            review_root = review_bin_root().resolve()
            destination = review_root / Path(asset["relative_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination = destination.with_name(f"{destination.stem}-{dt.datetime.now():%Y%m%d-%H%M%S}{destination.suffix}")
            destination.resolve().relative_to(review_root)
            shutil.move(str(source), str(destination))
            review_id = con.execute("""INSERT INTO review_bin(asset_id,original_path,original_relative_path,review_path,moved_at)
                VALUES (?,?,?,?,?)""", (asset_id, str(source), asset["relative_path"], str(destination), utc_now())).lastrowid
            con.execute("UPDATE assets SET path=?,in_review_bin=1 WHERE id=?", (str(destination), asset_id))
        self.send_json({"ok": True, "review_id": review_id})

    def restore_from_review_bin(self, body):
        review_id = int(body["review_id"])
        with self.db() as con:
            row = con.execute("SELECT * FROM review_bin WHERE id=? AND restored_at IS NULL", (review_id,)).fetchone()
            if not row: raise ValueError("review item is no longer available")
            source = Path(row["review_path"]).resolve(); destination = Path(row["original_path"]).resolve()
            destination.relative_to(self.library_root)
            if not source.is_file() or destination.exists(): raise ValueError("cannot restore because the source is missing or destination exists")
            destination.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(source), str(destination))
            con.execute("UPDATE assets SET path=?,in_review_bin=0 WHERE id=?", (str(destination), row["asset_id"]))
            con.execute("UPDATE review_bin SET restored_at=? WHERE id=?", (utc_now(), review_id))
        self.send_json({"ok": True})

    def serve_static(self, name: str):
        if not STATIC_NAME_RE.fullmatch(name):
            return self.send_error(404)
        path = (STATIC_ROOT / name).resolve()
        try:
            path.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            return self.send_error(403)
        if not path.is_file():
            return self.send_error(404)
        content_type = STATIC_CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self.send_bytes(path.read_bytes(), content_type, cache="public, max-age=31536000, immutable")

    def serve_logo(self):
        path = Path(__file__).with_name("assets") / "lensledger-logo.png"
        if not path.is_file(): return self.send_error(404)
        self.send_bytes(path.read_bytes(), "image/png", cache="private, max-age=86400")

    def serve_world_map(self):
        path = Path(__file__).with_name("assets") / "world-map.svg"
        if not path.is_file(): return self.send_error(404)
        self.send_bytes(path.read_bytes(), "image/svg+xml", cache="private, max-age=86400")

    def serve_media(self, params):
        try: asset_id = int(params.get("id", [""])[0])
        except ValueError: return self.send_error(400)
        with self.db() as con:
            row = con.execute("SELECT path FROM assets WHERE id=? AND in_review_bin=0", (asset_id,)).fetchone()
        if not row: return self.send_error(404)
        path = Path(row["path"]).resolve()
        try: path.relative_to(self.library_root)
        except ValueError: return self.send_error(403)
        if not path.is_file(): return self.send_error(404)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(path.stat().st_size)); self.send_header("Cache-Control", "private, max-age=3600"); self.end_headers()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024): self.wfile.write(chunk)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_NAME} {APP_VERSION}")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--port", type=int, default=5309)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    root = (args.root or load_library_state()).resolve()
    database = (args.db or library_db_path(root)).resolve()
    SearchHandler.current_library = (root, database); SearchHandler.csrf_token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), SearchHandler); url = f"http://127.0.0.1:{args.port}/"
    print("\n" + "=" * 62, flush=True); print(f"  {APP_NAME} v{APP_VERSION}\n  {APP_TAGLINE}\n", flush=True); print(f"  Local library: {url}\n  Press Ctrl+C in this window to stop LensLedger.", flush=True); print("=" * 62 + "\n", flush=True)
    if not args.no_open:
        browser_timer = threading.Timer(1.0, webbrowser.open, args=(url,))
        browser_timer.daemon = True
        browser_timer.start()
    try: server.serve_forever()
    except KeyboardInterrupt: print(f"\n{APP_NAME} is stopping...", flush=True)
    finally: server.server_close(); print(f"{APP_NAME} stopped.", flush=True)


if __name__ == "__main__":
    main()
