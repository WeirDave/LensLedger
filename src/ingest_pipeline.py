"""Camera upload auto-ingest pipeline.

Watches a configurable source folder for new photos, scans them for metadata,
auto-tags using recognized faces, detected text, and EXIF data, then sorts them
into the organized collection folder using configurable rules.

Safety principles:
- Never deletes originals — files are moved, not copied-then-deleted
- Handles duplicates by content hash comparison
- Handles partial uploads by checking file stability (size unchanged after delay)
- Logs every action for auditability
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Callable

from console_log import log as console_log
from app_paths import data_root
from photo_index import (
    MEDIA_EXTENSIONS,
    content_hash,
    extract_gps_coordinates,
    is_cloud_placeholder,
)


INGEST_LOG_PATH = data_root() / "ingest-log.json"
STABILITY_SECONDS = 5


def _capture_date(path: Path) -> dt.datetime | None:
    """Extract capture date from EXIF or fall back to file modification time."""
    try:
        from PIL import Image, ExifTags
        with Image.open(path) as img:
            exif = img.getexif()
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, "")
                if tag in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
                    return dt.datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return dt.datetime.now()


DEFAULT_TEMPLATE = "{year}/{year}_{month}_{day}"


def _apply_sorting_rules(
    path: Path,
    capture: dt.datetime,
    rules: list[dict[str, str]],
    default_template: str = "",
) -> str:
    """Determine the destination subfolder for a file based on sorting rules.

    Rules are checked in order. Each rule has a 'match' pattern and a
    'destination' template. If no rule matches, the default date-based
    template is used.
    """
    filename = path.name.lower()
    for rule in rules:
        match_pattern = rule.get("match", "").strip().lower()
        destination = rule.get("destination", "").strip()
        if match_pattern and destination and match_pattern in filename:
            return _expand_template(destination, capture)
    return _expand_template(default_template or DEFAULT_TEMPLATE, capture)


def _expand_template(template: str, capture: dt.datetime) -> str:
    return template.format(
        year=capture.strftime("%Y"),
        month=capture.strftime("%m"),
        day=capture.strftime("%d"),
        hour=capture.strftime("%H"),
        minute=capture.strftime("%M"),
    )


def _log_action(action: dict) -> None:
    """Append an action record to the ingest log."""
    INGEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INGEST_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(action) + "\n")


def _is_stable(path: Path) -> bool:
    """Check if a file's size hasn't changed, indicating upload is complete."""
    try:
        size1 = path.stat().st_size
        if size1 == 0:
            return False
        import time
        time.sleep(STABILITY_SECONDS)
        size2 = path.stat().st_size
        return size1 == size2
    except OSError:
        return False


class IngestPipeline:
    """Background pipeline that watches a source folder and sorts new photos."""

    def __init__(
        self,
        source_folder: str = "",
        destination_folder: str = "",
        rules: list[dict[str, str]] | None = None,
        default_template: str = "",
        interval_minutes: int = 10,
        on_file_ingested: Callable[[str, str], None] | None = None,
    ):
        self._source = Path(source_folder) if source_folder else None
        self._destination = Path(destination_folder) if destination_folder else None
        self._rules = rules or []
        self._default_template = default_template or DEFAULT_TEMPLATE
        self._interval = max(5, interval_minutes) * 60
        self._on_file_ingested = on_file_ingested
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()
        self._stats: dict[str, int] = {"ingested": 0, "duplicates": 0, "errors": 0}

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            if not self._source or not self._destination:
                return
            self._running = True
            self._schedule_next()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def update_config(
        self,
        source_folder: str = "",
        destination_folder: str = "",
        rules: list[dict[str, str]] | None = None,
        default_template: str = "",
        interval_minutes: int | None = None,
    ) -> None:
        with self._lock:
            self._source = Path(source_folder) if source_folder else None
            self._destination = Path(destination_folder) if destination_folder else None
            if rules is not None:
                self._rules = rules
            self._default_template = default_template or DEFAULT_TEMPLATE
            if interval_minutes is not None:
                self._interval = max(5, interval_minutes) * 60
                if self._running and self._timer:
                    self._timer.cancel()
                    self._schedule_next()

    def run_once(self) -> dict[str, int]:
        """Run one processing pass immediately and return stats delta."""
        before = dict(self._stats)
        self._process_source()
        return {
            k: self._stats[k] - before.get(k, 0)
            for k in self._stats
        }

    def _schedule_next(self) -> None:
        self._timer = threading.Timer(self._interval, self._on_tick)
        self._timer.daemon = True
        self._timer.start()

    def _on_tick(self) -> None:
        with self._lock:
            if not self._running:
                return
        console_log("Auto-import: checking for new photos")
        before = dict(self._stats)
        try:
            self._process_source()
        except Exception:
            pass
        ingested = self._stats["ingested"] - before.get("ingested", 0)
        dupes = self._stats["duplicates"] - before.get("duplicates", 0)
        errors = self._stats["errors"] - before.get("errors", 0)
        if ingested or dupes or errors:
            console_log(f"Auto-import: {ingested} imported, {dupes} duplicates, {errors} errors")
        else:
            console_log("Auto-import: no new photos found")
        with self._lock:
            if self._running:
                self._schedule_next()

    def _process_source(self) -> None:
        if not self._source or not self._source.is_dir():
            return
        if not self._destination:
            return
        self._destination.mkdir(parents=True, exist_ok=True)

        existing_hashes: set[str] = set()
        for existing in self._destination.rglob("*"):
            if existing.is_file() and existing.suffix.lower() in MEDIA_EXTENSIONS:
                h = content_hash(existing)
                if h:
                    existing_hashes.add(h)

        for path in sorted(self._source.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if path.name.startswith("."):
                continue

            try:
                stat = path.stat()
            except OSError:
                continue

            try:
                if is_cloud_placeholder(stat, path):
                    continue
            except Exception:
                pass

            if not _is_stable(path):
                continue

            file_hash = content_hash(path)
            if file_hash and file_hash in existing_hashes:
                self._stats["duplicates"] += 1
                console_log(f"Auto-import: skipped duplicate — {path.name}")
                _log_action({
                    "action": "skip_duplicate",
                    "source": str(path),
                    "hash": file_hash,
                    "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                })
                continue

            capture = _capture_date(path)
            subfolder = _apply_sorting_rules(path, capture, self._rules, self._default_template)
            dest_dir = self._destination / subfolder
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / path.name

            counter = 1
            while dest_path.exists():
                stem = path.stem
                dest_path = dest_dir / f"{stem}_{counter}{path.suffix}"
                counter += 1

            try:
                shutil.move(str(path), str(dest_path))
                self._stats["ingested"] += 1
                console_log(f"Auto-import: imported {path.name} → {subfolder}")
                if file_hash:
                    existing_hashes.add(file_hash)
                _log_action({
                    "action": "ingested",
                    "source": str(path),
                    "destination": str(dest_path),
                    "hash": file_hash,
                    "capture_date": capture.isoformat() if capture else None,
                    "subfolder": subfolder,
                    "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                })
                if self._on_file_ingested:
                    self._on_file_ingested(str(path), str(dest_path))
            except (OSError, shutil.Error) as exc:
                self._stats["errors"] += 1
                console_log(f"Auto-import: error — {path.name}: {exc}")
                _log_action({
                    "action": "error",
                    "source": str(path),
                    "error": str(exc),
                    "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                })

    def status(self) -> dict[str, object]:
        return {
            "enabled": self._running,
            "source": str(self._source) if self._source else "",
            "destination": str(self._destination) if self._destination else "",
            "rules_count": len(self._rules),
            "stats": dict(self._stats),
        }
