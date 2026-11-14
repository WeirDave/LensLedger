"""Filters that combine with any search, and the saved searches built from them.

The photo list has always had a search box, a scope, one date, a minimum rating
and a map location. These add a date range, several people at once, the kind of
file, the camera, the shape of the picture, whether it has a location and the
folder, and every one of them applies to every scope.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass, field

# Query-string keys a saved collection may keep. Anything else (page number,
# selected photo) describes where the person was, not what they were looking for.
COLLECTION_KEYS = (
    "q", "scope", "sort", "rating", "date", "from", "to", "people", "kind",
    "camera", "shape", "gps", "folder", "near",
)
KINDS = {"image": "Photos", "video": "Videos", "raw": "RAW files"}
SHAPES = {"landscape": "Landscape", "portrait": "Portrait", "square": "Square"}
LOCATIONS = {"has": "Has a location", "none": "No location"}
MAX_COLLECTIONS = 100
MAX_NAME = 60

_LIKE_ESCAPE_RE = re.compile(r"([\\%_])")


def _like(value: str) -> str:
    return "%" + _LIKE_ESCAPE_RE.sub(r"\\\1", value) + "%"


_PARTIAL_DATE_RE = re.compile(r"^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?$")


def _valid_date(value: str, end: bool = False) -> str:
    """A full date, or a year or year and month widened to the first day
    (or, with ``end``, the last day) of that period. Anything else is ""."""
    match = _PARTIAL_DATE_RE.match(value.strip())
    if not match:
        return ""
    year, month, day = (int(part) if part else None for part in match.groups())
    try:
        if month is None:
            return dt.date(year, 12, 31).isoformat() if end else dt.date(year, 1, 1).isoformat()
        if day is None:
            if end:
                following = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
                return (following - dt.timedelta(days=1)).isoformat()
            return dt.date(year, month, 1).isoformat()
        return dt.date(year, month, day).isoformat()
    except (ValueError, OverflowError):
        return ""


@dataclass
class Filters:
    from_date: str = ""
    to_date: str = ""
    people: list[int] = field(default_factory=list)
    kind: str = ""
    camera: str = ""
    shape: str = ""
    gps: str = ""
    folder: str = ""

    @property
    def active(self) -> bool:
        return bool(self.from_date or self.to_date or self.people or self.kind
                    or self.camera or self.shape or self.gps or self.folder)


def parse_filters(params: dict[str, list[str]]) -> Filters:
    """Read the filter fields from a parsed query string, dropping anything
    that is not valid rather than failing the whole page."""
    def one(name: str) -> str:
        return (params.get(name) or [""])[0].strip()

    people: list[int] = []
    for raw in params.get("people") or []:
        for part in str(raw).split(","):
            if part.strip().isdecimal() and int(part) not in people:
                people.append(int(part))
    return Filters(
        from_date=_valid_date(one("from")),
        to_date=_valid_date(one("to"), end=True),
        people=people[:12],
        kind=one("kind") if one("kind") in KINDS else "",
        camera=one("camera")[:80],
        shape=one("shape") if one("shape") in SHAPES else "",
        gps=one("gps") if one("gps") in LOCATIONS else "",
        folder=one("folder")[:120],
    )


def filter_clauses(filters: Filters | None) -> tuple[list[str], list[object]]:
    """SQL conditions on the ``assets a`` row for every active filter."""
    clauses: list[str] = []
    values: list[object] = []
    if filters is None:
        return clauses, values
    if filters.from_date:
        clauses.append("a.capture_date>=?")
        values.append(filters.from_date)
    if filters.to_date:
        clauses.append("a.capture_date<=?")
        values.append(filters.to_date)
    for person_id in filters.people:
        clauses.append("""EXISTS (
                SELECT 1 FROM asset_people fp
                WHERE fp.asset_id=a.id AND fp.person_id=? AND fp.state='confirmed'
            )""")
        values.append(person_id)
    if filters.kind:
        clauses.append("a.media_type=?")
        values.append(filters.kind)
    if filters.camera:
        clauses.append("(a.camera_make || ' ' || a.camera_model) LIKE ? ESCAPE '\\'")
        values.append(_like(filters.camera))
    if filters.shape == "landscape":
        clauses.append("a.width>a.height AND a.height>0")
    elif filters.shape == "portrait":
        clauses.append("a.height>a.width AND a.width>0")
    elif filters.shape == "square":
        clauses.append("a.width=a.height AND a.width>0")
    if filters.gps == "has":
        clauses.append("a.gps_latitude IS NOT NULL AND a.gps_longitude IS NOT NULL")
    elif filters.gps == "none":
        clauses.append("(a.gps_latitude IS NULL OR a.gps_longitude IS NULL)")
    if filters.folder:
        clauses.append("a.folder LIKE ? ESCAPE '\\'")
        values.append(_like(filters.folder))
    return clauses, values


def keep_matching_ids(con: sqlite3.Connection, ids: list[int], where: str,
                      values: list[object] | tuple[object, ...], chunk: int = 800) -> set[int]:
    """The ids among ``ids`` whose photo satisfies ``where``. Asked in chunks,
    because SQLite refuses a list of more than about 32,000 values."""
    keep: set[int] = set()
    for start in range(0, len(ids), chunk):
        part = ids[start:start + chunk]
        marks = ",".join("?" for _ in part)
        for row in con.execute(
            f"SELECT a.id FROM assets a WHERE a.id IN ({marks}) AND {where}", [*part, *values]
        ):
            keep.add(int(row[0]))
    return keep


def collection_query(raw: object) -> dict[str, list[str]]:
    """The part of a query string worth saving, as {key: [values]}.

    ``raw`` is a query string or a parsed one. Only known keys survive, values
    are trimmed, and a saved search never carries a page number or a selected
    photo.
    """
    if isinstance(raw, str):
        parsed = urllib.parse.parse_qs(raw.lstrip("?"), keep_blank_values=False)
    elif isinstance(raw, dict):
        parsed = {str(key): [str(value) for value in (values if isinstance(values, list) else [values])]
                  for key, values in raw.items()}
    else:
        parsed = {}
    cleaned: dict[str, list[str]] = {}
    for key in COLLECTION_KEYS:
        values = [value.strip()[:200] for value in parsed.get(key, []) if str(value).strip()]
        if values:
            cleaned[key] = values[:12] if key == "people" else values[:1]
    return cleaned


def collection_url(query: dict[str, list[str]]) -> str:
    pairs = [(key, value) for key, values in query.items() for value in values]
    return "/?" + urllib.parse.urlencode(pairs) if pairs else "/"


def list_collections(con: sqlite3.Connection) -> list[dict]:
    result = []
    for row in con.execute("SELECT id, name, query_json FROM saved_searches ORDER BY name COLLATE NOCASE"):
        try:
            query = collection_query(json.loads(row["query_json"]))
        except (TypeError, ValueError):
            continue
        result.append({"id": int(row["id"]), "name": row["name"], "query": query, "url": collection_url(query)})
    return result


def save_collection(con: sqlite3.Connection, name: str, raw_query: object) -> tuple[int, bool]:
    """Save (or replace, when the name is taken) a collection. Returns its id
    and whether an existing one was replaced."""
    name = " ".join(str(name).split())[:MAX_NAME]
    if not name:
        raise ValueError("Give the collection a name")
    query = collection_query(raw_query)
    if not query:
        raise ValueError("Set a search or a filter first; there is nothing to save yet")
    existing = con.execute("SELECT id FROM saved_searches WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if existing:
        con.execute("UPDATE saved_searches SET query_json=?, updated_at=? WHERE id=?",
                    (json.dumps(query), stamp, existing["id"]))
        return int(existing["id"]), True
    if int(con.execute("SELECT COUNT(*) FROM saved_searches").fetchone()[0]) >= MAX_COLLECTIONS:
        raise ValueError(f"A library can keep {MAX_COLLECTIONS} collections. Delete one first")
    cursor = con.execute(
        "INSERT INTO saved_searches(name, query_json, created_at, updated_at) VALUES (?,?,?,?)",
        (name, json.dumps(query), stamp, stamp),
    )
    return int(cursor.lastrowid), False


def delete_collection(con: sqlite3.Connection, collection_id: int) -> bool:
    return con.execute("DELETE FROM saved_searches WHERE id=?", (collection_id,)).rowcount > 0
