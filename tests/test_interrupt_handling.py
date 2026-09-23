from __future__ import annotations

import signal
import threading
import time
import unittest
from unittest.mock import patch


class FakeServer:
    def __init__(self):
        self.shutdown_calls = 0
        self.shutdown_event = threading.Event()

    def shutdown(self):
        self.shutdown_calls += 1
        self.shutdown_event.set()


class FakeHandler:
    """Stands in for SearchHandler with the same job/cancel attributes."""

    def __init__(self):
        import photo_search

        for _label, lock_attr, job_attr, cancel_attr, _states in photo_search.CANCELLABLE_JOBS:
            setattr(self, lock_attr, threading.Lock())
            setattr(self, job_attr, {"state": "idle", "message": ""})
            setattr(self, cancel_attr, threading.Event())

    def set_running(self, job_attr: str, state: str = "running"):
        setattr(self, job_attr, {"state": state, "message": "working"})


class TestInterruptCancelsRatherThanQuitting(unittest.TestCase):
    """Ctrl+C should stop the batch, not the program.

    Each test drives the real signal handler that main() installs, through the
    same signal.signal call, rather than checking that the code looks right.
    """

    def setUp(self):
        import photo_search

        self.photo_search = photo_search
        self.server = FakeServer()
        self.handler = FakeHandler()
        self.logged: list[str] = []
        self.captured = {}

        def capture_signal(_signum, callback):
            self.captured["on_interrupt"] = callback

        self.patches = [
            patch.object(photo_search, "console_log", side_effect=self.logged.append),
            patch("signal.signal", side_effect=capture_signal),
        ]
        for item in self.patches:
            item.start()
        photo_search.install_interrupt_handler(self.server, self.handler)
        self.on_interrupt = self.captured.get("on_interrupt")
        self.assertIsNotNone(self.on_interrupt, "no interrupt handler was installed")

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def press(self):
        self.on_interrupt(2, None)

    def wait_for_shutdown(self, timeout: float = 2.0) -> bool:
        return self.server.shutdown_event.wait(timeout)

    def test_a_press_while_work_is_running_cancels_it_and_keeps_serving(self):
        self.handler.set_running("semantic_job")

        self.press()

        self.assertTrue(self.handler.semantic_cancel.is_set(),
                        "the running job should have been asked to stop")
        self.assertFalse(self.wait_for_shutdown(0.4),
                         "LensLedger must keep running after cancelling a job")
        joined = " ".join(self.logged)
        self.assertIn("meaning search", joined)
        self.assertIn("still running", joined)

    def test_it_cancels_every_running_job_not_only_the_first(self):
        self.handler.set_running("ocr_job")
        self.handler.set_running("write_tags_job")

        self.press()

        self.assertTrue(self.handler.ocr_cancel.is_set())
        self.assertTrue(self.handler.write_tags_cancel.is_set())

    def test_it_leaves_idle_jobs_alone(self):
        self.handler.set_running("ocr_job")

        self.press()

        self.assertTrue(self.handler.ocr_cancel.is_set())
        self.assertFalse(self.handler.face_scan_cancel.is_set(),
                         "a job that was not running must not be marked cancelled")

    def test_a_press_with_nothing_running_quits(self):
        self.press()

        self.assertTrue(self.wait_for_shutdown(), "with no work to stop, Ctrl+C should quit")

    def test_pressing_again_quits_even_while_work_is_running(self):
        """It must never become something you cannot get out of."""
        self.handler.set_running("face_scan_job")

        self.press()
        self.assertFalse(self.wait_for_shutdown(0.3))

        self.press()
        self.assertTrue(self.wait_for_shutdown(), "a second press must always quit")

    def test_a_much_later_press_cancels_again_rather_than_quitting(self):
        self.handler.set_running("face_scan_job")
        self.press()
        self.handler.face_scan_cancel.clear()

        # Well outside the window, this is a fresh decision, not a double press.
        later = self.photo_search.time.monotonic() + self.photo_search.INTERRUPT_QUIT_WINDOW_SECONDS + 10
        with patch.object(self.photo_search.time, "monotonic", return_value=later):
            self.press()

        self.assertTrue(self.handler.face_scan_cancel.is_set())
        self.assertFalse(self.wait_for_shutdown(0.3),
                         "an unrelated later press should cancel, not quit")

    def test_the_library_scan_counts_as_running_under_its_own_state_name(self):
        """The location scan says 'scanning' where the others say 'running'."""
        self.handler.set_running("library_job", state="scanning")

        self.press()

        self.assertTrue(self.handler.library_cancel.is_set())
        self.assertFalse(self.wait_for_shutdown(0.3))

    def test_shutdown_never_runs_on_the_serving_thread(self):
        """Calling shutdown() from inside serve_forever() would deadlock."""
        calling_threads = []

        def record_thread():
            calling_threads.append(threading.current_thread())
            self.server.shutdown_event.set()

        self.server.shutdown = record_thread
        self.press()
        self.assertTrue(self.server.shutdown_event.wait(2))
        self.assertNotIn(threading.current_thread(), calling_threads,
                         "shutdown must be handed to another thread")


class TestTheRealSignalPath(unittest.TestCase):
    """Raise an actual SIGINT at a real running server and see it survive.

    The tests above call the handler directly. This one goes through the
    operating system and Python's own signal delivery, so it would catch the
    handler not being installed at all.
    """

    def setUp(self):
        import photo_search
        from http.server import ThreadingHTTPServer

        self.photo_search = photo_search
        self.previous = signal.getsignal(signal.SIGINT)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), photo_search.SearchHandler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        self.saved_job = dict(photo_search.SearchHandler.ocr_job)
        photo_search.SearchHandler.ocr_cancel.clear()

    def tearDown(self):
        import photo_search

        signal.signal(signal.SIGINT, self.previous)
        photo_search.SearchHandler.ocr_job = self.saved_job
        photo_search.SearchHandler.ocr_cancel.clear()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def server_answers(self) -> bool:
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/ocr/status", timeout=3
            ) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def test_a_real_sigint_cancels_the_job_and_leaves_the_server_answering(self):
        import photo_search

        self.assertTrue(self.server_answers(), "the server should be up to begin with")

        photo_search.SearchHandler.ocr_job = {"state": "running", "message": "working"}
        photo_search.install_interrupt_handler(self.server, photo_search.SearchHandler)

        signal.raise_signal(signal.SIGINT)
        # Signal handlers run between bytecodes in the main thread.
        for _ in range(50):
            if photo_search.SearchHandler.ocr_cancel.is_set():
                break
            time.sleep(0.02)

        self.assertTrue(photo_search.SearchHandler.ocr_cancel.is_set(),
                        "a real SIGINT should have cancelled the running job")
        self.assertTrue(self.server_answers(),
                        "the server must still be answering after Ctrl+C")


if __name__ == "__main__":
    unittest.main()
