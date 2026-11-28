"""The folders Auto-import has filled, and naming them.

Auto-import makes folders named for the day the photos were taken. This lists
those folders and renames one to "2026_08_22 - Beach day", moving everything
LensLedger records by path along with it. Later imports for the same day still
go into the renamed folder, because Auto-import matches on the start of a name.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path, PurePosixPath
from typing import Callable

from app_paths import data_root, write_json_atomically
from console_log import log as console_log
from generate_historical_folder_tags import infer_tags
from ingest_pipeline import log_rename_failure
from photo_index import (
    MEDIA_EXTENSIONS, connect, delete_search_rows, rebuild_search_row, set_source_tags,
)

SEPARATOR = " - "
DESCRIPTION_LIMIT = 100
LIST_LIMIT = 20
# Folders still waiting for a name are listed however long ago they were filled.
UNNAMED_LIMIT = 100
# How many times a waiting rename is tried while the disk or catalog refuses it.
RETRY_LIMIT = 12
# Rows these hold are found by a photo's path, so they have to move with it.
PERSONAL_TABLES = ("asset_annotations", "asset_tag_exclusions", "asset_ratings", "asset_rating_sync")

_NAMED = re.compile(r"^(\d{4}(?:[_.\-]?\d{2}){0,2})(?:\s+-+\s*|\s*-+\s+|\s+)(\S.*)$")
_FULL_DATE = re.compile(r"\d{4}[_.\-]?\d{2}[_.\-]?\d{2}")
_DATE_ONLY = re.compile(r"\d{4}[_.\-]?\d{2}(?:[_.\-]?\d{2})?")
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class FolderInUse(ValueError):
    """The disk refused the rename for now, so it is worth trying again."""


class FolderGone(ValueError):
    """The folder is no longer one that can be named."""


def split_folder_name(name: str, planned: str = "") -> tuple[str, str]:
    """A folder name as the part Auto-import made and the description after it.

    ``planned`` is the name the sorting template produced, when it was logged.
    """
    if planned:
        if name.casefold() == planned.casefold():
            return name, ""
        head, rest = name[:len(planned)], name[len(planned):]
        if head.casefold() == planned.casefold() and rest.startswith(SEPARATOR):
            return head, rest[len(SEPARATOR):]
    match = _NAMED.match(name)
    if match:
        return match.group(1), match.group(2)
    return name, ""


def clean_description(text: str) -> str:
    cleaned = " ".join(text.split()).lstrip("- ").rstrip(". ")
    if _INVALID.search(cleaned):
        raise ValueError('A folder name cannot contain any of these characters: \\ / : * ? " < > |')
    if len(cleaned) > DESCRIPTION_LIMIT:
        raise ValueError(f"Keep the description to {DESCRIPTION_LIMIT} characters or fewer.")
    return cleaned


def folder_name(base: str, description: str) -> str:
    return f"{base}{SEPARATOR}{description}" if description else base


def read_log(path: Path) -> list[dict]:
    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    except OSError:
        return []
    return entries


def _replay(entries: list[dict]) -> list[dict]:
    """Every folder the log says received photos, under its latest logged name,
    most recently filled first."""
    found: dict[Path, dict] = {}
    for entry in entries:
        action = entry.get("action")
        if action == "imported":
            subfolder = PurePosixPath(str(entry.get("subfolder") or ""))
            destination = str(entry.get("destination") or "")
            # No parts means the photo went into the destination folder
            # itself, which is not a folder to rename.
            if not destination or not subfolder.parts:
                continue
            folder = Path(destination).parent
            record = found.setdefault(folder, {"folder": folder, "planned": ""})
            record["depth"] = len(subfolder.parts)
            record["imported_at"] = str(entry.get("timestamp") or "")
            planned = PurePosixPath(str(entry.get("planned") or "")).name
            record["planned"] = planned or record["planned"]
        elif action == "renamed":
            old = Path(str(entry.get("source") or ""))
            new = Path(str(entry.get("destination") or ""))
            for folder in [item for item in found if item == old or old in item.parents]:
                record = found.pop(folder)
                record["folder"] = new / folder.relative_to(old)
                found[record["folder"]] = record
    return sorted(found.values(), key=lambda item: (item["imported_at"], item["folder"].name), reverse=True)


def _current_folders(record: dict, listings: dict[Path, list[Path]]) -> list[Path]:
    """The logged folder, or, when it was renamed outside LensLedger, the
    folders beside it that begin with the same full date."""
    folder = record["folder"]
    if folder.is_dir():
        return [folder]
    base = split_folder_name(folder.name, record["planned"])[0]
    if not _FULL_DATE.fullmatch(base):
        return []
    parent = folder.parent
    if parent not in listings:
        try:
            listings[parent] = sorted(item for item in parent.iterdir() if item.is_dir())
        except OSError:
            listings[parent] = []
    return [
        item for item in listings[parent]
        if item.name.startswith(base) and not item.name[len(base):len(base) + 1].isalnum()
    ]


def _media_count(folder: Path) -> int:
    try:
        return sum(1 for item in folder.iterdir()
                   if item.suffix.lower() in MEDIA_EXTENSIONS and item.is_file())
    except OSError:
        return 0


def list_folders(entries: list[dict], limit: int = LIST_LIMIT) -> list[dict]:
    """The folders still named only for a date, then the ``limit`` most recently
    filled of the others. Each group is most recent first."""
    listings: dict[Path, list[Path]] = {}
    seen: set[Path] = set()
    unnamed: list[dict] = []
    others: list[dict] = []
    for record in _replay(entries):
        for folder in _current_folders(record, listings):
            if folder in seen:
                continue
            seen.add(folder)
            base, description = split_folder_name(folder.name, record["planned"])
            needs_name = not description and bool(_DATE_ONLY.fullmatch(base))
            group, room = (unnamed, UNNAMED_LIMIT) if needs_name else (others, limit)
            if len(group) >= room:
                continue
            items = _media_count(folder)
            if not items:
                continue
            group.append({
                "folder": folder, "base": base, "description": description, "needs_name": needs_name,
                "display": "/".join(folder.parts[-record["depth"]:]),
                "items": items, "imported_at": record["imported_at"],
            })
    return unnamed + others


def _below(prefix: str) -> tuple[str, str]:
    """The bounds of every path that starts with ``prefix``, which ends in "/"."""
    return prefix, prefix[:-1] + "0"


def move_folder_records(con: sqlite3.Connection, root: Path, old: str, new: str) -> int:
    """Point everything recorded under library folder ``old`` at ``new``.

    Photos keep their database records, so tags, people, ratings, text and
    scan results stay with them. Returns the number of photos moved.
    """
    old_prefix, new_prefix = old + "/", new + "/"
    if con.execute(
        "SELECT 1 FROM assets WHERE relative_path >= ? AND relative_path < ? "
        "UNION ALL SELECT 1 FROM metadata_publications "
        "WHERE relative_path >= ? AND relative_path < ? AND restored_at IS NULL",
        (*_below(new_prefix), *_below(new_prefix)),
    ).fetchone():
        raise ValueError(
            "LensLedger still has records for a folder with that name. "
            "Run Scan photos first, or choose a different description."
        )

    def moved(path: str) -> str:
        return new_prefix + path[len(old_prefix):]

    assets = con.execute(
        "SELECT id, relative_path, folder, in_review_bin FROM assets "
        "WHERE relative_path >= ? AND relative_path < ?", _below(old_prefix),
    ).fetchall()
    for asset in assets:
        target = moved(asset["relative_path"])
        con.execute(
            "UPDATE assets SET relative_path=?, folder=? WHERE id=?",
            (target, PurePosixPath(target).parent.as_posix(), asset["id"]),
        )
        # A photo in the Review Bin stays where it is on disk.
        if not asset["in_review_bin"]:
            con.execute("UPDATE assets SET path=? WHERE id=?", (str(root / target), asset["id"]))
        # Whatever was left at the new path belongs to a photo that is gone.
        for table in PERSONAL_TABLES:
            con.execute(f"DELETE FROM {table} WHERE relative_path=?", (target,))

    for table in (*PERSONAL_TABLES, "metadata_publications", "face_embeddings"):
        con.execute(
            f"UPDATE OR REPLACE {table} SET relative_path = ? || SUBSTR(relative_path, ?) "
            "WHERE relative_path >= ? AND relative_path < ?",
            (new_prefix, len(old_prefix) + 1, *_below(old_prefix)),
        )
    for row in con.execute(
        "SELECT id, original_relative_path FROM review_bin "
        "WHERE original_relative_path >= ? AND original_relative_path < ?", _below(old_prefix),
    ).fetchall():
        target = moved(row["original_relative_path"])
        con.execute(
            "UPDATE review_bin SET original_relative_path=?, original_path=? WHERE id=?",
            (target, str(root / target), row["id"]),
        )

    folders = {asset["folder"] for asset in assets}
    folders.update(row[0] for row in con.execute(
        "SELECT folder FROM folder_tags WHERE folder=? OR (folder >= ? AND folder < ?)",
        (old, *_below(old_prefix)),
    ))
    for folder in folders:
        target = new + folder[len(old):]
        # Tags that came from the old name go with it. Tags added by hand stay.
        automatic = {name.casefold() for name in infer_tags(folder)}
        names = [row[0] for row in con.execute("SELECT tag FROM folder_tags WHERE folder=?", (folder,))
                 if row[0].casefold() not in automatic]
        known = {name.casefold() for name in names}
        names += [name for name in infer_tags(target) if name.casefold() not in known]
        con.execute("DELETE FROM folder_tags WHERE folder IN (?, ?)", (folder, target))
        for name in names:
            con.execute("INSERT OR IGNORE INTO folder_tags(folder, tag) VALUES (?, ?)", (target, name))

    # One delete for all of them: each one reads the whole search table.
    delete_search_rows(con, [asset["id"] for asset in assets])
    for asset in assets:
        folder = new + asset["folder"][len(old):]
        names = [row[0] for row in con.execute("SELECT tag FROM folder_tags WHERE folder=?", (folder,))]
        set_source_tags(con, asset["id"], "folder_rule", names)
        rebuild_search_row(con, asset["id"], replace=False)
    return len(assets)


def check_name_free(folder: Path, name: str) -> None:
    target = folder.with_name(name)
    if target.exists() and not os.path.samefile(target, folder):
        raise ValueError("A folder with that name is already there. Choose a different description.")


def rename_folder(folder: Path, name: str, libraries: list[tuple[Path, Path]]) -> Path:
    """Rename ``folder`` on disk and in each library that holds it.

    ``libraries`` is a (root, database) pair for every library the folder is
    inside. Either the folder and the records all change, or none do.
    """
    target = folder.with_name(name)
    if name == folder.name:
        return folder
    check_name_free(folder, name)
    connections: list[sqlite3.Connection] = []
    try:
        for root, database in libraries:
            con = connect(database)
            connections.append(con)
            con.execute("BEGIN IMMEDIATE")
            old = folder.relative_to(root)
            move_folder_records(con, root, old.as_posix(), old.with_name(name).as_posix())
        try:
            os.rename(folder, target)
        except OSError as exc:
            raise FolderInUse(
                "The folder could not be renamed. Another program may have one of its files open. "
                f"Close it and try again. ({exc.strerror or exc})"
            ) from exc
        try:
            for con in connections:
                con.commit()
        except Exception:
            os.rename(target, folder)
            raise
    except Exception:
        for con in connections:
            con.rollback()
        raise
    finally:
        for con in connections:
            con.close()
    return target


def waiting_path() -> Path:
    """Resolved per call so the file follows the configured data folder."""
    return data_root() / "waiting-renames.json"


class WaitingRenames:
    """Names entered while LensLedger was busy, applied once it is free.

    They are saved to disk, so closing LensLedger does not lose them.
    ``apply(path, description)`` returns False while LensLedger is still busy.
    """

    def __init__(self, apply: Callable[[str, str], bool], seconds: float = 5.0):
        self._apply = apply
        self._seconds = seconds
        # Whoever holds this may go on to take the library lock, never the
        # other way round.
        self.lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._stopped = False

    def items(self) -> dict[str, dict]:
        with self.lock:
            try:
                data = json.loads(waiting_path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            if not isinstance(data, dict):
                return {}
            return {path: entry for path, entry in data.items() if isinstance(entry, dict)}

    def add(self, path: str, description: str) -> None:
        with self.lock:
            items = self._without(path)
            items[path] = {"description": description, "error": "", "attempts": 0}
            write_json_atomically(waiting_path(), items)
            self._schedule()

    def discard(self, path: str) -> None:
        with self.lock:
            items = self._without(path)
            if len(items) != len(self.items()):
                write_json_atomically(waiting_path(), items)

    def _without(self, path: str) -> dict[str, dict]:
        wanted = os.path.normcase(path)
        return {key: entry for key, entry in self.items().items() if os.path.normcase(key) != wanted}

    def start(self) -> None:
        with self.lock:
            self._stopped = False
            self._schedule()

    def stop(self) -> None:
        with self.lock:
            self._stopped = True
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _schedule(self) -> None:
        """Caller must hold the lock."""
        if self._timer or self._stopped:
            return
        if all(entry.get("error") for entry in self.items().values()):
            return
        self._timer = threading.Timer(self._seconds, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        with self.lock:
            self._timer = None
            try:
                self.run_pending()
            finally:
                self._schedule()

    def run_pending(self) -> None:
        """Try each waiting rename once. One that cannot be done keeps the
        reason, for the page to show, and is not tried again."""
        with self.lock:
            for path, entry in self.items().items():
                if entry.get("error"):
                    continue
                try:
                    if self._apply(path, str(entry.get("description", ""))):
                        self.discard(path)
                except FolderGone as exc:
                    self.discard(path)
                    self._report(path, str(exc))
                except (FolderInUse, sqlite3.OperationalError) as exc:
                    attempts = int(entry.get("attempts", 0)) + 1
                    final = attempts >= RETRY_LIMIT
                    self._update(path, {**entry, "attempts": attempts, "error": str(exc) if final else ""})
                    if final:
                        self._report(path, str(exc))
                except Exception as exc:  # one failure must not strand the others
                    self._update(path, {**entry, "error": str(exc)})
                    self._report(path, str(exc))

    def _update(self, path: str, entry: dict) -> None:
        items = self.items()
        items[path] = entry
        write_json_atomically(waiting_path(), items)

    @staticmethod
    def _report(path: str, error: str) -> None:
        console_log(f"Auto-import: waiting folder rename not done — {Path(path).name}: {error}")
        log_rename_failure(Path(path), error)
