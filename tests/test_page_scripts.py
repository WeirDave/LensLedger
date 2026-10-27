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


if __name__ == "__main__":
    unittest.main()
