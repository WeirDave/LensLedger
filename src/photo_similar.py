"""Find photos that look alike without being the same file."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

# Bits that may differ, out of 64, for two photos to count as the same picture.
# 5 keeps a resized, recompressed or lightly brightened copy together while
# staying far from the ~32 that unrelated photos average.
MAX_DISTANCE = 5
# A hash with almost no set bits is a flat picture (a blank wall, a black
# frame); every flat picture would match every other.
MIN_BITS_SET = 6
_BANDS = 8  # 8 bands of 8 bits: two hashes within 7 bits of each other share one

_COPY_NAME_RE = re.compile(r"\bcopy\b|\(\d+\)", re.IGNORECASE)


def find_similar_groups(
    con: sqlite3.Connection,
    where: str = "a.in_review_bin=0",
    values: list[object] | tuple[object, ...] = (),
    tokens: list[str] | tuple[str, ...] = (),
    max_distance: int = MAX_DISTANCE,
) -> list[list[dict]]:
    """Groups of two or more photos that look the same, most-likely original first.

    Groups made only of byte-identical files are left out: those are what
    "Exact duplicates" is for, and listing them here too would make the two
    views repeat each other. `tokens` keeps a group when any member's path
    contains every token, then shows the whole group.
    """
    rows = con.execute(
        f"""SELECT a.id, a.relative_path, a.filename, a.folder, a.capture_date,
                   a.media_type, a.size_bytes, a.content_hash, a.visual_hash
            FROM assets a WHERE {where} AND a.visual_hash<>''""",
        list(values),
    ).fetchall()
    hashes = []
    for row in rows:
        bits = int(row["visual_hash"], 16)
        if bits.bit_count() >= MIN_BITS_SET:
            hashes.append((bits, row))

    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, (bits, _row) in enumerate(hashes):
        for band in range(_BANDS):
            buckets[(band, (bits >> (band * 8)) & 0xFF)].append(index)

    parent = list(range(len(hashes)))

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    seen_pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        for position, first in enumerate(members):
            for second in members[position + 1:]:
                pair = (first, second)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if (hashes[first][0] ^ hashes[second][0]).bit_count() <= max_distance:
                    parent[find(first)] = find(second)

    clusters: dict[int, list[int]] = defaultdict(list)
    for index in range(len(hashes)):
        clusters[find(index)].append(index)

    wanted = [token.casefold() for token in tokens if token.strip()]
    groups: list[list[dict]] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        group = [hashes[index][1] for index in members]
        if len({(row["content_hash"], row["size_bytes"]) for row in group}) == 1 and group[0]["content_hash"]:
            continue
        if wanted and not any(
            all(token in str(row["relative_path"]).casefold() for token in wanted) for row in group
        ):
            continue
        # The largest file is most likely the original: a resized or
        # recompressed copy is smaller. Names marked as a copy go last.
        group.sort(key=lambda row: (
            bool(_COPY_NAME_RE.search(row["filename"].rsplit(".", 1)[0])),
            -int(row["size_bytes"]), len(row["filename"]), row["relative_path"],
        ))
        groups.append([
            {"id": int(row["id"]), "filename": row["filename"], "folder": row["folder"],
             "capture_date": row["capture_date"], "media_type": row["media_type"]}
            for row in group
        ])
    groups.sort(key=lambda g: (g[0]["folder"], g[0]["filename"]))
    return groups
