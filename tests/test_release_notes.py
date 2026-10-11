"""The notes file for the version being released follows the subtitle convention.

The update dialog shows the first-line `# ` heading of each release's notes next
to its version number, so it has to be present and read as a short subtitle.
Only the notes for the current `APP_VERSION` are checked; older notes predate
the convention and are left as they are.
"""

import re
import unittest
from pathlib import Path

from product import APP_VERSION

REPOSITORY = Path(__file__).resolve().parent.parent
NOTES = REPOSITORY / "docs" / "releases" / f"v{APP_VERSION}.md"
MAX_SUBTITLE_CHARS = 80


class ReleaseNotesTests(unittest.TestCase):
    def test_current_release_notes_open_with_a_one_line_subtitle(self):
        self.assertTrue(NOTES.is_file(), f"missing release notes: {NOTES.name}")
        first = NOTES.read_text(encoding="utf-8").splitlines()[0]
        match = re.fullmatch(r"# (\S.*)", first)
        self.assertIsNotNone(match, f"first line must be a '# ' subtitle, got: {first!r}")
        subtitle = match.group(1)
        self.assertLessEqual(len(subtitle), MAX_SUBTITLE_CHARS)
        self.assertFalse(subtitle.endswith("."), "no trailing period")
        self.assertNotRegex(subtitle, r"\bv?\d+\.\d+\.\d+\b", "no version number")
        self.assertNotIn("LensLedger v", subtitle, "GitHub already shows the release title")

    def test_current_release_notes_have_only_that_one_h1(self):
        lines = NOTES.read_text(encoding="utf-8").splitlines()
        self.assertEqual([line for line in lines if re.match(r"# \S", line)], lines[:1])


if __name__ == "__main__":
    unittest.main()
