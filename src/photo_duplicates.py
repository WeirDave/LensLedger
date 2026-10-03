"""Find photos stored more than once, byte for byte."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from pathlib import Path

_HASH_CHUNK = 1024 * 1024
# "IMG_0001 (copy).jpg", "Copy of IMG_0001.jpg", "IMG_0001 (2).jpg", "IMG_0001 - Copy.jpg"
_COPY_NAME_RE = re.compile(r"\bcopy\b|\(\d+\)", re.IGNORECASE)
_full_hash_cache: dict[tuple[str, int, int], str] = {}
_cache_lock = threading.Lock()


def _full_hash(path: str, size: int, mtime_ns: int) -> str:
    key = (path, size, mtime_ns)
    with _cache_lock:
        cached = _full_hash_cache.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(_HASH_CHUNK):
                digest.update(chunk)
    except OSError:
        return ""
    value = digest.hexdigest()
    with _cache_lock:
        _full_hash_cache[key] = value
    return value


def find_exact_duplicate_groups(
    con: sqlite3.Connection,
    where: str = "a.in_review_bin=0",
    values: list[object] | tuple[object, ...] = (),
    tokens: list[str] | tuple[str, ...] = (),
) -> list[list[dict]]:
    """Groups of two or more photos whose files are identical.

    The stored fingerprint only covers the first 8 KB and the size, so it
    narrows the field and a full-file hash decides. A false match here would
    invite someone to bin the only copy of a photo.

    `tokens` keeps a group when any copy's path contains every token, and
    then shows the whole group: filtering copies one by one would leave a
    lone copy that no longer looks like a duplicate.
    """
    rows = con.execute(
        f"""SELECT a.id, a.path, a.relative_path, a.filename, a.folder, a.capture_date,
                   a.media_type, a.size_bytes, a.mtime_ns, a.content_hash
            FROM assets a
            WHERE {where} AND a.content_hash<>''
              AND a.content_hash IN (
                  SELECT content_hash FROM assets
                  WHERE in_review_bin=0 AND content_hash<>''
                  GROUP BY content_hash, size_bytes HAVING COUNT(*) > 1
              )""",
        list(values),
    ).fetchall()
    candidates: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for row in rows:
        candidates.setdefault((row["content_hash"], int(row["size_bytes"])), []).append(row)

    wanted = [token.casefold() for token in tokens if token.strip()]
    groups: list[list[dict]] = []
    for members in candidates.values():
        if len(members) < 2:
            continue
        by_full: dict[str, list[sqlite3.Row]] = {}
        for row in members:
            if not Path(row["path"]).is_file():
                continue
            full = _full_hash(row["path"], int(row["size_bytes"]), int(row["mtime_ns"]))
            if full:
                by_full.setdefault(full, []).append(row)
        for same in by_full.values():
            if len(same) < 2:
                continue
            if wanted and not any(
                all(token in str(row["relative_path"]).casefold() for token in wanted) for row in same
            ):
                continue
            # The likely original first: names marked as a copy go last, then
            # the shortest name.
            ordered = sorted(same, key=lambda row: (
                bool(_COPY_NAME_RE.search(Path(row["filename"]).stem)),
                len(row["filename"]), row["relative_path"],
            ))
            groups.append([
                {
                    "id": int(row["id"]), "filename": row["filename"], "folder": row["folder"],
                    "capture_date": row["capture_date"], "media_type": row["media_type"],
                }
                for row in ordered
            ])
    groups.sort(key=lambda group: (group[0]["folder"], group[0]["filename"]))
    return groups
