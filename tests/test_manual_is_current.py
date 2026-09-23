from __future__ import annotations

import re
import threading
import unittest
import urllib.request
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
MANUAL_FILE = REPOSITORY / "docs" / "user-manual.md"


class TestTheTwoManualsAgree(unittest.TestCase):
    """LensLedger has two manuals and they can drift apart.

    docs/user-manual.md is the written one; the page at /manual is hand-built
    HTML inside the server. Someone updating one and not the other leaves the
    manual people actually read saying the wrong thing, which is how the
    features shipped today came to be missing from it.
    """

    @classmethod
    def setUpClass(cls):
        import photo_search
        import tempfile
        from http.server import ThreadingHTTPServer

        # The manual page renders the navigation, which needs a current library.
        cls.temporary = tempfile.TemporaryDirectory()
        library = Path(cls.temporary.name) / "photos"
        library.mkdir()
        cls.saved_library = getattr(photo_search.SearchHandler, "current_library", None)
        photo_search.SearchHandler.current_library = (
            library.resolve(), Path(cls.temporary.name) / "library.sqlite3")
        photo_search.SearchHandler.csrf_token = "manual-test"

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), photo_search.SearchHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{cls.server.server_port}/manual", timeout=15
        ) as response:
            cls.page = response.read().decode("utf-8", "replace")
        cls.markdown = MANUAL_FILE.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        import photo_search

        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        if cls.saved_library is not None:
            photo_search.SearchHandler.current_library = cls.saved_library
        cls.temporary.cleanup()

    @staticmethod
    def normalise(text: str) -> str:
        """Compare wording, not punctuation style or markup."""
        text = re.sub(r"<[^>]+>", " ", text)
        text = (text.replace("&mdash;", "-").replace("&rsquo;", "'")
                    .replace("&gt;", ">").replace("&amp;", "&")
                    .replace("—", "-").replace("’", "'"))
        return re.sub(r"\s+", " ", text).lower()

    def test_the_page_covers_everything_the_written_manual_does(self):
        """Each numbered section of the written manual must exist on the page."""
        sections = re.findall(r"^## (.+)$", self.markdown, re.MULTILINE)
        page = self.normalise(self.page)
        missing = [
            title for title in sections
            if title.lower() not in ("table of contents",)
            and self.normalise(title).strip() not in page
        ]
        self.assertEqual(
            missing, [],
            "these sections of docs/user-manual.md have no counterpart on the "
            f"/manual page: {missing}",
        )

    def test_features_named_in_the_app_are_explained_in_both(self):
        """A control the app shows should be findable in the manual."""
        controls = [
            "Fill in missing",
            "Re-scan everything",
            "Read photos again",
            "Write all tags",
            "Clear all safety copies",
            "Restore last publish",
            "--data-dir",
        ]
        page = self.normalise(self.page)
        written = self.normalise(self.markdown)
        for control in controls:
            needle = self.normalise(control).strip()
            with self.subTest(control=control):
                self.assertIn(needle, written,
                              f'"{control}" is in the app but not in docs/user-manual.md')
                self.assertIn(needle, page,
                              f'"{control}" is in the app but not on the /manual page')

    def test_the_contents_list_matches_the_sections_on_the_page(self):
        links = re.findall(r'<li><a href="#([a-z0-9-]+)">', self.page)
        ids = set(re.findall(r'<section class="manual-section" id="([a-z0-9-]+)">', self.page))
        missing = [link for link in links if link not in ids]
        self.assertEqual(missing, [],
                         f"contents entries with no matching section: {missing}")

    def test_the_sections_are_numbered_in_order_without_gaps(self):
        numbers = [int(n) for n in re.findall(r"<h2>(\d+)\. ", self.page)]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)),
                         f"section numbering is out of order or repeats: {numbers}")


if __name__ == "__main__":
    unittest.main()
