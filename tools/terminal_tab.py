"""Open the launcher in a Windows Terminal tab instead of a window of its own.

`Start LensLedger.cmd` calls this before it does anything else:

    python -m tools.terminal_tab "<path to the .cmd>"

Exit code 0 means "handed off - a tab is running the launcher, this window has
nothing left to do"; 1 means "run here as usual". Nothing is printed on the way
to either, so the launcher stays quiet when it has nothing to say.

**What it can and cannot do.** Windows has no way to move a process into a
shell that is already running, so "attach to the open PowerShell" is not on
offer. What exists is `wt.exe -w 0 new-tab`: a tab in the most recently used
Windows Terminal window, or a new window when there is none. That is what this
does, and it leaves the launcher exactly as it was in every other case:

* already inside Windows Terminal (`WT_SESSION`) - run in this tab. This is
  also what happens when the launcher is typed into an open PowerShell tab.
* inside another terminal host (VS Code sets `TERM_PROGRAM`) - run there; the
  person chose that terminal.
* the person turned it off (Settings -> Startup), or `wt.exe` is not
  installed, or this is not Windows - run here.
* `--in-tab`, which the tab passes back to the launcher, is a second guard
  against launching a tab from inside the tab it just opened.

macOS and Linux are untouched.
"""
from __future__ import annotations

import ntpath
import os
import shutil
import subprocess
import sys
from pathlib import Path

TAB_TITLE = "LensLedger"

# Handed to the launcher by the tab it opens, so a launcher that cannot see
# WT_SESSION still never opens a second tab from inside the first.
IN_TAB_FLAG = "--in-tab"

# The server exits with this when it stops to restart. A launcher that opened
# its replacement as a tab leaves cleanly on it, which is what closes a Windows
# Terminal tab: it keeps one open after a non-zero exit, and that is what
# killing the launcher's cmd.exe produces.
RESTART_EXIT_CODE = 75

HANDED_OFF = 0
RUN_HERE = 1

_SRC = Path(__file__).resolve().parent.parent / "src"


def tab_enabled(settings=None) -> bool:
    """The `startup.terminal_tab` preference. On unless it is explicitly off.

    Any failure to read settings means on: an unreadable file must not be the
    reason the launcher behaves differently from the one every other install
    has.
    """
    try:
        if settings is None:
            if str(_SRC) not in sys.path:
                sys.path.insert(0, str(_SRC))
            import settings_config
            settings = settings_config.load_settings()
        return (settings.get("startup") or {}).get("terminal_tab") is not False
    except Exception:
        return True


def find_wt(which=shutil.which):
    return which("wt.exe") or which("wt")


def already_in_a_terminal(env) -> bool:
    return bool(env.get("WT_SESSION") or env.get("TERM_PROGRAM"))


def tab_command(wt: str, launcher: str, *, title: str = TAB_TITLE, extra_args=()):
    """The argv that opens `launcher` in a new tab of the current window.

    `-w 0` is "the most recently used window"; Windows Terminal makes one when
    there is none. The tab runs `cmd /c`, so closing the launcher's last
    prompt closes the tab. `;` is Windows Terminal's own command separator and
    cannot be quoted for it through this route - `can_hand_off` refuses a
    path that has one.
    """
    return [
        wt, "-w", "0", "new-tab",
        "--title", title, "--suppressApplicationTitle",
        "-d", ntpath.dirname(launcher) or ".",
        "cmd.exe", "/c", launcher, IN_TAB_FLAG, *extra_args,
    ]


def can_hand_off(launcher: str, *, os_name=None, env=None, which=shutil.which,
                 settings=None, argv=()) -> bool:
    os_name = os.name if os_name is None else os_name
    env = os.environ if env is None else env
    if os_name != "nt":
        return False
    if IN_TAB_FLAG in argv:
        return False
    if already_in_a_terminal(env):
        return False
    if ";" in launcher:
        return False
    if not tab_enabled(settings):
        return False
    return bool(find_wt(which))


def open_tab(launcher: str, *, popen=subprocess.Popen, which=shutil.which,
             extra_args=()) -> bool:
    """Start the tab. False if Windows Terminal could not be started."""
    wt = find_wt(which)
    if not wt:
        return False
    try:
        popen(tab_command(wt, launcher, extra_args=extra_args), close_fds=True)
    except OSError as exc:
        _note_failure(exc)
        return False
    return True


def restart_in_tab(launcher: str, *, os_name=None, which=shutil.which,
                   popen=subprocess.Popen, settings=None, extra_args=()) -> bool:
    """Open a replacement launcher in a tab; True if it did.

    Used where a restart has to start a launcher of its own. The environment
    there is the old server's and says nothing about whether *this* launch
    should be a tab - only the preference and the presence of `wt.exe` do.
    """
    os_name = os.name if os_name is None else os_name
    if os_name != "nt" or ";" in launcher or not tab_enabled(settings):
        return False
    return open_tab(launcher, popen=popen, which=which, extra_args=extra_args)


def _note_failure(exc: BaseException) -> None:
    try:
        if str(_SRC) not in sys.path:
            sys.path.insert(0, str(_SRC))
        import console_log
        console_log.log(f"Could not open a Windows Terminal tab ({exc}); starting in this window instead.")
    except Exception:
        pass


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    launcher = args[0] if args else ""
    if not launcher:
        return RUN_HERE
    if not can_hand_off(launcher, argv=args):
        return RUN_HERE
    return HANDED_OFF if open_tab(launcher) else RUN_HERE


if __name__ == "__main__":
    sys.exit(main())
