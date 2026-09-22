"""Timestamped console + file logging for LensLedger.

The log is the only window into a program that reads and writes someone's
photos. Anything the user starts, and anything the program does on its own,
should be visible here: what began, how it progressed, and how it ended --
including the names of files it could not handle.

The log file lives in the per-user application data folder, never beside the
code, and it does contain real file paths because naming the file that failed
is the whole point. That makes the file itself private: see PRIVACY_NOTICE.
"""

from __future__ import annotations

import datetime as dt
import sys
import threading
import time
from logging import getLogger, Formatter
from logging.handlers import RotatingFileHandler

# The log uses em dashes and other non-ASCII punctuation. A real Windows
# console handles them, but a redirected or captured stream falls back to the
# system code page, where writing one raises and would take the running job
# down with it. Ask for UTF-8, and fall back to replacing what cannot be sent.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

_file_logger = None
_current_log_path = None
_session_header_written = False
_header_lock = threading.Lock()

PRIVACY_NOTICE = (
    "This log records real folder names and photo paths from this computer. "
    "Remove them before pasting it anywhere public."
)


def _close_handlers():
    global _current_log_path, _session_header_written
    if _file_logger:
        for h in list(_file_logger.handlers):
            h.close()
            _file_logger.removeHandler(h)
    _current_log_path = None
    _session_header_written = False


def _get_file_logger():
    global _file_logger, _current_log_path
    if _file_logger is None:
        _file_logger = getLogger("lensledger.file")
        _file_logger.setLevel(20)
        _file_logger.propagate = False
    try:
        from app_paths import data_root
        target = data_root() / "Logs" / "LensLedger.log"
    except Exception:
        return None
    if _current_log_path == target and _file_logger.handlers:
        return _file_logger
    _close_handlers()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target, maxBytes=5 * 1024 * 1024, backupCount=1,
            encoding="utf-8", delay=True,
        )
        handler.setFormatter(Formatter("%(message)s"))
        _file_logger.addHandler(handler)
        _current_log_path = target
        return _file_logger
    except Exception:
        return None


def _write_session_header(logger, ts: str) -> None:
    """Once per run, say what this file is and that it is personal."""
    global _session_header_written
    with _header_lock:
        if _session_header_written:
            return
        _session_header_written = True
    try:
        logger.info("")
        logger.info("[%s] --- LensLedger log session started ---", ts)
        logger.info("[%s] %s", ts, PRIVACY_NOTICE)
        for h in logger.handlers:
            h.flush()
    except Exception:
        pass


def log(message: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    line = f"  [{ts}] {message}"
    try:
        print(line, flush=True)
    except (UnicodeEncodeError, OSError):
        # Never let writing a log line stop the work it is describing.
        try:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
    logger = _get_file_logger()
    if logger and logger.handlers:
        _write_session_header(logger, ts)
        logger.info("[%s] %s", ts, message)
        for h in logger.handlers:
            h.flush()


def describe_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 1:
        return "less than a second"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {rest}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


class Action:
    """One thing the user asked for, logged from start to outcome.

    Every action logs when it starts and again when it stops, including when
    it stops having done nothing -- silence after a button press is the thing
    this is here to prevent. Progress lines are rate limited so a long run
    shows movement without burying everything else.
    """

    def __init__(self, name: str, detail: str = "", progress_seconds: float = 10.0):
        self.name = name
        self.started_at = time.monotonic()
        self._last_progress = self.started_at
        self._progress_seconds = progress_seconds
        self._finished = False
        log(f"{name}: started" + (f" — {detail}" if detail else ""))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def note(self, message: str) -> None:
        """Something worth recording mid-run, always logged."""
        log(f"{self.name}: {message}")

    def progress(self, message: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_progress < self._progress_seconds:
            return
        self._last_progress = now
        log(f"{self.name}: {message}")

    def failure(self, item: str, reason: str) -> None:
        """A single item that could not be handled. Always logged, by name."""
        log(f"{self.name}: could not process {item} — {reason}")

    def finish(self, summary: str = "") -> None:
        if self._finished:
            return
        self._finished = True
        took = describe_duration(self.elapsed)
        tail = f" — {summary}" if summary else ""
        log(f"{self.name}: finished in {took}{tail}")

    def cancelled(self, summary: str = "") -> None:
        if self._finished:
            return
        self._finished = True
        took = describe_duration(self.elapsed)
        tail = f" — {summary}" if summary else ""
        log(f"{self.name}: stopped after {took}{tail}")

    def failed(self, reason: str) -> None:
        if self._finished:
            return
        self._finished = True
        took = describe_duration(self.elapsed)
        log(f"{self.name}: failed after {took} — {reason}")

    def __enter__(self) -> "Action":
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if exc is not None:
            self.failed(str(exc) or exc_type.__name__)
        else:
            self.finish()
        return False


def shutdown() -> None:
    _close_handlers()
