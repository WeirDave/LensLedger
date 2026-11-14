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
                   a.media_type, a.size_bytes, a.mtime_ns, a.content_hash,
                   h.sha256 AS stored_sha, h.size_bytes AS stored_size, h.mtime_ns AS stored_mtime
            FROM assets a LEFT JOIN asset_full_hashes h ON h.asset_id=a.id
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
    to_store: list[tuple[int, int, int, str]] = []
    for members in candidates.values():
        if len(members) < 2:
            continue
        by_full: dict[str, list[sqlite3.Row]] = {}
        for row in members:
            if not Path(row["path"]).is_file():
                continue
            size, mtime = int(row["size_bytes"]), int(row["mtime_ns"])
            if row["stored_sha"] and row["stored_size"] == size and row["stored_mtime"] == mtime:
                full = row["stored_sha"]
            else:
                full = _full_hash(row["path"], size, mtime)
                if full:
                    to_store.append((int(row["id"]), size, mtime, full))
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
    _store_full_hashes(con, to_store)
    groups.sort(key=lambda group: (group[0]["folder"], group[0]["filename"]))
    return groups


def _store_full_hashes(con: sqlite3.Connection, rows: list[tuple[int, int, int, str]]) -> None:
    """Remember hashes for next time. Best effort: a catalog that is busy or
    read-only just means the hash is worked out again."""
    if not rows:
        return
    try:
        con.executemany(
            "INSERT OR REPLACE INTO asset_full_hashes(asset_id, size_bytes, mtime_ns, sha256) VALUES (?,?,?,?)",
            rows,
        )
        con.commit()
    except sqlite3.Error:
        try:
            con.rollback()
        except sqlite3.Error:
            pass


def library_has_identical_file(con: sqlite3.Connection, path: Path) -> bool:
    """True when a byte-identical copy of ``path`` is already catalogued.

    The stored fingerprint narrows the field by size and the first 8 KB, and a
    full-file hash decides, so a lookalike is never mistaken for a copy.
    """
    from photo_index import content_hash

    try:
        size = path.stat().st_size
    except OSError:
        return False
    fingerprint = content_hash(path)
    if not fingerprint:
        return False
    rows = con.execute(
        """SELECT path, size_bytes, mtime_ns FROM assets
           WHERE content_hash=? AND size_bytes=? AND in_review_bin=0""",
        (fingerprint, size),
    ).fetchall()
    if not rows:
        return False
    mine = _full_hash(str(path), size, path.stat().st_mtime_ns)
    if not mine:
        return False
    for row in rows:
        if Path(row["path"]).is_file() and _full_hash(row["path"], int(row["size_bytes"]), int(row["mtime_ns"])) == mine:
            return True
    return False


_group_cache: dict[tuple, tuple[tuple, list[list[dict]]]] = {}
_GROUP_CACHE_ENTRIES = 8


def _library_signature(con: sqlite3.Connection) -> tuple:
    """Changes whenever photos are added, rescanned, removed or binned."""
    row = con.execute(
        """SELECT COUNT(*), COALESCE(SUM(in_review_bin), 0), COALESCE(MAX(indexed_at), ''),
                  COALESCE(MAX(id), 0), COALESCE(SUM(mtime_ns % 1000003), 0) FROM assets"""
    ).fetchone()
    return tuple(row)


def cached_groups(con: sqlite3.Connection, kind: str, compute, where: str,
                  values, tokens) -> list[list[dict]]:
    """``compute(con, where, values, tokens)``, remembered until the library
    changes. The filmstrip asks for the same grouping once per page; without
    this every page regrouped the whole library."""
    try:
        database = str(con.execute("PRAGMA database_list").fetchone()[2])
    except (sqlite3.Error, IndexError, TypeError):
        database = ""
    key = (database, kind, where, tuple(values), tuple(tokens))
    signature = _library_signature(con)
    with _cache_lock:
        cached = _group_cache.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    groups = compute(con, where, values, tokens)
    signature = _library_signature(con)
    with _cache_lock:
        _group_cache[key] = (signature, groups)
        while len(_group_cache) > _GROUP_CACHE_ENTRIES:
            _group_cache.pop(next(iter(_group_cache)))
    return groups


def clear_group_cache() -> None:
    with _cache_lock:
        _group_cache.clear()


def plan_keep_best(con: sqlite3.Connection, groups: list[list[dict]]) -> list[dict]:
    """For each set of identical files, the copy to keep and the copies to bin.

    The copy kept is the likely original, the one the set already lists first
    (names marked as a copy go last, then the shortest name). What the user
    attached to the others is carried over by ``merge_into_keeper``. Only
    files that exist qualify.
    """
    plan = []
    for group in groups:
        details = []
        for item in group:
            row = con.execute(
                "SELECT id, path, relative_path, filename, folder, size_bytes FROM assets "
                "WHERE id=? AND in_review_bin=0",
                (int(item["id"]),),
            ).fetchone()
            if row is None or not Path(row["path"]).is_file():
                continue
            details.append(row)
        if len(details) < 2:
            continue
        keeper = details[0]
        extras = details[1:]
        plan.append({
            "keeper": {"id": int(keeper["id"]), "filename": keeper["filename"], "folder": keeper["folder"]},
            "extras": [
                {"id": int(row["id"]), "filename": row["filename"], "folder": row["folder"],
                 "size_bytes": int(row["size_bytes"])}
                for row in extras
            ],
        })
    return plan


def plan_token(plan: list[dict]) -> str:
    """Identifies exactly which files a preview promised to keep and to bin."""
    digest = hashlib.sha256()
    for entry in plan:
        extras = ",".join(str(extra["id"]) for extra in entry["extras"])
        digest.update(f"{entry['keeper']['id']}:{extras};".encode())
    return digest.hexdigest()[:20]


def merge_into_keeper(con: sqlite3.Connection, keeper_id: int, extra_ids: list[int]) -> dict[str, int]:
    """Copy what the user attached to the extras onto the keeper, removing
    nothing from either: a rating if the keeper has none or a lower one, a
    subject if it has none, tags it lacks (including the tags its folder gave it), people confirmed on an extra and
    not on the keeper, a caption or note if it has none. The extras keep
    their own records, so restoring one from the Review Bin loses nothing."""
    from photo_index import apply_asset_annotation, rebuild_search_row, sync_person_tags, utc_now

    keeper = con.execute("SELECT id, relative_path FROM assets WHERE id=?", (keeper_id,)).fetchone()
    merged = {"rating": 0, "subject": 0, "tags": 0, "people": 0, "text": 0}
    for extra_id in extra_ids:
        extra = con.execute("SELECT id, relative_path FROM assets WHERE id=?", (extra_id,)).fetchone()
        if extra is None:
            continue
        theirs = con.execute(
            "SELECT rating FROM asset_ratings WHERE relative_path=?", (extra["relative_path"],)
        ).fetchone()
        mine = con.execute(
            "SELECT rating FROM asset_ratings WHERE relative_path=?", (keeper["relative_path"],)
        ).fetchone()
        if theirs and (mine is None or int(theirs["rating"]) > int(mine["rating"])):
            con.execute(
                """INSERT INTO asset_ratings(relative_path, rating, updated_at) VALUES (?,?,?)
                   ON CONFLICT(relative_path) DO UPDATE SET rating=excluded.rating, updated_at=excluded.updated_at""",
                (keeper["relative_path"], int(theirs["rating"]), utc_now()),
            )
            merged["rating"] += 1

        note = con.execute(
            "SELECT subject, tags FROM asset_annotations WHERE relative_path=?", (extra["relative_path"],)
        ).fetchone()
        own = con.execute(
            "SELECT subject, tags FROM asset_annotations WHERE relative_path=?", (keeper["relative_path"],)
        ).fetchone()
        own_subject = str(own["subject"]).strip() if own else ""
        own_tags = [part.strip() for part in str(own["tags"]).split(";") if part.strip()] if own else []
        known = {row[0].casefold() for row in con.execute(
            "SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=?", (keeper_id,)
        )} | {tag.casefold() for tag in own_tags}
        incoming = [part.strip() for part in str(note["tags"]).split(";")] if note else []
        incoming += [row[0] for row in con.execute(
            """SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
               WHERE at.asset_id=? AND at.source='folder_rule'""", (extra_id,)
        )]
        added = []
        for tag in incoming:
            if tag and tag.casefold() not in known:
                known.add(tag.casefold())
                added.append(tag)
        subject = own_subject or (str(note["subject"]).strip() if note else "")
        if subject != own_subject or added:
            con.execute(
                """INSERT INTO asset_annotations(relative_path, subject, tags) VALUES (?,?,?)
                   ON CONFLICT(relative_path) DO UPDATE SET subject=excluded.subject, tags=excluded.tags""",
                (keeper["relative_path"], subject, ";".join(own_tags + added)),
            )
            merged["subject"] += 1 if subject != own_subject else 0
            merged["tags"] += len(added)

        for person in con.execute(
            "SELECT person_id, confidence FROM asset_people WHERE asset_id=? AND state='confirmed'", (extra_id,)
        ).fetchall():
            existing = con.execute(
                "SELECT state FROM asset_people WHERE asset_id=? AND person_id=?",
                (keeper_id, person["person_id"]),
            ).fetchone()
            if existing is None:
                con.execute(
                    """INSERT INTO asset_people(asset_id, person_id, state, confidence, face_id, source, updated_at)
                       VALUES (?,?,'confirmed',?,NULL,'duplicate_merge',?)""",
                    (keeper_id, person["person_id"], person["confidence"], utc_now()),
                )
                merged["people"] += 1
            elif existing["state"] == "suggested":
                con.execute(
                    "UPDATE asset_people SET state='confirmed', updated_at=? WHERE asset_id=? AND person_id=?",
                    (utc_now(), keeper_id, person["person_id"]),
                )
                merged["people"] += 1

        theirs_text = con.execute("SELECT caption, notes FROM text_data WHERE asset_id=?", (extra_id,)).fetchone()
        if theirs_text and (str(theirs_text["caption"]).strip() or str(theirs_text["notes"]).strip()):
            con.execute("INSERT OR IGNORE INTO text_data(asset_id) VALUES (?)", (keeper_id,))
            own_text = con.execute("SELECT caption, notes FROM text_data WHERE asset_id=?", (keeper_id,)).fetchone()
            caption = str(own_text["caption"]).strip() or str(theirs_text["caption"])
            notes = str(own_text["notes"]).strip() or str(theirs_text["notes"])
            if caption != own_text["caption"] or notes != own_text["notes"]:
                con.execute("UPDATE text_data SET caption=?, notes=? WHERE asset_id=?", (caption, notes, keeper_id))
                merged["text"] += 1

    apply_asset_annotation(con, keeper_id, keeper["relative_path"])
    sync_person_tags(con, keeper_id)
    rebuild_search_row(con, keeper_id)
    return merged
