from __future__ import annotations

import datetime as dt
import threading
import unittest
from unittest.mock import patch

import photo_search

Handler = photo_search.SearchHandler


def _ago(**delta) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**delta)).isoformat()


class UpdateCheckIntervalTests(unittest.TestCase):
    """How long a status request reuses the last answer before asking GitHub again.

    Each case sets the stored answer, calls the real ``_begin_update_check`` and
    counts whether ``check_for_update`` was reached, so a wrong interval fails here
    rather than only changing a constant.
    """

    def setUp(self):
        self._saved = Handler.update_job
        self.calls = 0
        self.done = threading.Event()

        def fake_check(_version, token=None):
            self.calls += 1
            self.done.set()
            raise RuntimeError("stopped before the network")

        patcher = patch.object(photo_search, "check_for_update", side_effect=fake_check)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: setattr(Handler, "update_job", self._saved))

    def started(self, job: dict, force: bool = False) -> bool:
        self.calls = 0
        self.done.clear()
        Handler.update_job = job
        began = Handler._begin_update_check(force=force)
        if began:
            self.assertTrue(self.done.wait(5), "the check never ran")
            # The worker stores its result after the stub raises; wait for it.
            for _ in range(500):
                if Handler.update_job.get("state") != "checking":
                    break
                threading.Event().wait(0.01)
        self.assertEqual(self.calls, 1 if began else 0)
        return began

    def test_a_current_answer_is_reused_for_under_an_hour(self):
        self.assertFalse(self.started({"state": "current", "checked_at": _ago(minutes=50)}))

    def test_a_current_answer_is_replaced_after_an_hour(self):
        self.assertTrue(self.started({"state": "current", "checked_at": _ago(minutes=70)}))

    def test_a_failed_check_is_not_retried_straight_away(self):
        self.assertFalse(self.started({"state": "error", "checked_at": _ago(minutes=10)}))

    def test_a_failed_check_is_retried_after_half_an_hour(self):
        self.assertTrue(self.started({"state": "error", "checked_at": _ago(minutes=40)}))

    def test_an_error_with_no_recorded_time_is_retried(self):
        self.assertTrue(self.started({"state": "error", "message": "no time recorded"}))

    def test_nothing_checked_yet_checks_at_once(self):
        self.assertTrue(self.started({"state": "idle", "message": ""}))

    def test_an_update_already_found_is_not_asked_for_again(self):
        self.assertFalse(self.started({"state": "available", "checked_at": _ago(hours=5)}))

    def test_a_manual_check_ignores_the_interval(self):
        self.assertTrue(self.started({"state": "current", "checked_at": _ago(minutes=1)}, force=True))


if __name__ == "__main__":
    unittest.main()
