from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path


class TestBackupHousekeeping(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Metadata Backups"
        self.root.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def make_backup(self, name: str, size: int = 1024, age_days: float = 0) -> Path:
        path = self.root / f"{name}.before-write-tags-20260101T000000Z.jpg"
        path.write_bytes(b"x" * size)
        if age_days:
            when = time.time() - (age_days * 86400)
            os.utime(path, (when, when))
        return path

    def test_usage_counts_only_safety_copies(self):
        from metadata_backups import usage

        self.make_backup("a", size=2048)
        self.make_backup("b", size=1024)
        (self.root / "notes.txt").write_bytes(b"unrelated")

        result = usage(self.root)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["bytes"], 3072)

    def test_prune_removes_copies_past_the_age_limit(self):
        from metadata_backups import prune, usage

        old = self.make_backup("old", size=1024, age_days=90)
        recent = self.make_backup("recent", size=1024, age_days=1)

        result = prune(self.root, keep_days=30, max_bytes=None)
        self.assertEqual(result["removed"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists(), "a copy inside the age limit must survive")
        self.assertEqual(usage(self.root)["count"], 1)

    def test_prune_drops_oldest_first_to_fit_a_size_limit(self):
        from metadata_backups import prune

        oldest = self.make_backup("oldest", size=1000, age_days=5)
        middle = self.make_backup("middle", size=1000, age_days=3)
        newest = self.make_backup("newest", size=1000, age_days=1)

        prune(self.root, keep_days=None, max_bytes=2000)
        self.assertFalse(oldest.exists(), "the oldest copy should go first")
        self.assertTrue(middle.exists())
        self.assertTrue(newest.exists())

    def test_zero_limits_mean_no_pruning_by_that_rule(self):
        from metadata_backups import prune

        kept = self.make_backup("kept", size=1024, age_days=999)
        result = prune(self.root, keep_days=None, max_bytes=None)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(kept.exists())

    def test_clear_all_removes_every_copy(self):
        from metadata_backups import clear_all, usage

        self.make_backup("a")
        self.make_backup("b")
        result = clear_all(self.root)
        self.assertEqual(result["removed"], 2)
        self.assertEqual(usage(self.root)["count"], 0)

    def test_space_check_refuses_when_the_copies_would_not_fit(self):
        from metadata_backups import FREE_SPACE_MARGIN_BYTES, space_check

        generous = space_check(self.root, required_bytes=1)
        self.assertTrue(generous["ok"], "a single byte should always fit")

        impossible = space_check(self.root, required_bytes=1 << 60)
        self.assertFalse(impossible["ok"])
        self.assertGreater(impossible["shortfall_bytes"], 0)
        self.assertEqual(impossible["margin_bytes"], FREE_SPACE_MARGIN_BYTES)

    def test_estimate_required_bytes_sums_the_photos_to_be_written(self):
        from metadata_backups import estimate_required_bytes

        first = Path(self.temporary.name) / "one.jpg"
        second = Path(self.temporary.name) / "two.jpg"
        first.write_bytes(b"a" * 500)
        second.write_bytes(b"b" * 700)

        total = estimate_required_bytes([first, second, Path("does-not-exist.jpg")])
        self.assertEqual(total, 1200, "a missing file must not break the estimate")

    def test_file_digest_detects_a_copy_that_is_not_identical(self):
        from metadata_backups import file_digest

        original = Path(self.temporary.name) / "original.jpg"
        same_size = Path(self.temporary.name) / "same-size.jpg"
        original.write_bytes(b"A" * 64)
        same_size.write_bytes(b"B" * 64)

        self.assertEqual(original.stat().st_size, same_size.stat().st_size)
        self.assertNotEqual(
            file_digest(original), file_digest(same_size),
            "a size check would have passed this; the content check must not",
        )

    def test_human_bytes_reads_plainly(self):
        from metadata_backups import human_bytes

        self.assertEqual(human_bytes(0), "0 bytes")
        self.assertIn("KB", human_bytes(2048))
        self.assertIn("GB", human_bytes(5 * 1024 ** 3))


if __name__ == "__main__":
    unittest.main()
