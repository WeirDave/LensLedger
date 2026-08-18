"""Automatic library folder watching with scheduled incremental scans.

Runs a background timer thread that triggers an incremental library scan
at a configurable interval. Uses the same scan_library() function as the
manual scan, so cloud placeholder detection, content hashing, and orphan
reconciliation all apply.

Watchdog-based filesystem notifications are avoided because cloud-synced
folders (OneDrive, Dropbox) generate spurious events for placeholder files,
and the incremental scan is already fast for unchanged files.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


class FolderWatcher:
    """Periodically triggers an incremental library scan."""

    def __init__(
        self,
        interval_minutes: int = 30,
        scan_fn: Callable[[], None] | None = None,
    ):
        self._interval = max(5, interval_minutes) * 60
        self._scan_fn = scan_fn
        self._timer: threading.Timer | None = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def interval_minutes(self) -> int:
        return self._interval // 60

    def start(self, interval_minutes: int | None = None) -> None:
        with self._lock:
            if interval_minutes is not None:
                self._interval = max(5, interval_minutes) * 60
            if self._running:
                return
            self._running = True
            self._schedule_next()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def update_interval(self, interval_minutes: int) -> None:
        with self._lock:
            self._interval = max(5, interval_minutes) * 60
            if self._running and self._timer:
                self._timer.cancel()
                self._schedule_next()

    def _schedule_next(self) -> None:
        self._timer = threading.Timer(self._interval, self._on_tick)
        self._timer.daemon = True
        self._timer.start()

    def _on_tick(self) -> None:
        with self._lock:
            if not self._running:
                return
        try:
            if self._scan_fn:
                self._scan_fn()
        except Exception:
            pass
        with self._lock:
            if self._running:
                self._schedule_next()

    def status(self) -> dict[str, object]:
        return {
            "enabled": self._running,
            "interval_minutes": self._interval // 60,
        }
