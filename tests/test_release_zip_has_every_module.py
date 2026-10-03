from __future__ import annotations

import re
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
WORKFLOW = REPOSITORY / ".github" / "workflows" / "release.yml"


def zip_arguments() -> list[str]:
    """What the release workflow hands to `zip -r`, without the exclusions."""
    text = WORKFLOW.read_text(encoding="utf-8")
    block = re.search(r'zip -r "\$ZIPNAME" \\\n(.*?)\n\s*-x ', text, re.S)
    if not block:
        raise AssertionError("the zip command in release.yml moved; update this test with it")
    return [token.strip('"') for token in block.group(1).replace("\\", " ").split()]


def covered(path: str, arguments: list[str]) -> bool:
    """A directory argument ("src/") takes everything below it; a file takes itself."""
    return any(path == argument or (argument.endswith("/") and path.startswith(argument)) for argument in arguments)


class TheReleaseZipCarriesTheWholeApp(unittest.TestCase):
    """The ZIP once named its source files one by one and fell behind.

    v1.13.3 and v1.14.0 shipped 14 of 23 modules, so a ZIP install died on
    `from console_log import ...`. Every module is imported at startup, so
    one missing from the ZIP is an app that cannot start.
    """

    def test_every_source_module_is_in_the_zip(self):
        arguments = zip_arguments()
        missing = sorted(
            f"src/{path.name}" for path in (REPOSITORY / "src").iterdir()
            if path.suffix in {".py", ".ps1"} and not covered(f"src/{path.name}", arguments)
        )
        self.assertEqual(missing, [], f"modules the release ZIP leaves out: {missing}")

    def test_the_updater_refuses_a_download_that_lacks_a_module(self):
        from lensledger_updater import REQUIRED_FILES

        missing = sorted(
            f"src/{path.name}" for path in (REPOSITORY / "src").iterdir()
            if path.suffix == ".py" and f"src/{path.name}" not in REQUIRED_FILES
        )
        self.assertEqual(missing, [], f"modules the updater would accept a ZIP without: {missing}")

    def test_the_web_assets_and_tools_are_in_the_zip(self):
        arguments = zip_arguments()
        for needed in ("web/js/viewer.js", "web/css/viewer.css", "tools/ExifTool/ExifTool.exe", "assets/lensledger-logo.png"):
            with self.subTest(needed=needed):
                self.assertTrue(covered(needed, arguments), needed)


if __name__ == "__main__":
    unittest.main()
