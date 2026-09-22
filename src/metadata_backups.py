"""Manage the safety copies taken before LensLedger writes into a photo.

Every embedded write copies the whole photo first, so the copies are the same
order of size as the library itself. Left alone they grow without limit on the
same disk as the photos they protect, which is how a safety measure turns into
the thing that fills the drive. This module measures them, keeps them inside a
budget, and answers whether there is room before a run starts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
from pathlib import Path

# Headroom left free after a run, so filling the disk is never the outcome of
# pressing a button in LensLedger.
FREE_SPACE_MARGIN_BYTES = 2 * 1024 ** 3

BACKUP_SUFFIXES = (".before-", ".before-people-", ".before-write-tags-", ".before-repair-")


def _is_backup(path: Path) -> bool:
    return ".before-" in path.name


def usage(backup_root: Path) -> dict[str, object]:
    """Total size, count and age span of the safety copies kept on disk."""
    total = 0
    count = 0
    oldest: float | None = None
    newest: float | None = None
    if backup_root.is_dir():
        for path in backup_root.rglob("*"):
            if not path.is_file() or not _is_backup(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            count += 1
            oldest = stat.st_mtime if oldest is None else min(oldest, stat.st_mtime)
            newest = stat.st_mtime if newest is None else max(newest, stat.st_mtime)
    return {
        "bytes": total,
        "count": count,
        "oldest": dt.datetime.fromtimestamp(oldest, dt.timezone.utc).isoformat() if oldest else None,
        "newest": dt.datetime.fromtimestamp(newest, dt.timezone.utc).isoformat() if newest else None,
        "root": str(backup_root),
    }


def free_bytes(path: Path) -> int:
    """Free space on the drive holding `path`, walking up to a folder that exists."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return 0


def space_check(backup_root: Path, required_bytes: int) -> dict[str, object]:
    """Whether `required_bytes` of safety copies will fit, with headroom."""
    free = free_bytes(backup_root)
    needed = int(required_bytes) + FREE_SPACE_MARGIN_BYTES
    return {
        "ok": free >= needed,
        "free_bytes": free,
        "required_bytes": int(required_bytes),
        "margin_bytes": FREE_SPACE_MARGIN_BYTES,
        "shortfall_bytes": max(0, needed - free),
    }


def prune(backup_root: Path, keep_days: int | None = None,
          max_bytes: int | None = None) -> dict[str, object]:
    """Delete the oldest safety copies until they fit the retention budget.

    `keep_days` drops anything older than that many days. `max_bytes` then drops
    the oldest remaining copies until the total fits. Either may be None to skip
    that rule. Copies are only ever deleted here, never the photos themselves.
    """
    if not backup_root.is_dir():
        return {"removed": 0, "freed_bytes": 0}

    entries: list[tuple[float, int, Path]] = []
    for path in backup_root.rglob("*"):
        if not path.is_file() or not _is_backup(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
    entries.sort()

    removed = 0
    freed = 0

    def drop(entry) -> None:
        nonlocal removed, freed
        _mtime, size, path = entry
        try:
            path.unlink()
        except OSError:
            return
        removed += 1
        freed += size

    survivors = entries
    if keep_days is not None and keep_days >= 0:
        cutoff = dt.datetime.now().timestamp() - (keep_days * 86400)
        expired = [entry for entry in entries if entry[0] < cutoff]
        for entry in expired:
            drop(entry)
        survivors = [entry for entry in entries if entry[0] >= cutoff]

    if max_bytes is not None and max_bytes >= 0:
        total = sum(entry[1] for entry in survivors)
        index = 0
        while total > max_bytes and index < len(survivors):
            entry = survivors[index]
            size_before = entry[1]
            drop(entry)
            total -= size_before
            index += 1

    _remove_empty_directories(backup_root)
    return {"removed": removed, "freed_bytes": freed}


def clear_all(backup_root: Path) -> dict[str, object]:
    """Delete every safety copy. The photos themselves are untouched."""
    return prune(backup_root, keep_days=None, max_bytes=0)


def _remove_empty_directories(root: Path) -> None:
    if not root.is_dir():
        return
    for directory in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir():
            try:
                next(directory.iterdir())
            except StopIteration:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            except OSError:
                pass


def file_digest(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """SHA-256 of a file's bytes, for verifying a copy is really a copy."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def human_bytes(value: int | float) -> str:
    size = float(value or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "bytes":
                return f"{int(size):,} bytes"
            return f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def estimate_required_bytes(paths) -> int:
    """Total size of the photos about to be written, which is what gets copied."""
    total = 0
    for path in paths:
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    return total
