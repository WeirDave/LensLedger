import io
import json
import os
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


class WeeklyPackageUpdates(unittest.TestCase):
    REAL = REPOSITORY / "requirements.txt"
    DAY = 24 * 3600

    def plan(self, installed, tag=""):
        return check.update_plan(self.REAL, installed.get, tag)

    def test_the_optional_packages_are_read_from_the_file(self):
        sections = check.optional_sections(self.REAL)
        self.assertIn("meaning-search", sections)
        self.assertIn("face-locations", sections)
        self.assertIn("torch>=2.13,<3", sections["meaning-search"])

    def test_with_no_optional_features_only_pip_and_the_base_packages_are_updated(self):
        plan = self.plan({"torch": None})
        self.assertEqual(plan[0], ["install", "--upgrade", "pip"])
        self.assertEqual(plan[1][:3], ["install", "--upgrade", "-r"])
        self.assertEqual(len(plan), 2)

    def test_a_graphics_card_pytorch_is_upgraded_from_its_own_index_not_pypi(self):
        plan = self.plan({"torch": "2.14.1+cu130", "torchvision": "0.28.0+cpu", "open-clip-torch": "3.3.0"}, "cu130")
        torch_steps = [args for args in plan if "torch>=2.13,<3" in args]
        self.assertEqual(len(torch_steps), 1)
        self.assertIn("--index-url", torch_steps[0])
        self.assertIn("https://download.pytorch.org/whl/cu130", torch_steps[0])
        self.assertIn("torchvision", torch_steps[0])
        later = [args for args in plan if "open_clip_torch>=3.3,<4" in args]
        self.assertEqual(len(later), 1)
        self.assertNotIn("torch>=2.13,<3", later[0], "the PyPI upgrade never touches PyTorch")

    def test_a_processor_pytorch_is_upgraded_together_with_torchvision(self):
        plan = self.plan({"torch": "2.13.0+cpu", "torchvision": "0.28.0+cpu"})
        step = next(args for args in plan if "torch>=2.13,<3" in args)
        self.assertEqual(step, ["install", "--upgrade", "torch>=2.13,<3", "torchvision"])

    def test_the_graphics_card_onnx_runtime_is_not_swapped_for_the_processor_one(self):
        plan = self.plan({"insightface": "1.0.1", "onnxruntime-gpu": "1.32.0"})
        step = next(args for args in plan if any(a.startswith("insightface") for a in args))
        self.assertIn("onnxruntime-gpu>=1.28,<2", step)
        self.assertNotIn("onnxruntime>=1.28,<2", step)

    def test_the_processor_onnx_runtime_stays_as_it_is(self):
        plan = self.plan({"insightface": "1.0.1", "onnxruntime": "1.29.0"})
        step = next(args for args in plan if any(a.startswith("insightface") for a in args))
        self.assertIn("onnxruntime>=1.28,<2", step)

    def test_an_update_is_due_a_week_after_the_last_success(self):
        now = 100 * self.DAY
        self.assertFalse(check.update_due({}, now), "no record yet: the clock starts, nothing runs")
        self.assertFalse(check.update_due({"last_success": now - 6 * self.DAY, "last_attempt": now - 6 * self.DAY}, now))
        self.assertTrue(check.update_due({"last_success": now - 8 * self.DAY, "last_attempt": now - 8 * self.DAY}, now))
        self.assertFalse(check.update_due({"last_success": now - 8 * self.DAY, "last_attempt": now - 3600}, now),
                         "a failed attempt waits a day before trying again")
        self.assertTrue(check.update_due({"last_success": now - 8 * self.DAY, "last_attempt": now - 2 * self.DAY}, now))

    def test_switching_updates_off_in_settings_is_honoured(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / "settings.json"
            self.assertTrue(check.updates_wanted(settings), "no settings file: on")
            settings.write_text('{"startup": {"update_packages": false}}', encoding="utf-8")
            self.assertFalse(check.updates_wanted(settings))
            settings.write_text('{"startup": {"terminal_tab": true}}', encoding="utf-8")
            self.assertTrue(check.updates_wanted(settings))


class RefreshRunsOnlyWhenDue(unittest.TestCase):
    DAY = 24 * 3600

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.previous = os.environ.get("LENSLEDGER_DATA_DIR")
        os.environ["LENSLEDGER_DATA_DIR"] = self.folder.name
        self.addCleanup(self.restore)
        self.state = Path(self.folder.name) / check.STATE_FILE

    def restore(self):
        if self.previous is None:
            os.environ.pop("LENSLEDGER_DATA_DIR", None)
        else:
            os.environ["LENSLEDGER_DATA_DIR"] = self.previous

    def run_refresh(self, now, running=False, returncode=0):
        class Done:
            pass

        done = Done()
        done.returncode = returncode
        with patch.object(check.subprocess, "run", return_value=done) as run, \
                patch.object(check, "server_running", return_value=running), \
                patch.object(check, "cuda_wheel_tag", return_value=""), \
                patch.object(check, "installed_version", return_value=None), \
                redirect_stdout(io.StringIO()):
            check.refresh_packages(REPOSITORY / "requirements.txt", now)
        return run

    def test_the_first_launch_only_starts_the_clock(self):
        run = self.run_refresh(1000 * self.DAY)
        run.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text())["last_success"], 1000 * self.DAY)

    def test_a_due_update_runs_pip_and_records_the_time(self):
        self.state.write_text(json.dumps({"last_success": 0, "last_attempt": 0}))
        run = self.run_refresh(1000 * self.DAY)
        self.assertEqual(run.call_count, 2, "pip itself, then the base packages")
        self.assertEqual(json.loads(self.state.read_text())["last_success"], 1000 * self.DAY)

    def test_a_failed_update_is_retried_tomorrow_not_next_week(self):
        self.state.write_text(json.dumps({"last_success": 0, "last_attempt": 0}))
        self.run_refresh(1000 * self.DAY, returncode=1)
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["last_success"], 0)
        self.assertEqual(saved["last_attempt"], 1000 * self.DAY)

    def test_nothing_is_updated_while_LensLedger_is_running(self):
        self.state.write_text(json.dumps({"last_success": 0, "last_attempt": 0}))
        self.run_refresh(1000 * self.DAY, running=True).assert_not_called()

    def test_an_update_that_is_not_due_does_nothing(self):
        now = 1000 * self.DAY
        self.state.write_text(json.dumps({"last_success": now - 2 * self.DAY, "last_attempt": now - 2 * self.DAY}))
        self.run_refresh(now).assert_not_called()

    def test_updates_switched_off_in_settings_do_nothing(self):
        (Path(self.folder.name) / "settings.json").write_text('{"startup": {"update_packages": false}}')
        self.state.write_text(json.dumps({"last_success": 0, "last_attempt": 0}))
        self.run_refresh(1000 * self.DAY).assert_not_called()


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
