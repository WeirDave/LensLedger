#!/usr/bin/env python3
"""LensLedger localhost-only photo review and metadata staging interface."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import io
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

from PIL import Image, ImageOps
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from console_log import log as console_log
from app_paths import (
    backup_root, data_root, database_backup_root, review_bin_root,
)
from face_learning import SUGGESTION_THRESHOLD, centroid, decode_vector, dot, learn as learn_faces
from face_locations import is_available as face_is_available
from face_scan import list_errors as face_scan_list_errors, scan_for_faces, status as face_scan_status
from library_config import (
    associate_db_path, choose_library_folder, library_db_path, library_db_path_appdata, load_library_config,
    load_library_state, save_library_state, suggested_library_roots,
)
from folder_watcher import FolderWatcher
from ingest_pipeline import IngestPipeline
from settings_config import (
    AVAILABLE_MODELS, load_settings, save_settings,
)
from lensledger_updater import check_for_update, is_managed_install, managed_install_root, updates_root
from metadata_reader import pixel_hash as _pixel_hash, read_embedded_metadata
from photo_index import (
    SCHEMA_VERSION, SQLITE_BUSY_TIMEOUT_MS, connect, extract_xmp_keywords, ocr_assets, rebuild_search_row, scan_library,
    set_source_tags, sync_person_tags, utc_now,
)
from product import APP_NAME, APP_TAGLINE, APP_VERSION

_STARTUP_VERSION = APP_VERSION
_STARTED_AT = dt.datetime.now(dt.timezone.utc).isoformat()
from semantic_index import (
    build_index as build_semantic_index,
    is_available as semantic_is_available,
    search as semantic_search,
    status as semantic_status,
)


TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
LIKE_ESCAPE_RE = re.compile(r"([\\%_])")
PAGE_SIZE = 250  # default; overridden per-request from settings
PUBLISHABLE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif"}
WEB_ROOT = Path(__file__).parent.parent / "web"
WEB_ASSET_NAME_RE = re.compile(r"(?:css|js)/[a-z][a-z0-9-]*\.(?:css|js)")
WEB_ASSET_CONTENT_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


def asset_url(name: str) -> str:
    """A web asset URL that changes on every release, so the browser cache
    is safely bypassed after an update without needing any other cache-busting."""
    return f"/web/{name}?v={APP_VERSION}"


def _on_disk_app_version(install_root: Path) -> str | None:
    """Read APP_VERSION straight from product.py on disk, bypassing the
    already-imported APP_VERSION above -- which stays frozen at whatever it
    was when this process started, even after `git pull` changes the file."""
    try:
        text = (install_root / "src" / "product.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


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


def nav_menu(current_page: str = "", library_root: str = "") -> str:
    def _link(href, label, page_key):
        active = " active" if current_page == page_key else ""
        return f'<a class="menu-item{active}" href="{href}">{label}</a>'

    lib_display = html.escape(library_root) if library_root else ""
    lib_footer = (
        '<div class="menu-library">'
        f'<span class="menu-library-label">Current library</span>'
        f'<span class="menu-library-path" title="{html.escape(library_root)}">{lib_display}</span>'
        '</div>'
    ) if library_root else ""

    lib_line = f'<p>Current photo library: <code>{html.escape(library_root)}</code></p>' if library_root else ""

    about_modal = (
        '<div class="about-overlay" id="aboutOverlay">'
        '<div class="about-panel">'
        '<div class="about-header">'
        '<h2>About LensLedger</h2>'
        '<button type="button" class="about-close" id="aboutClose">&times;</button>'
        '</div>'
        '<div class="about-body">'
        f'<p><strong>LensLedger v{APP_VERSION}</strong> &mdash; {APP_TAGLINE}</p>'
        + lib_line
        + '<p>Local-first photo and video indexing, search, people review, and safe metadata publishing for Windows. '
        'Your photos stay on your machine &mdash; nothing is uploaded, no cloud required.</p>'
        '<p>Each library has a separate index. Use <a href="/publish">Publish photos</a> to write confirmed names to your JPEG metadata on your schedule. '
        'Every write creates a safety backup and verifies the picture pixels afterward.</p>'
        '<hr>'
        '<p><strong>Other projects by the developer</strong></p>'
        '<ul>'
        '<li><a href="https://github.com/WeirDave/WaxFrame-Professional" target="_blank" rel="noopener">WaxFrame Professional</a> '
        '&mdash; Multi-AI document refinement in your browser.</li>'
        '<li><a href="https://github.com/WeirDave/WaxFrame-Free" target="_blank" rel="noopener">WaxFrame Free</a> '
        '&mdash; Standalone browser-based free version. No install required.</li>'
        '<li><a href="https://github.com/WeirDave/Subscription-Wizard" target="_blank" rel="noopener">Subscription Wizard</a> '
        '&mdash; Firefox extension to compare and manage Amazon Subscribe &amp; Save subscriptions.</li>'
        '<li><a href="https://github.com/WeirDave/WD-Wireless-Tools" target="_blank" rel="noopener">WD Wireless Tools</a> '
        '&mdash; Ekahau workflow tools for wireless network professionals.</li>'
        '</ul>'
        '</div></div></div>'
    )

    return (
        '<div class="menu-backdrop" id="menuBackdrop"></div>'
        '<nav class="menu-panel" id="menuPanel">'
        '<div class="menu-panel-header">'
        '<div class="menu-panel-brand">'
        f'<img src="/logo.png?v={APP_VERSION}" alt="">'
        '<div class="menu-panel-brand-text">'
        f'<span class="menu-panel-title">{APP_NAME}</span>'
        f'<span class="menu-panel-tagline">{APP_TAGLINE}</span>'
        f'<span class="menu-panel-version">v{APP_VERSION}</span>'
        '<button type="button" class="menu-panel-about-btn" data-panel="about">ℹ About</button>'
        '</div></div>'
        '<button type="button" class="menu-close-btn" id="menuClose">&times;</button>'
        '</div>'
        '<div class="menu-body">'
        '<details class="menu-section" open>'
        '<summary class="menu-section-label">Navigation</summary>'
        + _link("/", "⌂ Home", "home")
        + _link("/scan-photos", "\U0001f50e Scan photos", "scan-photos")
        + _link("/faces-review", "\U0001f642 Name faces", "faces-review")
        + _link("/people-review", "\U0001f465 Review people", "people-review")
        + _link("/publish", "\U0001f4e4 Publish photos", "publish")
        + _link("/map", "\U0001f30d Photo map", "map")
        + _link("/auto-import", "\U0001f4f7 Auto-import photos", "auto-import")
        + _link("/settings", "⚙ Settings", "settings")
        + '</details>'
        '<div class="menu-divider"></div>'
        '<details class="menu-section">'
        '<summary class="menu-section-label">Help &amp; Support</summary>'
        '<button type="button" class="menu-item" data-panel="guide">\U0001f4d6 Quick guide</button>'
        + '<a class="menu-item" href="/manual" target="_blank" rel="noopener">\U0001f4d3 User manual</a>'
        + '<a class="menu-item" href="https://github.com/WeirDave/LensLedger/issues" target="_blank" rel="noopener">❓ Help &amp; support</a>'
        '<button type="button" class="menu-item" onclick="copyDiagnostics()">\U0001f4cb Copy diagnostics</button>'
        '<button type="button" class="menu-item" id="updateMenu" data-panel="update">⬆️ Check for updates</button>'
        '</details>'
        + lib_footer
        + '</div></nav>'
        + about_modal
    )


EXIFTOOL_PATH = Path(__file__).parent.parent / "tools" / "ExifTool" / "ExifTool.exe"
BACKUP_ROOT = backup_root()


def clean_tag(value: str) -> str:
    return " ".join(value.strip().split())[:120]


def like_pattern(value: str) -> str:
    """Build a SQL LIKE '%...%' pattern that matches a value literally.

    Without escaping, a user typing "%" or "_" gets surprisingly broad
    matches instead of searching for those literal characters. Pair with
    ``LIKE ? ESCAPE '\\'`` in the query.
    """
    escaped = LIKE_ESCAPE_RE.sub(r"\\\1", value)
    return f"%{escaped}%"


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


def _run_library_scan_job(handler_class, root, database, started_at):
    """Run one location/library scan pass, updating handler_class.library_job as it goes."""
    console_log(f"Scan started: {root}")
    try:
        def update_progress(counts):
            job_counts = {k: v for k, v in counts.items() if k != "error_details"}
            with handler_class.library_lock:
                handler_class.library_job = {
                    "state": "scanning", "message": f"Discovered {int(counts['scanned']):,} media files…",
                    "target_root": str(root), "started_at": started_at,
                    "error_details": counts.get("error_details", []),
                    **job_counts,
                }

        result = scan_library(
            root, database, progress=update_progress,
            should_cancel=handler_class.library_cancel.is_set,
            quiet=True,
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
            error_details = handler_class.library_job.get("error_details", [])
            state = "cancelled" if result == 3 else "complete"
            error_count = int(progress_counts.get("errors", 0))
            if result == 3:
                message = "Scan paused. Run it again to resume."
            elif error_count:
                message = f"Scan complete with {error_count:,} error{'s' if error_count != 1 else ''} — click the Errors count below for details."
            else:
                message = f"Library ready: {root}"
            handler_class.library_job = {
                "state": state, "message": message,
                "target_root": str(root), "error_details": error_details,
                **progress_counts, "summary": summary,
            }
        changed = int(progress_counts.get("changed", 0))
        removed = int(progress_counts.get("removed", 0))
        errors = int(progress_counts.get("errors", 0))
        total = summary.get("assets", 0)
        if state == "cancelled":
            console_log(f"Scan paused — {changed:,} changed, {total:,} total assets")
        elif errors:
            console_log(f"Scan complete with {errors:,} error(s) — {changed:,} changed, {removed:,} removed, {total:,} total assets")
        else:
            console_log(f"Scan complete — {changed:,} changed, {removed:,} removed, {total:,} total assets")
    except Exception as exc:
        console_log(f"Scan failed: {exc}")
        with handler_class.library_lock:
            handler_class.library_job = {
                "state": "error", "message": str(exc), "target_root": str(root),
            }


def _run_ocr_job(handler_class, database, since, workers, started_at):
    """Run one OCR pass, updating handler_class.ocr_job as it goes."""
    console_log("OCR started")
    try:
        def update_progress(counts):
            with handler_class.ocr_lock:
                handler_class.ocr_job = {
                    "state": "running",
                    "message": f"Processed {int(counts['attempted']):,} of {int(counts['total']):,} images…",
                    "started_at": started_at,
                    **counts,
                }

        result = ocr_assets(
            database, since, workers, progress=update_progress,
            should_cancel=handler_class.ocr_cancel.is_set,
            quiet=True,
        )
        with handler_class.ocr_lock:
            current = dict(handler_class.ocr_job)
            state = "cancelled" if result == 3 else "complete"
            error_count = int(current.get("errors", 0))
            if result == 3:
                message = "OCR paused safely. Start it again to resume."
            elif error_count:
                message = f"OCR complete with {error_count:,} error{'s' if error_count != 1 else ''} — click the Errors count below for details."
            else:
                message = "OCR pass complete."
            current.update({"state": state, "message": message})
            handler_class.ocr_job = current
        attempted = int(current.get("attempted", 0))
        if state == "cancelled":
            console_log(f"OCR paused after {attempted:,} images")
        elif error_count:
            console_log(f"OCR complete — {attempted:,} images, {error_count:,} error(s)")
        else:
            console_log(f"OCR complete — {attempted:,} images processed")
    except Exception as exc:
        console_log(f"OCR failed: {exc}")
        with handler_class.ocr_lock:
            handler_class.ocr_job = {"state": "error", "message": str(exc)}


def _run_semantic_index_job(handler_class, database, batch_size, started_at):
    """Run one meaning-search indexing pass, updating handler_class.semantic_job as it goes."""
    console_log("Meaning search indexing started")
    try:
        def update_progress(counts):
            with handler_class.semantic_lock:
                handler_class.semantic_job = {
                    "state": "running",
                    "message": f"Indexed {int(counts['indexed']):,} of {int(counts['total']):,} images this pass…",
                    "total": int(counts["total"]),
                    "indexed_this_pass": int(counts["indexed"]),
                    "errors": int(counts["errors"]),
                    "started_at": started_at,
                }

        result = build_semantic_index(
            database, batch_size=batch_size, progress=update_progress,
            should_cancel=handler_class.semantic_cancel.is_set,
        )
        with handler_class.semantic_lock:
            error_count = int(result["errors"])
            if result["cancelled"]:
                sem_message = "Meaning indexing paused safely."
            elif error_count:
                sem_message = f"Meaning index complete with {error_count:,} error{'s' if error_count != 1 else ''} — some images could not be indexed."
            else:
                sem_message = "Meaning index is ready."
            handler_class.semantic_job = {
                "state": "cancelled" if result["cancelled"] else "complete",
                "message": sem_message,
                "total": int(result["total"]),
                "indexed_this_pass": int(result["indexed"]),
                "errors": int(result["errors"]),
            }
        indexed = int(result["indexed"])
        if result["cancelled"]:
            console_log(f"Meaning search paused after {indexed:,} images")
        elif error_count:
            console_log(f"Meaning search complete — {indexed:,} indexed, {error_count:,} error(s)")
        else:
            console_log(f"Meaning search complete — {indexed:,} images indexed")
    except Exception as exc:
        console_log(f"Meaning search failed: {exc}")
        with handler_class.semantic_lock:
            handler_class.semantic_job = {"state": "error", "message": str(exc)}


def _run_face_scan_job(handler_class, database, library_root, started_at):
    console_log("Face detection started")
    """Run one face-detection pass, updating handler_class.face_scan_job as it goes."""
    try:
        def update_progress(counts):
            with handler_class.face_scan_lock:
                handler_class.face_scan_job = {
                    "state": "running",
                    "message": f"Scanned {int(counts['processed']):,} of {int(counts['total']):,} photos, "
                               f"{int(counts['faces_found']):,} faces found…",
                    "started_at": started_at,
                    **counts,
                }

        result = scan_for_faces(
            database, library_root, progress=update_progress,
            should_cancel=handler_class.face_scan_cancel.is_set,
        )
        with handler_class.face_scan_lock:
            current = dict(handler_class.face_scan_job)
            state = "cancelled" if result["cancelled"] else "complete"
            face_error_count = int(result.get("errors", 0))
            if result["cancelled"]:
                face_message = "Face detection paused safely. Start it again to resume."
            elif face_error_count:
                face_message = (
                    f"Face detection complete with {face_error_count:,} error{'s' if face_error_count != 1 else ''}: "
                    f"{result['faces_found']:,} faces found — click the Errors count below for details."
                )
            else:
                face_message = (
                    f"Face detection complete: {result['faces_found']:,} faces found. "
                    f"Name a few people in \"Name faces\" above, then go to \"Review people\" "
                    f"where LensLedger will suggest matches across your whole library."
                )
            current.update({"state": state, "message": face_message})
            handler_class.face_scan_job = current
        faces = int(result.get("faces_found", 0))
        processed = int(result.get("processed", 0))
        if result["cancelled"]:
            console_log(f"Face detection paused after {processed:,} photos, {faces:,} faces found")
        elif face_error_count:
            console_log(f"Face detection complete — {faces:,} faces in {processed:,} photos, {face_error_count:,} error(s)")
        else:
            console_log(f"Face detection complete — {faces:,} faces found in {processed:,} photos")
    except Exception as exc:
        console_log(f"Face detection failed: {exc}")
        with handler_class.face_scan_lock:
            handler_class.face_scan_job = {"state": "error", "message": str(exc)}


class _ScanAllStopped(Exception):
    """Raised internally to unwind _run_scan_all_job after a cancel request."""


_SCAN_ALL_LABELS = {
    "location": "Scanning photo locations",
    "ocr": "Running local text recognition (OCR)",
    "semantic": "Building the meaning-search index",
    "face": "Scanning for faces",
}


def _run_scan_all_job(handler_class, root, database, scan_all_started_at):
    """Run location -> OCR -> meaning search -> face detection back to back.

    Meaning search and face detection are only run if the user already
    installed their optional local models; installing is a separate,
    explicit, consenting action (large downloads) that this orchestrator
    never triggers on its own.
    """

    def set_step(step):
        with handler_class.scan_all_lock:
            handler_class.scan_all_job = {
                "state": "running", "step": step,
                "message": _SCAN_ALL_LABELS[step] + "…",
                "started_at": scan_all_started_at,
            }

    def check_step(job_dict, failure_message):
        if job_dict.get("state") == "error":
            raise ValueError(job_dict.get("message") or failure_message)
        if handler_class.scan_all_cancel.is_set() or job_dict.get("state") == "cancelled":
            raise _ScanAllStopped()

    console_log("Run all scans: started (manual)")
    ran: list[str] = []
    skipped: list[str] = []
    try:
        set_step("location")
        with handler_class.library_lock:
            handler_class.library_job = {
                "state": "scanning", "message": "Discovering photos and videos…",
                "target_root": str(root), "scanned": 0, "changed": 0,
                "unchanged": 0, "removed": 0, "errors": 0, "placeholders": 0,
                "started_at": utc_now(),
            }
            handler_class.library_cancel.clear()
        _run_library_scan_job(handler_class, root, database, handler_class.library_job["started_at"])
        check_step(handler_class.library_job, "the location scan failed")
        ran.append("photo locations")

        set_step("ocr")
        with handler_class.ocr_lock:
            handler_class.ocr_job = {
                "state": "running", "message": "Preparing local text recognition…",
                "total": 0, "attempted": 0, "with_text": 0, "errors": 0, "started_at": utc_now(),
            }
            handler_class.ocr_cancel.clear()
        _run_ocr_job(handler_class, database, None, 4, handler_class.ocr_job["started_at"])
        check_step(handler_class.ocr_job, "OCR failed")
        ran.append("OCR")

        if semantic_is_available():
            set_step("semantic")
            with handler_class.semantic_lock:
                handler_class.semantic_job = {
                    "state": "running", "message": "Loading the optional local meaning model…",
                    "total": 0, "indexed_this_pass": 0, "errors": 0, "started_at": utc_now(),
                }
                handler_class.semantic_cancel.clear()
            _run_semantic_index_job(handler_class, database, 16, handler_class.semantic_job["started_at"])
            check_step(handler_class.semantic_job, "meaning indexing failed")
            ran.append("meaning search")
        else:
            skipped.append("meaning search")

        if face_is_available():
            set_step("face")
            with handler_class.face_scan_lock:
                handler_class.face_scan_job = {
                    "state": "running", "message": "Preparing local face detection…",
                    "total": 0, "processed": 0, "faces_found": 0, "errors": 0, "started_at": utc_now(),
                }
                handler_class.face_scan_cancel.clear()
            _run_face_scan_job(handler_class, database, root, handler_class.face_scan_job["started_at"])
            check_step(handler_class.face_scan_job, "face detection failed")
            ran.append("face detection")
        else:
            skipped.append("face detection")

        total_errors = (
            int(handler_class.library_job.get("errors", 0))
            + int(handler_class.ocr_job.get("errors", 0))
            + int(handler_class.semantic_job.get("errors", 0))
            + int(handler_class.face_scan_job.get("errors", 0))
        )
        message = "Ran: " + ", ".join(ran) + "."
        if total_errors:
            message += f" {total_errors:,} error{'s' if total_errors != 1 else ''} — check each section below for details."
        if skipped:
            message += " Skipped (not set up yet): " + ", ".join(skipped) + " — set up below."
        console_log(f"Run all scans: complete — {', '.join(ran)}, {total_errors} error(s)")
        with handler_class.scan_all_lock:
            handler_class.scan_all_job = {
                "state": "complete", "step": None, "message": message,
            }
    except _ScanAllStopped:
        with handler_class.scan_all_lock:
            handler_class.scan_all_job = {
                "state": "cancelled", "step": None,
                "message": "Stopped after the current step. Run it again to continue.",
            }
    except Exception as exc:
        with handler_class.scan_all_lock:
            handler_class.scan_all_job = {"state": "error", "step": None, "message": str(exc)}


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
    semantic_install_lock = threading.Lock()
    semantic_install_job: dict[str, object] = {"state": "idle", "message": ""}
    face_scan_lock = threading.Lock()
    face_scan_job: dict[str, object] = {"state": "idle", "message": ""}
    face_scan_cancel = threading.Event()
    face_install_lock = threading.Lock()
    face_install_job: dict[str, object] = {"state": "idle", "message": ""}
    scan_all_lock = threading.Lock()
    scan_all_job: dict[str, object] = {"state": "idle", "message": ""}
    scan_all_cancel = threading.Event()
    people_merge_lock = threading.Lock()
    update_lock = threading.Lock()
    update_job: dict[str, object] = {"state": "idle", "message": "Checking has not started."}
    folder_watcher: FolderWatcher | None = None
    ingest_pipeline: IngestPipeline | None = None

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
        if url.path.startswith("/web/"):
            return self.serve_web_asset(url.path.removeprefix("/web/"))
        if url.path == "/logo.png":
            return self.serve_logo()
        if url.path == "/favicon.png":
            return self.serve_favicon()
        if url.path == "/world-map.svg":
            return self.serve_world_map()
        if url.path == "/media":
            return self.serve_media(params)
        if url.path == "/media-face":
            return self.serve_face_media(params)
        if url.path == "/api/asset":
            return self.asset_detail(params)
        if url.path == "/api/trash":
            return self.trash_history()
        if url.path == "/api/library/status":
            return self.library_status()
        if url.path == "/api/library/errors":
            return self.library_errors()
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
        if url.path == "/api/ocr/errors":
            return self.ocr_errors()
        if url.path == "/api/semantic/status":
            return self.semantic_job_status()
        if url.path == "/api/semantic/errors":
            return self.semantic_errors()
        if url.path == "/api/faces/status":
            return self.face_scan_job_status()
        if url.path == "/api/faces/errors":
            return self.face_scan_errors()
        if url.path == "/api/scan-all/status":
            return self.scan_all_status()
        if url.path == "/api/version":
            return self.version_info()
        if url.path == "/api/update/status":
            return self.update_status()
        if url.path == "/api/people/review/queue":
            return self.people_review_queue(params)
        if url.path == "/api/faces/unidentified":
            return self.unidentified_faces(params)
        if url.path == "/people-review":
            return self.people_review_page(params)
        if url.path == "/faces-review":
            return self.faces_review_page()
        if url.path == "/publish":
            return self.publish_page()
        if url.path == "/api/publish/pending":
            return self.publish_pending()
        if url.path == "/map":
            return self.map_page()
        if url.path == "/scan-photos":
            return self.scan_photos_page()
        if url.path == "/auto-import":
            return self.auto_import_page()
        if url.path == "/auto-ingest":
            self.send_response(301)
            self.send_header("Location", "/auto-import")
            self.end_headers()
            return
        if url.path == "/settings":
            return self.settings_page()
        if url.path == "/manual":
            return self.manual_page()
        if url.path == "/api/settings":
            return self.get_settings()
        if url.path == "/api/settings/export-status":
            return self.export_status()
        if url.path == "/api/watcher/status":
            return self.watcher_status()
        if url.path == "/api/ingest/status":
            return self.ingest_status()
        if url.path == "/api/ingest/log":
            return self.ingest_log()
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
            if route == "/api/faces/name":
                return self.name_face(body)
            if route == "/api/faces/ignore":
                return self.ignore_face(body)
            if route == "/api/faces/unknown":
                return self.mark_face_unknown(body)
            if route == "/api/faces/find-more":
                return self.find_more_faces(body)
            if route == "/api/faces/name-batch":
                return self.name_face_batch(body)
            if route == "/api/faces/publish-person":
                return self.publish_person_metadata(body)
            if route == "/api/publish/run":
                return self.publish_run(body)
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
            if route == "/api/tag/add-batch":
                return self.add_tag_batch(body)
            if route == "/api/review-bin":
                return self.move_to_review_bin(body)
            if route == "/api/review-bin/batch":
                return self.move_to_review_bin_batch(body)
            if route == "/api/review-bin/restore":
                return self.restore_from_review_bin(body)
            if route == "/api/review-bin/delete":
                return self.delete_from_review_bin(body)
            if route == "/api/review-bin/empty":
                return self.empty_review_bin()
            if route == "/api/publish/preview":
                return self.preview_publish(body)
            if route == "/api/publish":
                return self.publish_metadata(body)
            if route == "/api/publish/restore":
                return self.restore_published_metadata(body)
            if route == "/api/library/browse":
                return self.browse_library(body)
            if route == "/api/library/add":
                return self.add_library(body)
            if route == "/api/library/relocate":
                return self.relocate_library(body)
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
            if route == "/api/semantic/install":
                return self.install_semantic_requirements(body)
            if route == "/api/faces/start":
                return self.start_face_scan(body)
            if route == "/api/faces/cancel":
                return self.cancel_face_scan(body)
            if route == "/api/faces/install":
                return self.install_face_requirements(body)
            if route == "/api/scan-all/start":
                return self.start_scan_all(body)
            if route == "/api/scan-all/cancel":
                return self.cancel_scan_all(body)
            if route == "/api/update/check":
                return self.check_update(body)
            if route == "/api/update/install":
                return self.install_update(body)
            if route == "/api/update/restart-source":
                return self.restart_source(body)
            if route == "/api/reveal-file":
                return self.reveal_file(body)
            if route == "/api/settings/save":
                return self.save_settings_api(body)
            if route == "/api/settings/remove-library":
                return self.remove_library(body)
            if route == "/api/ingest/save":
                return self.save_ingest_config(body)
            if route == "/api/ingest/run":
                return self.ingest_run_now(body)
            if route == "/api/settings/export":
                return self.export_database(body)
            if route == "/api/settings/import":
                return self.import_database(body)
            return self.send_json({"error": "not found"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                return self.send_json({
                    "error": "This change did not go through -- a scan or another change is using the "
                             "catalog right now. It's not stuck, just slower than usual because of that; "
                             "try again in a few seconds.",
                    "busy": True,
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
<title>Set up {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/onboarding.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body {bootstrap_attr({"csrf": self.csrf_token})}><main class="shell"><header class="brand"><img src="/logo.png?v={APP_VERSION}" alt=""><div><h1>{APP_NAME}</h1><p>{APP_TAGLINE}</p></div><span class="version">v{APP_VERSION}</span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button></header><section class="card">
<div class="intro"><h2>Let’s find your photo library</h2><p>Choose a folder that contains photos or videos. LensLedger will build a private, searchable inventory without moving, renaming, uploading, or changing your files.</p><p class="data-location" id="dataLocationNote">Your photos stay exactly where they are. The searchable index is stored in a hidden <code>.LensLedger</code> folder inside your photo library by default, so the index travels with your photos.</p></div>
<div class="steps"><div class="step"><strong>1 · Discover</strong><span>Record file locations, types, dates, and locally available metadata.</span></div><div class="step"><strong>2 · Review</strong><span>See exactly what was found, including cloud files that are not downloaded.</span></div><div class="step"><strong>3 · Enrich</strong><span>Add subjects, people, OCR, and approved metadata at your pace.</span></div></div>
<section class="chooser"><h3>Choose your first library</h3><p>You can add and switch between more libraries later. Start with the folder that best represents one photo collection.</p><div class="suggestions" id="suggestions"></div><div class="path-row"><input id="libraryPath" aria-label="Photo library folder" placeholder="C:\\Users\\you\\Pictures"><button type="button" class="secondary" id="browse">Browse…</button></div><div class="db-location-row"><label for="dbLocation">Store the database in:</label><select id="dbLocation"><option value="library">Inside the photo library folder (default)</option><option value="appdata">Application data folder</option></select><span class="hint">The database is stored in a hidden <code>.LensLedger</code> folder inside your photo library, so the index travels with your photos.</span></div><div class="actions"><span class="privacy">🔒 The index stays on this computer. Cloud placeholders are counted without forcing a download.</span><span class="spacer"></span><button type="button" id="start">Build my library</button></div></section>
<section class="progress-panel" id="progressPanel" aria-live="polite"><div class="progress-head"><div><h3 id="progressTitle">Building your library</h3><p id="progressMessage">Preparing scan…</p></div><span class="spacer"></span><button type="button" class="danger" id="cancel">Pause scan</button></div><div class="bar"><span></span></div><div class="metrics"><div class="metric"><strong id="scanned">0</strong><span>discovered</span></div><div class="metric"><strong id="changed">0</strong><span>indexed</span></div><div class="metric"><strong id="unchanged">0</strong><span>unchanged</span></div><div class="metric"><strong id="placeholders">0</strong><span>cloud-only</span></div><div class="metric"><strong id="errors">0</strong><span>errors</span></div></div><div class="complete-grid" id="completeGrid"></div><div class="next-step" id="nextStep"><p>Want LensLedger to also read visible text in your photos (signs, screenshots, receipts) so it's searchable? This runs in the background on your computer — it keeps going even if you move on or close this tab — and you can pause it any time from the "Scan your photos" page.</p><button type="button" class="secondary" id="startOcr">Scan for text now</button></div><div class="completion-actions"><button type="button" id="enterLibrary">Open my library</button></div></section>
</section></main><script src="{asset_url('js/onboarding.js')}" defer></script>
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
                   GROUP BY ROUND(gps_latitude, 3), ROUND(gps_longitude, 3)
                   ORDER BY photo_count DESC, first_date DESC
                   LIMIT 50000"""
            ).fetchall()
        self.send_json({
            "located": located,
            "pending": pending,
            "clusters": [dict(row) for row in rows],
        })

    def map_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Photo map — {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/map.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body {bootstrap_attr({"csrf": self.csrf_token})}><header><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div><h1>Photo map</h1><p>Embedded locations from the current library · read-only and kept local</p></div><span class="spacer"></span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button><span class="count" id="count">Loading locations…</span></header>
{nav_menu("map", str(self.library_root))}
<main class="map-shell" id="viewport"><div id="world"></div><div class="controls"><button type="button" id="zoomIn" aria-label="Zoom in">+</button><button type="button" id="zoomOut" aria-label="Zoom out">−</button><button type="button" id="reset" aria-label="Reset map">⌂</button></div><div class="legend"><strong>Photo locations</strong>Scroll to zoom and drag to pan. Nearby coordinates are grouped; select a marker to browse every photo from that place.<small class="map-credit">Coastlines © <a href="https://commons.wikimedia.org/wiki/User:Tubs" target="_blank" rel="noopener">TUBS</a>, Wikimedia Commons (CC BY-SA 3.0)</small></div><aside class="details" id="details"><img id="preview" alt="Representative photo from this location"><div class="details-body"><h2 id="placeTitle"></h2><p id="placeDates"></p><p id="placeCoords"></p><div class="details-actions"><a class="button" id="openPhoto">Open photo</a><a class="button secondary" id="viewAllHere">View all photos here</a><a class="button secondary" id="openStreetMap" target="_blank" rel="noopener">Open in OpenStreetMap ↗</a><button type="button" id="closeDetails">Close</button></div></div></aside><section class="empty" id="empty"><div><h2>No mapped photos yet</h2><p id="emptyText">Run an incremental library scan to collect embedded GPS coordinates. LensLedger reads them locally and never writes location data back to your files.</p><a class="button" href="/scan-photos">Scan your photos</a></div></section></main>
<script src="{asset_url('js/map.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def scan_photos_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scan your photos — {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/scan-photos.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body {bootstrap_attr({"csrf": self.csrf_token, "currentLibrary": str(self.library_root), "version": APP_VERSION})}><header><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div><h1>Scan your photos</h1><p>Everything that makes your library searchable, and your backups</p></div><span class="spacer"></span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button><span class="version">v{APP_VERSION}</span></header>
{nav_menu("scan-photos", str(self.library_root))}
<main>
<section class="card"><h2>Overview</h2><div class="health-summary" id="healthSummary"></div><p class="cloud-scope" id="cloudScope"></p><details class="scan-details"><summary>Database &amp; folder details</summary><div class="health-paths" id="healthPaths"></div><p class="data-location">Your photos stay exactly where they are. The searchable index, backups, and everything else LensLedger creates live separately at <code>{html.escape(str(data_root()))}</code> — never inside your photo folders.</p></details></section>
<section class="card job-card"><div class="section-title"><h2>Run all scans</h2><button type="button" class="info-button" data-help="scanAllHelp" aria-label="About Run all scans">i</button></div><div class="help-popover" id="scanAllHelp">Runs the scans below back to back — photo locations, then OCR, then meaning search and face detection if you've already set them up — so you do not have to start each one by hand.</div><div class="job-status"><span class="spinner" id="scanAllSpinner"></span><p id="scanAllMessage">Checking status…</p><span class="elapsed" id="scanAllElapsed"></span></div><div class="progress-bar" id="scanAllBarWrap" hidden><span id="scanAllBar"></span></div><div class="job-actions"><span class="spacer"></span><button type="button" class="secondary" id="pauseScanAll">Stop after this step</button><button type="button" id="startScanAll">Run all scans</button></div></section>
<section class="card job-card"><div class="section-title"><h2>Photo locations (GPS)</h2><button type="button" class="info-button" data-help="locationHelp" aria-label="About Photo locations">i</button></div><div class="help-popover" id="locationHelp">Finds GPS coordinates embedded in your photos so they appear on the Photo Map. This runs a full incremental scan of your library — it also picks up any new or changed files — and is safe to run any time.</div><div class="job-status"><span class="spinner" id="locationSpinner"></span><p id="locationMessage">Checking status…</p><span class="elapsed" id="locationElapsed"></span></div><div class="progress-bar" id="locationBarWrap" hidden><span id="locationBar"></span></div><div class="health-summary ocr-summary" id="locationMetrics"></div><div class="job-actions"><span class="spacer"></span><button type="button" class="secondary" id="pauseLocation">Pause</button><button type="button" id="startLocation">Scan for photo locations</button></div></section>
<section class="card job-card"><div class="section-title"><h2>Local text recognition (OCR)</h2><button type="button" class="info-button" data-help="ocrHelp" aria-label="About OCR">i</button></div><div class="help-popover" id="ocrHelp">Reads visible text in photos — signs, screenshots, receipts — so it becomes searchable.</div><div class="job-status"><span class="spinner" id="ocrSpinner"></span><p id="ocrMessage">Loading OCR status…</p><span class="elapsed" id="ocrElapsed"></span></div><div class="progress-bar" id="ocrBarWrap" hidden><span id="ocrBar"></span></div><div class="health-summary ocr-summary" id="ocrMetrics"></div><div class="job-actions"><label>Only since <input type="date" id="ocrSince" title="Skip photos taken before this date — useful for scanning only recent additions"></label><span class="spacer"></span><button type="button" class="secondary" id="pauseOcr">Pause</button><button type="button" id="startOcr">Start / resume OCR</button></div></section>
<section class="card job-card"><div class="section-title"><h2>Meaning search (optional)</h2><button type="button" class="info-button" data-help="semanticHelp" aria-label="About Meaning search">i</button></div><div class="help-popover" id="semanticHelp">Search photos by what they show, not just their tags — try "a birthday cake" or "someone holding a dog." Runs entirely on this computer; nothing is ever uploaded. It is optional because the model software is a large download (roughly 1-2 GB) most people do not need.</div><div class="job-status"><span class="spinner" id="semanticSpinner"></span><p id="semanticMessage">Checking status…</p><span class="elapsed" id="semanticElapsed"></span></div><div class="progress-bar" id="semanticBarWrap" hidden><span id="semanticBar"></span></div><div class="health-summary ocr-summary" id="semanticMetrics"></div><div class="job-actions" id="semanticInstallActions"><span class="spacer"></span><a href="/settings#meaning-search" class="setup-link" id="installSemantic">Set up meaning search in Settings</a></div><div class="job-actions" id="semanticBuildActions"><span class="spacer"></span><button type="button" class="secondary" id="pauseSemantic">Pause</button><button type="button" id="startSemantic">Build / resume meaning index</button></div></section>
<section class="card job-card"><div class="section-title"><h2>Face detection (optional)</h2><button type="button" class="info-button" data-help="faceHelp" aria-label="About Face detection">i</button></div><div class="help-popover" id="faceHelp">Find faces in photos LensLedger has not looked at yet, so more of your library becomes eligible for People suggestions. Runs entirely on this computer using a local model; nothing is ever uploaded. Scanning tens of thousands of photos can take a while, so it runs in the background and can be paused any time. It is optional and a separate download (roughly 500 MB) because the face-detection model's license does not allow LensLedger to bundle or redistribute it.</div><div class="job-status"><span class="spinner" id="faceScanSpinner"></span><p id="faceScanMessage">Checking status…</p><span class="elapsed" id="faceScanElapsed"></span></div><div class="progress-bar" id="faceScanBarWrap" hidden><span id="faceScanBar"></span></div><div class="health-summary ocr-summary" id="faceScanMetrics"></div><div class="job-actions" id="faceInstallActions"><span class="spacer"></span><button type="button" id="installFaceScan">Set up face detection</button></div><div class="job-actions" id="faceScanActions"><span class="spacer"></span><button type="button" class="secondary" id="pauseFaceScan">Pause</button><button type="button" id="startFaceScan">Scan for faces</button></div></section>
<section class="card"><h2>Backups</h2><div class="backup-row"><button type="button" class="secondary" id="backupDatabase">Create verified database backup</button><span id="backupStatus"></span></div></section>
</main>
<div class="modal-backdrop" id="scanModalBackdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="scanModalTitle"><div class="modal-head"><h2 id="scanModalTitle"></h2><button type="button" class="modal-close" id="scanModalClose">Close</button></div><div id="scanModalBody"></div></section></div>
<script src="{asset_url('js/scan-photos.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def settings_page(self):
        settings = load_settings()
        config = load_library_config()
        libraries = []
        for value in config.get("libraries", []):
            root = Path(str(value))
            if root.is_dir():
                libraries.append({"label": root.name or str(root), "path": str(root.resolve())})
        scan = settings.get("scan", {})
        display = settings.get("display", {})
        watch = settings.get("watch", {})
        ingest = settings.get("ingest", {})
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settings — {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/settings.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body {bootstrap_attr({"csrf": self.csrf_token, "settings": settings, "libraries": libraries, "currentRoot": str(self.library_root), "models": AVAILABLE_MODELS})}><header><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div><h1>Settings</h1><p>Configure LensLedger's behavior, libraries, and preferences</p></div><span class="spacer"></span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button><span class="version">v{APP_VERSION}</span></header>
{nav_menu("settings", str(self.library_root))}
<main class="settings-layout"><nav class="settings-toc"><h3>Settings</h3><ul><li><a href="#libraries">Photo libraries</a></li><li><a href="#scan-prefs">Scan preferences</a></li><li><a href="#meaning-search">Meaning search</a></li><li><a href="#display-prefs">Display preferences</a></li><li><a href="#folder-watching">Folder watching</a></li><li><a href="#auto-import">Auto-import photos</a></li><li><a href="#database">Database</a></li></ul></nav><div class="settings-content">
<section class="card" id="libraries"><h2>Photo libraries</h2><p>Manage your photo collections. Switch between libraries or add new ones.</p><div class="library-list" id="libraryList"></div><div class="library-actions"><button type="button" class="secondary" id="addLibrary">Add library…</button></div></section>
<section class="card" id="scan-prefs"><h2>Scan preferences</h2>
<div class="field"><label for="ocrWorkers">OCR worker threads</label><input type="number" id="ocrWorkers" min="1" max="16" value="{int(scan.get('ocr_workers', 4))}"><span class="hint">More workers scan faster but use more CPU. Default: 4</span></div>
<div class="field"><label for="ocrBatchSize">OCR batch size</label><input type="number" id="ocrBatchSize" min="10" max="500" value="{int(scan.get('ocr_batch_size', 50))}"><span class="hint">Photos processed per OCR commit. Default: 50</span></div>
<div class="field"><label for="semanticBatchSize">Meaning search batch</label><input type="number" id="semanticBatchSize" min="1" max="128" value="{int(scan.get('semantic_batch_size', 16))}"><span class="hint">Images per CLIP encoding batch. Default: 16</span></div></section>
<section class="card" id="meaning-search"><h2>Meaning search model</h2><div id="semanticSetupArea"><p id="semanticSetupMsg">Checking meaning search status…</p><div class="progress-bar" id="semanticInstallBarWrap" hidden><span id="semanticInstallBar"></span></div><div class="semantic-install-actions" id="semanticInstallActions"><button type="button" id="installSemantic">Set up meaning search</button></div></div><p class="model-intro" id="modelIntro" hidden>Choose the CLIP model for meaning search. Better models produce higher quality results but are larger and slower. Changing the model will re-index your photos on the next meaning search run.</p><div class="model-list" id="modelList"></div></section>
<section class="card" id="display-prefs"><h2>Display preferences</h2>
<div class="field"><label for="photosPerPage">Photos per page</label><input type="number" id="photosPerPage" min="50" max="1000" value="{int(display.get('photos_per_page', 250))}"><span class="hint">Number of photos loaded per filmstrip page. Default: 250</span></div>
<div class="field"><label for="defaultSort">Default sort order</label><select id="defaultSort"><option value="newest" {"selected" if display.get("default_sort", "newest") == "newest" else ""}>Newest first</option><option value="oldest" {"selected" if display.get("default_sort") == "oldest" else ""}>Oldest first</option><option value="name" {"selected" if display.get("default_sort") == "name" else ""}>By name</option></select></div>
<div class="field"><label for="filmstripSize">Filmstrip thumbnail size</label><select id="filmstripSize"><option value="small" {"selected" if display.get("filmstrip_size") == "small" else ""}>Small</option><option value="medium" {"selected" if display.get("filmstrip_size", "medium") == "medium" else ""}>Medium</option><option value="large" {"selected" if display.get("filmstrip_size") == "large" else ""}>Large</option></select></div></section>
<section class="card" id="folder-watching"><h2>Folder watching</h2><p>Automatically detect new and changed photos without manually running a scan.</p>
<div class="toggle-row"><label class="toggle-switch"><input type="checkbox" id="watchEnabled" {"checked" if watch.get("enabled") else ""}><span class="slider"></span></label><label for="watchEnabled">Enable automatic folder watching</label></div>
<div class="field"><label for="watchInterval">Check interval (minutes)</label><input type="number" id="watchInterval" min="5" max="1440" value="{int(watch.get('interval_minutes', 5))}"><span class="hint">How often to check for new files when watching is enabled. Default: 5</span></div></section>
<section class="card" id="auto-import"><h2>Auto-import photos</h2><p>Automatically import new photos from a source folder and sort them into your library. <a href="/auto-import">Configure source, destination, and sorting rules →</a></p>
<div class="toggle-row"><label class="toggle-switch"><input type="checkbox" id="ingestEnabled" {"checked" if ingest.get("enabled") else ""}><span class="slider"></span></label><label for="ingestEnabled">Enable automatic photo import</label></div>
<div class="field"><label for="ingestInterval">Check interval (minutes)</label><input type="number" id="ingestInterval" min="5" max="1440" value="{int(ingest.get('interval_minutes', 10))}"><span class="hint">How often to check for new photos when import is enabled. Default: 10</span></div></section>
<section class="card" id="database"><h2>Database</h2><p>Export creates a portable copy of your tags, people, and scan results — useful for backups or moving to a new machine. Import restores from a previous export. Your photos are not included; only the LensLedger index is transferred.</p>
<div class="library-actions"><button type="button" class="secondary" id="exportDatabase">Export database</button><button type="button" class="secondary" id="importDatabase">Import database</button><span class="export-status" id="exportStatus"></span></div>
<p class="data-location">Database: <code>{html.escape(str(library_db_path(Path(self.library_root))))}</code></p>
<p class="data-location">Application data: <code>{html.escape(str(data_root()))}</code></p></section>
<div class="actions-bar"><button type="button" id="saveSettings">Save settings</button></div>
</div></main>
<div class="toast" id="toast"></div>
<script src="{asset_url('js/settings.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def manual_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>User Manual — {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/manual.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body><header><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div><h1>User Manual</h1><p>Complete guide to using LensLedger</p></div><span class="spacer"></span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button><span class="version">v{APP_VERSION}</span></header>
{nav_menu("manual", str(self.library_root))}
<main id="top">

<div class="manual-toc">
<h2>Contents</h2>
<ol>
<li><a href="#getting-started">Getting Started</a></li>
<li><a href="#library-management">Library Management</a></li>
<li><a href="#scanning">Scanning Your Photos</a></li>
<li><a href="#searching">Searching and Browsing</a></li>
<li><a href="#viewing">Viewing and Editing Metadata</a></li>
<li><a href="#people">People and Faces</a></li>
<li><a href="#map">Photo Map</a></li>
<li><a href="#auto-import">Auto-import Photos</a></li>
<li><a href="#publishing">Publishing Metadata</a></li>
<li><a href="#review-bin">Review Bin (Trash)</a></li>
<li><a href="#batch">Batch Editing</a></li>
<li><a href="#database">Database and Backups</a></li>
<li><a href="#settings">Settings</a></li>
<li><a href="#shortcuts">Keyboard and Mouse Shortcuts</a></li>
<li><a href="#formats">Supported File Formats</a></li>
<li><a href="#advanced">Advanced Configuration</a></li>
</ol>
</div>

<section class="manual-section" id="getting-started">
<h2>1. Getting Started</h2>
<p>When you first launch LensLedger, the setup page walks you through creating your first library.</p>
<h3>How libraries work</h3>
<p>A <strong>library = one root folder</strong>. Everything inside that folder (all subfolders, any depth) belongs to the library. LensLedger creates a separate database for each library &mdash; there is no shared master database. Each database stores only the index for its own library&rsquo;s photos.</p>
<ul>
<li>Add more subfolders, reorganize within the root &mdash; the next scan picks up the changes automatically.</li>
<li>Files outside the root folder are not tracked. If you move a photo out, the next scan marks it as removed.</li>
<li>You can have multiple libraries and switch between them from <a href="/settings">Settings</a>.</li>
</ul>
<h3>Choose a photo folder</h3>
<p>Select the folder that contains your photos. LensLedger shows suggested locations (Pictures, Dropbox Photos, Camera Uploads, OneDrive, removable drives) or you can click <strong>Browse</strong> to pick any folder.</p>
<h3>Where the database is stored</h3>
<p>By default, the database is stored in a hidden <code>.LensLedger</code> folder inside your photo library (e.g. <code>Photos\\.LensLedger\\LensLedger-Photos.sqlite3</code>). This means the index travels with your photos &mdash; copy the folder to another drive and everything comes with it.</p>
<p>You can also choose to store the database in the application data folder (<code>%LOCALAPPDATA%\\LensLedger\\Libraries\\</code>) if you prefer to keep the photo folder completely clean.</p>
<h3>Build your library</h3>
<p>Click <strong>Build my library</strong> to start an initial scan. LensLedger discovers all photos and videos, records their locations, types, dates, and any embedded metadata (EXIF, IPTC, XMP). Cloud-only files (e.g. Dropbox Smart Sync placeholders) are counted without forcing a download.</p>
<p>The scan can be paused and resumed at any time.</p>
<h3>Optional text scanning</h3>
<p>After the initial scan completes, LensLedger offers to scan your photos for visible text (signs, screenshots, receipts) using local OCR. This runs in the background and can be started later from the <a href="/scan-photos">Scan your photos</a> page.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="library-management">
<h2>2. Library Management</h2>
<p>LensLedger supports multiple libraries. You can switch between them, add new ones, and relocate existing ones. All library management happens on the <a href="/settings">Settings</a> page.</p>
<h3>Adding a library</h3>
<p>Click <strong>Add library</strong> in Settings. Browse to the folder, then choose where to store the database. By default, the database goes in a hidden <code>.LensLedger</code> folder inside your photo library. The library is registered without starting a scan immediately, so you can add libraries even while another scan is running.</p>
<h3>Switching libraries</h3>
<p>Click <strong>Switch</strong> next to any library in the list. Your current library remains in the list and its index is preserved.</p>
<h3>Relocating a library</h3>
<p>If you moved your photos from one location to another (for example, from a USB drive to your hard drive), click <strong>Relocate</strong> next to the library. Browse to the new folder location. LensLedger updates all file paths in the database so your existing tags, people, OCR results, and scan data carry over.</p>
<div class="tip"><strong>Important:</strong> Your photos must already be at the new location before you relocate. LensLedger does not move files &mdash; it updates the database to point at the new path.</div>
<h3>Removing a library</h3>
<p>Click <strong>Remove</strong> to take a library out of the list. This does not delete any photos or the database &mdash; it only removes the entry from the library list. You cannot remove the currently active library.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="scanning">
<h2>3. Scanning Your Photos</h2>
<p>The <a href="/scan-photos">Scan your photos</a> page is the dashboard for all scanning operations. Each scan runs in the background and can be paused and resumed.</p>
<h3>Run all scans</h3>
<p>Runs photo locations, OCR, meaning search, and face detection back to back, so you don&rsquo;t have to start each one manually.</p>
<h3>Photo locations (GPS)</h3>
<p>An incremental library scan that discovers new and changed files and extracts embedded GPS coordinates. These coordinates power the <a href="/map">Photo map</a>. Safe to run any time.</p>
<h3>Local text recognition (OCR)</h3>
<p>Reads visible text in your photos &mdash; signs, screenshots, receipts, documents &mdash; and makes it searchable.</p>
<table>
<tr><th>Setting</th><th>Range</th><th>Default</th><th>Description</th></tr>
<tr><td>OCR workers</td><td>1&ndash;16</td><td>4</td><td>More workers scan faster but use more CPU</td></tr>
<tr><td>Batch size</td><td>10&ndash;500</td><td>50</td><td>Photos processed per commit</td></tr>
<tr><td>Only since</td><td>Date</td><td>&mdash;</td><td>Skip photos taken before this date</td></tr>
</table>
<h3>Meaning search (optional)</h3>
<p>Uses a local AI vision model (CLIP) to search photos by natural language descriptions like &ldquo;a birthday cake&rdquo; or &ldquo;sunset over water.&rdquo; Requires a one-time model download.</p>
<table>
<tr><th>Model</th><th>Size</th><th>Quality</th></tr>
<tr><td>ViT-B-32</td><td>~400 MB</td><td>Good general quality, fast</td></tr>
<tr><td>ViT-B-16</td><td>~600 MB</td><td>Better quality, slower</td></tr>
<tr><td>ViT-L-14</td><td>~1.8 GB</td><td>High quality, significantly slower</td></tr>
</table>
<p>Set up meaning search from <a href="/settings#meaning-search">Settings</a>. Changing the model re-indexes on the next run.</p>
<h3>Face detection (optional)</h3>
<p>Finds faces in your photos so they can be identified in <a href="/faces-review">Name faces</a> and <a href="/people-review">Review people</a>. Separate download (~500 MB). Set up from Scan your photos.</p>
<h3>Backups</h3>
<p>Click <strong>Create verified database backup</strong> to make a verified copy of your database with an integrity check.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="searching">
<h2>4. Searching and Browsing</h2>
<p>The <a href="/">home page</a> is the main photo browser with a search toolbar and scrollable filmstrip.</p>
<h3>Search scopes</h3>
<ul>
<li><strong>Everything</strong> (default) &mdash; searches all sources combined</li>
<li><strong>Visible image tags</strong> &mdash; subjects, objects, people, and OCR text</li>
<li><strong>Day/event context</strong> &mdash; folder-derived tags</li>
<li><strong>People</strong> &mdash; browse and filter by recognized people (shows a card grid)</li>
<li><strong>Meaning</strong> &mdash; semantic search with natural language (requires meaning search)</li>
</ul>
<h3>Sorting</h3>
<ul>
<li><strong>Best match</strong> &mdash; relevance ranking (with &ldquo;Everything&rdquo; scope)</li>
<li><strong>Newest first</strong> / <strong>Oldest first</strong> &mdash; by capture date</li>
<li><strong>Filename A&ndash;Z</strong> &mdash; alphabetical</li>
</ul>
<h3>Date filtering</h3>
<p>Click the date filter button to open a calendar picker. Use <strong>Previous day</strong> / <strong>Next day</strong> buttons to navigate between days with photos.</p>
<h3>Filmstrip</h3>
<p>Scroll horizontally or drag to browse thumbnails. More photos load automatically as you scroll. Click a thumbnail to view the full photo.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="viewing">
<h2>5. Viewing and Editing Metadata</h2>
<p>Click a photo in the filmstrip to view it. The sidebar shows editable metadata.</p>
<h3>Primary subject</h3>
<p>A short phrase describing the main thing in the photo (e.g. &ldquo;Golden Gate Bridge at sunset&rdquo;). Stored as IPTC/XMP Title and Headline when published.</p>
<h3>Photo tags</h3>
<p>Comma-separated searchable tags for other visible things (e.g. &ldquo;bridge, fog, bay, cars&rdquo;). Stored as IPTC/XMP Keywords when published.</p>
<h3>People in this photo</h3>
<p>Shows confirmed people and face-recognition suggestions. Accept or reject suggestions, or manually add a person using the picker with autocomplete.</p>
<h3>Event / folder tags</h3>
<p>Reusable tags applied to every photo in the same folder (e.g. &ldquo;Christmas 2025&rdquo;). Useful for shared context across a batch.</p>
<h3>Capture details</h3>
<p>Expandable section showing read-only embedded EXIF, IPTC, and XMP metadata. GPS coordinates link to the Photo map and OpenStreetMap.</p>
<h3>Hidden tags</h3>
<p>You can hide incorrect tags for individual photos. Hidden tags can be restored from the sidebar.</p>
<h3>Zooming</h3>
<ul>
<li>Scroll wheel on the main image to zoom (up to 20x)</li>
<li>Triple-click to toggle 3x zoom</li>
<li>Click and drag to pan while zoomed</li>
</ul>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="people">
<h2>6. People and Faces</h2>
<p>LensLedger has a two-stage workflow for identifying people in your photos. You don&rsquo;t need to name every face by hand &mdash; name a few, then let LensLedger find the rest.</p>
<h3>Stage 1: Name faces (seed the system)</h3>
<p>Go to <a href="/faces-review">Name faces</a>. A grid of unidentified face crops is shown, diversity-sampled for variety. Name a handful of different people (5&ndash;10 is plenty) &mdash; you don&rsquo;t need to work through the entire queue. For each face:</p>
<ul>
<li><strong>Name it</strong> &mdash; type a name (with autocomplete). Similar unidentified faces appear as &ldquo;Also looks like&rdquo; matches for one-click confirmation.</li>
<li><strong>Not a person</strong> &mdash; mark false detections (statues, posters, etc.)</li>
<li><strong>Unknown person</strong> &mdash; mark as a real person you don&rsquo;t want to name yet</li>
</ul>
<p>Use &ldquo;Enlarge&rdquo; or double-click any face crop to see the full photo for context. Once you&rsquo;ve named a few people, move on to Stage 2.</p>
<h3>Stage 2: Review people (the fast part)</h3>
<p>Go to <a href="/people-review">Review people</a>. LensLedger uses the faces you named in Stage 1 to find matches across your entire library &mdash; this is where the bulk of identification happens. Batches of face-match suggestions are shown per person. For each photo:</p>
<ul>
<li><strong>Confirm</strong> &mdash; this is the right person</li>
<li><strong>Wrong</strong> &mdash; this is not the right person</li>
<li><strong>Correct</strong> &mdash; reassign to a different person</li>
</ul>
<p>Use <strong>Save &amp; publish this group</strong> to confirm and write names into JPEG metadata. <strong>Undo last batch</strong> rolls back decisions including metadata changes. You can <strong>defer</strong> a person&rsquo;s suggestions for 1&ndash;30 days.</p>
<h3>Managing people</h3>
<p>From the People search scope on the home page:</p>
<ul>
<li><strong>Edit name</strong> &mdash; change a person&rsquo;s primary name (propagates to all JPEG metadata)</li>
<li><strong>Aliases</strong> &mdash; add alternate names (nicknames, maiden names) that also match in search</li>
<li><strong>Merge</strong> &mdash; combine duplicate person records, preserving aliases and updating metadata</li>
</ul>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="map">
<h2>7. Photo Map</h2>
<p>The <a href="/map">Photo map</a> shows an interactive world map with markers at every GPS-tagged photo location.</p>
<ul>
<li><strong>Scroll</strong> to zoom in and out</li>
<li><strong>Drag</strong> to pan</li>
<li><strong>Click a cluster marker</strong> to zoom into that area</li>
<li><strong>Click a single marker</strong> to see photo count, date range, coordinates, and a preview</li>
</ul>
<p>From marker details you can open the photo in the viewer, view all photos from that location, or open the coordinates in OpenStreetMap.</p>
<p>GPS coordinates are extracted during the Photo locations scan and are never written back to your files.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="auto-import">
<h2>8. Auto-import Photos</h2>
<p>The <a href="/auto-import">Auto-import photos</a> page sets up an automatic pipeline for sorting new photos from a camera upload folder into your collection.</p>
<h3>How it works</h3>
<ol>
<li>Set a <strong>source folder</strong> (e.g. <code>C:\\Users\\you\\Dropbox\\Camera Uploads</code>)</li>
<li>Set a <strong>destination folder</strong> (e.g. <code>C:\\Users\\you\\Pictures\\Sorted</code>)</li>
<li>Configure a <strong>date sorting template</strong> using placeholders</li>
</ol>
<table>
<tr><th>Placeholder</th><th>Example</th></tr>
<tr><td><code>{{year}}</code></td><td>2026</td></tr>
<tr><td><code>{{month}}</code></td><td>08</td></tr>
<tr><td><code>{{day}}</code></td><td>28</td></tr>
<tr><td><code>{{hour}}</code></td><td>14</td></tr>
<tr><td><code>{{minute}}</code></td><td>30</td></tr>
</table>
<p>The default template <code>{{year}}/{{year}}_{{month}}_{{day}}</code> creates folders like <code>2026/2026_08_28</code>.</p>
<h3>Override rules</h3>
<p>Route specific files to different destinations based on filename matching. For example, photos with &ldquo;Screenshot&rdquo; in the name could go to a Screenshots folder. Rules are checked in order; the first match wins.</p>
<h3>Controls</h3>
<ul>
<li><strong>Enable/disable toggle</strong> &mdash; when enabled, the pipeline checks at the configured interval (default: every 10 minutes)</li>
<li><strong>Check interval</strong> &mdash; how often to check for new photos (5 minutes to 24 hours)</li>
<li><strong>Run now</strong> &mdash; trigger an immediate pipeline run</li>
<li><strong>Activity log</strong> &mdash; shows every file processed</li>
</ul>
<p>If a file with the same name already exists at the destination, a numeric suffix is added automatically.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="publishing">
<h2>9. Publishing Metadata</h2>
<p>Publishing writes your subjects, people, tags, and descriptions back into the photo file&rsquo;s embedded metadata (IPTC/XMP). Only JPEG and HEIC/HEIF files are publishable.</p>
<h3>How to publish</h3>
<ol>
<li>Open a photo and fill in the metadata you want to save</li>
<li>Click <strong>Preview &amp; publish</strong> in the sidebar</li>
<li>Review the before/after comparison</li>
<li>Click <strong>Publish</strong> to write the metadata</li>
</ol>
<h3>Safety features</h3>
<ul>
<li>A <strong>safety backup</strong> is created before every write</li>
<li>After writing, LensLedger verifies the image pixels haven&rsquo;t changed (hash comparison)</li>
<li>Click <strong>Restore last publish</strong> to revert from the safety backup</li>
</ul>
<h3>Auto-publishing</h3>
<p>When you confirm people in <a href="/people-review">Review people</a>, their names are automatically published to the JPEG metadata.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="review-bin">
<h2>10. Review Bin (Trash)</h2>
<p>The review bin is a safe staging area for photos you want to remove. Photos are moved to a separate folder &mdash; not permanently deleted.</p>
<h3>Moving photos to the review bin</h3>
<ul>
<li>Click the trash icon on any photo</li>
<li>Use batch selection and click <strong>Trash selected</strong></li>
<li>An undo toast appears for 12 seconds after trashing</li>
</ul>
<h3>Managing the review bin</h3>
<p>Open from the hamburger menu (<strong>Trash &amp; restore</strong>):</p>
<ul>
<li><strong>Restore</strong> &mdash; move a photo back to its original location</li>
<li><strong>Delete</strong> &mdash; permanently remove a single photo</li>
<li><strong>Empty trash</strong> &mdash; permanently delete all items</li>
</ul>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="batch">
<h2>11. Batch Editing</h2>
<h3>Selecting photos</h3>
<ul>
<li><strong>Ctrl+click</strong> (or Cmd+click) a thumbnail to toggle its selection</li>
<li><strong>Shift+click</strong> a thumbnail to select a range</li>
</ul>
<p>A batch bar appears at the bottom with the selected count.</p>
<h3>Batch actions</h3>
<ul>
<li><strong>Add tags</strong> &mdash; add the same tags to all selected photos</li>
<li><strong>Trash</strong> &mdash; move all selected photos to the review bin</li>
<li><strong>Clear selection</strong> &mdash; deselect all</li>
</ul>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="database">
<h2>12. Database and Backups</h2>
<h3>Database location</h3>
<p>Each library has its own database. By default, it is stored inside the library in a hidden <code>.LensLedger</code> folder (e.g. <code>Photos\\.LensLedger\\LensLedger-Photos.sqlite3</code>). You can also choose to store it in <code>{html.escape(str(data_root()))}\\Libraries\\</code> when adding a library.</p>
<h3>Verified backups</h3>
<p>From <a href="/scan-photos">Scan your photos</a>, click <strong>Create verified database backup</strong>. Backups are stored in <code>{html.escape(str(data_root()))}\\Database Backups\\</code>.</p>
<h3>Export and import</h3>
<p>From <a href="/settings">Settings &gt; Database</a>:</p>
<ul>
<li><strong>Export database</strong> &mdash; creates a portable ZIP containing the database, face data, and a manifest. Useful for backups or moving to a new machine.</li>
<li><strong>Import database</strong> &mdash; restores from a previous export ZIP. File paths are automatically remapped to the current library location.</li>
</ul>
<div class="tip"><strong>Note:</strong> Your photos are not included in exports &mdash; only the LensLedger index is transferred.</div>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="settings">
<h2>13. Settings</h2>
<p>Access <a href="/settings">Settings</a> from the navigation menu.</p>
<h3>Photo libraries</h3>
<p>Add, switch, relocate, or remove libraries. See <a href="#library-management">Library Management</a>.</p>
<h3>Scan preferences</h3>
<table>
<tr><th>Setting</th><th>Range</th><th>Default</th><th>Description</th></tr>
<tr><td>OCR worker threads</td><td>1&ndash;16</td><td>4</td><td>More workers = faster scanning, more CPU</td></tr>
<tr><td>OCR batch size</td><td>10&ndash;500</td><td>50</td><td>Photos processed per commit</td></tr>
<tr><td>Meaning search batch</td><td>1&ndash;128</td><td>16</td><td>Images per CLIP encoding batch</td></tr>
</table>
<h3>Meaning search model</h3>
<p>Choose the CLIP model for meaning search. See <a href="#scanning">Scanning</a> for model details.</p>
<h3>Display preferences</h3>
<table>
<tr><th>Setting</th><th>Options</th><th>Default</th></tr>
<tr><td>Photos per page</td><td>50&ndash;1000</td><td>250</td></tr>
<tr><td>Default sort order</td><td>Newest / Oldest / Name</td><td>Newest</td></tr>
<tr><td>Filmstrip thumbnail size</td><td>Small / Medium / Large</td><td>Medium</td></tr>
</table>
<h3>Folder watching</h3>
<p>Automatically detect new and changed photos without manually running a scan.</p>
<table>
<tr><th>Setting</th><th>Range</th><th>Default</th></tr>
<tr><td>Enable watching</td><td>On/Off</td><td>Off</td></tr>
<tr><td>Check interval</td><td>5&ndash;1440 minutes</td><td>30</td></tr>
</table>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="shortcuts">
<h2>14. Keyboard and Mouse Shortcuts</h2>
<table>
<tr><th>Action</th><th>Shortcut</th></tr>
<tr><td>Next / previous photo</td><td>Left / Right arrow keys</td></tr>
<tr><td>Open photo in file explorer</td><td>Double-click the main image</td></tr>
<tr><td>Toggle 3x zoom</td><td>Triple-click the main image</td></tr>
<tr><td>Zoom in / out</td><td>Scroll wheel on the main image</td></tr>
<tr><td>Pan while zoomed</td><td>Click and drag</td></tr>
<tr><td>Select photo for batch editing</td><td>Ctrl+click (or Cmd+click) a thumbnail</td></tr>
<tr><td>Select a range of photos</td><td>Shift+click a thumbnail</td></tr>
<tr><td>Close any panel or dialog</td><td>Escape</td></tr>
</table>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="formats">
<h2>15. Supported File Formats</h2>
<table>
<tr><th>Format</th><th>Metadata</th><th>Faces</th><th>Viewable</th><th>Publishable</th></tr>
<tr><td>JPEG (.jpg, .jpeg)</td><td>Yes</td><td>Yes</td><td>Yes</td><td>Yes</td></tr>
<tr><td>PNG (.png)</td><td>Yes</td><td>Yes</td><td>Yes</td><td>&mdash;</td></tr>
<tr><td>WebP (.webp)</td><td>Yes</td><td>Yes</td><td>Yes</td><td>&mdash;</td></tr>
<tr><td>TIFF (.tif, .tiff)</td><td>Yes</td><td>Yes</td><td>Yes</td><td>&mdash;</td></tr>
<tr><td>GIF (.gif)</td><td>&mdash;</td><td>Yes</td><td>Yes</td><td>&mdash;</td></tr>
<tr><td>BMP (.bmp)</td><td>&mdash;</td><td>Yes</td><td>Yes</td><td>&mdash;</td></tr>
<tr><td>HEIC / HEIF</td><td>&mdash;</td><td>Yes</td><td>Yes *</td><td>Yes</td></tr>
<tr><td>RAW (.dng, .cr2, .cr3, .nef, .arw, .orf, .rw2, .raf)</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td></tr>
<tr><td>Video (.mp4, .mov, .avi, .wmv, .mpg, .mpeg, .mkv)</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td></tr>
</table>
<p>* HEIC/HEIF files are converted to JPEG on the fly for viewing. RAW and video files are indexed and searchable but not viewable or face-scanned.</p>
<p><strong>Publishable</strong> means LensLedger can write people names, tags, and subjects back into the file&rsquo;s embedded metadata.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

<section class="manual-section" id="advanced">
<h2>16. Advanced Configuration</h2>
<h3>Data directory</h3>
<p>All application data is stored at <code>{html.escape(str(data_root()))}</code> by default.</p>
<table>
<tr><th>Folder</th><th>Contents</th></tr>
<tr><td><code>Libraries\\</code></td><td>Per-library SQLite databases</td></tr>
<tr><td><code>Metadata Backups\\</code></td><td>Pre-publish safety backups</td></tr>
<tr><td><code>Database Backups\\</code></td><td>Verified database backups</td></tr>
<tr><td><code>Review Bin\\</code></td><td>Trashed photos</td></tr>
<tr><td><code>Face Data\\</code></td><td>Face detection data</td></tr>
<tr><td><code>Exports\\</code></td><td>Database export ZIPs</td></tr>
<tr><td><code>library-state.json</code></td><td>Library list and current library</td></tr>
<tr><td><code>settings.json</code></td><td>Application settings</td></tr>
</table>
<p>Override the data directory by setting the <code>LENSLEDGER_DATA_DIR</code> environment variable.</p>
<h3>Command-line options</h3>
<table>
<tr><th>Option</th><th>Description</th></tr>
<tr><td><code>--version</code></td><td>Print version and exit</td></tr>
<tr><td><code>--port N</code></td><td>Set the HTTP port (default: 5309)</td></tr>
<tr><td><code>--root PATH</code></td><td>Override the library root path</td></tr>
<tr><td><code>--db PATH</code></td><td>Override the database path</td></tr>
<tr><td><code>--no-open</code></td><td>Don&rsquo;t auto-open the browser on startup</td></tr>
</table>
<h3>Updates</h3>
<p>LensLedger checks for updates from GitHub automatically. When a new version is available, an update badge appears in the navigation menu. A banner appears on all pages when the on-disk code is newer than the running server, with a <strong>Restart</strong> button to load the new version.</p>
<div class="back-to-top"><a href="#top">Back to top</a></div>
</section>

</main>
<script src="{asset_url('js/manual.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def auto_import_page(self):
        settings = load_settings()
        ingest = settings.get("ingest", {})
        default_template = ingest.get("default_template", "{year}/{year}_{month}_{day}")
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auto-import — {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/auto-import.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body {bootstrap_attr({"csrf": self.csrf_token, "ingest": ingest})}><header><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div><h1>Auto-import photos</h1><p>Automatically sort new photos from a camera upload folder into your collection</p></div><span class="spacer"></span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button><span class="version">v{APP_VERSION}</span></header>
{nav_menu("auto-import", str(self.library_root))}
<main>
<section class="card"><h2>Status</h2>
<div class="status-grid"><div><strong id="statusEnabled">—</strong><span>Pipeline</span></div><div><strong id="statImported">0</strong><span>Imported</span></div><div><strong id="statErrors">0</strong><span>Errors</span></div></div></section>
<section class="card"><h2>Pipeline</h2><p>When enabled, the pipeline periodically checks the source folder for new photos and moves them into the destination folder, sorted by date.</p>
<div class="toggle-row"><label class="toggle-switch"><input type="checkbox" id="ingestEnabled" {"checked" if ingest.get("enabled") else ""}><span class="slider"></span></label><label for="ingestEnabled">Enable auto-import pipeline</label></div>
<div class="field"><label for="ingestSource">Source folder</label><input type="text" id="ingestSource" value="{html.escape(str(ingest.get('source_folder', '')))}" placeholder="e.g. C:\\Users\\you\\Dropbox\\Camera Uploads"><button type="button" class="secondary field-browse" id="browseIngestSource">Browse…</button></div>
<div class="field"><label for="ingestDest">Destination folder</label><input type="text" id="ingestDest" value="{html.escape(str(ingest.get('destination_folder', '')))}" placeholder="e.g. C:\\Users\\you\\Pictures\\Sorted"><button type="button" class="secondary field-browse" id="browseIngestDest">Browse…</button></div>
<div class="field"><label for="ingestInterval">Check interval (minutes)</label><input type="number" id="ingestInterval" min="5" max="1440" value="{int(ingest.get('interval_minutes', 10))}"><span class="hint">How often to check for new photos when the pipeline is enabled. Default: 10</span></div></section>
<section class="card"><h2>Date sorting template</h2><p>Photos are sorted into subfolders based on their capture date (from EXIF data, or file modification date as a fallback). Customise the folder structure using the placeholders below.</p>
<div class="field"><label for="templateInput">Template</label><input type="text" id="templateInput" value="{html.escape(default_template)}"></div>
<div class="template-preview" id="templatePreview"></div>
<div class="placeholder-list"><strong>Available placeholders:</strong><br><code>{{year}}</code> — four-digit year (e.g. 2026)<br><code>{{month}}</code> — two-digit month (01–12)<br><code>{{day}}</code> — two-digit day (01–31)<br><code>{{hour}}</code> — two-digit hour (00–23)<br><code>{{minute}}</code> — two-digit minute (00–59)</div></section>
<section class="card"><h2>Override rules</h2><p>If a photo's filename contains the match text, it goes to the rule's destination instead of the date template. Rules are checked in order; the first match wins. Leave this empty to sort everything by date.</p>
<div class="rule-list" id="ruleList"></div>
<button type="button" class="secondary" id="addRule">Add rule</button></section>
<section class="card"><h2>Activity log</h2><p>Recent pipeline activity — every file processed is logged here.</p>
<div id="logList"><p class="log-empty">Loading…</p></div>
<div class="actions-bar"><button type="button" class="secondary" id="refreshLog">Refresh log</button></div></section>
<div class="actions-bar"><button type="button" class="secondary" id="runNow">Run now</button><button type="button" id="saveConfig">Save configuration</button></div>
</main>
<div class="toast" id="toast"></div>
<script src="{asset_url('js/auto-import.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def save_ingest_config(self, body):
        config = body.get("config")
        if not isinstance(config, dict):
            raise ValueError("invalid config format")
        settings = load_settings()
        settings["ingest"] = config
        save_settings(settings)
        pipeline = type(self).ingest_pipeline
        if pipeline:
            pipeline.update_config(
                source_folder=str(config.get("source_folder", "")),
                destination_folder=str(config.get("destination_folder", "")),
                rules=config.get("rules", []),
                default_template=str(config.get("default_template", "")),
                interval_minutes=int(config.get("interval_minutes", 10)),
            )
            if config.get("enabled"):
                pipeline.start()
            else:
                pipeline.stop()
        self.send_json({"ok": True})

    def ingest_run_now(self, body):
        pipeline = type(self).ingest_pipeline
        if not pipeline:
            self.send_json({"error": "Pipeline not initialised"})
            return
        if not pipeline._source or not pipeline._destination:
            self.send_json({"error": "Source and destination folders must be configured first"})
            return
        delta = pipeline.run_once()
        self.send_json({"ok": True, "delta": delta})

    def get_settings(self):
        self.send_json(load_settings())

    def save_settings_api(self, body):
        values = body.get("settings")
        if not isinstance(values, dict):
            raise ValueError("invalid settings format")
        save_settings(values)
        watch_cfg = values.get("watch", {})
        watcher = type(self).folder_watcher
        if watcher:
            if watch_cfg.get("enabled"):
                watcher.update_interval(int(watch_cfg.get("interval_minutes", 5)))
                watcher.start()
            else:
                watcher.stop()
        ingest_cfg = values.get("ingest", {})
        pipeline = type(self).ingest_pipeline
        if pipeline:
            pipeline.update_config(
                source_folder=str(ingest_cfg.get("source_folder", "") or pipeline._source or ""),
                destination_folder=str(ingest_cfg.get("destination_folder", "") or pipeline._destination or ""),
                rules=ingest_cfg.get("rules") if ingest_cfg.get("rules") is not None else pipeline._rules,
                default_template=str(ingest_cfg.get("default_template", "") or pipeline._default_template),
                interval_minutes=int(ingest_cfg.get("interval_minutes", 10)),
            )
            if ingest_cfg.get("enabled"):
                pipeline.start()
            else:
                pipeline.stop()
        self.send_json({"ok": True})

    def watcher_status(self):
        watcher = type(self).folder_watcher
        self.send_json(watcher.status() if watcher else {"enabled": False, "interval_minutes": 30})

    def ingest_status(self):
        pipeline = type(self).ingest_pipeline
        self.send_json(pipeline.status() if pipeline else {"enabled": False, "stats": {}})

    def ingest_log(self):
        from ingest_pipeline import INGEST_LOG_PATH
        entries: list[dict] = []
        if INGEST_LOG_PATH.is_file():
            try:
                with open(INGEST_LOG_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            except OSError:
                pass
        self.send_json({"entries": entries[-100:]})

    def remove_library(self, body):
        path = str(body.get("path", "")).strip()
        if not path:
            raise ValueError("no path specified")
        config = load_library_config()
        libraries = config.get("libraries", [])
        resolved = str(Path(path).resolve()).casefold()
        current = str(self.library_root).casefold()
        if resolved == current:
            raise ValueError("cannot remove the active library")
        libraries = [p for p in libraries if str(Path(p).resolve()).casefold() != resolved]
        from library_config import LIBRARY_STATE_PATH
        LIBRARY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIBRARY_STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "current_root": str(self.library_root),
            "libraries": libraries,
        }, indent=2), encoding="utf-8")
        tmp.replace(LIBRARY_STATE_PATH)
        self.send_json({"ok": True})

    def export_status(self):
        backup_dir = database_backup_root()
        last = None
        if backup_dir.is_dir():
            backups = sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
            if backups:
                last = dt.datetime.fromtimestamp(backups[0].stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        self.send_json({"last_backup": last})

    def export_database(self, _body):
        import zipfile
        export_dir = data_root() / "Exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"lensledger-export-{timestamp}.zip"
        zip_path = export_dir / zip_name
        db_path = self.db_path
        face_dir = data_root() / "Face Data"
        manifest = {
            "library_root": str(self.library_root),
            "schema_version": SCHEMA_VERSION,
            "export_date": dt.datetime.now(dt.timezone.utc).isoformat(),
            "app_version": APP_VERSION,
        }
        with self.db() as con:
            manifest["photo_count"] = int(con.execute(
                "SELECT COUNT(*) FROM assets WHERE in_review_bin=0"
            ).fetchone()[0])
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            if db_path.is_file():
                backup_path = export_dir / f"_temp_{timestamp}.sqlite3"
                try:
                    import sqlite3 as _sqlite3
                    src_con = _sqlite3.connect(str(db_path))
                    dst_con = _sqlite3.connect(str(backup_path))
                    src_con.backup(dst_con)
                    src_con.close()
                    dst_con.close()
                    zf.write(backup_path, "database.sqlite3")
                finally:
                    backup_path.unlink(missing_ok=True)
            if face_dir.is_dir():
                for fp in face_dir.rglob("*"):
                    if fp.is_file():
                        zf.write(fp, f"FaceData/{fp.relative_to(face_dir)}")
        self.send_json({"ok": True, "path": str(zip_path)})

    def import_database(self, _body):
        import zipfile
        browse_script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Choose a LensLedger export file'
$dialog.Filter = 'ZIP files (*.zip)|*.zip'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.FileName
}
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", browse_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, check=False,
        )
        if result.returncode:
            raise ValueError((result.stderr or "The file chooser could not open").strip())
        zip_path = result.stdout.strip()
        if not zip_path:
            raise ValueError("no file selected")
        if not Path(zip_path).is_file():
            raise ValueError("selected file does not exist")
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if "manifest.json" not in names:
                raise ValueError("not a valid LensLedger export (no manifest.json)")
            manifest = json.loads(zf.read("manifest.json"))
            schema = manifest.get("schema_version", 0)
            if schema > SCHEMA_VERSION:
                raise ValueError(f"this export uses schema version {schema}, but this version of LensLedger only supports up to {SCHEMA_VERSION}")
            if "database.sqlite3" in names:
                db_dest = self.db_path
                temp_db = db_dest.with_suffix(".import.sqlite3")
                try:
                    with zf.open("database.sqlite3") as src, open(temp_db, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    old_root = manifest.get("library_root", "")
                    new_root = str(self.library_root)
                    if old_root and old_root != new_root:
                        import sqlite3 as _sqlite3
                        con = _sqlite3.connect(str(temp_db))
                        con.execute("UPDATE assets SET path=REPLACE(path,?,?)", (old_root, new_root))
                        old_rel_prefix = ""
                        con.execute("UPDATE assets SET relative_path=REPLACE(relative_path,?,?)", (old_rel_prefix, ""))
                        con.commit()
                        con.close()
                    backup_existing = db_dest.with_suffix(f".pre-import-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}.sqlite3")
                    if db_dest.is_file():
                        shutil.copy2(db_dest, backup_existing)
                    shutil.move(str(temp_db), str(db_dest))
                except Exception:
                    temp_db.unlink(missing_ok=True)
                    raise
            face_data_root = data_root() / "Face Data"
            for name in names:
                if name.startswith("FaceData/") and not name.endswith("/"):
                    rel = name[len("FaceData/"):]
                    dest = face_data_root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        self.send_json({"ok": True, "message": f"Imported from {Path(zip_path).name} ({manifest.get('photo_count', '?')} photos)"})

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
        near = params.get("near", [""])[0]
        try:
            near_lat_str, near_lon_str = near.split(",", 1)
            near_point = (round(float(near_lat_str), 1), round(float(near_lon_str), 1))
        except ValueError:
            near_point = None
        return query, selected_date, scope, person_id, sort, page_number, near_point

    def fetch_matching_photos(self, con, query, selected_date, scope, person_id, sort, page_number, near_point=None):
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
        if near_point:
            clauses.append("ROUND(a.gps_latitude,1)=? AND ROUND(a.gps_longitude,1)=?")
            values.extend(near_point)
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
        query, selected_date, scope, person_id, sort, page_number, near_point = self.parse_photo_query(params)
        if scope == "people" and not person_id:
            return self.send_json({"error": "this scope has no photo pages to fetch"}, 400)
        try:
            with self.db() as con:
                if scope == "people" and person_id:
                    exists = con.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone()
                    if not exists:
                        raise ValueError("That person is no longer available")
                items, total, selected_date = self.fetch_matching_photos(
                    con, query, selected_date, scope, person_id, sort, page_number, near_point,
                )
        except (sqlite3.Error, RuntimeError, ValueError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        has_more = page_number * PAGE_SIZE < total
        self.send_json({"items": items, "total": total, "page": page_number, "has_more": has_more})

    def viewer_page(self, params):
        with self.db() as con:
            if int(con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0]) == 0:
                return self.onboarding_page()
        query, selected_date, scope, person_id, sort, page_number, near_point = self.parse_photo_query(params)

        error = ""
        rows: list[dict] = []
        people_cards = []
        people_directory = []
        selected_person_name = ""
        selected_person_stats: dict[str, int] = {}
        total = 0
        trash_count = 0
        review_count = 0
        unidentified_faces_count = 0
        try:
            with self.db() as con:
                trash_count = int(con.execute(
                    "SELECT COUNT(*) FROM review_bin WHERE restored_at IS NULL"
                ).fetchone()[0])
                review_count = int(con.execute(
                    "SELECT COUNT(*) FROM asset_people WHERE state='suggested'"
                ).fetchone()[0])
                unidentified_faces_count = int(con.execute(
                    """SELECT COUNT(*) FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
                       WHERE f.ignored_at IS NULL AND a.in_review_bin=0
                         AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                         AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
                         AND NOT EXISTS (
                             SELECT 1 FROM asset_people ap WHERE ap.face_id=f.id
                             AND ap.state IN ('confirmed','suggested')
                         )"""
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
                        con, query, selected_date, scope, person_id, sort, page_number, near_point,
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
        elif near_point:
            view_label = "Photos near this location"
        else:
            view_label = {
                "newest": "Newest photos",
                "oldest": "Oldest photos",
                "name": "Filename order",
            }.get(sort, "Photos")
        summary = (
            f"{view_label} • {total:,}" if scope == "people" and not person_id
            else f"{view_label} • No matches" if not total
            else f"{view_label} • {first:,}–{last:,} of {total:,}"
        )
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
        stage_empty_text = "No photos match this search" if not items else "Choose a photo from the filmstrip"
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
                f'data-aliases="{html.escape(json.dumps(aliases), quote=True)}">Edit name</button></article>'
            )
        people_gallery_html = (
            '<main class="people-browser"><div class="people-browser-head"><div><h2>People</h2>'
            '<p>Choose a person to see confirmed photos. Use Edit name on a card to add nicknames, maiden names, or other aliases for that one person; separate each alternate name with a comma.</p></div>'
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
<title>{APP_NAME} — {APP_TAGLINE}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/person-picker.css')}"><link rel="stylesheet" href="{asset_url('css/viewer.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body class="{body_class}" {bootstrap_attr({
            "items": items, "personDirectory": people_directory, "csrf": self.csrf_token,
            "currentLibrary": str(self.library_root), "viewedPersonId": person_id,
            "selectedId": selected_id, "appVersion": APP_VERSION, "appTagline": APP_TAGLINE,
            "query": {
                "q": query, "date": selected_date, "scope": scope, "sort": sort, "person": person_id,
                "near": f"{near_point[0]},{near_point[1]}" if near_point else "",
            },
            "page": page_number, "hasMore": has_more,
        })}>
<header><div class="top"><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div class="identity"><h1>{APP_NAME}</h1><div class="tagline">{APP_TAGLINE}</div></div><span class="version">v{APP_VERSION}</span><span class="summary">{html.escape(summary)} <span class="error-inline">{html.escape(error)}</span></span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button></div>
<form class="toolbar">{person_hidden}<label class="search-field">Search<input name="q" value="{html.escape(query, quote=True)}" placeholder="{search_placeholder}"></label><label class="scope-field">Search scope<button type="button" class="info-button" data-help="scopeHelp" aria-label="About search scopes">i</button><div class="help-popover" id="scopeHelp"><strong>Visible image tags</strong> — matches tags describing what's in the photo: subjects, objects, people, and text found by OCR.<br><br><strong>Day/event context</strong> — matches tags inferred from the folder name (e.g. "Birthday", "Vacation 2019") rather than image contents.<br><br><strong>People</strong> — browse and filter by recognized people.<br><br><strong>Meaning (optional)</strong> — uses a local AI vision model to match your description against what the photos actually look like. Requires a one-time model install from the Scan page. Try natural phrases like "sunset over water" or "dog playing in snow".<br><br><strong>Everything</strong> — searches all of the above at once.</div><select name="scope" id="scopePicker">{scope_options}</select></label><button type="button" class="secondary" id="previousDay">◀ Day</button><div class="date-field"><span class="field-label">Date</span><button type="button" class="date-trigger" id="dateTrigger">{html.escape(selected_date, quote=True) if selected_date else 'Any date'}</button><input type="hidden" name="date" id="datePicker" value="{html.escape(selected_date, quote=True)}"><div class="date-popover" id="datePopover"><div class="date-popover-head"><button type="button" class="cal-nav" id="calPrevMonth" aria-label="Previous month">◀</button><select id="calMonth" aria-label="Month"></select><select id="calYear" aria-label="Year"></select><button type="button" class="cal-nav" id="calNextMonth" aria-label="Next month">▶</button></div><div class="date-weekdays"><span>Su</span><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span></div><div class="date-days" id="calDays"></div><div class="date-popover-actions"><button type="button" class="secondary" id="calToday">Today</button><button type="button" class="secondary" id="calClear">Clear</button></div></div></div><button type="button" class="secondary" id="nextDay">Day ▶</button><label class="sort-field optional">Sort<select name="sort">{sort_options}</select></label><button>View</button></form></header>
{nav_menu("home", str(self.library_root))}
{people_gallery_html}{people_result_bar}<main class="viewer{viewer_hidden_class}"><section class="upper"><div class="stage" id="stage"><div class="empty">{stage_empty_text}</div><button class="stage-nav" id="previousPhoto"><svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4L6 10L12 16"/></svg></button><button class="stage-nav" id="nextPhoto"><svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4L14 10L8 16"/></svg></button><div class="zoom-controls" id="zoomControls"><span class="zoom-level" id="zoomLevel">100%</span><button type="button" class="zoom-reset" id="zoomReset">Reset zoom</button></div><button type="button" class="sidebar-toggle" id="sidebarToggle" aria-label="Show photo details">ⓘ</button></div><aside class="sidebar" id="sidebar">
<div class="file-date" id="assetDate"></div><div class="file-name" id="assetName"></div><div class="folder" id="assetFolder"></div>
<div class="editor-compact"><strong>Metadata for this photo</strong><button type="button" class="info-button" data-help="editorHelp" aria-label="About metadata editing">i</button><div class="help-popover" id="editorHelp">Your edits stay in LensLedger until you use Publish to this photo. Nothing is written automatically.</div></div>
<div class="section"><div class="section-title"><h2>1. Primary subject</h2><button type="button" class="info-button" data-help="subjectHelp" aria-label="About primary subjects">i</button></div><div class="help-popover" id="subjectHelp">One short phrase naming the main thing in this photo. Stored as IPTC/XMP Title/Headline metadata.</div><div class="chips" id="subjectChip"></div><div class="row subject-editor"><input id="subjectInput" placeholder="Example: Formula 1 race cars"><button id="saveSubject">Save subject</button></div></div>
<div class="section"><div class="section-title"><h2>2. Photo tags</h2><button type="button" class="info-button" data-help="photoTagHelp" aria-label="About photo tags">i</button></div><div class="help-popover" id="photoTagHelp">Searchable people, objects, places, or activities visible in this photo. Use lowercase for ordinary things and activities; capitalize people, places, brands, and acronyms normally. Search ignores capitalization. These are stored as IPTC/XMP Keywords. Enter one or several separated by commas.</div><div class="chips" id="imageTags"></div><div class="row row-spaced"><input id="newTag" placeholder="Formula 1, race car, Honda, McLaren"><button id="addTag">Add tags</button></div></div>
<div class="section"><div class="section-title"><h2>3. People in this photo</h2><button type="button" class="info-button" data-help="peopleHelp" aria-label="About people in this photo">i</button></div><div class="help-popover" id="peopleHelp">Confirmed people become searchable and publish to the standard XMP People Shown field and Keywords. Face matches are suggestions only until you approve them.</div><div class="chips" id="confirmedPeople"></div><div id="suggestionLabel" class="suggestion-label">Face recognition suggestions — confirm or reject</div><div class="chips" id="suggestedPeople"></div><div class="row row-spaced"><div id="personPickerContainer"></div></div></div>
<div class="section"><div class="section-title"><h2>4. Event / folder tags</h2><button type="button" class="info-button" data-help="eventTagHelp" aria-label="About event and folder tags">i</button></div><div class="help-popover" id="eventTagHelp">Reusable context applied to every photo in this folder. These also map to Keywords; use Event name and Location below when a more precise standard field applies.</div><div class="chips" id="contextTags"></div><div class="row row-spaced"><input id="newContextTag" placeholder="Car show, family vacation, birthday"><button id="addContextTag">Add to event</button></div></div>
<section class="publish-section" id="publishSection"><div class="section-title"><h2>Publish to this photo</h2><button type="button" class="info-button" data-help="publishHelp" aria-label="About publishing">i</button></div><div class="help-popover" id="publishHelp">Writes the primary subject as Title/Headline, confirmed people as People Shown, all visible Photo, People, and Event tags as Keywords, and the description below into this JPEG. A safety backup is made first and the picture pixels are verified afterward.</div><label>Photo description<textarea id="publishDescription" placeholder="Describe what is actually in this photo"></textarea></label><div class="publish-actions"><button type="button" id="previewPublish">Preview &amp; publish</button><button type="button" class="secondary" id="restorePublish">Restore last publish</button></div><p class="publish-note" id="publishNote">Only this selected photo will be changed, and only after you approve the preview.</p></section>
<div class="section trash-section"><button type="button" class="danger" id="moveToTrash">🗑 Trash this photo</button></div>
<details class="metadata-details" id="embeddedMetadata"><summary>Capture details</summary><p class="metadata-note">Read directly from the photo. Nothing here changes the file.</p><div class="metadata-readout" id="metadataReadout"></div></details>
<div class="section" id="hiddenSection"><div class="section-title"><h2>Hidden tags</h2><button type="button" class="info-button" data-help="hiddenTagHelp" aria-label="About hidden tags">i</button></div><div class="help-popover" id="hiddenTagHelp">These tags are ignored only for this photo. Click one to restore it.</div><div class="chips" id="hiddenTags"></div></div><div class="status" id="status"></div>
</aside></section><div class="sidebar-backdrop" id="sidebarBackdrop"></div><section class="filmstrip" id="filmstrip"></section></main><div class="batch-bar" id="batchBar"><span class="batch-count" id="batchCount">0 selected</span><input id="batchTagInput" placeholder="Add tags to selected"><button type="button" id="batchAddTags">Add tags</button><button type="button" class="danger" id="batchTrash">Trash selected</button><button type="button" class="secondary" id="batchClear">Clear</button></div><div class="toast" id="toast"></div>
<div class="modal-backdrop" id="modalBackdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle"><div class="modal-head"><h2 id="modalTitle"></h2><button type="button" class="modal-close" id="modalClose">Close</button></div><div id="modalBody"></div></section></div>
<script src="{asset_url('js/person-picker.js')}" defer></script>
<script src="{asset_url('js/viewer.js')}" defer></script></body></html>"""
        self.send_html(page)

    def people_review_page(self, params):
        requested = params.get("person", [""])[0]
        initial_person_id = int(requested) if requested.isdigit() else None
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review people — {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/person-picker.css')}"><link rel="stylesheet" href="{asset_url('css/people-review.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body {bootstrap_attr({"csrf": self.csrf_token, "initialPersonId": initial_person_id, "appVersion": APP_VERSION, "appTagline": APP_TAGLINE})}>
<header><div class="topbar"><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div class="identity"><strong>{APP_NAME}</strong><small>People review</small></div><span class="version">v{APP_VERSION}</span><span class="top-spacer"></span><span class="progress" id="globalProgress">Loading suggestions…</span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button></div></header>
{nav_menu("people-review", str(self.library_root))}
<main><section id="reviewArea"><div class="empty"><div><h2>Loading people…</h2><p>Preparing the next group of photos.</p></div></div></section></main>
<div class="actionbar" id="actionbar" hidden><div class="actions"><button type="button" class="secondary" id="skipBatch">Skip these for now</button><button type="button" class="secondary" id="nextPerson">Next person</button><button type="button" class="secondary" id="deferPerson">Defer person 7 days</button><button type="button" class="secondary" id="undoBatch" disabled>Undo last batch</button><span class="spacer"></span><span><span class="selection-summary" id="selectionSummary"></span><span class="status" id="status"></span></span><button type="button" class="primary-action" id="confirmBatch">Save &amp; publish this group</button></div></div>
<div class="lightbox" id="lightbox"><div class="lightbox-head"><span id="lightboxInfo" class="lightbox-info"></span><div class="lightbox-actions"><button type="button" class="secondary" id="lightboxToggle">Mark as wrong</button><span id="lightboxCorrectionArea" class="lightbox-correction" hidden><span id="lightboxPicker" class="lightbox-picker"></span><button type="button" class="secondary" id="lightboxNotAPerson">Not a person</button><button type="button" class="secondary" id="lightboxUnknownPerson">Unknown person</button></span></div><button type="button" class="secondary" id="closeLightbox">Close</button></div><div class="lightbox-photo" id="largePhotoBox"><img id="largePhoto" alt="Enlarged photo"><div class="lb-zoom-controls"><span class="lb-zoom-level">100%</span><button type="button" class="lb-zoom-reset">Reset zoom</button></div></div></div>
<div class="saving-overlay" id="savingOverlay"><div class="saving-content"><div class="saving-spinner"></div><h2>Saving tags…</h2><p>Publishing metadata to your photos.</p></div></div>
<div class="toast" id="toast"></div>
<script src="{asset_url('js/person-picker.js')}" defer></script>
<script src="{asset_url('js/lightbox-zoom.js')}" defer></script>
<script src="{asset_url('js/people-review.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def faces_review_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Name faces — {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/person-picker.css')}"><link rel="stylesheet" href="{asset_url('css/faces-review.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body {bootstrap_attr({"csrf": self.csrf_token, "appVersion": APP_VERSION, "appTagline": APP_TAGLINE})}>
<header><div class="topbar"><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div class="identity"><strong>{APP_NAME}</strong><small>Name faces</small></div><span class="version">v{APP_VERSION}</span><span class="top-spacer"></span><span class="progress" id="globalProgress">Loading faces…</span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button></div></header>
{nav_menu("faces-review", str(self.library_root))}
<main><div class="loading-overlay" id="loadingOverlay"><div class="loading-content"><div class="loading-spinner"></div><h2>Loading faces…</h2></div></div><p class="intro" hidden>Faces LensLedger has detected but nobody has named yet. You don&#x27;t need to name every face here — just name a handful of different people (5–10 is plenty), confirming the &#x201c;Also looks like&#x201d; matches as they come up. Then head to <a href="/people-review">Review people</a>, where LensLedger uses what you taught it to suggest matches across your entire library. That&#x27;s much faster than naming thousands of faces one by one. Use &#x201c;Enlarge&#x201d; or double-click a portrait to see the full photo for context. If it&#x27;s not a real face, use &#x201c;Not a person&#x201d;; if it&#x27;s a real face you just can&#x27;t identify, use &#x201c;Unknown person&#x201d; so it stops resurfacing.</p><div class="match-groups" id="matchGroups" hidden></div><div class="face-grid" id="faceGrid" hidden></div><div class="empty" id="emptyState" hidden><div><h2 id="emptyHeading">No unidentified faces</h2><p id="emptyText">Every detected face already has a confirmed name, or none have been detected yet.</p><a class="button" href="/scan-photos">Scan for faces</a></div></div></main>
<div class="lightbox" id="lightbox"><div class="lightbox-head"><div class="lightbox-actions"><button type="button" class="secondary" id="lightboxNotAPerson">Not a person</button><button type="button" class="secondary" id="lightboxUnknownPerson">Unknown person</button><button type="button" class="danger" id="lightboxTrash">Trash photo</button></div><button type="button" class="secondary" id="closeLightbox">Close</button></div><div class="lightbox-photo" id="largePhotoBox"><img id="largePhoto" alt="Enlarged photo"><div class="lb-zoom-controls"><span class="lb-zoom-level">100%</span><button type="button" class="lb-zoom-reset">Reset zoom</button></div></div></div>
<div class="toast" id="toast"></div>
<script src="{asset_url('js/person-picker.js')}" defer></script>
<script src="{asset_url('js/lightbox-zoom.js')}" defer></script>
<script src="{asset_url('js/faces-review.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def publish_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Publish photos — {APP_NAME}</title><link rel="icon" href="/favicon.png?v={APP_VERSION}"><link rel="stylesheet" href="{asset_url('css/theme.css')}"><link rel="stylesheet" href="{asset_url('css/publish.css')}">
<script src="{asset_url('js/theme.js')}"></script></head><body {bootstrap_attr({"csrf": self.csrf_token, "appVersion": APP_VERSION, "appTagline": APP_TAGLINE})}>
<header><div class="topbar"><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png?v={APP_VERSION}" alt=""><div class="identity"><strong>{APP_NAME}</strong><small>Publish photos</small></div><span class="version">v{APP_VERSION}</span><span class="top-spacer"></span><span class="progress" id="globalProgress"></span><button type="button" class="theme-toggle" aria-label="Toggle theme"></button></div></header>
{nav_menu("publish", str(self.library_root))}
<main>
<p class="intro">Names confirmed in Name faces and Review people are saved to the database instantly, but the JPEG metadata on disk is updated here. Publishing writes person names into each photo&#x27;s XMP and IPTC tags so other apps (Lightroom, Google Photos, etc.) can read them. A safety backup is created for every file before writing.</p>
<div class="publish-summary" id="publishSummary"><div class="loading-spinner"></div> Loading&hellip;</div>
<div class="publish-table-wrap" id="publishTableWrap" hidden></div>
<div class="publish-actions" id="publishActions" hidden>
<button type="button" class="primary-action" id="publishAll">Publish all</button>
</div>
<div class="publish-progress" id="publishProgress" hidden>
<div class="progress-bar-track"><div class="progress-bar-fill" id="progressFill"></div></div>
<p class="progress-text" id="progressText"></p>
</div>
<div class="publish-done" id="publishDone" hidden></div>
</main>
<div class="toast" id="toast"></div>
<script src="{asset_url('js/publish.js')}" defer></script>
</body></html>"""
        self.send_html(page)

    def publish_pending(self):
        with self.db() as con:
            rows = con.execute(
                """SELECT p.id AS person_id, p.name, COUNT(*) AS pending
                   FROM asset_people ap
                   JOIN people p ON p.id = ap.person_id
                   JOIN assets a ON a.id = ap.asset_id
                   WHERE ap.state = 'confirmed' AND ap.published_at IS NULL AND a.in_review_bin = 0
                   GROUP BY p.id
                   ORDER BY pending DESC"""
            ).fetchall()
            total_photos = con.execute(
                """SELECT COUNT(DISTINCT ap.asset_id)
                   FROM asset_people ap
                   JOIN assets a ON a.id = ap.asset_id
                   WHERE ap.state = 'confirmed' AND ap.published_at IS NULL AND a.in_review_bin = 0"""
            ).fetchone()[0]
        people = [{"person_id": r["person_id"], "name": r["name"], "pending": r["pending"]} for r in rows]
        self.send_json({"ok": True, "people": people, "total_photos": total_photos})

    def publish_run(self, body):
        person_ids = body.get("person_ids")
        with self.db() as con:
            if person_ids:
                placeholders = ",".join("?" * len(person_ids))
                asset_ids = [r["asset_id"] for r in con.execute(
                    f"""SELECT DISTINCT ap.asset_id FROM asset_people ap
                        JOIN assets a ON a.id = ap.asset_id
                        WHERE ap.person_id IN ({placeholders})
                              AND ap.state = 'confirmed' AND ap.published_at IS NULL
                              AND a.in_review_bin = 0""",
                    [int(pid) for pid in person_ids],
                ).fetchall()]
            else:
                asset_ids = [r["asset_id"] for r in con.execute(
                    """SELECT DISTINCT ap.asset_id FROM asset_people ap
                       JOIN assets a ON a.id = ap.asset_id
                       WHERE ap.state = 'confirmed' AND ap.published_at IS NULL
                             AND a.in_review_bin = 0"""
                ).fetchall()]
        total = len(asset_ids)
        if not total:
            print("[Publish] Nothing to publish.", flush=True)
            self.send_json({"ok": True, "published": 0, "total": 0})
            return
        print(f"[Publish] Writing metadata to {total} photos…", flush=True)
        published = 0
        for i, asset_id in enumerate(asset_ids):
            try:
                with self.db() as con:
                    result = self._publish_people_metadata(con, asset_id)
                    if result:
                        published += 1
            except Exception:
                pass
            done = i + 1
            if done % 25 == 0 or done == total:
                print(f"[Publish] {done}/{total} photos", flush=True)
        print(f"[Publish] Done — {published}/{total} photos updated.", flush=True)
        self.send_json({"ok": True, "published": published, "total": total})

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
            raise ValueError(f"Publishing is not supported for this file type: {asset['filename']}")
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
            return None

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
        con.execute(
            "UPDATE asset_people SET published_at=? WHERE asset_id=? AND state='confirmed'",
            (utc_now(), asset_id),
        )
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
            unidentified_faces = int(con.execute(
                """SELECT COUNT(*) FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
                   WHERE f.ignored_at IS NULL AND f.unknown_at IS NULL AND a.in_review_bin=0
                   AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                   AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM asset_people ap WHERE ap.face_id=f.id
                       AND ap.state IN ('confirmed','suggested')
                   )"""
            ).fetchone()[0])
        self.send_json({
            "person": dict(person) if person else None,
            "suggestions": suggestions,
            "remaining_total": remaining_total,
            "people_remaining": people_remaining,
            "deferred_people": deferred_people,
            "people_options": people_options,
            "unidentified_faces": unidentified_faces,
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
        print("[People review] Learning from confirmed faces…", flush=True)
        result = learn_faces(self.db_path, apply=True)
        auto_count = len(result["auto_confirmed"])
        sug_count = result["suggestions"]
        print(f"[People review] Built {result['profiles']} profile(s), "
              f"{sug_count} suggestion(s), {auto_count} auto-confirmed", flush=True)
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
            "suggestions": sug_count,
            "auto_confirmed": auto_count,
        })

    FACE_DISPOSITION_COLUMNS = {"not_a_person": "ignored_at", "unknown_person": "unknown_at"}

    def _apply_people_review_decision(self, con, asset_id, person_id, action, corrected_name=""):
        asset_id = int(asset_id); person_id = int(person_id); action = str(action)
        if action not in {"confirmed", "rejected", "corrected", *self.FACE_DISPOSITION_COLUMNS}:
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
        face_disposition = None
        if action in self.FACE_DISPOSITION_COLUMNS:
            face_disposition = action
            final_action = "rejected"
            if original["face_id"] is not None:
                column = self.FACE_DISPOSITION_COLUMNS[action]
                con.execute(
                    f"""UPDATE face_embeddings SET {column}=?
                        WHERE id=? AND ignored_at IS NULL AND unknown_at IS NULL""",
                    (utc_now(), original["face_id"]),
                )
        else:
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
                   corrected_previous_json,face_disposition,created_at
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (asset_id, person_id, "corrected" if corrected_person_id else final_action,
             json.dumps(previous), corrected_person_id,
             json.dumps(corrected_previous) if corrected_person_id else None,
             face_disposition, utc_now()),
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
                removed = [person["name"]] if action in {"rejected", "corrected", *self.FACE_DISPOSITION_COLUMNS} else []
                published.append(self._publish_people_metadata(
                    con, int(body["asset_id"]), removed, action_id
                ))
                asset_row = con.execute("SELECT filename FROM assets WHERE id=?", (int(body["asset_id"]),)).fetchone()
                log_fname = asset_row["filename"] if asset_row else f"asset #{body['asset_id']}"
        except Exception:
            self._restore_people_batch(published)
            raise
        person_name = str(person["name"])
        corrected = body.get("corrected_name", "").strip()
        if action == "confirmed":
            print(f'[People review] Confirmed "{person_name}" in {log_fname}', flush=True)
        elif action == "corrected" and corrected:
            print(f'[People review] Corrected "{person_name}" → "{corrected}" in {log_fname}', flush=True)
        elif action == "rejected":
            print(f'[People review] Rejected "{person_name}" in {log_fname}', flush=True)
        elif action in self.FACE_DISPOSITION_COLUMNS:
            label = "not a person" if action == "not_a_person" else "unknown person"
            print(f'[People review] Marked {label} in {log_fname}', flush=True)
        self.send_json({"ok": True, "action_id": action_id, "published": 1})

    def people_review_batch_decision(self, body):
        person_id = int(body["person_id"])
        decisions = body.get("decisions")
        if not isinstance(decisions, list) or not 1 <= len(decisions) <= 200:
            raise ValueError("choose between one and 200 photos")
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
                    if action in {"rejected", "corrected", *self.FACE_DISPOSITION_COLUMNS}:
                        removals.setdefault(int(item["asset_id"]), set()).add(person["name"])
                actions_by_asset = dict(zip(asset_ids, action_ids))
                filenames = {int(row["id"]): row["filename"] for row in con.execute(
                    f"SELECT id, filename FROM assets WHERE id IN ({','.join('?' * len(asset_ids))})",
                    asset_ids
                )}
                person_name = str(person["name"])
                for item in decisions:
                    aid = int(item["asset_id"])
                    result = self._publish_people_metadata(
                        con, aid, removals.get(aid, set()), actions_by_asset[aid]
                    )
                    if result is not None:
                        published.append(result)
                    fname = filenames.get(aid, f"asset #{aid}")
                    action = item.get("action", "")
                    corrected = item.get("corrected_name", "").strip()
                    if action == "confirmed":
                        print(f'[People review] Confirmed "{person_name}" in {fname}', flush=True)
                    elif action == "corrected" and corrected:
                        print(f'[People review] Corrected "{person_name}" → "{corrected}" in {fname}', flush=True)
                    elif action == "rejected":
                        print(f'[People review] Rejected "{person_name}" in {fname}', flush=True)
                    elif action in self.FACE_DISPOSITION_COLUMNS:
                        label = "not a person" if action == "not_a_person" else "unknown person"
                        print(f'[People review] Marked {label} in {fname}', flush=True)
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
        disposition_column = self.FACE_DISPOSITION_COLUMNS.get(action["face_disposition"])
        if disposition_column and previous["face_id"] is not None:
            con.execute(
                f"UPDATE face_embeddings SET {disposition_column}=NULL WHERE id=?",
                (previous["face_id"],),
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
        if not isinstance(action_ids, list) or not 1 <= len(action_ids) <= 200:
            raise ValueError("choose between one and 200 review decisions")
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

    def unidentified_faces(self, params):
        """Detected faces with no confirmed name yet, diversity-sampled.

        Instead of showing faces newest-first (which floods the page with
        the same person over and over), this uses greedy diversity sampling:
        pick a face, skip all faces that look too similar to any already
        picked, repeat.  The result is one representative per visual
        cluster -- the user sees a variety of different people and names
        each once.  The existing "Also looks like X" match groups handle
        confirming the rest of that person's photos after naming.

        Still capped to one face per photo per batch to avoid group-shot
        flooding, and still requires a recovered bounding box for cropping."""
        try:
            limit = max(1, min(100, int(params.get("limit", ["30"])[0])))
        except ValueError:
            limit = 30
        where = """f.ignored_at IS NULL AND f.unknown_at IS NULL AND a.in_review_bin=0
                    AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                    AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM asset_people ap WHERE ap.face_id=f.id
                        AND ap.state IN ('confirmed','suggested')
                    )"""
        with self.db() as con:
            total = int(con.execute(
                f"SELECT COUNT(*) FROM face_embeddings f JOIN assets a ON a.id=f.asset_id WHERE {where}"
            ).fetchone()[0])
            pool_limit = min(total, 2000)
            rows = con.execute(
                f"""WITH ranked AS (
                        SELECT f.id AS face_id, f.asset_id, a.filename, a.folder, a.capture_date,
                               f.box_left, f.box_top, f.box_right, f.box_bottom, f.embedding_f32,
                               ROW_NUMBER() OVER (PARTITION BY f.asset_id ORDER BY f.id) AS rn
                        FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
                        WHERE {where}
                    )
                    SELECT face_id, asset_id, filename, folder, capture_date,
                           box_left, box_top, box_right, box_bottom, embedding_f32
                    FROM ranked WHERE rn=1 ORDER BY RANDOM() LIMIT ?""",
                (pool_limit,),
            ).fetchall()
            people_names = [row[0] for row in con.execute(
                "SELECT name FROM people ORDER BY name COLLATE NOCASE"
            )]

        DIVERSITY_THRESHOLD = 0.78
        candidates = []
        for row in rows:
            vector = decode_vector(row["embedding_f32"]) if row["embedding_f32"] else ()
            candidates.append((row, vector))

        selected = []
        selected_vectors = []
        for row, vector in candidates:
            if len(selected) >= limit:
                break
            if not vector:
                selected.append(row)
                continue
            too_similar = False
            for sv in selected_vectors:
                if dot(vector, sv) >= DIVERSITY_THRESHOLD:
                    too_similar = True
                    break
            if not too_similar:
                selected.append(row)
                selected_vectors.append(vector)

        self.send_json({
            "total": total,
            "people_options": people_names,
            "faces": [
                {
                    "face_id": int(row["face_id"]), "asset_id": int(row["asset_id"]),
                    "filename": row["filename"], "folder": row["folder"],
                    "capture_date": row["capture_date"],
                    "box_left": row["box_left"], "box_top": row["box_top"],
                    "box_right": row["box_right"], "box_bottom": row["box_bottom"],
                }
                for row in selected
            ],
        })

    def serve_face_media(self, params):
        """Serve a padded crop of one detected face as a JPEG thumbnail."""
        try:
            face_id = int(params.get("face_id", [""])[0])
        except ValueError:
            return self.send_error(400)
        with self.db() as con:
            row = con.execute(
                """SELECT a.path,f.box_left,f.box_top,f.box_right,f.box_bottom
                   FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
                   WHERE f.id=? AND a.in_review_bin=0""",
                (face_id,),
            ).fetchone()
        if not row or row["box_left"] is None:
            return self.send_error(404)
        path = Path(row["path"]).resolve()
        try:
            path.relative_to(self.library_root)
        except ValueError:
            return self.send_error(403)
        if not path.is_file():
            return self.send_error(404)
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                width, height = image.size
                left, top, right, bottom = row["box_left"], row["box_top"], row["box_right"], row["box_bottom"]
                pad_x = (right - left) * width * 0.35
                pad_y = (bottom - top) * height * 0.35
                crop_left = max(0, int(left * width - pad_x))
                crop_top = max(0, int(top * height - pad_y))
                crop_right = min(width, int(right * width + pad_x))
                crop_bottom = min(height, int(bottom * height + pad_y))
                if crop_right <= crop_left or crop_bottom <= crop_top:
                    return self.send_error(404)
                cropped = image.crop((crop_left, crop_top, crop_right, crop_bottom))
                cropped.thumbnail((360, 360))
                buffer = io.BytesIO()
                cropped.save(buffer, format="JPEG", quality=85)
        except Exception:
            return self.send_error(404)
        data = buffer.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def name_face(self, body):
        """Attach a name directly to one detected face -- the missing link
        between face detection (which only fills face_embeddings) and the
        name-first confirmation flow the rest of People review is built on.
        Because face_id is set, face_learning.build_profile can use this
        confirmation even when the photo has other, unnamed faces in it."""
        face_id = int(body["face_id"]); name = clean_tag(str(body.get("name", "")))
        if not name:
            raise ValueError("enter a person's name")
        with self.db() as con:
            face = con.execute(
                "SELECT asset_id,embedding_f32 FROM face_embeddings WHERE id=?", (face_id,)
            ).fetchone()
            if not face or face["asset_id"] is None:
                raise ValueError("this face is no longer available")
            asset_id = int(face["asset_id"])
            self.get_active_asset(con, asset_id)
            person_id = self.resolve_or_create_person(con, name)
            con.execute(
                """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
                   VALUES (?,?,'confirmed',NULL,?,'manual_face',?)
                   ON CONFLICT(asset_id,person_id) DO UPDATE SET
                       state='confirmed',face_id=excluded.face_id,source='manual_face',
                       updated_at=excluded.updated_at""",
                (asset_id, person_id, face_id, utc_now()),
            )
            sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)
            fname = con.execute("SELECT filename, relative_path FROM assets WHERE id=?", (asset_id,)).fetchone()
            matches = self._find_similar_unidentified_faces(con, face_id, face["embedding_f32"])
        print(f'[Name faces] Named "{name}" in {fname["relative_path"] if fname else f"asset #{asset_id}"}', flush=True)
        self.send_json({"ok": True, "person_id": person_id, "matches": matches})

    def name_face_batch(self, body):
        """Confirm multiple faces as the same person in one request.  Metadata
        publishing is deferred -- only database records are written here so the
        confirm+find-more loop stays fast.  The client calls
        /api/faces/publish-person once the loop is exhausted."""
        person_id = int(body["person_id"])
        face_ids = body.get("face_ids", [])
        if not isinstance(face_ids, list) or not face_ids:
            raise ValueError("face_ids must be a non-empty list")
        face_ids = [int(fid) for fid in face_ids]
        confirmed = 0
        with self.db() as con:
            person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
            if not person:
                raise ValueError("person not found")
            name = person["name"]
            now = utc_now()
            for face_id in face_ids:
                face = con.execute(
                    "SELECT asset_id FROM face_embeddings WHERE id=?", (face_id,)
                ).fetchone()
                if not face or face["asset_id"] is None:
                    continue
                asset_id = int(face["asset_id"])
                try:
                    self.get_active_asset(con, asset_id)
                except Exception:
                    continue
                con.execute(
                    """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
                       VALUES (?,?,'confirmed',NULL,?,'manual_face',?)
                       ON CONFLICT(asset_id,person_id) DO UPDATE SET
                           state='confirmed',face_id=excluded.face_id,source='manual_face',
                           updated_at=excluded.updated_at""",
                    (asset_id, person_id, face_id, now),
                )
                sync_person_tags(con, asset_id)
                rebuild_search_row(con, asset_id)
                confirmed += 1
        print(f'[Name faces] Batch confirmed {confirmed} as "{name}"', flush=True)
        self.send_json({"ok": True, "confirmed": confirmed})

    def publish_person_metadata(self, body):
        """Publish metadata to JPEG files for all confirmed faces of a person.
        Called once after the matching chain is exhausted so metadata writes
        don't slow down the interactive confirm-and-find-more loop.

        Opens and closes the DB connection per photo so other requests (like
        naming a new person) aren't blocked for the entire publish run."""
        person_id = int(body["person_id"])
        with self.db() as con:
            person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
            if not person:
                raise ValueError("person not found")
            asset_ids = [int(row["asset_id"]) for row in con.execute(
                """SELECT DISTINCT ap.asset_id FROM asset_people ap
                   JOIN assets a ON a.id=ap.asset_id
                   WHERE ap.person_id=? AND ap.state='confirmed' AND a.in_review_bin=0""",
                (person_id,),
            ).fetchall()]
        name = person["name"]
        total = len(asset_ids)
        if not total:
            print(f'[Name faces] No photos to publish for "{name}"', flush=True)
            self.send_json({"ok": True, "published": 0})
            return
        print(f'[Name faces] Publishing "{name}" to {total} photos…', flush=True)
        published = 0
        for i, asset_id in enumerate(asset_ids):
            try:
                with self.db() as con:
                    result = self._publish_people_metadata(con, asset_id)
                    if result:
                        published += 1
            except Exception:
                pass
            done = i + 1
            if done % 25 == 0 or done == total:
                print(f'[Name faces] Published "{name}" — {done}/{total}', flush=True)
        self.send_json({"ok": True, "published": published})

    def _find_similar_unidentified_faces(self, con, face_id, embedding_blob, limit=200):
        """Other still-unidentified faces that likely show the same person as
        the one just named -- lets the Name-faces page group repeats of one
        person behind a single "confirm all" instead of one dropdown pick
        each. Direct face-to-face similarity (not a person profile centroid)
        so it works from the very first photo named, before enough confirmed
        faces exist to build a profile in face_learning.build_profile."""
        vector = decode_vector(embedding_blob) if embedding_blob else ()
        if not vector:
            return []
        rows = con.execute(
            """SELECT f.id AS face_id, f.asset_id, f.embedding_f32,
                      f.box_left, f.box_top, f.box_right, f.box_bottom,
                      a.filename, a.folder, a.capture_date
               FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
               WHERE f.id!=? AND f.ignored_at IS NULL AND f.unknown_at IS NULL AND a.in_review_bin=0
                     AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                     AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM asset_people ap WHERE ap.face_id=f.id AND ap.state='confirmed'
                     )
               ORDER BY f.id DESC LIMIT 50000""",
            (face_id,),
        ).fetchall()
        SIMILAR_FACE_THRESHOLD = 0.65
        scored = []
        for row in rows:
            candidate = decode_vector(row["embedding_f32"])
            if not candidate:
                continue
            score = dot(vector, candidate)
            if score >= SIMILAR_FACE_THRESHOLD:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"face_id": int(row["face_id"]), "asset_id": int(row["asset_id"]),
             "score": round(score, 4),
             "box_left": row["box_left"], "box_top": row["box_top"],
             "box_right": row["box_right"], "box_bottom": row["box_bottom"],
             "filename": row["filename"], "folder": row["folder"], "capture_date": row["capture_date"]}
            for score, row in scored[:limit]
        ]

    def find_more_faces(self, body):
        """After confirming a batch of 'Also looks like' matches, search for
        more unidentified faces similar to this person.  High-confidence matches
        (>=0.75) are auto-confirmed in the database without metadata publishing;
        only borderline matches (0.65-0.75) are returned for manual review."""
        person_id = int(body["person_id"])
        AUTO_CONFIRM_THRESHOLD = 0.75
        SIMILAR_FACE_THRESHOLD = 0.65
        with self.db() as con:
            person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
            if not person:
                raise ValueError("person not found")
            refs = con.execute(
                """SELECT f.embedding_f32 FROM face_embeddings f
                   JOIN asset_people ap ON ap.face_id=f.id
                   WHERE ap.person_id=? AND ap.state='confirmed' AND f.embedding_f32 IS NOT NULL""",
                (person_id,),
            ).fetchall()
            vectors = [v for row in refs if (v := decode_vector(row["embedding_f32"]))]
            if not vectors:
                self.send_json({"ok": True, "matches": [], "auto_confirmed": 0, "name": person["name"]})
                return
            center = centroid(vectors)
            if not center:
                self.send_json({"ok": True, "matches": [], "auto_confirmed": 0, "name": person["name"]})
                return
            rows = con.execute(
                """SELECT f.id AS face_id, f.asset_id, f.embedding_f32,
                          f.box_left, f.box_top, f.box_right, f.box_bottom,
                          a.filename, a.folder, a.capture_date
                   FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
                   WHERE f.ignored_at IS NULL AND f.unknown_at IS NULL AND a.in_review_bin=0
                         AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                         AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
                         AND NOT EXISTS (
                             SELECT 1 FROM asset_people ap WHERE ap.face_id=f.id AND ap.state='confirmed'
                         )
                   ORDER BY f.id DESC LIMIT 50000""",
            ).fetchall()
            high = []
            borderline = []
            for row in rows:
                candidate = decode_vector(row["embedding_f32"])
                if not candidate:
                    continue
                score = dot(center, candidate)
                if score >= AUTO_CONFIRM_THRESHOLD:
                    high.append((score, row))
                elif score >= SIMILAR_FACE_THRESHOLD:
                    borderline.append((score, row))
            now = utc_now()
            auto_confirmed = 0
            for _score, row in high:
                face_id = int(row["face_id"])
                asset_id = int(row["asset_id"])
                try:
                    self.get_active_asset(con, asset_id)
                except Exception:
                    continue
                con.execute(
                    """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
                       VALUES (?,?,'confirmed',NULL,?,'manual_face',?)
                       ON CONFLICT(asset_id,person_id) DO UPDATE SET
                           state='confirmed',face_id=excluded.face_id,source='manual_face',
                           updated_at=excluded.updated_at""",
                    (asset_id, person_id, face_id, now),
                )
                sync_person_tags(con, asset_id)
                rebuild_search_row(con, asset_id)
                auto_confirmed += 1
            borderline.sort(key=lambda item: item[0], reverse=True)
            matches = [
                {"face_id": int(row["face_id"]), "asset_id": int(row["asset_id"]),
                 "score": round(score, 4),
                 "box_left": row["box_left"], "box_top": row["box_top"],
                 "box_right": row["box_right"], "box_bottom": row["box_bottom"],
                 "filename": row["filename"], "folder": row["folder"],
                 "capture_date": row["capture_date"]}
                for score, row in borderline[:200]
            ]
        n = len(vectors)
        print(f'[Name faces] {auto_confirmed} auto-confirmed, {len(matches)} borderline for "{person["name"]}" (centroid from {n} confirmed)', flush=True)
        self.send_json({"ok": True, "matches": matches, "auto_confirmed": auto_confirmed, "name": person["name"]})

    def ignore_face(self, body):
        face_id = int(body["face_id"])
        with self.db() as con:
            updated = con.execute(
                "UPDATE face_embeddings SET ignored_at=? WHERE id=? AND ignored_at IS NULL AND unknown_at IS NULL",
                (utc_now(), face_id),
            ).rowcount
        if not updated:
            raise ValueError("this face was already handled")
        self.send_json({"ok": True})

    def mark_face_unknown(self, body):
        """A real face, just not one the reviewer can name -- distinct from
        ignore_face's "the detector was wrong, this isn't a face at all".
        Mirrors the same unknown_at flag People review's "Unknown person"
        disposition already sets on face_embeddings (see
        FACE_DISPOSITION_COLUMNS), so both routes into that state stay
        equivalent: excluded from face_learning.learn()'s suggestion source
        and from ever being proposed again, for anyone."""
        face_id = int(body["face_id"])
        with self.db() as con:
            updated = con.execute(
                "UPDATE face_embeddings SET unknown_at=? WHERE id=? AND ignored_at IS NULL AND unknown_at IS NULL",
                (utc_now(), face_id),
            ).rowcount
        if not updated:
            raise ValueError("this face was already handled")
        self.send_json({"ok": True})

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

    def version_info(self):
        install_root = Path(__file__).parent.parent.resolve()
        on_disk = _on_disk_app_version(install_root)
        self.send_json({
            "version": _STARTUP_VERSION,
            "startedAt": _STARTED_AT,
            "onDiskVersion": on_disk,
            "restartReady": bool(on_disk and on_disk != _STARTUP_VERSION),
            "pid": os.getpid(),
        })

    def diagnostics(self):
        semantic = semantic_status(type(self).db_path)
        with self.db() as con:
            integrity = str(con.execute("PRAGMA quick_check").fetchone()[0])
            schema_version = int(con.execute("PRAGMA user_version").fetchone()[0])
            counts = {
                "assets": int(con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0]),
                "metadata_ready": int(con.execute("SELECT COUNT(*) FROM assets WHERE metadata_scanned=1 AND in_review_bin=0").fetchone()[0]),
                "cloud_only": int(con.execute("SELECT COUNT(*) FROM assets WHERE metadata_scanned=0 AND in_review_bin=0").fetchone()[0]),
                "mapped": int(con.execute("SELECT COUNT(*) FROM assets WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL AND in_review_bin=0").fetchone()[0]),
                "location_pending": int(con.execute("SELECT COUNT(*) FROM assets WHERE location_scanned=0 AND in_review_bin=0").fetchone()[0]),
                "ocr_complete": int(con.execute("SELECT COUNT(*) FROM text_data WHERE ocr_scanned=1").fetchone()[0]),
                "ocr_with_text": int(con.execute("SELECT COUNT(*) FROM text_data WHERE ocr_text<>''").fetchone()[0]),
                "ocr_pending": int(con.execute("""SELECT COUNT(*) FROM text_data x JOIN assets a ON a.id=x.asset_id
                    WHERE a.media_type='image' AND a.metadata_scanned=1 AND a.in_review_bin=0 AND x.ocr_scanned=0""").fetchone()[0]),
                "ocr_errors": int(con.execute("SELECT COUNT(*) FROM text_data WHERE ocr_error<>''").fetchone()[0]),
                "scan_errors": int(con.execute("SELECT COUNT(*) FROM assets WHERE scan_error<>''").fetchone()[0]),
                "semantic_errors": int(con.execute("SELECT COUNT(*) FROM assets WHERE semantic_error<>''").fetchone()[0]),
                "face_scan_errors": int(con.execute("SELECT COUNT(*) FROM assets WHERE face_scan_error<>''").fetchone()[0]),
                "people_pending": int(con.execute("SELECT COUNT(*) FROM asset_people WHERE state='suggested'").fetchone()[0]),
                "unidentified_faces": int(con.execute(
                    """SELECT COUNT(*) FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
                       WHERE f.ignored_at IS NULL AND a.in_review_bin=0
                         AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                         AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
                         AND NOT EXISTS (
                             SELECT 1 FROM asset_people ap WHERE ap.face_id=f.id
                             AND ap.state IN ('confirmed','suggested')
                         )"""
                ).fetchone()[0]),
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
        install_root = Path(__file__).parent.parent.resolve()
        source_checkout = (install_root / ".git").exists()
        on_disk_version = _on_disk_app_version(install_root) if source_checkout else None
        job.update({
            "current_version": APP_VERSION,
            "managed_install": is_managed_install(install_root),
            "is_source_checkout": source_checkout,
            "current_install_root": str(install_root),
            "managed_install_root": str(managed_install_root()),
            "on_disk_version": on_disk_version,
            # The running process's APP_VERSION is baked in at import time and stays
            # stale until restarted; `git pull` (or any other on-disk change) doesn't
            # touch it. Comparing against a fresh read of product.py lets the update
            # panel tell "you're behind the on-disk code" apart from "you're behind
            # the latest GitHub release" and offer a plain restart for the former.
            "restart_ready": bool(source_checkout and on_disk_version and on_disk_version != APP_VERSION),
        })
        self.send_json(job)

    def check_update(self, _body):
        started = type(self)._begin_update_check(force=True)
        self.send_json({"ok": True, "state": "checking", "started": started}, 202)

    def _spawn_updater_helper(self, extra_args):
        helper_root = updates_root()
        helper_root.mkdir(parents=True, exist_ok=True)
        helper = helper_root / "lensledger-updater-helper.py"
        shutil.copy2(Path(__file__).parent / "lensledger_updater.py", helper)
        command = [sys.executable, str(helper), *extra_args]
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

    def _schedule_shutdown(self):
        def stop_server():
            threading.Event().wait(1.0)
            self.server.shutdown()

        threading.Thread(target=stop_server, name="LensLedger-update-shutdown", daemon=True).start()

    def install_update(self, _body):
        install_root = Path(__file__).parent.parent.resolve()
        if not is_managed_install(install_root):
            raise ValueError(
                "This copy is not a managed installation, so it cannot update itself. "
                "Update a source checkout with git pull, or run Install LensLedger.cmd "
                "once to create a separate managed copy."
            )
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

        command_args = [
            "install-latest",
            "--current-root", str(install_root),
            "--current", APP_VERSION,
            "--wait-pid", str(os.getpid()),
            "--old-window-pid", str(os.getppid()),
        ]
        if (install_root / "photo-index.sqlite3").is_file():
            command_args.extend(["--legacy-root", str(install_root)])
        self._spawn_updater_helper(command_args)
        self._schedule_shutdown()
        self.send_json({
            "ok": True,
            "state": "restarting",
            "message": "The verified update is being installed. LensLedger will reopen automatically.",
        }, 202)

    def restart_source(self, _body):
        """Restart this process in place -- no download, no file changes. For a
        source checkout where `git pull` (or any other on-disk edit) already
        moved the code past what this running process has loaded; see
        update_status's `restart_ready`."""
        install_root = Path(__file__).parent.parent.resolve()
        if not (install_root / ".git").exists():
            raise ValueError("This copy is not a source checkout, so there is no on-disk code to restart into.")
        with type(self).update_lock:
            type(self).update_job = {
                "state": "restarting",
                "message": "Restarting LensLedger to load the code already on disk…",
                "current_version": APP_VERSION,
            }

        self._spawn_updater_helper([
            "restart-source",
            "--current-root", str(install_root),
            "--wait-pid", str(os.getpid()),
            "--old-window-pid", str(os.getppid()),
        ])
        self._schedule_shutdown()
        self.send_json({
            "ok": True,
            "state": "restarting",
            "message": "Restarting to load the code already on disk. LensLedger will reopen automatically.",
        }, 202)

    def reveal_file(self, body):
        asset_id = int(body["id"])
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
        source_path = self.library_root / Path(asset["relative_path"])
        if not source_path.exists():
            raise ValueError("File not found on disk")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(source_path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(source_path)])
        else:
            subprocess.Popen(["xdg-open", str(source_path.parent)])
        self.send_json({"ok": True})

    def ocr_status(self):
        with type(self).ocr_lock:
            job = dict(type(self).ocr_job)
        self.send_json(job)

    def ocr_errors(self):
        with self.db() as con:
            rows = con.execute(
                """SELECT a.relative_path,x.ocr_error FROM text_data x JOIN assets a ON a.id=x.asset_id
                   WHERE x.ocr_error<>'' ORDER BY a.id DESC LIMIT 200"""
            ).fetchall()
        self.send_json({"errors": [{"path": row["relative_path"], "error": row["ocr_error"]} for row in rows]})

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
            if type(self).scan_all_job.get("state") == "running":
                raise ValueError("wait for \"Run all scans\" to finish, or stop it first")
            type(self).ocr_job = {
                "state": "running", "message": "Preparing local text recognition…",
                "total": 0, "attempted": 0, "with_text": 0, "errors": 0, "started_at": utc_now(),
            }
            type(self).ocr_cancel.clear()
        handler_class = type(self)
        database = handler_class.db_path
        started_at = handler_class.ocr_job["started_at"]
        threading.Thread(
            target=_run_ocr_job, args=(handler_class, database, since, workers, started_at),
            name="LensLedger-ocr", daemon=True,
        ).start()
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
        with type(self).semantic_install_lock:
            install = dict(type(self).semantic_install_job)
        self.send_json({**coverage, **job, "install": install})

    def semantic_errors(self):
        with self.db() as con:
            rows = con.execute(
                """SELECT relative_path, semantic_error FROM assets
                   WHERE semantic_error<>'' AND in_review_bin=0
                   ORDER BY id DESC LIMIT 200"""
            ).fetchall()
            if not rows:
                model_row = con.execute(
                    "SELECT model FROM semantic_embeddings LIMIT 1"
                ).fetchone()
                if model_row:
                    rows = con.execute(
                        """SELECT a.relative_path FROM assets a
                           LEFT JOIN semantic_embeddings se ON se.asset_id=a.id AND se.model=?
                           WHERE a.media_type='image' AND a.metadata_scanned=1
                                 AND a.in_review_bin=0 AND se.asset_id IS NULL
                           ORDER BY a.id DESC LIMIT 200""",
                        (model_row["model"],),
                    ).fetchall()
                    self.send_json({
                        "errors": [{"path": row["relative_path"], "error": "Not indexed — no error recorded. Try rebuilding the meaning index."} for row in rows],
                    })
                    return
        self.send_json({
            "errors": [{"path": row["relative_path"], "error": row["semantic_error"]} for row in rows],
        })

    def start_semantic_index(self, body):
        settings = load_settings()
        scan_cfg = settings.get("scan", {})
        batch_size = max(1, min(64, int(body.get("batch_size", scan_cfg.get("semantic_batch_size", 16)))))
        with type(self).semantic_lock:
            if type(self).semantic_job.get("state") == "running":
                raise ValueError("meaning indexing is already running")
            with type(self).library_lock:
                if type(self).library_job.get("state") == "scanning":
                    raise ValueError("wait for the library scan to finish first")
            with type(self).ocr_lock:
                if type(self).ocr_job.get("state") == "running":
                    raise ValueError("pause OCR before starting meaning indexing")
            if type(self).scan_all_job.get("state") == "running":
                raise ValueError("wait for \"Run all scans\" to finish, or stop it first")
            type(self).semantic_job = {
                "state": "running", "message": "Loading the optional local meaning model…",
                "total": 0, "indexed_this_pass": 0, "errors": 0, "started_at": utc_now(),
            }
            type(self).semantic_cancel.clear()
        handler_class = type(self)
        database = handler_class.db_path
        started_at = handler_class.semantic_job["started_at"]
        threading.Thread(
            target=_run_semantic_index_job, args=(handler_class, database, batch_size, started_at),
            name="LensLedger-semantic", daemon=True,
        ).start()
        self.send_json({"ok": True, "state": "running"}, 202)

    def cancel_semantic_index(self, _body):
        with type(self).semantic_lock:
            if type(self).semantic_job.get("state") != "running":
                raise ValueError("meaning indexing is not running")
            type(self).semantic_cancel.set()
            type(self).semantic_job["message"] = "Pausing after the active image batch…"
        self.send_json({"ok": True, "state": "cancelling"}, 202)

    def install_semantic_requirements(self, _body):
        with type(self).semantic_install_lock:
            if type(self).semantic_install_job.get("state") == "installing":
                raise ValueError("Meaning search setup is already in progress")
            if semantic_is_available():
                raise ValueError("Meaning search is already installed")
            type(self).semantic_install_job = {
                "state": "installing",
                "message": "Downloading and installing the local meaning-search model software…",
                "started_at": utc_now(),
            }
        handler_class = type(self)
        requirements = Path(__file__).parent.parent / "requirements-semantic.txt"

        def worker():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
                    capture_output=True, text=True, timeout=1800,
                )
                if result.returncode == 0:
                    with handler_class.semantic_install_lock:
                        handler_class.semantic_install_job = {
                            "state": "complete",
                            "message": "Meaning search is installed. You can build the index below.",
                        }
                else:
                    tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-8:])
                    with handler_class.semantic_install_lock:
                        handler_class.semantic_install_job = {
                            "state": "error",
                            "message": f"Install failed: {tail or 'unknown error'}",
                        }
            except Exception as exc:
                with handler_class.semantic_install_lock:
                    handler_class.semantic_install_job = {"state": "error", "message": str(exc)}

        threading.Thread(target=worker, name="LensLedger-semantic-install", daemon=True).start()
        self.send_json({"ok": True, "state": "installing"}, 202)

    def face_scan_job_status(self):
        coverage = face_scan_status(type(self).db_path)
        with type(self).face_scan_lock:
            job = dict(type(self).face_scan_job)
        with type(self).face_install_lock:
            install = dict(type(self).face_install_job)
        self.send_json({**coverage, **job, "install": install})

    def face_scan_errors(self):
        self.send_json({"errors": face_scan_list_errors(type(self).db_path)})

    def start_face_scan(self, _body):
        with type(self).face_scan_lock:
            if type(self).face_scan_job.get("state") == "running":
                raise ValueError("Face detection is already running")
            if not face_is_available():
                raise ValueError(
                    'Face detection is not set up yet. Click "Set up face detection" on the Scan your photos page first.'
                )
            with type(self).library_lock:
                if type(self).library_job.get("state") == "scanning":
                    raise ValueError("wait for the library scan to finish before starting face detection")
            if type(self).scan_all_job.get("state") == "running":
                raise ValueError("wait for \"Run all scans\" to finish, or stop it first")
            type(self).face_scan_job = {
                "state": "running", "message": "Preparing local face detection…",
                "total": 0, "processed": 0, "faces_found": 0, "errors": 0, "started_at": utc_now(),
            }
            type(self).face_scan_cancel.clear()
        handler_class = type(self)
        database = handler_class.db_path
        library_root = handler_class.library_root
        started_at = handler_class.face_scan_job["started_at"]
        threading.Thread(
            target=_run_face_scan_job, args=(handler_class, database, library_root, started_at),
            name="LensLedger-face-scan", daemon=True,
        ).start()
        self.send_json({"ok": True, "state": "running"}, 202)

    def cancel_face_scan(self, _body):
        with type(self).face_scan_lock:
            if type(self).face_scan_job.get("state") != "running":
                raise ValueError("Face detection is not running")
            type(self).face_scan_cancel.set()
            type(self).face_scan_job["message"] = "Pausing after the active photo finishes…"
        self.send_json({"ok": True, "state": "cancelling"}, 202)

    def install_face_requirements(self, _body):
        with type(self).face_install_lock:
            if type(self).face_install_job.get("state") == "installing":
                raise ValueError("Face detection setup is already in progress")
            if face_is_available():
                raise ValueError("Face detection is already installed")
            type(self).face_install_job = {
                "state": "installing",
                "message": "Downloading and installing the local face-detection model software…",
                "started_at": utc_now(),
            }
        handler_class = type(self)
        requirements = Path(__file__).parent.parent / "requirements-face.txt"

        def worker():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
                    capture_output=True, text=True, timeout=1800,
                )
                if result.returncode == 0:
                    with handler_class.face_install_lock:
                        handler_class.face_install_job = {
                            "state": "complete",
                            "message": "Face detection is installed. You can scan for faces below.",
                        }
                else:
                    tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-8:])
                    with handler_class.face_install_lock:
                        handler_class.face_install_job = {
                            "state": "error",
                            "message": f"Install failed: {tail or 'unknown error'}",
                        }
            except Exception as exc:
                with handler_class.face_install_lock:
                    handler_class.face_install_job = {"state": "error", "message": str(exc)}

        threading.Thread(target=worker, name="LensLedger-face-install", daemon=True).start()
        self.send_json({"ok": True, "state": "installing"}, 202)

    def scan_all_status(self):
        with type(self).scan_all_lock:
            job = dict(type(self).scan_all_job)
        self.send_json(job)

    def start_scan_all(self, _body):
        handler_class = type(self)
        with handler_class.scan_all_lock:
            if handler_class.scan_all_job.get("state") == "running":
                raise ValueError("\"Run all scans\" is already in progress")
            with handler_class.library_lock:
                if handler_class.library_job.get("state") == "scanning":
                    raise ValueError("wait for the current location scan to finish first")
            with handler_class.ocr_lock:
                if handler_class.ocr_job.get("state") == "running":
                    raise ValueError("wait for the current OCR pass to finish first")
            with handler_class.semantic_lock:
                if handler_class.semantic_job.get("state") == "running":
                    raise ValueError("wait for the current meaning-search pass to finish first")
            with handler_class.face_scan_lock:
                if handler_class.face_scan_job.get("state") == "running":
                    raise ValueError("wait for the current face-detection pass to finish first")
            started_at = utc_now()
            handler_class.scan_all_job = {
                "state": "running", "step": None, "message": "Starting…", "started_at": started_at,
            }
            handler_class.scan_all_cancel.clear()
        root = handler_class.library_root
        database = handler_class.db_path
        threading.Thread(
            target=_run_scan_all_job, args=(handler_class, root, database, started_at),
            name="LensLedger-scan-all", daemon=True,
        ).start()
        self.send_json({"ok": True, "state": "running"}, 202)

    def cancel_scan_all(self, _body):
        handler_class = type(self)
        with handler_class.scan_all_lock:
            if handler_class.scan_all_job.get("state") != "running":
                raise ValueError("\"Run all scans\" is not running")
            handler_class.scan_all_cancel.set()
            current_step = handler_class.scan_all_job.get("step")
            handler_class.scan_all_job["message"] = "Stopping after the current step…"
        if current_step == "location":
            handler_class.library_cancel.set()
        elif current_step == "ocr":
            handler_class.ocr_cancel.set()
        elif current_step == "semantic":
            handler_class.semantic_cancel.set()
        elif current_step == "face":
            handler_class.face_scan_cancel.set()
        self.send_json({"ok": True, "state": "cancelling"}, 202)

    def backup_database(self, _body):
        destination = create_verified_database_backup(type(self).db_path)
        self.send_json({"ok": True, "path": str(destination), "bytes": destination.stat().st_size})

    def library_status(self):
        with type(self).library_lock:
            job = dict(type(self).library_job)
        job.pop("error_details", None)
        job["current_root"] = str(type(self).library_root)
        self.send_json(job)

    def library_errors(self):
        with type(self).library_lock:
            in_memory = list(type(self).library_job.get("error_details", []))
        if in_memory:
            self.send_json({"errors": in_memory[:200]})
            return
        with self.db() as con:
            rows = con.execute(
                """SELECT relative_path, scan_error FROM assets
                   WHERE scan_error<>'' ORDER BY id DESC LIMIT 200"""
            ).fetchall()
        self.send_json({"errors": [{"path": row["relative_path"], "error": row["scan_error"]} for row in rows]})

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

    def add_library(self, body):
        value = str(body.get("path", "")).strip()
        if not value:
            raise ValueError("choose a photo library folder")
        root = Path(value).resolve()
        if not root.is_dir():
            raise ValueError("that folder does not exist")
        db_location = str(body.get("db_location", "library")).strip()
        if db_location == "appdata":
            database = library_db_path_appdata(root).resolve()
        else:
            database = library_db_path(root).resolve()
        database.parent.mkdir(parents=True, exist_ok=True)
        associate_db_path(root, database)
        save_library_state(root)
        self.send_json({"ok": True, "path": str(root), "database": str(database)})

    def relocate_library(self, body):
        old_path = str(body.get("old_path", "")).strip()
        new_path = str(body.get("new_path", "")).strip()
        if not old_path or not new_path:
            raise ValueError("both old and new paths are required")
        new_root = Path(new_path).resolve()
        if not new_root.is_dir():
            raise ValueError("the new folder does not exist")
        old_root = Path(old_path).resolve()
        database = library_db_path(old_root)
        if not database.is_file():
            raise ValueError("no database found for the original library")
        with type(self).library_lock:
            if type(self).library_job.get("state") == "scanning":
                raise ValueError("cannot relocate while a scan is running")
        associate_db_path(new_root, database)
        with connect(database) as con:
            old_prefix = str(old_root)
            new_prefix = str(new_root)
            con.execute(
                "UPDATE assets SET path = ? || SUBSTR(path, ?) WHERE path LIKE ? ESCAPE '\\'",
                (new_prefix, len(old_prefix) + 1, old_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"),
            )
            try:
                con.execute("UPDATE assets SET library_root = ? WHERE library_root = ?", (new_prefix, old_prefix))
            except sqlite3.OperationalError:
                pass
        config = load_library_config()
        libraries = config.get("libraries", [])
        old_cf = str(old_root).casefold()
        libraries = [new_prefix if str(Path(p).resolve()).casefold() == old_cf else p for p in libraries]
        if new_prefix not in libraries:
            libraries.insert(0, new_prefix)
        current = config.get("current_root", "")
        if current and str(Path(current).resolve()).casefold() == old_cf:
            current = new_prefix
        from library_config import LIBRARY_STATE_PATH
        tmp = LIBRARY_STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"current_root": current, "libraries": libraries}, indent=2), encoding="utf-8")
        tmp.replace(LIBRARY_STATE_PATH)
        if str(self.library_root).casefold() == old_cf:
            type(self).current_library = (new_root, database)
        self.send_json({"ok": True, "path": str(new_root)})

    def open_library(self, body):
        value = str(body.get("path", "")).strip()
        if not value:
            raise ValueError("choose a photo library folder")
        root = Path(value).resolve()
        if not root.is_dir():
            raise ValueError("that photo library folder does not exist")
        db_location = str(body.get("db_location", "library")).strip()
        if db_location == "appdata":
            database = library_db_path_appdata(root).resolve()
        else:
            database = library_db_path(root).resolve()
        database.parent.mkdir(parents=True, exist_ok=True)
        associate_db_path(root, database)
        with type(self).library_lock:
            if type(self).library_job.get("state") == "scanning":
                raise ValueError("another photo library is currently being indexed")
            if type(self).scan_all_job.get("state") == "running":
                raise ValueError("wait for \"Run all scans\" to finish, or stop it first")
            type(self).library_job = {
                "state": "scanning", "message": "Discovering photos and videos…",
                "target_root": str(root), "scanned": 0, "changed": 0,
                "unchanged": 0, "removed": 0, "errors": 0, "placeholders": 0,
                "started_at": utc_now(),
            }
            type(self).library_cancel.clear()
        handler_class = type(self)
        started_at = handler_class.library_job["started_at"]
        threading.Thread(
            target=_run_library_scan_job, args=(handler_class, root, database, started_at),
            name="LensLedger-library-index", daemon=True,
        ).start()
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

    def add_tag_batch(self, body):
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            raise ValueError("select at least one photo")
        if len(ids) > 500:
            raise ValueError("too many photos selected (max 500)")
        incoming = [
            clean_tag(part) for part in re.split(r"[,;\n]+", str(body.get("tag", "")))
            if clean_tag(part)
        ]
        if not incoming:
            raise ValueError("enter at least one tag")
        total = 0
        with self.db() as con:
            for asset_id in ids:
                asset_id = int(asset_id)
                asset = self.get_active_asset(con, asset_id)
                row = con.execute("SELECT subject,tags FROM asset_annotations WHERE relative_path=?", (asset["relative_path"],)).fetchone()
                names = split_tags(row["tags"] if row else "")
                known = {name.casefold() for name in names}
                added = 0
                for tag in incoming:
                    if tag.casefold() not in known:
                        names.append(tag); known.add(tag.casefold()); added += 1
                if added:
                    con.execute("""INSERT INTO asset_annotations(relative_path,subject,tags) VALUES (?,?,?)
                        ON CONFLICT(relative_path) DO UPDATE SET tags=excluded.tags""", (asset["relative_path"], row["subject"] if row else "", ";".join(names)))
                    for tag in incoming:
                        con.execute("DELETE FROM asset_tag_exclusions WHERE relative_path=? AND tag=? COLLATE NOCASE", (asset["relative_path"], tag))
                    set_source_tags(con, asset_id, "asset_rule", names); rebuild_search_row(con, asset_id)
                    total += 1
        self.send_json({"ok": True, "photos_tagged": total, "tags_applied": len(incoming)})

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

    def move_to_review_bin_batch(self, body):
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            raise ValueError("select at least one photo")
        if len(ids) > 500:
            raise ValueError("too many photos selected (max 500)")
        review_ids = []
        with self.db() as con:
            for asset_id in ids:
                asset_id = int(asset_id)
                asset = self.get_active_asset(con, asset_id)
                source = Path(asset["path"]).resolve(); source.relative_to(self.library_root)
                if any(part in {"!LensLedger", "_PhotoIndex", "_FaceData"} for part in source.relative_to(self.library_root).parts) or not source.is_file():
                    continue
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
                review_ids.append(review_id)
        self.send_json({"ok": True, "moved": len(review_ids), "review_ids": review_ids})

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

    def delete_from_review_bin(self, body):
        review_id = int(body["review_id"])
        with self.db() as con:
            row = con.execute("SELECT * FROM review_bin WHERE id=? AND restored_at IS NULL", (review_id,)).fetchone()
            if not row:
                raise ValueError("review item is no longer available")
            source = Path(row["review_path"]).resolve()
            if source.is_file():
                source.unlink()
            con.execute("DELETE FROM asset_tags WHERE asset_id=?", (row["asset_id"],))
            con.execute("DELETE FROM asset_people WHERE asset_id=?", (row["asset_id"],))
            con.execute("DELETE FROM face_embeddings WHERE asset_id=?", (row["asset_id"],))
            con.execute("DELETE FROM search_fts WHERE asset_id=?", (row["asset_id"],))
            con.execute("DELETE FROM assets WHERE id=?", (row["asset_id"],))
            con.execute("DELETE FROM review_bin WHERE id=?", (review_id,))
        self.send_json({"ok": True})

    def empty_review_bin(self):
        deleted = 0
        with self.db() as con:
            rows = con.execute("SELECT * FROM review_bin WHERE restored_at IS NULL").fetchall()
            for row in rows:
                source = Path(row["review_path"]).resolve()
                if source.is_file():
                    source.unlink()
                con.execute("DELETE FROM asset_tags WHERE asset_id=?", (row["asset_id"],))
                con.execute("DELETE FROM asset_people WHERE asset_id=?", (row["asset_id"],))
                con.execute("DELETE FROM face_embeddings WHERE asset_id=?", (row["asset_id"],))
                con.execute("DELETE FROM search_fts WHERE asset_id=?", (row["asset_id"],))
                con.execute("DELETE FROM assets WHERE id=?", (row["asset_id"],))
                con.execute("DELETE FROM review_bin WHERE id=?", (row["id"],))
                deleted += 1
        self.send_json({"ok": True, "deleted": deleted})

    def serve_web_asset(self, name: str):
        if not WEB_ASSET_NAME_RE.fullmatch(name):
            return self.send_error(404)
        path = (WEB_ROOT / name).resolve()
        try:
            path.relative_to(WEB_ROOT.resolve())
        except ValueError:
            return self.send_error(403)
        if not path.is_file():
            return self.send_error(404)
        content_type = WEB_ASSET_CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self.send_bytes(path.read_bytes(), content_type, cache="public, max-age=31536000, immutable")

    def serve_logo(self):
        path = Path(__file__).parent.parent / "assets" / "lensledger-logo.png"
        if not path.is_file(): return self.send_error(404)
        self.send_bytes(path.read_bytes(), "image/png", cache="private, max-age=86400")

    def serve_favicon(self):
        path = Path(__file__).parent.parent / "assets" / "lensledger-favicon.png"
        if not path.is_file(): return self.send_error(404)
        self.send_bytes(path.read_bytes(), "image/png", cache="private, max-age=86400")

    def serve_world_map(self):
        path = Path(__file__).parent.parent / "assets" / "world-map.svg"
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
        if path.suffix.lower() in (".heic", ".heif"):
            try:
                with Image.open(path) as image:
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=90)
            except Exception:
                return self.send_error(500)
            data = buffer.getvalue()
            self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "private, max-age=3600"); self.end_headers()
            self.wfile.write(data)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(path.stat().st_size)); self.send_header("Cache-Control", "private, max-age=3600"); self.end_headers()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024): self.wfile.write(chunk)

    def log_message(self, format, *args):
        pass


class LensLedgerHTTPServer(ThreadingHTTPServer):
    """Silences the stock traceback dump for client disconnects mid-response.

    A browser tab navigating away or refreshing while a long-poll status
    request is in flight aborts the socket -- that is normal client
    behavior, not a server bug, so it should not spam the console."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_NAME} {APP_VERSION}")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--port", type=int, default=5309)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--restarted", action="store_true")
    args = parser.parse_args()
    root = (args.root or load_library_state()).resolve()
    database = (args.db or library_db_path(root)).resolve()
    associate_db_path(root, database)
    SearchHandler.current_library = (root, database); SearchHandler.csrf_token = secrets.token_urlsafe(32)
    server = LensLedgerHTTPServer(("localhost", args.port), SearchHandler); url = f"http://localhost:{args.port}/"
    def watcher_scan():
        with SearchHandler.library_lock:
            if SearchHandler.library_job.get("state") == "scanning":
                return
        try:
            scan_library(SearchHandler.library_root, SearchHandler.db_path, quiet=True)
        except Exception:
            pass
    settings = load_settings()
    watch_cfg = settings.get("watch", {})
    watcher = FolderWatcher(
        interval_minutes=int(watch_cfg.get("interval_minutes", 30)),
        scan_fn=watcher_scan,
    )
    SearchHandler.folder_watcher = watcher
    if watch_cfg.get("enabled"):
        watcher.start()
    ingest_cfg = settings.get("ingest", {})
    def _after_import(count):
        console_log(f"Auto-import: triggering scan for {count} new file(s)")
        watcher.trigger_soon(delay=5)
    pipeline = IngestPipeline(
        source_folder=str(ingest_cfg.get("source_folder", "")),
        destination_folder=str(ingest_cfg.get("destination_folder", "")),
        rules=ingest_cfg.get("rules", []),
        default_template=str(ingest_cfg.get("default_template", "")),
        on_batch_complete=_after_import,
    )
    SearchHandler.ingest_pipeline = pipeline
    if ingest_cfg.get("enabled"):
        pipeline.start()
    print("\n" + "=" * 62, flush=True)
    if args.restarted:
        print(f"  {APP_NAME} v{APP_VERSION} — new version loaded.\n  {APP_TAGLINE}\n", flush=True)
    else:
        print(f"  {APP_NAME} v{APP_VERSION}\n  {APP_TAGLINE}\n", flush=True)
    print(f"  Local library: {url}\n  Press Ctrl+C in this window to stop LensLedger.", flush=True)
    print("=" * 62 + "\n", flush=True)
    console_log(f"Library: {root}")
    console_log(f"Database: {database}")
    if watch_cfg.get("enabled"):
        console_log(f"Folder watcher: enabled (every {int(watch_cfg.get('interval_minutes', 30))} min)")
    if ingest_cfg.get("enabled"):
        console_log(f"Auto-import: enabled (every {int(ingest_cfg.get('interval_minutes', 10))} min)")
    if not args.restarted and not args.no_open:
        browser_timer = threading.Timer(1.0, webbrowser.open, args=(url,))
        browser_timer.daemon = True
        browser_timer.start()
    try: server.serve_forever()
    except KeyboardInterrupt: print(f"\n{APP_NAME} is stopping...", flush=True)
    finally:
        watcher.stop()
        pipeline.stop()
        server.server_close(); print(f"{APP_NAME} stopped.", flush=True)


if __name__ == "__main__":
    main()
