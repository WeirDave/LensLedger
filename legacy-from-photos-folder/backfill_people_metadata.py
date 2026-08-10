#!/usr/bin/env python3
"""Publish all previously confirmed LensLedger people into their JPEG files."""

from __future__ import annotations

import argparse
from pathlib import Path

from photo_index import connect
from photo_search import DEFAULT_LIBRARY_ROOT, SearchHandler, library_db_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    database = (args.db or library_db_path(root)).resolve()
    SearchHandler.library_root = root
    SearchHandler.db_path = database
    publisher = SearchHandler.__new__(SearchHandler)

    with connect(database) as con:
        asset_ids = [row[0] for row in con.execute(
            """SELECT DISTINCT a.id
               FROM assets a JOIN asset_people ap ON ap.asset_id=a.id
               WHERE ap.state='confirmed' AND a.in_review_bin=0
                 AND lower(a.extension) IN ('.jpg','.jpeg')
                 AND NOT EXISTS (
                     SELECT 1 FROM metadata_publications mp
                     WHERE mp.asset_id=a.id AND mp.operation='people-backfill'
                       AND mp.restored_at IS NULL
                 )
               ORDER BY a.id"""
        )]

    failures: list[tuple[int, str]] = []
    total = len(asset_ids)
    print(f"Publishing confirmed people to {total} JPEG photos...", flush=True)
    for number, asset_id in enumerate(asset_ids, 1):
        try:
            with connect(database) as con:
                publisher._publish_people_metadata(
                    con, asset_id, operation="people-backfill"
                )
        except Exception as exc:
            failures.append((asset_id, str(exc)))
            print(f"FAILED asset {asset_id}: {exc}", flush=True)
        if number % 10 == 0 or number == total:
            print(f"Progress: {number}/{total} ({len(failures)} failed)", flush=True)

    if failures:
        print("Backfill completed with failures:", flush=True)
        for asset_id, error in failures:
            print(f"  asset {asset_id}: {error}", flush=True)
        return 1
    print(f"Backfill complete: {total}/{total} photos published safely.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
