"""Timestamped console + file logging for LensLedger."""

from __future__ import annotations

import datetime as dt
import os
from logging import getLogger, Formatter
from logging.handlers import RotatingFileHandler

_file_logger = None
_current_log_path = None


def _close_handlers():
    global _current_log_path
    if _file_logger:
        for h in list(_file_logger.handlers):
            h.close()
            _file_logger.removeHandler(h)
    _current_log_path = None


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


def log(message: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    line = f"  [{ts}] {message}"
    print(line, flush=True)
    logger = _get_file_logger()
    if logger and logger.handlers:
        logger.info("[%s] %s", ts, message)
        for h in logger.handlers:
            h.flush()


def shutdown() -> None:
    _close_handlers()
