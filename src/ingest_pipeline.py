"""Camera upload auto-ingest pipeline.

Watches a configurable source folder for new photos, scans them for metadata,
auto-tags using recognized faces, detected text, and EXIF data, then sorts them
into the organized collection folder using configurable rules.

Safety principles:
- Never deletes originals — files are moved, not copied-then-deleted
- Handles partial uploads by checking file stability (size unchanged after delay)
- Handles filename collisions by appending a numeric suffix
- Logs every action for auditability
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import threading
from pathlib import Path
from typing import Callable

from console_log import log as console_log
from app_paths import data_root
from photo_index import (
    MEDIA_EXTENSIONS,
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
        on_batch_complete: Callable[[int], None] | None = None,
    ):
        self._source = Path(source_folder) if source_folder else None
        self._destination = Path(destination_folder) if destination_folder else None
        self._rules = rules or []
        self._default_template = default_template or DEFAULT_TEMPLATE
        self._interval = max(5, interval_minutes) * 60
        self._on_file_ingested = on_file_ingested
        self._on_batch_complete = on_batch_complete
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()
        self._stats: dict[str, int] = {"imported": 0, "errors": 0}

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
            self._schedule_next(delay=5)

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

    def _schedule_next(self, delay: int | None = None) -> None:
        self._timer = threading.Timer(delay if delay is not None else self._interval, self._on_tick)
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
        except Exception as exc:
            console_log(f"Auto-import: error during processing — {exc}")
        imported = self._stats["imported"] - before.get("imported", 0)
        errors = self._stats["errors"] - before.get("errors", 0)
        if imported or errors:
            console_log(f"Auto-import: {imported} imported, {errors} errors")
            if imported and self._on_batch_complete:
                self._on_batch_complete(imported)
        else:
            console_log("Auto-import: no new photos found")
        with self._lock:
            if self._running:
                self._schedule_next()

    def _find_existing_folder(self, subfolder: str) -> Path:
        """Match an existing folder with the same date prefix.

        If the template produces '2026/2026_08_22' but a folder named
        '2026/2026_08_22 - Beach Day' already exists, use that instead.
        """
        dest_dir = self._destination / subfolder
        if dest_dir.exists():
            return dest_dir
        parent = dest_dir.parent
        leaf = dest_dir.name
        if parent.is_dir():
            for existing in parent.iterdir():
                if existing.is_dir() and existing.name.startswith(leaf):
                    return existing
        return dest_dir

    def _process_source(self) -> None:
        if not self._source or not self._source.is_dir():
            return
        if not self._destination:
            return
        self._destination.mkdir(parents=True, exist_ok=True)

        candidates = []
        placeholders = 0
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
                    placeholders += 1
                    continue
            except Exception:
                pass
            candidates.append(path)

        if placeholders:
            console_log(f"Auto-import: skipped {placeholders} cloud-only files (not downloaded)")
        if not candidates:
            return

        console_log(f"Auto-import: found {len(candidates)} photos to check")

        for path in candidates:
            if not _is_stable(path):
                console_log(f"Auto-import: skipped {path.name} (still uploading)")
                continue

            capture = _capture_date(path)
            subfolder = _apply_sorting_rules(path, capture, self._rules, self._default_template)
            dest_dir = self._find_existing_folder(subfolder)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / path.name

            if dest_path.exists():
                try:
                    if path.stat().st_size == dest_path.stat().st_size:
                        console_log(f"Auto-import: already exists — {path.name}")
                        continue
                except OSError:
                    pass
                counter = 1
                while dest_path.exists():
                    dest_path = dest_dir / f"{path.stem}_{counter}{path.suffix}"
                    counter += 1

            try:
                shutil.move(str(path), str(dest_path))
                self._stats["imported"] += 1
                actual_subfolder = dest_dir.relative_to(self._destination).as_posix()
                console_log(f"Auto-import: imported {path.name} → {actual_subfolder}")
                _log_action({
                    "action": "imported",
                    "source": str(path),
                    "destination": str(dest_path),
                    "capture_date": capture.isoformat() if capture else None,
                    "subfolder": actual_subfolder,
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
