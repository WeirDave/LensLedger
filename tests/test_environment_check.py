import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import environment_check as check

REPOSITORY = Path(__file__).resolve().parent.parent


class VersionComparison(unittest.TestCase):
    def test_versions_compare_by_their_numbers(self):
        self.assertEqual(check.parse_version("13.0.0"), (13, 0, 0))
        self.assertEqual(check.parse_version("13.0.0rc1"), (13, 0, 0))
        self.assertEqual(check.parse_version("1.8"), (1, 8))
        self.assertTrue(check.clause_met("13.0.0", ">=", "13.0"))
        self.assertFalse(check.clause_met("12.3.0", ">=", "13.0"))
        self.assertTrue(check.clause_met("13.9.1", "<", "14"))
        self.assertFalse(check.clause_met("14.0", "<", "14"))
        self.assertTrue(check.clause_met("1.10", ">", "1.9"))
        self.assertTrue(check.clause_met("2.0", "==", "2.0.0"))

    def test_python_below_the_minimum_is_reported(self):
        self.assertIsNone(check.python_problem((3, 11, 0)))
        self.assertIsNone(check.python_problem((3, 14, 10)))
        self.assertIn("Python 3.11 or newer", check.python_problem((3, 10, 9)))
        self.assertIn("3.9.2", check.python_problem((3, 9, 2)))


class RequirementsAreReadFromTheFile(unittest.TestCase):
    def requirements(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "requirements.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_commented_optional_packages_are_ignored(self):
        path = self.requirements("# header\nPillow>=13.0,<14\npillow-heif>=1.5,<2  # heic\n#   torch>=2.13,<3\n")
        self.assertEqual(
            check.requirement_lines(path),
            [("Pillow", [(">=", "13.0"), ("<", "14")]), ("pillow-heif", [(">=", "1.5"), ("<", "2")])],
        )

    def test_the_real_file_lists_pillow_and_pillow_heif(self):
        names = [name for name, _ in check.requirement_lines(REPOSITORY / "requirements.txt")]
        self.assertEqual(names, ["Pillow", "pillow-heif"])

    def test_a_newer_minimum_in_the_file_is_noticed_without_editing_the_launcher(self):
        installed = {"Pillow": "13.0.0", "pillow-heif": "1.8.0"}
        with patch.object(check, "installed_version", installed.get), patch.object(check, "importable", return_value=True):
            self.assertEqual(check.unmet_requirements(self.requirements("Pillow>=13.0,<14\npillow-heif>=1.5,<2\n")), [])
            problems = check.unmet_requirements(self.requirements("Pillow>=13.1,<14\npillow-heif>=1.5,<2\n"))
            self.assertEqual(problems, ["Pillow 13.0.0 does not meet >=13.1"])
            problems = check.unmet_requirements(self.requirements("Pillow>=13.0,<14\nnewpackage>=1\n"))
            self.assertEqual(problems, ["newpackage is not installed"])
            problems = check.unmet_requirements(self.requirements("Pillow>=12,<13\n"))
            self.assertEqual(problems, ["Pillow 13.0.0 does not meet <13"])

    def test_a_registered_package_that_cannot_be_loaded_is_reported(self):
        path = self.requirements("Pillow>=13.0,<14\n")
        with patch.object(check, "installed_version", return_value="13.0.0"), patch.object(check, "importable", return_value=False):
            self.assertEqual(check.unmet_requirements(path), ["Pillow 13.0.0 is installed but cannot be loaded"])

    def test_this_environment_meets_the_real_requirements(self):
        self.assertEqual(check.unmet_requirements(), [])


class CommandLine(unittest.TestCase):
    def run_main(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = check.main(list(arguments))
        return code, output.getvalue()

    def write(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "requirements.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_a_ready_environment_exits_quietly(self):
        self.assertEqual(self.run_main(), (0, ""))

    def test_check_only_reports_and_changes_nothing(self):
        path = self.write("definitely-not-a-real-package-xyz>=1\n")
        with patch.object(check, "install") as install:
            code, text = self.run_main("--requirements", path)
        self.assertEqual(code, 1)
        self.assertIn("definitely-not-a-real-package-xyz is not installed", text)
        install.assert_not_called()

    def test_fix_installs_then_confirms(self):
        path = self.write("definitely-not-a-real-package-xyz>=1\n")
        with patch.object(check, "install", return_value=True) as install, \
                patch.object(check, "unmet_requirements", side_effect=[["x is not installed"], []]):
            code, text = self.run_main("--requirements", path, "--fix")
        self.assertEqual(code, 0)
        install.assert_called_once()
        self.assertIn("Updating LensLedger's Python packages", text)

    def test_fix_that_cannot_install_fails_clearly(self):
        path = self.write("definitely-not-a-real-package-xyz>=1\n")
        with patch.object(check, "install", return_value=False):
            code, text = self.run_main("--requirements", path, "--fix")
        self.assertEqual(code, 3)
        self.assertIn("could not install its Python requirements", text)

    def test_python_too_old_stops_before_any_install(self):
        with patch.object(check, "python_problem", return_value="LensLedger needs Python 3.11 or newer"), \
                patch.object(check, "install") as install:
            code, text = self.run_main("--fix")
        self.assertEqual(code, 2)
        self.assertIn("Python 3.11 or newer", text)
        install.assert_not_called()

    def test_python_only_ignores_packages(self):
        with patch.object(check, "unmet_requirements", side_effect=AssertionError("packages were checked")):
            self.assertEqual(self.run_main("--python-only")[0], 0)


class LaunchersUseTheCheck(unittest.TestCase):
    def script(self, name):
        return (REPOSITORY / name).read_text(encoding="utf-8")

    def test_start_checks_requirements_before_launch_and_after_a_restart(self):
        text = self.script("Start LensLedger.cmd")
        self.assertEqual(text.count("environment_check.py --fix"), 2)
        self.assertNotIn("PIL.__version__", text)
        self.assertNotIn("pip install", text)

    def test_nothing_in_the_launchers_calls_a_bare_python(self):
        for name in ("Start LensLedger.cmd", "Install LensLedger.cmd"):
            for number, line in enumerate(self.script(name).splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("REM") or stripped.startswith("echo"):
                    continue
                self.assertNotRegex(stripped, r"^python\b", f"{name}:{number} {stripped}")

    def test_both_launchers_choose_the_newest_python_that_is_not_a_prerelease(self):
        for name in ("Start LensLedger.cmd", "Install LensLedger.cmd"):
            text = self.script(name)
            self.assertIn('set "PY=py -3"', text, name)
            self.assertIn("releaselevel == 'final'", text, name)

    def test_the_updater_requires_the_check_to_be_present(self):
        import lensledger_updater
        self.assertIn("src/environment_check.py", lensledger_updater.REQUIRED_FILES)


if __name__ == "__main__":
    unittest.main()
