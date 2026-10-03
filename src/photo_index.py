#!/usr/bin/env python3
"""Incremental indexer for the organized photo library.

The index database is separate from the media. Scan results (tags, people,
OCR text) can optionally be written back to photo XMP/IPTC metadata via
the publish system in photo_search.py, either embedded in the file or as
XMP sidecar files.
"""

from __future__ import annotations

import argparse
import array
import concurrent.futures
import csv
import datetime as dt
import hashlib
import html
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Callable

from PIL import ExifTags, Image, ImageOps

from media_dates import raw_date, video_dates
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from app_paths import libraries_root
from generate_historical_folder_tags import infer_tags
from product import APP_NAME, APP_VERSION


MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif",
    ".mp4", ".mov", ".avi", ".wmv", ".mpg", ".mpeg", ".mkv",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf",
}
RAW_EXTENSIONS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".wmv", ".mpg", ".mpeg", ".mkv"}
IMAGE_EXTENSIONS = MEDIA_EXTENSIONS - VIDEO_EXTENSIONS - RAW_EXTENSIONS
SCHEMA_VERSION = 23
SQLITE_BUSY_TIMEOUT_MS = 30_000
SKIP_DIRECTORIES = {"!LensLedger", "_FaceData", "_PhotoIndex"}
XMP_SUBJECT_RE = re.compile(
    rb"<dc:subject\b[^>]*(?<!/)>.*?</dc:subject>", re.IGNORECASE | re.DOTALL
)
XMP_LAST_KEYWORD_RE = re.compile(
    rb"<([\w.-]+):LastKeywordXMP\b[^>]*(?<!/)>.*?</\1:LastKeywordXMP>", re.IGNORECASE | re.DOTALL
)
XMP_PERSON_RE = re.compile(
    rb"<([\w.-]+):PersonInImage\b[^>]*(?<!/)>.*?</\1:PersonInImage>", re.IGNORECASE | re.DOTALL
)
EMBEDDED_TAG_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".tif", ".tiff"}
# Raised whenever extract_embedded_tags learns to read another field, so the
# next scan re-reads unchanged files once instead of trusting what an older
# reader stored for them.
EMBEDDED_TAGS_VERSION = 3
RDF_ITEM_RE = re.compile(rb"<rdf:li\b[^>]*>(.*?)</rdf:li>", re.IGNORECASE | re.DOTALL)
DATE_RE = re.compile(r"(?P<year>19\d{2}|20\d{2})[-_](?P<month>\d{2})[-_](?P<day>\d{2})")
# Phones and cameras name files IMG_20240101_123456, PXL_20240101_..., or
# 20240101_123456. The digit guards stop it matching inside a longer number.
COMPACT_DATE_RE = re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})(?P<month>\d{2})(?P<day>\d{2})(?!\d)")
EXIF_DATE_RE = re.compile(r"^\s*(?P<year>\d{4})[:-](?P<month>\d{2})[:-](?P<day>\d{2})")
# Raised whenever capture_date learns another source, so the next scan
# re-reads the date of unchanged files once.
CAPTURE_DATE_VERSION = 2
# Raised whenever the visual hash changes, so unchanged photos are re-hashed once.
VISUAL_HASH_VERSION = 1


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    relative_path TEXT NOT NULL UNIQUE,
    folder TEXT NOT NULL,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    metadata_scanned INTEGER NOT NULL DEFAULT 0,
    location_scanned INTEGER NOT NULL DEFAULT 0,
    face_scanned INTEGER NOT NULL DEFAULT 0,
    face_scan_error TEXT NOT NULL DEFAULT '',
    scan_error TEXT NOT NULL DEFAULT '',
    semantic_error TEXT NOT NULL DEFAULT '',
    in_review_bin INTEGER NOT NULL DEFAULT 0,
    gps_latitude REAL,
    gps_longitude REAL,
    capture_date TEXT,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_filename ON assets(filename);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    confidence REAL,
    PRIMARY KEY (asset_id, tag_id, source)
);

CREATE TABLE IF NOT EXISTS text_data (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    ocr_text TEXT NOT NULL DEFAULT '',
    ocr_scanned INTEGER NOT NULL DEFAULT 0,
    ocr_error TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS folder_tags (
    folder TEXT NOT NULL,
    tag TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (folder, tag)
);

CREATE TABLE IF NOT EXISTS asset_annotations (
    relative_path TEXT PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS asset_tag_exclusions (
    relative_path TEXT NOT NULL,
    tag TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (relative_path, tag)
);

CREATE TABLE IF NOT EXISTS review_bin (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    original_path TEXT NOT NULL,
    original_relative_path TEXT NOT NULL,
    review_path TEXT NOT NULL UNIQUE,
    moved_at TEXT NOT NULL,
    restored_at TEXT
);

CREATE TABLE IF NOT EXISTS metadata_publications (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    relative_path TEXT NOT NULL,
    backup_path TEXT NOT NULL UNIQUE,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    operation TEXT NOT NULL DEFAULT 'full',
    review_action_id INTEGER,
    published_at TEXT NOT NULL,
    completed_at TEXT,
    restored_at TEXT
);

CREATE TABLE IF NOT EXISTS face_embeddings (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_face_id INTEGER NOT NULL,
    asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    relative_path TEXT NOT NULL,
    gender_marker TEXT,
    dimensions INTEGER NOT NULL,
    embedding_f32 BLOB NOT NULL,
    box_left REAL,
    box_top REAL,
    box_right REAL,
    box_bottom REAL,
    localization_similarity REAL,
    localized_at TEXT,
    skipped_at TEXT,
    UNIQUE (source, source_face_id)
);
-- Deleting an asset cascades into these tables; without an index on
-- asset_id every deleted photo scans the whole table.
CREATE INDEX IF NOT EXISTS idx_face_embeddings_asset ON face_embeddings(asset_id);
CREATE INDEX IF NOT EXISTS idx_metadata_publications_asset ON metadata_publications(asset_id);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS person_aliases (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    alias TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE INDEX IF NOT EXISTS idx_person_aliases_person ON person_aliases(person_id);

CREATE TABLE IF NOT EXISTS person_groups (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS person_group_members (
    group_id INTEGER NOT NULL REFERENCES person_groups(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, person_id)
);
CREATE INDEX IF NOT EXISTS idx_person_group_members_person ON person_group_members(person_id);

CREATE TABLE IF NOT EXISTS asset_people (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('suggested','confirmed','rejected')),
    confidence REAL,
    face_id INTEGER REFERENCES face_embeddings(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (asset_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_people_state ON asset_people(asset_id, state);

CREATE TABLE IF NOT EXISTS person_face_profiles (
    person_id INTEGER PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
    dimensions INTEGER NOT NULL,
    centroid_f32 BLOB NOT NULL,
    training_face_ids_json TEXT NOT NULL,
    training_assets INTEGER NOT NULL,
    cohesion REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS semantic_embeddings (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding_f32 BLOB NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_embeddings_model ON semantic_embeddings(model);

CREATE TABLE IF NOT EXISTS people_review_actions (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('confirmed','rejected','corrected')),
    previous_json TEXT NOT NULL,
    corrected_person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    corrected_previous_json TEXT,
    created_at TEXT NOT NULL,
    undone_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_people_review_actions_active ON people_review_actions(id, undone_at);

CREATE TABLE IF NOT EXISTS person_review_deferrals (
    person_id INTEGER PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
    deferred_at TEXT NOT NULL,
    deferred_until TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_duplicate_dismissals (
    person_low INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    person_high INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    dismissed_at TEXT NOT NULL,
    PRIMARY KEY (person_low, person_high)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    scanned INTEGER NOT NULL DEFAULT 0,
    changed INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS library_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    asset_id UNINDEXED,
    path,
    folder,
    tags,
    ocr_text,
    caption,
    tokenize='unicode61 remove_diacritics 2'
);
"""


class LensLedgerConnection(sqlite3.Connection):
    """A SQLite connection whose context manager also releases the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


CONTENT_HASH_BLOCK = 8192


def content_hash(path: Path) -> str:
    """Fast content fingerprint: SHA-256 of the first 8 KB + file size."""
    try:
        size = path.stat().st_size
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read(CONTENT_HASH_BLOCK))
        h.update(size.to_bytes(8, "little"))
        return h.hexdigest()[:24]
    except OSError:
        return ""


def get_library_id(db_path: Path) -> str | None:
    try:
        con = sqlite3.connect(str(db_path), timeout=5)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT value FROM library_metadata WHERE key='library_id'").fetchone()
        con.close()
        return str(row["value"]) if row else None
    except (sqlite3.Error, OSError):
        return None


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(
        db_path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
        factory=LensLedgerConnection,
    )
    try:
        return _configure_connection(con)
    except Exception:
        con.close()
        raise


_broken_stream_cleared = False

def _configure_connection(con: sqlite3.Connection) -> sqlite3.Connection:
    global _broken_stream_cleared
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    con.executescript(SCHEMA)
    columns = {row[1] for row in con.execute("PRAGMA table_info(assets)")}
    if "metadata_scanned" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN metadata_scanned INTEGER NOT NULL DEFAULT 0")
    if "in_review_bin" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN in_review_bin INTEGER NOT NULL DEFAULT 0")
    if "location_scanned" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN location_scanned INTEGER NOT NULL DEFAULT 0")
    if "gps_latitude" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN gps_latitude REAL")
    if "gps_longitude" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN gps_longitude REAL")
    if "tags_written" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN tags_written TEXT")
    if "face_scanned" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN face_scanned INTEGER NOT NULL DEFAULT 0")
        con.execute(
            "UPDATE assets SET face_scanned=1 WHERE id IN (SELECT DISTINCT asset_id FROM face_embeddings WHERE asset_id IS NOT NULL)"
        )
    if "face_scan_error" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN face_scan_error TEXT NOT NULL DEFAULT ''")
    if "scan_error" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN scan_error TEXT NOT NULL DEFAULT ''")
    if "semantic_error" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN semantic_error TEXT NOT NULL DEFAULT ''")
    people_columns = {row[1] for row in con.execute("PRAGMA table_info(asset_people)")}
    if "face_id" not in people_columns:
        con.execute("ALTER TABLE asset_people ADD COLUMN face_id INTEGER REFERENCES face_embeddings(id) ON DELETE SET NULL")
    if "published_at" not in people_columns:
        con.execute("ALTER TABLE asset_people ADD COLUMN published_at TEXT")
        con.execute("UPDATE asset_people SET published_at = updated_at WHERE state = 'confirmed'")
    face_columns = {row[1] for row in con.execute("PRAGMA table_info(face_embeddings)")}
    for name in ("box_left", "box_top", "box_right", "box_bottom", "localization_similarity"):
        if name not in face_columns:
            con.execute(f"ALTER TABLE face_embeddings ADD COLUMN {name} REAL")
    if "localized_at" not in face_columns:
        con.execute("ALTER TABLE face_embeddings ADD COLUMN localized_at TEXT")
    if "ignored_at" not in face_columns:
        con.execute("ALTER TABLE face_embeddings ADD COLUMN ignored_at TEXT")
    if "unknown_at" not in face_columns:
        con.execute("ALTER TABLE face_embeddings ADD COLUMN unknown_at TEXT")
    if "person_id" not in face_columns:
        con.execute("ALTER TABLE face_embeddings ADD COLUMN person_id INTEGER REFERENCES people(id) ON DELETE SET NULL")
        con.execute("""UPDATE face_embeddings SET person_id = (
            SELECT ap.person_id FROM asset_people ap
            WHERE ap.face_id = face_embeddings.id AND ap.state IN ('confirmed','suggested')
        ) WHERE EXISTS (
            SELECT 1 FROM asset_people ap
            WHERE ap.face_id = face_embeddings.id AND ap.state IN ('confirmed','suggested')
        )""")
    if "skipped_at" not in face_columns:
        con.execute("ALTER TABLE face_embeddings ADD COLUMN skipped_at TEXT")
    person_columns = {row[1] for row in con.execute("PRAGMA table_info(people)")}
    if "card_asset_id" not in person_columns:
        con.execute("ALTER TABLE people ADD COLUMN card_asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL")
    review_action_columns = {row[1] for row in con.execute("PRAGMA table_info(people_review_actions)")}
    if "face_disposition" not in review_action_columns:
        con.execute("ALTER TABLE people_review_actions ADD COLUMN face_disposition TEXT")
    publication_columns = {row[1] for row in con.execute("PRAGMA table_info(metadata_publications)")}
    if "operation" not in publication_columns:
        con.execute("ALTER TABLE metadata_publications ADD COLUMN operation TEXT NOT NULL DEFAULT 'full'")
    if "review_action_id" not in publication_columns:
        con.execute("ALTER TABLE metadata_publications ADD COLUMN review_action_id INTEGER")
    run_columns = {row[1] for row in con.execute("PRAGMA table_info(runs)")}
    if "cancelled" not in run_columns:
        con.execute("ALTER TABLE runs ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0")
    text_columns = {row[1] for row in con.execute("PRAGMA table_info(text_data)")}
    if "ocr_scanned" not in text_columns:
        con.execute("ALTER TABLE text_data ADD COLUMN ocr_scanned INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE text_data SET ocr_scanned=1 WHERE ocr_text<>''")
    if "ocr_error" not in text_columns:
        con.execute("ALTER TABLE text_data ADD COLUMN ocr_error TEXT NOT NULL DEFAULT ''")
    if "content_hash" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
    if "tags_scanned" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN tags_scanned INTEGER NOT NULL DEFAULT 0")
    if "date_scanned" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN date_scanned INTEGER NOT NULL DEFAULT 0")
    if "visual_hash" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN visual_hash TEXT NOT NULL DEFAULT ''")
    if "visual_scanned" not in columns:
        con.execute("ALTER TABLE assets ADD COLUMN visual_scanned INTEGER NOT NULL DEFAULT 0")
    publication_columns = {row[1] for row in con.execute("PRAGMA table_info(metadata_publications)")}
    if "completed_at" not in publication_columns:
        con.execute("ALTER TABLE metadata_publications ADD COLUMN completed_at TEXT")
        # Rows written before this column existed all finished, or they would
        # have been rolled back; marking them complete avoids reporting every
        # historical write as interrupted.
        con.execute("UPDATE metadata_publications SET completed_at=published_at")
    if int(con.execute("PRAGMA user_version").fetchone()[0]) < 20:
        # Publishing people and single-photo publishes recorded their write
        # only after it had finished and been verified, but left completed_at
        # empty, so every one was later reported as interrupted. Only
        # "Write all tags" records a write before it happens.
        con.execute(
            "UPDATE metadata_publications SET completed_at=published_at "
            "WHERE completed_at IS NULL AND operation <> 'write_tags'"
        )
    if not con.execute("SELECT 1 FROM library_metadata WHERE key='library_id'").fetchone():
        import uuid
        con.execute("INSERT OR IGNORE INTO library_metadata(key,value) VALUES ('library_id',?)", (str(uuid.uuid4()),))
    if not _broken_stream_cleared:
        con.execute(
            "UPDATE assets SET face_scanned=0, face_scan_error='' "
            "WHERE face_scan_error LIKE '%broken data stream%'"
        )
        con.execute(
            "UPDATE assets SET semantic_error='' "
            "WHERE semantic_error LIKE '%broken data stream%'"
        )
        skip = unsupported_image_extensions()
        if skip:
            placeholders = ",".join("?" * len(skip))
            con.execute(
                f"UPDATE assets SET semantic_error='' "
                f"WHERE extension IN ({placeholders}) AND semantic_error LIKE '%cannot identify image file%'",
                tuple(sorted(skip)),
            )
            con.execute(
                f"UPDATE assets SET face_scanned=0, face_scan_error='' "
                f"WHERE extension IN ({placeholders}) AND face_scan_error LIKE '%cannot identify image file%'",
                tuple(sorted(skip)),
            )
        _broken_stream_cleared = True
    con.commit()
    if int(con.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        con.commit()
    return con


def media_type(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in RAW_EXTENSIONS:
        return "raw"
    return "image"


def unsupported_image_extensions() -> set[str]:
    """Image extensions in MEDIA_EXTENSIONS that PIL cannot currently decode."""
    from PIL import Image
    supported = {ext.lower() for ext in Image.registered_extensions()}
    return IMAGE_EXTENSIONS - supported


def _actual_allocation_size(path: Path) -> int | None:
    """Bytes actually resident on disk for `path`, without hydrating a cloud file.

    Opened with FILE_FLAG_OPEN_REPARSE_POINT so a cloud-file reparse point is
    opened as itself rather than followed -- this never triggers a download.
    Returns None if the query is unsupported (non-Windows, or the call failed).
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _FILE_STANDARD_INFO(ctypes.Structure):
        _fields_ = [
            ("AllocationSize", ctypes.c_longlong),
            ("EndOfFile", ctypes.c_longlong),
            ("NumberOfLinks", wintypes.DWORD),
            ("DeletePending", wintypes.BOOLEAN),
            ("Directory", wintypes.BOOLEAN),
        ]

    FILE_STANDARD_INFO_CLASS = 1
    GENERIC_READ = 0x80000000
    FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateFileW(
        str(path), GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if not handle or handle == INVALID_HANDLE_VALUE:
        return None
    try:
        info = _FILE_STANDARD_INFO()
        if not kernel32.GetFileInformationByHandleEx(
            handle, FILE_STANDARD_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
        ):
            return None
        return int(info.AllocationSize)
    finally:
        kernel32.CloseHandle(handle)


def is_cloud_placeholder(stat_result, path: Path | None = None) -> bool:
    """Return True for an unhydrated Windows cloud file placeholder.

    OneDrive and Dropbox both set RECALL_ON_*/OFFLINE attribute bits on files
    under their control even when fully downloaded -- those bits alone mean
    "cloud-managed," not "absent." The only reliable signal that a file is
    genuinely not downloaded is that its actual on-disk allocation is smaller
    than its logical size (a real placeholder is a near-zero-allocation sparse
    file); a hydrated file's allocation is always >= its logical size, rounded
    up to a cluster. When `path` is given, the attribute bits are only used to
    decide whether this check is worth running at all -- they never decide the
    answer by themselves.
    """
    attributes = getattr(stat_result, "st_file_attributes", 0)
    offline = 0x00001000
    recall_on_open = 0x00040000
    recall_on_data_access = 0x00400000
    flagged = bool(attributes & (offline | recall_on_open | recall_on_data_access))
    if not flagged:
        return False
    if path is None:
        return True
    allocation = _actual_allocation_size(path)
    if allocation is None:
        return True
    return allocation < stat_result.st_size


def _date_from_match(match) -> str | None:
    if not match:
        return None
    try:
        value = dt.date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None
    # Cameras with an unset clock write 0000:00:00 or 1970/1980 defaults;
    # neither is a date anyone took the photo on.
    if value.year < 1900 or value > dt.date.today() + dt.timedelta(days=2):
        return None
    return value.isoformat()


def capture_date_from_path(path: Path) -> str | None:
    for name in (path.name, path.parent.name):
        found = _date_from_match(DATE_RE.search(name)) or _date_from_match(COMPACT_DATE_RE.search(name))
        if found:
            return found
    return None


def extract_exif_dates(path: Path) -> tuple[str | None, str | None]:
    """(taken, modified) from EXIF, without changing or hydrating the file.

    "Taken" is DateTimeOriginal, else DateTimeDigitized. "Modified" is the
    main-image DateTime, which editors rewrite on save, so it only beats
    nothing at all.
    """
    if media_type(path) != "image":
        return None, None
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            details = exif.get_ifd(ExifTags.IFD.Exif)
            taken = None
            for tag in (0x9003, 0x9004):  # DateTimeOriginal, DateTimeDigitized
                taken = _date_from_match(EXIF_DATE_RE.match(str(details.get(tag) or "")))
                if taken:
                    break
            modified = _date_from_match(EXIF_DATE_RE.match(str(exif.get(0x0132) or "")))
            return taken, modified
    except (KeyError, OSError, TypeError, ValueError, SyntaxError):
        return None, None


def visual_hash(path: Path) -> str:
    """64-bit difference hash of how the picture looks, as 16 hex digits.

    Scales the photo to 9x8 greyscale and records, for each pixel, whether
    the one to its right is darker. A resized, recompressed or lightly
    edited copy keeps nearly all of those bits; a different photo does not.
    Returns "" for anything that is not a decodable still image (videos,
    RAW files, damaged files), which simply takes no part in similar-photo
    matching.
    """
    if media_type(path) != "image":
        return ""
    try:
        with Image.open(path) as image:
            if image.format == "JPEG":
                # Lets the decoder return a small version directly, which is
                # most of the cost of hashing a large JPEG.
                image.draft("L", (128, 128))
            image = ImageOps.exif_transpose(image)
            small = image.convert("L").resize((9, 8), Image.LANCZOS)
            pixels = list(small.getdata())
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return ""
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | (1 if pixels[row * 9 + column] > pixels[row * 9 + column + 1] else 0)
    return f"{bits:016x}"


def capture_date_for(path: Path) -> str | None:
    """When the photo was taken: EXIF first, then a date in the file or folder name.

    Reading the name alone left every camera and phone file (IMG_1234.jpg)
    with no date, so date filtering, day stepping and newest-first did
    nothing for most libraries. Videos and RAW files read their own header
    the same way; a video's UTC header date ranks below the file name, since
    it can be a day away from the day it was shot.
    """
    kind = media_type(path)
    if kind == "video":
        local, utc = video_dates(path)
        return local or capture_date_from_path(path) or utc
    if kind == "raw":
        return raw_date(path) or capture_date_from_path(path)
    taken, modified = extract_exif_dates(path)
    return taken or capture_date_from_path(path) or modified


def _unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if value and value.casefold() not in seen:
            result.append(value); seen.add(value.casefold())
    return result


def _xmp_items(pattern: re.Pattern, data: bytes) -> list[str]:
    block = pattern.search(data)
    if not block:
        return []
    return [html.unescape(item.decode("utf-8", errors="replace"))
            for item in RDF_ITEM_RE.findall(block.group(0))]


def _iim_keywords(iim: bytes) -> list[str]:
    raw: list[bytes] = []
    utf8 = False
    pos = 0
    while pos + 5 <= len(iim) and iim[pos] == 0x1C:
        record, dataset = iim[pos + 1], iim[pos + 2]
        size = int.from_bytes(iim[pos + 3:pos + 5], "big")
        pos += 5
        if size & 0x8000:
            width = size & 0x7FFF
            size = int.from_bytes(iim[pos:pos + width], "big")
            pos += width
        value = iim[pos:pos + size]
        pos += size
        if record == 1 and dataset == 90 and value == b"\x1b%G":
            utf8 = True
        elif record == 2 and dataset == 25:
            raw.append(value)
    keywords = []
    for value in raw:
        try:
            keywords.append(value.decode("utf-8"))
        except UnicodeDecodeError:
            # IPTC without the UTF-8 marker is conventionally Latin-1/cp1252.
            keywords.append(value.decode("utf-8" if utf8 else "cp1252", errors="replace"))
    return keywords


def _jpeg_iptc_keywords(data: bytes) -> list[str]:
    resources = b""
    pos = 2
    while pos + 4 <= len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0xD9, 0xDA):
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        segment = data[pos + 4:pos + 2 + length]
        if marker == 0xED and segment.startswith(b"Photoshop 3.0\x00"):
            resources += segment[14:]
        pos += 2 + length
    keywords: list[str] = []
    pos = 0
    while pos + 12 <= len(resources) and resources[pos:pos + 4] == b"8BIM":
        resource_id = int.from_bytes(resources[pos + 4:pos + 6], "big")
        name_length = resources[pos + 6] + 1
        name_length += name_length % 2
        body_start = pos + 6 + name_length + 4
        if body_start > len(resources):
            break
        size = int.from_bytes(resources[body_start - 4:body_start], "big")
        if resource_id == 0x0404:
            keywords.extend(_iim_keywords(resources[body_start:body_start + size]))
        pos = body_start + size + size % 2
    return keywords


def extract_embedded_tags(path: Path) -> tuple[list[str], list[str]]:
    """Return (keywords, people) stored inside a photo.

    Keywords are merged from every field LensLedger itself writes -- XMP
    dc:subject, IPTC Keywords and Microsoft's LastKeywordXMP -- and people
    from IPTC PersonInImage. Reading only dc:subject meant a later "Write all
    tags" cleared whatever other software had put in the rest.
    """
    suffix = path.suffix.lower()
    if suffix not in EMBEDDED_TAG_EXTENSIONS:
        return [], []
    try:
        with path.open("rb") as stream:
            if suffix in {".jpg", ".jpeg"}:
                # XMP is near the beginning of a JPEG. Some older Microsoft-written
                # files place it in a nonstandard application segment, so inspect
                # the complete header region rather than assuming it is always APP1.
                data = stream.read(1024 * 1024)
                if not data.startswith(b"\xff\xd8"):
                    return [], []
                iptc = _jpeg_iptc_keywords(data)
            else:
                # HEIC, PNG, WebP and TIFF keep XMP in a block that can sit anywhere,
                # and exiftool appends rewritten metadata at the end.
                data = stream.read()
                start = data.find(b"<x:xmpmeta")
                end = data.find(b"</x:xmpmeta>", start) if start >= 0 else -1
                data = data[start:end + 12] if end >= 0 else b""
                iptc = []
    except OSError:
        return [], []
    keywords = _unique([*_xmp_items(XMP_SUBJECT_RE, data), *iptc,
                        *_xmp_items(XMP_LAST_KEYWORD_RE, data)])
    return keywords, _unique(_xmp_items(XMP_PERSON_RE, data))


def store_embedded_tags(con: sqlite3.Connection, asset_id: int, keywords: list[str],
                        people: list[str]) -> None:
    set_source_tags(con, asset_id, "embedded_xmp", keywords)
    set_source_tags(con, asset_id, "embedded_people", people)


def refresh_embedded_tags(con: sqlite3.Connection, asset_id: int, path: Path) -> None:
    store_embedded_tags(con, asset_id, *extract_embedded_tags(path))
    con.execute("UPDATE assets SET tags_scanned=? WHERE id=?", (EMBEDDED_TAGS_VERSION, asset_id))


def _gps_decimal(values, reference: str) -> float | None:
    try:
        degrees, minutes, seconds = (float(value) for value in values)
        coordinate = degrees + minutes / 60 + seconds / 3600
        return -coordinate if reference.upper() in {"S", "W"} else coordinate
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def extract_gps_coordinates(path: Path) -> tuple[float | None, float | None]:
    """Read embedded EXIF GPS coordinates without changing or hydrating media."""
    if media_type(path) != "image":
        return None, None
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
            latitude = _gps_decimal(gps.get(2), str(gps.get(1, "")))
            longitude = _gps_decimal(gps.get(4), str(gps.get(3, "")))
            if latitude is None or longitude is None:
                return None, None
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                return None, None
            return latitude, longitude
    except (KeyError, OSError, TypeError, ValueError):
        return None, None


def iter_media(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_DIRECTORIES for part in rel.parts):
            continue
        yield path, rel


def ensure_tag(con: sqlite3.Connection, name: str) -> int:
    con.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
    return int(con.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()[0])


def set_source_tags(con: sqlite3.Connection, asset_id: int, source: str, names: list[str]) -> None:
    con.execute("DELETE FROM asset_tags WHERE asset_id = ? AND source = ?", (asset_id, source))
    for name in names:
        tag_id = ensure_tag(con, name)
        con.execute(
            "INSERT OR IGNORE INTO asset_tags(asset_id, tag_id, source) VALUES (?, ?, ?)",
            (asset_id, tag_id, source),
        )


def sync_person_tags(con: sqlite3.Connection, asset_id: int) -> None:
    names = [row[0] for row in con.execute(
        """SELECT p.name FROM asset_people ap JOIN people p ON p.id=ap.person_id
           WHERE ap.asset_id=? AND ap.state='confirmed' ORDER BY p.name""", (asset_id,)
    )]
    set_source_tags(con, asset_id, "person", names)


def apply_asset_annotation(con: sqlite3.Connection, asset_id: int, relative_path: str) -> None:
    row = con.execute(
        "SELECT subject, tags FROM asset_annotations WHERE relative_path = ?", (relative_path,)
    ).fetchone()
    subject = [row["subject"]] if row and row["subject"].strip() else []
    extra = [part.strip() for part in row["tags"].split(";") if part.strip()] if row else []
    set_source_tags(con, asset_id, "subject", subject)
    set_source_tags(con, asset_id, "asset_rule", extra)


def rebuild_search_row(con: sqlite3.Connection, asset_id: int) -> None:
    row = con.execute(
        """
        SELECT a.relative_path, a.folder,
               COALESCE((
                   SELECT GROUP_CONCAT(name, ',') FROM (
                       SELECT DISTINCT t.name
                       FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                       WHERE at.asset_id=a.id AND NOT EXISTS (
                           SELECT 1 FROM asset_tag_exclusions e
                           WHERE e.relative_path=a.relative_path AND e.tag=t.name
                       )
                       ORDER BY t.name
                   )
               ), '') AS tags,
               COALESCE(x.ocr_text, '') AS ocr_text,
               COALESCE(x.caption, '') AS caption
        FROM assets a
        LEFT JOIN text_data x ON x.asset_id = a.id
        WHERE a.id = ?
        """,
        (asset_id,),
    ).fetchone()
    con.execute("DELETE FROM search_fts WHERE asset_id = ?", (asset_id,))
    if row:
        con.execute(
            "INSERT INTO search_fts(asset_id, path, folder, tags, ocr_text, caption) VALUES (?, ?, ?, ?, ?, ?)",
            (asset_id, row["relative_path"], row["folder"], row["tags"], row["ocr_text"], row["caption"]),
        )


def scan_library(
    root: Path,
    db_path: Path,
    progress: Callable[[dict[str, int | bool]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    quiet: bool = False,
) -> int:
    root = root.resolve()
    con = connect(db_path)
    started = utc_now()
    run_id = con.execute("INSERT INTO runs(started_at) VALUES (?)", (started,)).lastrowid
    known = {row["relative_path"]: row for row in con.execute(
        "SELECT id, relative_path, size_bytes, mtime_ns, metadata_scanned, "
        "location_scanned, tags_scanned, date_scanned, visual_scanned, in_review_bin FROM assets"
    )}
    seen: set[str] = set()
    counts: dict[str, int | bool | list] = {
        "scanned": 0, "changed": 0, "unchanged": 0, "removed": 0,
        "errors": 0, "placeholders": 0, "cancelled": False,
        "total_estimate": len(known),
        "error_details": [],
    }

    def report() -> None:
        if progress:
            progress(dict(counts))

    report()

    for path, rel in iter_media(root):
        if should_cancel and should_cancel():
            counts["cancelled"] = True
            break
        counts["scanned"] += 1
        rel_text = rel.as_posix()
        seen.add(rel_text)
        try:
            stat = path.stat()
            old = known.get(rel_text)
            placeholder = is_cloud_placeholder(stat, path)
            if placeholder:
                counts["placeholders"] += 1
            if (old and old["size_bytes"] == stat.st_size and old["mtime_ns"] == stat.st_mtime_ns
                    and (old["metadata_scanned"] or placeholder)
                    and (old["location_scanned"] or placeholder)):
                counts["unchanged"] += 1
                if not placeholder and old["date_scanned"] < CAPTURE_DATE_VERSION:
                    con.execute(
                        "UPDATE assets SET capture_date=?, date_scanned=? WHERE id=?",
                        (capture_date_for(path), CAPTURE_DATE_VERSION, int(old["id"])),
                    )
                    if counts["unchanged"] % 500 == 0:
                        con.commit()
                if not placeholder and old["visual_scanned"] < VISUAL_HASH_VERSION:
                    con.execute(
                        "UPDATE assets SET visual_hash=?, visual_scanned=? WHERE id=?",
                        (visual_hash(path), VISUAL_HASH_VERSION, int(old["id"])),
                    )
                    if counts["unchanged"] % 500 == 0:
                        con.commit()
                if (not placeholder and old["tags_scanned"] < EMBEDDED_TAGS_VERSION
                        and path.suffix.lower() in EMBEDDED_TAG_EXTENSIONS):
                    refresh_embedded_tags(con, int(old["id"]), path)
                    rebuild_search_row(con, int(old["id"]))
                    if counts["unchanged"] % 500 == 0:
                        con.commit()
                continue
            folder = rel.parent.as_posix()
            latitude, longitude = (None, None) if placeholder else extract_gps_coordinates(path)
            file_hash = "" if placeholder else content_hash(path)
            con.execute(
                """
                INSERT INTO assets(path, relative_path, folder, filename, extension, media_type,
                                   size_bytes, mtime_ns, metadata_scanned, location_scanned,
                                   gps_latitude, gps_longitude, capture_date, date_scanned,
                                   visual_hash, visual_scanned, content_hash, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    path=excluded.path, folder=excluded.folder, filename=excluded.filename,
                    extension=excluded.extension, media_type=excluded.media_type,
                    size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns,
                    in_review_bin=0,
                    metadata_scanned=excluded.metadata_scanned,
                    location_scanned=excluded.location_scanned,
                    gps_latitude=excluded.gps_latitude,
                    gps_longitude=excluded.gps_longitude,
                    capture_date=excluded.capture_date,
                    date_scanned=excluded.date_scanned,
                    visual_hash=excluded.visual_hash, visual_scanned=excluded.visual_scanned,
                    content_hash=excluded.content_hash, indexed_at=excluded.indexed_at,
                    scan_error=''
                """,
                (str(path), rel_text, folder, path.name, path.suffix.lower(), media_type(path),
                 stat.st_size, stat.st_mtime_ns, 0 if placeholder else 1, 0 if placeholder else 1,
                 latitude, longitude,
                 capture_date_from_path(path) if placeholder else capture_date_for(path),
                 0 if placeholder else CAPTURE_DATE_VERSION,
                 "" if placeholder else visual_hash(path), 0 if placeholder else VISUAL_HASH_VERSION,
                 file_hash, utc_now()),
            )
            asset_id = int(con.execute("SELECT id FROM assets WHERE relative_path = ?", (rel_text,)).fetchone()[0])
            if not placeholder:
                refresh_embedded_tags(con, asset_id, path)
            folder_names = [r[0] for r in con.execute("SELECT tag FROM folder_tags WHERE folder = ?", (folder,))]
            if not folder_names:
                inferred = infer_tags(folder)
                if inferred:
                    for tag in inferred:
                        con.execute("INSERT OR IGNORE INTO folder_tags(folder, tag) VALUES (?, ?)", (folder, tag))
                    folder_names = inferred
            set_source_tags(con, asset_id, "folder_rule", folder_names)
            apply_asset_annotation(con, asset_id, rel_text)
            con.execute("INSERT OR IGNORE INTO text_data(asset_id) VALUES (?)", (asset_id,))
            rebuild_search_row(con, asset_id)
            counts["changed"] += 1
            if counts["changed"] % 500 == 0:
                con.commit()
        except Exception as exc:  # keep indexing the rest of the library
            counts["errors"] += 1
            error_text = str(exc)[:1000]
            counts["error_details"].append({"path": rel_text, "error": error_text})
            try:
                con.execute(
                    "UPDATE assets SET scan_error=? WHERE relative_path=?",
                    (error_text, rel_text),
                )
            except Exception:
                pass
            print(f"ERROR\t{path}\t{exc}", file=sys.stderr)
        if int(counts["scanned"]) % 100 == 0:
            report()

    if not counts["cancelled"]:
        missing = {rel for rel, row in known.items() if not row["in_review_bin"]} - seen
        if missing:
            orphan_hashes: dict[str, list[str]] = {}
            for rel_text in missing:
                row = con.execute(
                    "SELECT content_hash FROM assets WHERE relative_path=?", (rel_text,)
                ).fetchone()
                h = row["content_hash"] if row else ""
                if h:
                    orphan_hashes.setdefault(h, []).append(rel_text)
            new_hashes: dict[str, str] = {}
            for rel_text in seen - set(known.keys()):
                row = con.execute(
                    "SELECT content_hash FROM assets WHERE relative_path=?", (rel_text,)
                ).fetchone()
                h = row["content_hash"] if row else ""
                if h and h not in new_hashes:
                    new_hashes[h] = rel_text
            pairs = [(orphans[0], new_hashes[h]) for h, orphans in orphan_hashes.items() if h in new_hashes]
            # The fingerprint covers the start of the file, where embedded
            # tags live, and older versions did not refresh it after writing
            # tags -- so a moved photo can carry a stale one. A rename or move
            # keeps the size and the modification time to the nanosecond, so
            # a pair that is unique on both sides is the same photo.
            matched_old = {old for old, _new in pairs}
            matched_new = {new for _old, new in pairs}
            by_stat_old: dict[tuple, list[str]] = {}
            for rel_text in missing - matched_old:
                row = known[rel_text]
                by_stat_old.setdefault((row["size_bytes"], row["mtime_ns"]), []).append(rel_text)
            by_stat_new: dict[tuple, list[str]] = {}
            for rel_text in seen - set(known.keys()) - matched_new:
                row = con.execute(
                    "SELECT size_bytes, mtime_ns FROM assets WHERE relative_path=?", (rel_text,)
                ).fetchone()
                if row:
                    by_stat_new.setdefault((row["size_bytes"], row["mtime_ns"]), []).append(rel_text)
            for key, olds in by_stat_old.items():
                news = by_stat_new.get(key, [])
                if len(olds) == 1 and len(news) == 1:
                    pairs.append((olds[0], news[0]))
            remapped = 0
            for old_rel, new_rel in pairs:
                old_id = int(known[old_rel]["id"])
                new_row = con.execute(
                    "SELECT id, content_hash FROM assets WHERE relative_path=?", (new_rel,)
                ).fetchone()
                if new_row:
                    con.execute("UPDATE assets SET content_hash=? WHERE id=?", (new_row["content_hash"], old_id))
                    new_id = int(new_row["id"])
                    con.execute("DELETE FROM search_fts WHERE asset_id=?", (new_id,))
                    con.execute("DELETE FROM assets WHERE id=?", (new_id,))
                new_path = str(root / new_rel)
                new_folder = str(Path(new_rel).parent.as_posix())
                new_filename = Path(new_rel).name
                con.execute(
                    """UPDATE assets SET path=?, relative_path=?, folder=?, filename=?, extension=?,
                       scan_error='', semantic_error='' WHERE id=?""",
                    (new_path, new_rel, new_folder, new_filename, Path(new_rel).suffix.lower(), old_id),
                )
                # These are keyed by path, not by photo, so they would
                # otherwise stay behind at the old path: the subject would
                # be cleared, removed tags would return, and the last write
                # could no longer be restored.
                for table in ("asset_annotations", "asset_tag_exclusions"):
                    con.execute(f"UPDATE OR IGNORE {table} SET relative_path=? WHERE relative_path=?",
                                (new_rel, old_rel))
                con.execute("UPDATE metadata_publications SET relative_path=? WHERE relative_path=?",
                            (new_rel, old_rel))
                apply_asset_annotation(con, old_id, new_rel)
                folder_names = [r[0] for r in con.execute(
                    "SELECT tag FROM folder_tags WHERE folder=?", (new_folder,)
                )]
                if not folder_names:
                    inferred = infer_tags(new_folder)
                    if inferred:
                        for tag in inferred:
                            con.execute("INSERT OR IGNORE INTO folder_tags(folder,tag) VALUES(?,?)", (new_folder, tag))
                        folder_names = inferred
                set_source_tags(con, old_id, "folder_rule", folder_names)
                rebuild_search_row(con, old_id)
                missing.discard(old_rel)
                remapped += 1
            if remapped:
                counts["changed"] += remapped
        counts["removing_total"] = len(missing)
        report()
        for rel_text in missing:
            asset_id = int(known[rel_text]["id"])
            con.execute("DELETE FROM search_fts WHERE asset_id = ?", (asset_id,))
            con.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
            counts["removed"] += 1
            if int(counts["removed"]) % 100 == 0:
                report()

    con.execute(
        """UPDATE runs SET finished_at=?, scanned=?, changed=?, unchanged=?, removed=?, errors=?, cancelled=? WHERE id=?""",
        (utc_now(), counts["scanned"], counts["changed"], counts["unchanged"], counts["removed"],
         counts["errors"], int(bool(counts["cancelled"])), run_id),
    )
    con.commit()
    con.close()
    report()
    if not quiet:
        print("\n".join(f"{key}: {value}" for key, value in counts.items()))
    if counts["cancelled"]:
        return 3
    return 0 if counts["errors"] == 0 else 2


def pending_scan_counts(db_path: Path) -> dict[str, int]:
    """Return counts of photos still needing each scan type."""
    con = connect(db_path)
    try:
        ocr = int(con.execute(
            """SELECT COUNT(*) FROM text_data x JOIN assets a ON a.id=x.asset_id
               WHERE a.media_type='image' AND a.metadata_scanned=1 AND a.in_review_bin=0
                 AND x.ocr_scanned=0"""
        ).fetchone()[0])
        face = int(con.execute(
            "SELECT COUNT(*) FROM assets WHERE face_scanned=0 AND metadata_scanned=1 AND in_review_bin=0"
        ).fetchone()[0])
        semantic = int(con.execute(
            """SELECT COUNT(*) FROM assets a
               WHERE a.metadata_scanned=1 AND a.in_review_bin=0
                 AND NOT EXISTS (SELECT 1 FROM semantic_embeddings e WHERE e.asset_id=a.id)"""
        ).fetchone()[0])
    except Exception:
        return {"ocr": 0, "semantic": 0, "face": 0}
    finally:
        con.close()
    return {"ocr": ocr, "semantic": semantic, "face": face}


def import_folder_tags(db_path: Path, csv_path: Path) -> int:
    con = connect(db_path)
    imported = 0
    affected_folders = {row[0] for row in con.execute("SELECT DISTINCT folder FROM folder_tags")}
    con.execute("DELETE FROM folder_tags")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            folder = row["folder"].strip().replace("\\", "/")
            affected_folders.add(folder)
            for tag in (part.strip() for part in row["tags"].split(";")):
                if tag:
                    con.execute("INSERT OR IGNORE INTO folder_tags(folder, tag) VALUES (?, ?)", (folder, tag))
                    imported += 1
    # Apply rules only to folders affected by the old or new rule set.
    if affected_folders:
        placeholders = ",".join("?" for _ in affected_folders)
        assets = con.execute(
            f"SELECT id, folder FROM assets WHERE folder IN ({placeholders})", tuple(affected_folders)
        ).fetchall()
        for asset in assets:
            tags = [r[0] for r in con.execute("SELECT tag FROM folder_tags WHERE folder = ?", (asset["folder"],))]
            set_source_tags(con, int(asset["id"]), "folder_rule", tags)
            rebuild_search_row(con, int(asset["id"]))
    con.commit()
    con.close()
    print(f"folder tags imported: {imported}")
    return 0


def import_asset_annotations(db_path: Path, csv_path: Path) -> int:
    """Import per-photo subjects and tags without changing the media files."""
    con = connect(db_path)
    old_paths = {row[0] for row in con.execute("SELECT relative_path FROM asset_annotations")}
    new_rows: list[tuple[str, str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            relative_path = row["relative_path"].strip().replace("\\", "/")
            if relative_path:
                new_rows.append((relative_path, row.get("subject", "").strip(), row.get("tags", "").strip()))
    new_paths = {row[0] for row in new_rows}
    con.execute("DELETE FROM asset_annotations")
    con.executemany(
        "INSERT INTO asset_annotations(relative_path, subject, tags) VALUES (?, ?, ?)", new_rows
    )
    affected = old_paths | new_paths
    if affected:
        placeholders = ",".join("?" for _ in affected)
        assets = con.execute(
            f"SELECT id, relative_path FROM assets WHERE relative_path IN ({placeholders})", tuple(affected)
        ).fetchall()
        for asset in assets:
            apply_asset_annotation(con, int(asset["id"]), asset["relative_path"])
            rebuild_search_row(con, int(asset["id"]))
    con.commit()
    con.close()
    print(f"asset annotations imported: {len(new_rows)}")
    return 0


def query(db_path: Path, terms: str, limit: int) -> int:
    con = connect(db_path)
    rows = con.execute(
        """
        SELECT a.relative_path, a.capture_date,
               COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags
        FROM search_fts s
        JOIN assets a ON a.id = s.asset_id
        LEFT JOIN asset_tags at ON at.asset_id = a.id
        LEFT JOIN tags t ON t.id = at.tag_id
        WHERE search_fts MATCH ?
        GROUP BY a.id
        ORDER BY a.capture_date DESC, a.relative_path
        LIMIT ?
        """,
        (terms, limit),
    ).fetchall()
    for row in rows:
        print(f"{row['capture_date'] or ''}\t{row['relative_path']}\t{row['tags']}")
    print(f"matches: {len(rows)}")
    con.close()
    return 0


def stats(db_path: Path) -> int:
    con = connect(db_path)
    values = {
        "assets": con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0],
        "images": con.execute("SELECT COUNT(*) FROM assets WHERE media_type='image' AND in_review_bin=0").fetchone()[0],
        "videos": con.execute("SELECT COUNT(*) FROM assets WHERE media_type='video' AND in_review_bin=0").fetchone()[0],
        "raw_files": con.execute("SELECT COUNT(*) FROM assets WHERE media_type='raw' AND in_review_bin=0").fetchone()[0],
        "review_bin": con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=1").fetchone()[0],
        "tags": con.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
        "tag_assignments": con.execute("SELECT COUNT(*) FROM asset_tags").fetchone()[0],
        "ocr_assets": con.execute("SELECT COUNT(*) FROM text_data WHERE ocr_text <> ''").fetchone()[0],
        "face_embeddings": con.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0],
    }
    for key, value in values.items():
        print(f"{key}: {value}")
    con.close()
    return 0


def run_windows_ocr(script: Path, path: str) -> tuple[str, str, str | None]:
    command = [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-InputFile", path,
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )
    except Exception as exc:
        return path, "", str(exc)
    if completed.returncode != 0:
        return path, "", (completed.stderr or f"exit {completed.returncode}").strip()
    return path, " ".join(completed.stdout.split()), None


def ocr_assets(
    db_path: Path,
    since: str | None,
    workers: int,
    progress: Callable[[dict[str, int | bool]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    quiet: bool = False,
    rescan: bool = False,
) -> int:
    """Read text out of photos.

    By default only photos that have never been read are processed. With
    ``rescan`` set, photos that were already read are read again -- the
    deliberate re-run, rather than "new files only".
    """
    con = connect(db_path)
    sql = """
        SELECT a.id, a.path, a.relative_path FROM assets a
        JOIN text_data x ON x.asset_id = a.id
        WHERE a.media_type='image' AND a.metadata_scanned=1
    """
    params: list[str] = []
    if not rescan:
        sql += " AND x.ocr_scanned=0"
    if since:
        # Validation also prevents surprising lexical date comparisons.
        dt.date.fromisoformat(since)
        sql += " AND a.capture_date >= ?"
        params.append(since)
    sql += " ORDER BY a.capture_date, a.relative_path"
    rows = con.execute(sql, params).fetchall()
    script = Path(__file__).with_name("windows_ocr.ps1")
    counts: dict[str, object] = {
        "total": len(rows), "attempted": 0, "with_text": 0,
        "errors": 0, "cancelled": False, "failures": [],
    }
    paths_by_id = {int(row["id"]): row["relative_path"] for row in rows}

    def report() -> None:
        if progress:
            copy = dict(counts)
            copy["failures"] = list(counts["failures"])
            progress(copy)

    worker_count = max(1, workers)
    pending_rows = iter(rows)
    report()
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures: dict[concurrent.futures.Future, tuple[int, str]] = {}

        def submit_next() -> bool:
            try:
                row = next(pending_rows)
            except StopIteration:
                return False
            asset_id, path = int(row["id"]), str(row["path"])
            futures[pool.submit(run_windows_ocr, script, path)] = (asset_id, path)
            return True

        for _ in range(min(worker_count, len(rows))):
            submit_next()
        while futures:
            if should_cancel and should_cancel():
                counts["cancelled"] = True
                for future in futures:
                    future.cancel()
                break
            done, _ = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                asset_id, expected_path = futures.pop(future)
                path, text, error = future.result()
                counts["attempted"] += 1
                if error:
                    counts["errors"] += 1
                    con.execute(
                        "UPDATE text_data SET ocr_error=? WHERE asset_id=?",
                        (error[:1000], asset_id),
                    )
                    if len(counts["failures"]) < 200:
                        counts["failures"].append({
                            "path": paths_by_id.get(asset_id, path or expected_path),
                            "full_path": path or expected_path,
                            "error": error[:1000],
                        })
                    if not quiet:
                        print(f"OCR_ERROR\t{path or expected_path}\t{error}", file=sys.stderr)
                else:
                    if text:
                        counts["with_text"] += 1
                    con.execute(
                        "UPDATE text_data SET ocr_text=?,ocr_scanned=1,ocr_error='' WHERE asset_id=?",
                        (text, asset_id),
                    )
                    rebuild_search_row(con, asset_id)
                if int(counts["attempted"]) % 20 == 0:
                    con.commit()
                    if not quiet:
                        print(f"ocr progress: {counts['attempted']}/{len(rows)}", file=sys.stderr)
                report()
                if not (should_cancel and should_cancel()):
                    submit_next()
            if should_cancel and should_cancel():
                counts["cancelled"] = True
                for future in futures:
                    future.cancel()
                break
    con.commit()
    con.close()
    report()
    if not quiet:
        print(f"ocr attempted: {counts['attempted']}")
        print(f"ocr with text: {counts['with_text']}")
        print(f"ocr errors: {counts['errors']}")
        print(f"ocr cancelled: {counts['cancelled']}")
    if counts["cancelled"]:
        return 3
    return 0 if counts["errors"] == 0 else 2


def import_face_db(db_path: Path, tsv_path: Path) -> int:
    con = connect(db_path)
    imported = remapped = unmatched = errors = 0
    with tsv_path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) == 4:
                    source_id, relative_path, marker, vector_text = fields
                    bounds = (None, None, None, None)
                elif len(fields) == 8:
                    source_id, relative_path, marker, *box_text, vector_text = fields
                    bounds = tuple(float(value) for value in box_text)
                    if not (0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1):
                        raise ValueError("face bounds must be normalized values between 0 and 1")
                else:
                    raise ValueError("expected 4 columns, or 8 columns including normalized face bounds")
                relative_path = relative_path.replace("\\", "/")
                vector = array.array("f", (float(value) for value in vector_text.split(",")))
                asset = con.execute("SELECT id FROM assets WHERE relative_path = ?", (relative_path,)).fetchone()
                asset_id = int(asset[0]) if asset else None
                if asset_id is None:
                    candidates = con.execute(
                        "SELECT id FROM assets WHERE filename = ?", (Path(relative_path).name,)
                    ).fetchall()
                    if len(candidates) == 1:
                        asset_id = int(candidates[0][0])
                        remapped += 1
                if asset_id is None:
                    unmatched += 1
                con.execute(
                    """
                    INSERT INTO face_embeddings(source, source_face_id, asset_id, relative_path,
                                                gender_marker, dimensions, embedding_f32,
                                                box_left,box_top,box_right,box_bottom)
                    VALUES ('recovered_claude_2026', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, source_face_id) DO UPDATE SET
                        asset_id=excluded.asset_id, relative_path=excluded.relative_path,
                        gender_marker=excluded.gender_marker, dimensions=excluded.dimensions,
                        embedding_f32=excluded.embedding_f32,
                        box_left=COALESCE(excluded.box_left,face_embeddings.box_left),
                        box_top=COALESCE(excluded.box_top,face_embeddings.box_top),
                        box_right=COALESCE(excluded.box_right,face_embeddings.box_right),
                        box_bottom=COALESCE(excluded.box_bottom,face_embeddings.box_bottom)
                    """,
                    (int(source_id), asset_id, relative_path, marker, len(vector), vector.tobytes(), *bounds),
                )
                imported += 1
            except Exception as exc:
                errors += 1
                print(f"FACE_IMPORT_ERROR\tline {line_number}\t{exc}", file=sys.stderr)
    con.commit()
    con.close()
    print(f"face embeddings imported: {imported}")
    print(f"face paths uniquely remapped: {remapped}")
    print(f"face paths unmatched: {unmatched}")
    print(f"face import errors: {errors}")
    return 0 if errors == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_NAME} {APP_VERSION}")
    parser.add_argument("--db", type=Path, default=libraries_root() / "default.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="incrementally scan a media library")
    scan.add_argument("root", type=Path)
    seed = sub.add_parser("import-folder-tags", help="import folder tag rules from CSV")
    seed.add_argument("csv", type=Path)
    annotations = sub.add_parser("import-asset-annotations", help="import per-photo subjects and tags from CSV")
    annotations.add_argument("csv", type=Path)
    search = sub.add_parser("query", help="full-text search")
    search.add_argument("terms")
    search.add_argument("--limit", type=int, default=100)
    ocr = sub.add_parser("ocr", help="run local Windows OCR on indexed local images")
    ocr.add_argument("--since", help="only assets captured on or after YYYY-MM-DD")
    ocr.add_argument("--workers", type=int, default=4)
    faces = sub.add_parser("import-face-db", help="preserve recovered TSV face embeddings")
    faces.add_argument("tsv", type=Path)
    sub.add_parser("stats", help="show index statistics")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "scan":
        return scan_library(args.root, args.db)
    if args.command == "import-folder-tags":
        return import_folder_tags(args.db, args.csv)
    if args.command == "import-asset-annotations":
        return import_asset_annotations(args.db, args.csv)
    if args.command == "query":
        return query(args.db, args.terms, args.limit)
    if args.command == "ocr":
        return ocr_assets(args.db, args.since, args.workers)
    if args.command == "import-face-db":
        return import_face_db(args.db, args.tsv)
    if args.command == "stats":
        return stats(args.db)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
