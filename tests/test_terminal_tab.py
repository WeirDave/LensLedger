"""`tools/terminal_tab.py` is what `Start LensLedger.cmd` runs first. It decides
one thing - hand off to `wt.exe -w 0 new-tab`, or stay in this window - and the
failure that matters is a launcher that opens a tab when it should not, or does
not start at all.

What no test here can show is how `wt.exe` itself parses the command line, and
that Windows Terminal closes the old tab on a clean exit. Both need a real
Windows Terminal; they are the two things to check by hand.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lensledger_updater as updater
import settings_config
from tools import terminal_tab

LAUNCHER = r"C:\Users\Someone\AppData\Local\Programs\LensLedger\Start LensLedger.cmd"
WT = r"C:\Program Files\WindowsApps\wt.exe"


def have_wt(name):
    return WT if name in ("wt.exe", "wt") else None


def no_wt(name):
    return None


def decide(launcher=LAUNCHER, **overrides):
    """can_hand_off for a bare Windows double-click, with one thing changed."""
    kwargs = dict(os_name="nt", env={}, which=have_wt, settings={}, argv=())
    kwargs.update(overrides)
    return terminal_tab.can_hand_off(launcher, **kwargs)


class HandOffDecisionTests(unittest.TestCase):
    def test_a_bare_windows_double_click_hands_off(self):
        self.assertTrue(decide())

    def test_each_reason_to_stay_keeps_the_launcher_in_its_own_window(self):
        cases = {
            "not windows": dict(os_name="posix"),
            "inside windows terminal": dict(env={"WT_SESSION": "1234"}),
            "inside vs code": dict(env={"TERM_PROGRAM": "vscode"}),
            "already the tab's own launch": dict(argv=("--in-tab",)),
            "switched off": dict(settings={"startup": {"terminal_tab": False}}),
            "no wt on the path": dict(which=no_wt),
            "launcher path has a semicolon": dict(launcher=r"C:\a;b\Start LensLedger.cmd"),
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                self.assertFalse(decide(**overrides))

    def test_other_startup_settings_do_not_switch_it_off(self):
        self.assertTrue(decide(settings={"startup": {"show_library_picker": True}}))
        self.assertTrue(decide(settings={"startup": {"terminal_tab": True}}))
        self.assertTrue(decide(settings={"startup": {"terminal_tab": "no"}}),
                        "only an explicit false turns it off")

    def test_wt_is_found_under_either_name(self):
        self.assertTrue(decide(which=lambda n: WT if n == "wt" else None))
        self.assertTrue(decide(which=lambda n: WT if n == "wt.exe" else None))


class TabCommandTests(unittest.TestCase):
    def test_the_command_is_exactly_this_argument_by_argument(self):
        self.assertEqual(terminal_tab.tab_command(WT, LAUNCHER), [
            WT, "-w", "0", "new-tab",
            "--title", "LensLedger", "--suppressApplicationTitle",
            "-d", r"C:\Users\Someone\AppData\Local\Programs\LensLedger",
            "cmd.exe", "/c", LAUNCHER, "--in-tab",
        ])

    def test_the_directory_is_a_windows_path_even_when_built_on_linux(self):
        command = terminal_tab.tab_command("wt", r"D:\Apps\LensLedger\Start LensLedger.cmd")
        self.assertEqual(command[command.index("-d") + 1], r"D:\Apps\LensLedger")

    def test_a_restart_passes_restarted_after_the_tab_flag(self):
        command = terminal_tab.tab_command(WT, LAUNCHER, extra_args=("--restarted",))
        self.assertEqual(command[-2:], ["--in-tab", "--restarted"])


class OpenTabTests(unittest.TestCase):
    def test_popen_raising_oserror_means_run_here_and_is_logged(self):
        def refuse(*args, **kwargs):
            raise OSError("The system cannot find the file specified")

        with patch("console_log.log") as log:
            self.assertFalse(terminal_tab.open_tab(LAUNCHER, popen=refuse, which=have_wt))
        log.assert_called_once()
        self.assertIn("Windows Terminal tab", log.call_args.args[0])

    def test_main_runs_here_rather_than_crashing_when_the_tab_cannot_start(self):
        with patch.object(terminal_tab, "can_hand_off", return_value=True), \
             patch.object(terminal_tab, "open_tab", return_value=False):
            self.assertEqual(terminal_tab.main([LAUNCHER]), terminal_tab.RUN_HERE)

    def test_main_reports_a_handoff_only_when_a_tab_opened(self):
        with patch.object(terminal_tab, "can_hand_off", return_value=True), \
             patch.object(terminal_tab, "open_tab", return_value=True):
            self.assertEqual(terminal_tab.main([LAUNCHER]), terminal_tab.HANDED_OFF)

    def test_main_stays_in_place_inside_the_tab_it_opened(self):
        """Everything else says hand off here, so only the flag can stop it."""
        with patch.object(terminal_tab.os, "name", "nt"), \
             patch.dict(os.environ, {}, clear=True), \
             patch.object(terminal_tab, "tab_enabled", return_value=True), \
             patch.object(terminal_tab, "find_wt", return_value=WT), \
             patch.object(terminal_tab, "open_tab") as opened:
            self.assertEqual(terminal_tab.main([LAUNCHER, "--in-tab"]), terminal_tab.RUN_HERE)
            self.assertEqual(terminal_tab.main([LAUNCHER]), terminal_tab.HANDED_OFF)
        self.assertEqual(opened.call_count, 1, "only the launch without the flag may open a tab")

    def test_main_with_no_launcher_path_runs_here(self):
        with patch.object(terminal_tab, "can_hand_off", return_value=True), \
             patch.object(terminal_tab, "open_tab") as opened:
            self.assertEqual(terminal_tab.main([]), terminal_tab.RUN_HERE)
        opened.assert_not_called()

    def test_restart_in_tab_ignores_the_old_servers_terminal_variables(self):
        """The server that is restarting is inside a tab (WT_SESSION is set);
        the replacement is still opened as one."""
        popen = MagicMock()
        with patch.dict(os.environ, {"WT_SESSION": "1234", "TERM_PROGRAM": "vscode"}):
            opened = terminal_tab.restart_in_tab(
                LAUNCHER, os_name="nt", which=have_wt, popen=popen, settings={})
        self.assertTrue(opened)
        popen.assert_called_once()

    def test_restart_in_tab_stays_out_when_off_or_not_windows_or_no_wt(self):
        popen = MagicMock()
        cases = {
            "off": dict(settings={"startup": {"terminal_tab": False}}),
            "not windows": dict(os_name="posix"),
            "no wt": dict(which=no_wt),
            "semicolon": dict(launcher=r"C:\a;b\Start LensLedger.cmd"),
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                kwargs = dict(os_name="nt", which=have_wt, popen=popen, settings={})
                kwargs.update(overrides)
                launcher = kwargs.pop("launcher", LAUNCHER)
                self.assertFalse(terminal_tab.restart_in_tab(launcher, **kwargs))
        popen.assert_not_called()


class SettingRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        patcher = patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": self.data.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_is_on_by_default(self):
        self.assertIs(settings_config.load_settings()["startup"]["terminal_tab"], True)
        self.assertTrue(terminal_tab.tab_enabled())

    def test_switching_it_off_reaches_the_helper_through_the_real_loader(self):
        settings_config.save_settings({"startup": {"terminal_tab": False}})
        self.assertIs(settings_config.load_settings()["startup"]["terminal_tab"], False)
        self.assertFalse(terminal_tab.tab_enabled())
        settings_config.save_settings({"startup": {"terminal_tab": True}})
        self.assertTrue(terminal_tab.tab_enabled())

    def test_saving_another_startup_setting_does_not_lose_it(self):
        settings_config.save_settings({"startup": {"terminal_tab": False}})
        saved = settings_config.load_settings()
        saved["startup"]["show_library_picker"] = True
        settings_config.save_settings(saved)
        self.assertFalse(terminal_tab.tab_enabled())

    def test_an_unreadable_settings_file_leaves_it_on(self):
        settings_config.settings_file().write_text("{ not json", encoding="utf-8")
        self.assertTrue(terminal_tab.tab_enabled())

    def test_a_settings_loader_that_raises_leaves_it_on(self):
        with patch.object(settings_config, "load_settings", side_effect=RuntimeError("boom")):
            self.assertTrue(terminal_tab.tab_enabled())


class RestartTests(unittest.TestCase):
    """The helper that restarts LensLedger for a launcher that cannot restart
    it in place: a tab when there is one, otherwise the console it always used."""

    def run_launch(self, *, tab_opened, old_window_pid=777):
        events = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(updater.os, "name", "nt"), \
                 patch.object(updater, "_restart_in_terminal_tab",
                              lambda *a, **k: events.append(("tab", a[1], a[2])) or tab_opened), \
                 patch.object(updater.subprocess, "Popen",
                              lambda cmd, **k: events.append(("console", cmd, k.get("creationflags")))), \
                 patch.object(updater, "wait_for_process",
                              lambda pid, timeout_seconds=180: events.append(("wait", pid))), \
                 patch.object(updater, "close_old_launcher_window",
                              lambda pid, **k: events.append(("kill", pid))):
                updater.launch_lensledger(root, old_window_pid=old_window_pid)
        return events, root

    def test_a_tab_that_opens_means_no_console_and_no_window_kill(self):
        events, root = self.run_launch(tab_opened=True)
        kinds = [event[0] for event in events]
        self.assertEqual(kinds, ["tab", "wait"])
        self.assertEqual(events[0][2], ("--restarted",))
        self.assertTrue(events[0][1].endswith("Start LensLedger.cmd"))

    def test_no_tab_leaves_the_old_console_behaviour_unchanged(self):
        events, root = self.run_launch(tab_opened=False)
        self.assertEqual([event[0] for event in events], ["tab", "console", "wait", "kill"])
        command = events[1][1]
        self.assertEqual(command[:2], ["cmd.exe", "/c"])
        self.assertEqual(command[3:], ["--restarted"])
        self.assertEqual(events[1][2], getattr(subprocess, "CREATE_NEW_CONSOLE", 0))

    def test_a_first_launch_after_install_asks_for_no_restarted_flag(self):
        events, _ = self.run_launch(tab_opened=True, old_window_pid=None)
        self.assertEqual(events[0][2], ())
        self.assertEqual([event[0] for event in events], ["tab"])

    def test_a_tree_without_the_helper_falls_back_to_a_console(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(sys.modules, {"tools": None, "tools.terminal_tab": None}):
            self.assertFalse(updater._restart_in_terminal_tab(
                Path(temporary), "Start LensLedger.cmd", ()))

    def test_a_helper_that_raises_falls_back_to_a_console(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(terminal_tab, "restart_in_tab", side_effect=RuntimeError("boom")):
            self.assertFalse(updater._restart_in_terminal_tab(
                Path(temporary), "Start LensLedger.cmd", ()))

    def test_the_server_exit_code_for_a_restart_matches_the_helpers(self):
        import photo_search
        self.assertEqual(photo_search.RESTART_EXIT_CODE, terminal_tab.RESTART_EXIT_CODE)

    def test_the_helper_ships_in_the_release(self):
        self.assertIn("tools/terminal_tab.py", updater.REQUIRED_FILES)
        self.assertIn("tools/__init__.py", updater.REQUIRED_FILES)


@unittest.skipUnless(shutil.which("node"), "node is needed to run the settings page's own script")
class SettingsPageScriptTests(unittest.TestCase):
    """Runs the real `collectSettings` from web/js/settings.js."""

    SCRIPT = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function collectSettings()');
const end = src.indexOf('async function saveSettings()');
const checked = process.argv[2] === 'checked';
const box = { checked, value: '' };
const $ = (sel) => (sel === '#terminalTab' ? box : { checked: false, value: '1' });
const settings = { startup: { terminal_tab: !checked } };
const document = { querySelector: () => null, querySelectorAll: () => [] };
eval(src.slice(start, end) + '; globalThis.out = collectSettings();');
console.log(JSON.stringify(globalThis.out.startup));
"""

    def collect(self, state):
        result = subprocess.run(
            ["node", "-e", self.SCRIPT, str(ROOT / "web" / "js" / "settings.js"), state],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_the_checkbox_is_sent_back_on_save(self):
        self.assertIs(self.collect("checked")["terminal_tab"], True)
        self.assertIs(self.collect("unchecked")["terminal_tab"], False)


class ServerFlagTests(unittest.TestCase):
    """The launcher passes its arguments on to the server, so the `--in-tab`
    the tab hands back has to be one the server accepts."""

    def test_the_server_accepts_the_flag_the_tab_passes_back(self):
        import argparse
        import photo_search

        class Parsed(Exception):
            pass

        real_parse = argparse.ArgumentParser.parse_args

        def stop_after_parsing(parser, *args, **kwargs):
            raise Parsed(real_parse(parser, *args, **kwargs))

        with patch.object(argparse.ArgumentParser, "parse_args", stop_after_parsing), \
             patch.object(sys, "argv", ["photo_search.py", "--in-tab", "--restarted"]):
            with self.assertRaises(Parsed) as parsed:
                photo_search.main()
        self.assertTrue(parsed.exception.args[0].in_tab)
        self.assertTrue(parsed.exception.args[0].restarted)


if __name__ == "__main__":
    unittest.main()
