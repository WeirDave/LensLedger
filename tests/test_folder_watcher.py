from __future__ import annotations

import threading
import time
import unittest


class TestFolderWatcher(unittest.TestCase):
    """The watcher drives every automatic scan and had no tests at all."""

    def setUp(self):
        from folder_watcher import FolderWatcher

        self.FolderWatcher = FolderWatcher
        self.calls: list[float] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.watchers: list = []

    def tearDown(self):
        self.release.set()
        for watcher in self.watchers:
            watcher.stop()

    def make(self, scan_fn=None, interval_minutes=5):
        watcher = self.FolderWatcher(interval_minutes=interval_minutes, scan_fn=scan_fn)
        self.watchers.append(watcher)
        return watcher

    def record(self):
        self.calls.append(time.monotonic())

    def test_the_interval_is_never_shorter_than_five_minutes(self):
        """A one-minute setting would scan a large library continuously."""
        watcher = self.make(interval_minutes=1)
        self.assertEqual(watcher.interval_minutes, 5)

        watcher.update_interval(0)
        self.assertEqual(watcher.interval_minutes, 5)

        watcher.update_interval(30)
        self.assertEqual(watcher.interval_minutes, 30)

    def test_status_reports_whether_it_is_running(self):
        watcher = self.make(scan_fn=self.record, interval_minutes=10)
        self.assertFalse(watcher.status()["enabled"])

        watcher.start()
        self.assertTrue(watcher.status()["enabled"])
        self.assertEqual(watcher.status()["interval_minutes"], 10)

        watcher.stop()
        self.assertFalse(watcher.status()["enabled"])

    def test_stopping_prevents_any_further_scan(self):
        watcher = self.make(scan_fn=self.record)
        watcher.start()
        watcher.stop()
        watcher.trigger_soon(delay=0)
        time.sleep(0.4)
        self.assertEqual(self.calls, [], "a stopped watcher must not scan")

    def test_a_failing_scan_does_not_stop_the_watcher(self):
        attempts = []

        def explode():
            attempts.append(1)
            raise RuntimeError("scan blew up")

        watcher = self.make(scan_fn=explode)
        watcher.start()
        watcher.trigger_soon(delay=0)
        for _ in range(40):
            if attempts:
                break
            time.sleep(0.05)
        self.assertTrue(attempts, "the scan should have run")
        self.assertTrue(watcher.status()["enabled"],
                        "a scan that raised must not take the watcher down with it")

    def test_asking_for_a_scan_while_one_is_running_does_not_leave_two_timers(self):
        """Two timers would double the scan rate, and double again each time.

        Auto-import asks for a scan as soon as it finishes importing, which can
        land while a scheduled scan is still going.
        """
        def slow_scan():
            self.record()
            self.started.set()
            self.release.wait(timeout=5)

        watcher = self.make(scan_fn=slow_scan)
        watcher.start()
        watcher.trigger_soon(delay=0)

        self.assertTrue(self.started.wait(3), "the first scan never started")
        # Ask again while the first is still inside the scan function.
        watcher.trigger_soon(delay=0)
        time.sleep(0.2)
        self.release.set()
        time.sleep(0.5)

        self.assertLessEqual(
            watcher.pending_timer_count(), 1,
            "more than one timer is pending, so scans will keep doubling",
        )

    def test_a_scan_asked_for_during_another_is_not_quietly_dropped(self):
        """Auto-import asks for a scan the moment it finishes importing.

        If the scan already running simply reschedules at the full interval
        when it ends, that request is lost and the new photos wait half an hour.
        """
        scans = []
        first_started = threading.Event()
        hold = threading.Event()

        def scan():
            scans.append(time.monotonic())
            if len(scans) == 1:
                first_started.set()
                hold.wait(timeout=5)

        watcher = self.make(scan_fn=scan, interval_minutes=60)
        watcher.start()
        watcher.trigger_soon(delay=0)
        self.assertTrue(first_started.wait(3), "the first scan never started")

        # Ask for a scan a second from now, then let the running one finish
        # first. The finishing scan must not cancel the request on its way out.
        watcher.trigger_soon(delay=1)
        time.sleep(0.2)
        hold.set()

        for _ in range(80):
            if len(scans) >= 2:
                break
            time.sleep(0.05)

        self.assertGreaterEqual(
            len(scans), 2,
            "the scan asked for while another was running never happened",
        )


if __name__ == "__main__":
    unittest.main()
