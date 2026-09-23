from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch


class TestActionLog(unittest.TestCase):
    """The Action helper: start, progress, outcome, failures by name."""

    def setUp(self):
        self.lines: list[str] = []
        self.patcher = patch("console_log.log", side_effect=self.lines.append)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_an_action_logs_when_it_starts(self):
        from console_log import Action

        Action("Fill in missing", "every photo with no meaning data")
        self.assertEqual(len(self.lines), 1)
        self.assertIn("Fill in missing: started", self.lines[0])
        self.assertIn("every photo with no meaning data", self.lines[0])

    def test_an_action_that_does_nothing_still_logs_an_outcome(self):
        """Silence after a button press is the thing this prevents."""
        from console_log import Action

        action = Action("Fill in missing")
        action.finish("nothing needed indexing")

        self.assertEqual(len(self.lines), 2)
        self.assertIn("started", self.lines[0])
        self.assertIn("finished", self.lines[1])
        self.assertIn("nothing needed indexing", self.lines[1])

    def test_failures_name_the_file_and_the_reason(self):
        from console_log import Action

        action = Action("Text recognition")
        action.failure("Holidays/beach.jpg", "cannot identify image file")

        self.assertIn("Holidays/beach.jpg", self.lines[-1])
        self.assertIn("cannot identify image file", self.lines[-1])

    def test_every_failure_is_logged_even_in_a_long_run(self):
        """Progress is rate limited; failures never are."""
        from console_log import Action

        action = Action("Meaning search", progress_seconds=3600)
        for index in range(5):
            action.progress(f"{index} done")
            action.failure(f"photo-{index}.jpg", "unreadable")

        failure_lines = [line for line in self.lines if "could not process" in line]
        self.assertEqual(len(failure_lines), 5, "no failure may be rate limited away")

    def test_progress_is_rate_limited_but_can_be_forced(self):
        from console_log import Action

        action = Action("Meaning search", progress_seconds=3600)
        for _ in range(10):
            action.progress("still going")
        self.assertEqual(len([l for l in self.lines if "still going" in l]), 0)

        action.progress("still going", force=True)
        self.assertEqual(len([l for l in self.lines if "still going" in l]), 1)

    def test_cancelling_and_failing_both_record_an_outcome(self):
        from console_log import Action

        cancelled = Action("Write all tags")
        cancelled.cancelled("12 of 40 photos written")
        self.assertIn("stopped after", self.lines[-1])

        self.lines.clear()
        failed = Action("Write all tags")
        failed.failed("disk full")
        self.assertIn("failed after", self.lines[-1])
        self.assertIn("disk full", self.lines[-1])

    def test_an_action_reports_an_outcome_exactly_once(self):
        from console_log import Action

        action = Action("Classify photos")
        action.finish("done")
        action.finish("done again")
        action.failed("too late")

        outcomes = [l for l in self.lines if "finished" in l or "failed" in l]
        self.assertEqual(len(outcomes), 1)

    def test_used_as_a_context_manager_it_always_reports(self):
        from console_log import Action

        with self.assertRaises(ValueError):
            with Action("Classify photos"):
                raise ValueError("model missing")

        self.assertIn("failed after", self.lines[-1])
        self.assertIn("model missing", self.lines[-1])


class TestLogPrivacy(unittest.TestCase):
    def test_the_log_warns_that_it_holds_personal_paths(self):
        from console_log import PRIVACY_NOTICE

        self.assertIn("public", PRIVACY_NOTICE.lower())
        self.assertIn("photo paths", PRIVACY_NOTICE.lower())

    def test_the_log_file_lives_outside_the_repository(self):
        """It holds real paths, so it must not sit anywhere near the code."""
        import app_paths

        log_location = app_paths.log_dir().resolve()
        repository = Path(__file__).resolve().parent.parent
        self.assertFalse(
            str(log_location).startswith(str(repository)),
            f"the log directory {log_location} must not be inside the repository",
        )

    def test_request_logging_does_not_record_names_or_paths(self):
        """The per-request line describes scope, never library content."""
        import photo_search

        body = {
            "mode": "missing",
            "path": "C:/Photos/Family/Wedding/portrait.jpg",
            "name": "Someone Real",
            "names": ["Someone Real"],
            "subject": "a private subject",
        }
        detail = photo_search.describe_action_scope("/api/semantic/start", body)
        for leaked in ("Photos", "portrait.jpg", "Someone Real", "private subject"):
            self.assertNotIn(leaked, detail)

        generic = photo_search.describe_action_scope("/api/person/add", body)
        for leaked in ("Photos", "portrait.jpg", "Someone Real", "private subject"):
            self.assertNotIn(leaked, generic)


class TestLogSurvivesAwkwardConsoles(unittest.TestCase):
    def test_a_line_that_cannot_be_encoded_does_not_stop_the_work(self):
        """A log line must never be the thing that kills a running scan."""
        import console_log

        class RefusesNonAscii:
            def write(self, text):
                text.encode("ascii")

            def flush(self):
                pass

        with patch("sys.stdout", RefusesNonAscii()):
            console_log.log("Fill in missing: finished — nothing needed indexing")

    def test_em_dashes_reach_the_file_intact(self):
        import tempfile
        import console_log

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"LENSLEDGER_DATA_DIR": directory}):
                console_log.shutdown()
                console_log.log("Fill in missing: finished — nothing needed indexing")
                console_log.shutdown()
                written = (Path(directory) / "Logs" / "LensLedger.log").read_text(encoding="utf-8")

        self.assertIn("—", written)
        self.assertIn(console_log.PRIVACY_NOTICE, written)


class TestEveryActionIsNamed(unittest.TestCase):
    """A new state-changing route must not be able to go unlogged unnoticed."""

    # Routes that read state or are too chatty to be worth a line. Adding to
    # this list is a deliberate choice, which is the point of listing them.
    DELIBERATELY_QUIET = {
        "/api/dev/set",
        "/api/library/browse",
        "/api/publish/preview",
        "/api/reveal-file",
        "/api/reveal-path",
        "/api/update/check",
    }

    def test_every_post_route_is_either_named_or_deliberately_quiet(self):
        import photo_search

        source = Path(photo_search.__file__).with_suffix(".py").read_text(encoding="utf-8")
        routes = set(re.findall(r'if route == "(/api/[a-z0-9/-]+)"', source))
        self.assertGreater(len(routes), 50, "route scan found suspiciously few routes")

        unaccounted = routes - set(photo_search.ACTION_LABELS) - self.DELIBERATELY_QUIET
        self.assertEqual(
            unaccounted, set(),
            "these actions would happen with nothing in the log — add them to "
            "ACTION_LABELS, or to DELIBERATELY_QUIET if they only read state: "
            f"{sorted(unaccounted)}",
        )

    def test_no_stale_labels(self):
        import photo_search

        source = Path(photo_search.__file__).with_suffix(".py").read_text(encoding="utf-8")
        routes = set(re.findall(r'if route == "(/api/[a-z0-9/-]+)"', source))
        stale = set(photo_search.ACTION_LABELS) - routes
        self.assertEqual(stale, set(), f"labels for routes that no longer exist: {sorted(stale)}")

    def test_the_three_meaning_search_buttons_are_named_apart(self):
        """The reported bug: a run gave no sign of which button started it."""
        import photo_search

        names = photo_search.SEMANTIC_ACTION_NAMES
        self.assertEqual(len(set(names.values())), 3, "each mode needs its own name")
        self.assertIn("Fill in missing", names.values())

        detail = photo_search.describe_action_scope("/api/semantic/start", {"mode": "missing"})
        self.assertIn("fill in missing", detail.lower())


if __name__ == "__main__":
    unittest.main()


class TestNoInlineStylesOrScripts(unittest.TestCase):
    """The Content-Security-Policy forbids them, so any that exist are blocked.

    A blocked style is not a warning the user ever sees -- the rule simply does
    not apply, and the page quietly renders wrong.
    """

    def served_html(self) -> str:
        import photo_search

        return Path(photo_search.__file__).with_suffix(".py").read_text(encoding="utf-8")

    def test_no_style_attributes_in_any_served_page(self):
        offenders = re.findall(r'style="[^"]*"', self.served_html())
        self.assertEqual(
            offenders, [],
            "the policy omits 'unsafe-inline', so these are blocked and never "
            f"take effect -- move them to a stylesheet: {offenders}",
        )

    def test_the_policy_still_forbids_inline_styles(self):
        """If this ever gains 'unsafe-inline', the test above stops meaning anything."""
        source = self.served_html()
        index = source.index("Content-Security-Policy")
        policy = source[index:index + 500]
        self.assertIn("style-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)
