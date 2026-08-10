#!/usr/bin/env python3
"""Create, inspect, verify, back up, migrate, and rebuild LensLedger databases."""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path

from app_paths import database_backup_root, libraries_root
from photo_index import SCHEMA_VERSION, connect, rebuild_search_row


DEFAULT_DB = libraries_root() / "default.sqlite3"


def require_database(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"Database does not exist: {path}\nRun database_tools.py init first.")
    return path


def initialize(path: Path) -> int:
    path = path.expanduser().resolve()
    existed = path.exists()
    con = connect(path)
    con.close()
    print(f"{'Opened' if existed else 'Created'} database: {path}")
    print(f"Schema version: {SCHEMA_VERSION}")
    return 0


def status(path: Path) -> int:
    path = require_database(path)
    con = sqlite3.connect(path)
    values = {
        "path": path,
        "schema_version": con.execute("PRAGMA user_version").fetchone()[0],
        "assets": con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0],
        "people": con.execute("SELECT COUNT(*) FROM people").fetchone()[0],
        "tags": con.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
        "scan_runs": con.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
    }
    latest = con.execute(
        "SELECT finished_at,scanned,changed,unchanged,removed,errors FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    for key, value in values.items():
        print(f"{key}: {value}")
    if latest:
        print("last_scan: " + ", ".join(str(value) for value in latest))
    else:
        print("last_scan: never")
    return 0


def verify(path: Path) -> int:
    path = require_database(path)
    con = sqlite3.connect(path)
    integrity = con.execute("PRAGMA quick_check").fetchone()[0]
    foreign_keys = con.execute("PRAGMA foreign_key_check").fetchall()
    version = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    print(f"integrity: {integrity}")
    print(f"foreign_key_errors: {len(foreign_keys)}")
    print(f"schema_version: {version} (current: {SCHEMA_VERSION})")
    return 0 if integrity == "ok" and not foreign_keys and version == SCHEMA_VERSION else 2


def migrate(path: Path) -> int:
    path = require_database(path)
    before_connection = sqlite3.connect(path)
    before = before_connection.execute("PRAGMA user_version").fetchone()[0]
    before_connection.close()
    con = connect(path)
    con.close()
    print(f"Migrated database: {path}")
    print(f"Schema version: {before} -> {SCHEMA_VERSION}")
    return 0


def backup(path: Path, destination: Path | None) -> int:
    path = require_database(path)
    if destination is None:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = database_backup_root() / f"{path.stem}-{stamp}.sqlite3"
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise SystemExit(f"Backup destination already exists: {destination}")
    source = sqlite3.connect(path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    verification = sqlite3.connect(destination)
    integrity = verification.execute("PRAGMA quick_check").fetchone()[0]
    verification.close()
    if integrity != "ok":
        destination.unlink(missing_ok=True)
        raise SystemExit("Backup verification failed; incomplete backup removed.")
    print(f"Backup created: {destination}")
    return 0


def rebuild_search(path: Path) -> int:
    path = require_database(path)
    with connect(path) as con:
        asset_ids = [int(row[0]) for row in con.execute("SELECT id FROM assets")]
        con.execute("DELETE FROM search_fts")
        for asset_id in asset_ids:
            rebuild_search_row(con, asset_id)
    print(f"Search rows rebuilt: {len(asset_ids)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create an empty database or open an existing one")
    commands.add_parser("status", help="show schema and library counts")
    commands.add_parser("verify", help="run SQLite integrity and relationship checks")
    commands.add_parser("migrate", help="upgrade an existing database to the current schema")
    backup_parser = commands.add_parser("backup", help="create and verify a consistent database backup")
    backup_parser.add_argument("destination", nargs="?", type=Path)
    commands.add_parser("rebuild-search", help="recreate the full-text search index")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init":
        return initialize(args.db)
    if args.command == "status":
        return status(args.db)
    if args.command == "verify":
        return verify(args.db)
    if args.command == "migrate":
        return migrate(args.db)
    if args.command == "backup":
        return backup(args.db, args.destination)
    if args.command == "rebuild-search":
        return rebuild_search(args.db)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
