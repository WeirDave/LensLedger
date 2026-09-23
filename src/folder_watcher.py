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

from console_log import log as console_log


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
        # Each scheduled tick carries the generation it was scheduled under. A
        # tick whose generation is stale has been superseded -- by trigger_soon,
        # or by an interval change -- and must not schedule another, or the two
        # would run on side by side and the scan rate would double every time.
        self._generation = 0
        self._live_timers: set[threading.Timer] = set()

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
            self._schedule_next(delay=30)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._generation += 1
            self._discard_timer()

    def update_interval(self, interval_minutes: int) -> None:
        with self._lock:
            self._interval = max(5, interval_minutes) * 60
            if self._running:
                self._schedule_next()

    def trigger_soon(self, delay: int = 5) -> None:
        """Reschedule the next check to fire in `delay` seconds."""
        with self._lock:
            if not self._running:
                return
            self._schedule_next(delay=delay)

    def _schedule_next(self, delay: int | None = None) -> None:
        """Caller must hold the lock."""
        self._discard_timer()
        self._generation += 1
        generation = self._generation
        timer = threading.Timer(
            delay if delay is not None else self._interval,
            self._on_tick, args=(generation,),
        )
        timer.daemon = True
        self._timer = timer
        self._live_timers.add(timer)
        timer.start()

    def _discard_timer(self) -> None:
        """Caller must hold the lock."""
        if self._timer is not None:
            self._timer.cancel()
            self._live_timers.discard(self._timer)
            self._timer = None

    def pending_timer_count(self) -> int:
        """How many timers are still waiting to fire. Should never exceed one."""
        with self._lock:
            return sum(1 for timer in self._live_timers if timer.is_alive())

    def _on_tick(self, generation: int) -> None:
        with self._lock:
            self._live_timers.discard(self._timer)
            if not self._running or generation != self._generation:
                return
        try:
            if self._scan_fn:
                console_log("Folder watcher: checking for changes")
                self._scan_fn()
        except Exception as exc:
            console_log(f"Folder watcher: scan error — {exc}")
        with self._lock:
            if self._running and generation == self._generation:
                self._schedule_next()

    def status(self) -> dict[str, object]:
        return {
            "enabled": self._running,
            "interval_minutes": self._interval // 60,
        }
