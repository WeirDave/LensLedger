from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": str(self.root / "data")})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_initialize_creates_empty_current_database(self):
        from database_tools import initialize
        from photo_index import SCHEMA_VERSION

        database = self.root / "library.sqlite3"
        self.assertEqual(initialize(database), 0)
        con = sqlite3.connect(database)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)
        con.close()

    def test_scan_is_incremental_and_backup_is_valid(self):
        from database_tools import backup, verify
        from photo_index import scan_library

        library = self.root / "photos"
        library.mkdir()
        photo = library / "2026-08-09 sample.jpg"
        photo.write_bytes(b"not-a-real-jpeg")
        database = self.root / "library.sqlite3"

        self.assertEqual(scan_library(library, database), 0)
        self.assertEqual(scan_library(library, database), 0)
        con = sqlite3.connect(database)
        runs = con.execute("SELECT changed,unchanged,errors FROM runs ORDER BY id").fetchall()
        con.close()
        self.assertEqual(runs, [(1, 0, 0), (0, 1, 0)])

        destination = self.root / "backup.sqlite3"
        self.assertEqual(backup(database, destination), 0)
        self.assertEqual(verify(destination), 0)


if __name__ == "__main__":
    unittest.main()
