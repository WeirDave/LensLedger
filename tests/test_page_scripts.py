from __future__ import annotations

import re
import unittest
from pathlib import Path

WEB_JS = Path(__file__).resolve().parent.parent / "web" / "js"


def script(name: str) -> str:
    return (WEB_JS / name).read_text(encoding="utf-8")


class ThemeToggleIsBoundOnce(unittest.TestCase):
    def test_only_the_shared_theme_script_binds_the_toggle(self):
        """Two handlers on one button flip the theme against each other."""
        binders = [path.name for path in WEB_JS.glob("*.js")
                   if re.search(r"""querySelector(All)?\(\s*['"]\.theme-toggle""", path.read_text(encoding="utf-8"))
                   and path.name != "theme.js"]
        self.assertEqual(binders, [])
        self.assertIn(".theme-toggle", script("theme.js"))


class ScanPageHasOnePollingLoop(unittest.TestCase):
    def test_polling_is_scheduled_from_a_single_place(self):
        source = script("scan-photos.js")
        self.assertEqual(len(re.findall(r"setTimeout\(\s*refresh\b", source)), 1,
                         "every action button calls refresh(); a second timer chain multiplies requests")
        self.assertIn("pollInFlight", source)

    def test_the_costly_endpoints_are_not_fetched_on_every_tick(self):
        source = script("scan-photos.js")
        self.assertEqual(len(re.findall(r"fetch\('/api/diagnostics'\)", source)), 1)
        self.assertIn("document.hidden", source)


class RemovedSettingsStayRemoved(unittest.TestCase):
    """These controls were saved but read nowhere, so they were taken out."""

    REMOVED_IDS = ("ocrWorkers", "ocrBatchSize", "photosPerPage", "defaultSort", "filmstripSize")

    def test_the_settings_script_no_longer_collects_them(self):
        source = script("settings.js")
        for name in self.REMOVED_IDS:
            self.assertNotIn(name, source)

    def test_the_settings_page_no_longer_shows_them(self):
        import photo_search

        source = Path(photo_search.__file__).read_text(encoding="utf-8")
        for name in self.REMOVED_IDS:
            self.assertNotIn(f'id="{name}"', source)

    def test_the_defaults_no_longer_carry_them(self):
        from settings_config import DEFAULTS

        self.assertNotIn("display", DEFAULTS)
        self.assertNotIn("ocr_workers", DEFAULTS["scan"])
        self.assertNotIn("ocr_batch_size", DEFAULTS["scan"])


class MenuIconsAreDrawn(unittest.TestCase):
    def test_menu_entries_use_svg_icons_not_emoji(self):
        import photo_search

        menu = photo_search.nav_menu("home", "")
        self.assertGreaterEqual(menu.count('class="menu-icon"'), 19)
        self.assertEqual([c for c in menu if ord(c) > 0x2000 and ord(c) not in (0x2014, 0x00b7, 0x2026)], [])


if __name__ == "__main__":
    unittest.main()


class TrashDoesNotDependOnBrowserDialogs(unittest.TestCase):
    """A browser that has blocked a page's dialogs answers confirm() with
    Cancel and says nothing, which made the Trash buttons look dead."""

    def test_viewer_trash_asks_inside_the_page(self):
        source = script("viewer.js")
        self.assertNotRegex(source, r"confirm\(\s*'Move ")
        self.assertIn("askConfirm(", source)
        self.assertEqual(len(re.findall(r"await askConfirm\(", source)), 2)

    def test_viewer_trash_says_so_when_the_photo_has_not_loaded(self):
        source = script("viewer.js")
        start = source.index("async function moveToBin")
        body = source[start:source.index("\n", start)]
        self.assertIn("has not finished loading", body)

    def test_review_pages_trash_asks_inside_the_page(self):
        for name, expected in (("faces-review.js", 2), ("people-review.js", 1)):
            source = script(name)
            self.assertNotRegex(source, r"confirm\(\s*'Move ", name)
            self.assertEqual(len(re.findall(r"await askConfirm\(", source)), expected, name)

    def test_review_pages_load_the_shared_dialog(self):
        from pathlib import Path as _Path
        server = (_Path(__file__).resolve().parent.parent / "src" / "photo_search.py").read_text(encoding="utf-8")
        for title in ("<title>People — review suggestions — {APP_NAME}</title>", "<title>People — {APP_NAME}</title><link rel=\"icon\""):
            page = server[server.index(title):]
            page = page[:page.index("</body>")]
            self.assertIn("css/confirm-dialog.css", page, title)
            self.assertIn("js/confirm-dialog.js", page, title)
            self.assertLess(page.index("js/confirm-dialog.js"), page.index("-review.js"), title)

    def test_shared_dialog_does_not_use_browser_dialogs(self):
        source = script("confirm-dialog.js")
        self.assertIn("showModal()", source)
        self.assertNotRegex(source, r"(?<![\w.])(confirm|alert|prompt)\(")
