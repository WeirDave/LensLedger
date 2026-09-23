from __future__ import annotations

import unittest
import warnings
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent.parent / "src"
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


class TestSourcesCompileWithoutWarnings(unittest.TestCase):
    """No source file may compile with a SyntaxWarning.

    An unrecognised backslash escape is a warning today and a SyntaxError in a
    future Python. Left alone, one of these stops LensLedger starting at all on
    the day someone upgrades Python -- and the warning scrolls past unnoticed
    because the program still runs.
    """

    def python_files(self):
        for directory in (SOURCE_DIR, TOOLS_DIR):
            if directory.is_dir():
                yield from sorted(directory.glob("*.py"))

    def test_every_source_file_compiles_without_a_syntax_warning(self):
        offenders = []
        for path in self.python_files():
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", SyntaxWarning)
                try:
                    compile(path.read_text(encoding="utf-8"), str(path), "exec")
                except SyntaxError as error:
                    offenders.append(f"{path.name}:{error.lineno} {error.msg}")
                    continue
            for warning in caught:
                if issubclass(warning.category, SyntaxWarning):
                    offenders.append(f"{path.name}:{warning.lineno} {warning.message}")

        self.assertEqual(
            offenders, [],
            "these become hard errors on a future Python, which would stop "
            f"LensLedger starting: {offenders}",
        )

    def test_the_check_actually_looks_at_the_real_sources(self):
        """Guard the guard: it must be scanning a real, non-empty set of files."""
        files = list(self.python_files())
        self.assertGreater(len(files), 10, "the scan found suspiciously few source files")
        self.assertIn("photo_search.py", [path.name for path in files])


if __name__ == "__main__":
    unittest.main()
