"""The photo a tag write replaces is named by the caller, not guessed.

When ExifTool cannot write a photo where it is (a PNG saved as .jpg, or a
name too long for its temporary file), _run_exiftool_write writes a
short-named copy and copies it back over the photo. That copy-back overwrites
a file, so which file it is must never be inferred from the argument list: an
option value that happened to come last would otherwise be the file replaced.
ExifTool itself is stubbed here, so this runs everywhere.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import photo_search  # noqa: E402


class WriteTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lensledger-target-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.photo = self.tmp / "2026-08-15 screenshot.jpg"
        self.photo.write_bytes(b"original photo")
        self.decoy = self.tmp / "not-the-photo.txt"
        self.decoy.write_bytes(b"leave me alone")
        self.calls: list[list[str]] = []

    def fake_write(self, arguments):
        """Refuse the photo in place, as ExifTool does for a misnamed file,
        and 'write' the working copy by changing its bytes."""
        self.calls.append(list(arguments))
        written = Path(arguments[-1])
        if written == self.photo:
            raise ValueError("Error: Not a valid JPG (looks more like a PNG)")
        written.write_bytes(b"tagged photo")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def test_the_fallback_replaces_the_named_photo_and_nothing_else(self):
        # A path-shaped value in last position is exactly what reading the
        # target off the end of the list would have picked up.
        options = ["-overwrite_original", "-XMP-dc:Subject=Harbour", str(self.decoy)]
        with mock.patch.object(photo_search, "_write_in_place", side_effect=self.fake_write):
            photo_search._run_exiftool_write(options, self.photo)

        self.assertEqual(self.photo.read_bytes(), b"tagged photo")
        self.assertEqual(self.decoy.read_bytes(), b"leave me alone")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], [*options, str(self.photo)])
        self.assertEqual(self.calls[1][:-1], options)
        work = Path(self.calls[1][-1])
        self.assertEqual(work.suffix, ".png", "the working copy takes the real type")
        self.assertFalse(work.exists(), "the working copy is removed afterwards")

    def test_a_write_that_works_in_place_touches_only_the_photo(self):
        with mock.patch.object(photo_search, "_write_in_place",
                               return_value=mock.Mock(returncode=0)) as write:
            photo_search._run_exiftool_write(["-XMP-dc:Title=x"], self.photo)
        write.assert_called_once_with(["-XMP-dc:Title=x", str(self.photo)])
        self.assertEqual(self.photo.read_bytes(), b"original photo")


if __name__ == "__main__":
    unittest.main()
