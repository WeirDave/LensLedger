from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SettingsValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"LENSLEDGER_DATA_DIR": self.temporary.name})
        self.environment.start()
        import settings_config

        self.settings_config = settings_config
        self.file = Path(self.temporary.name) / "settings.json"

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_an_unacceptable_value_is_refused_and_nothing_is_written(self):
        self.settings_config.save_settings({"startup": {"terminal_tab": False}})
        before = self.file.read_text(encoding="utf-8")

        for bad in ({"watch": {"interval_minutes": 2}}, {"watch": {"interval_minutes": "soon"}},
                    {"watch": {"enabled": "yes"}}, {"scan": {"semantic_batch_size": 0}},
                    {"publish": {"backup_keep_days": -1}}, {"ingest": {"interval_minutes": 99999}},
                    {"watch": 5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.settings_config.save_settings(bad)
        self.assertEqual(self.file.read_text(encoding="utf-8"), before)

    def test_the_error_names_the_setting_and_what_is_allowed(self):
        with self.assertRaises(ValueError) as caught:
            self.settings_config.validate_settings({"watch": {"interval_minutes": 2}})
        self.assertIn("Folder watching check interval", str(caught.exception))
        self.assertIn("5 to 1440", str(caught.exception))

    def test_numbers_typed_as_text_are_stored_as_numbers(self):
        self.settings_config.save_settings({"watch": {"interval_minutes": "15"}})

        self.assertEqual(self.settings_config.load_settings()["watch"]["interval_minutes"], 15)

    def test_saving_part_of_the_settings_keeps_the_rest_as_stored(self):
        self.settings_config.save_settings({"startup": {"terminal_tab": False}})

        self.settings_config.save_settings({"watch": {"interval_minutes": 20}})

        loaded = self.settings_config.load_settings()
        self.assertIs(loaded["startup"]["terminal_tab"], False)
        self.assertEqual(loaded["watch"]["interval_minutes"], 20)

    def test_settings_that_are_not_known_are_kept(self):
        self.settings_config.save_settings({"future": {"option": 3}, "watch": {"extra": "x"}})

        loaded = self.settings_config.load_settings()
        self.assertEqual(loaded["future"], {"option": 3})
        self.assertEqual(loaded["watch"]["extra"], "x")

    def test_a_damaged_file_loads_with_defaults_for_the_damaged_values(self):
        self.file.write_text(json.dumps({
            "watch": {"interval_minutes": "abc", "enabled": False},
            "ingest": 7,
            "publish": {"backup_keep_days": "x", "classify_threshold": 9},
            "scan": {"semantic_batch_size": 5000},
        }), encoding="utf-8")

        loaded = self.settings_config.load_settings()

        defaults = self.settings_config.DEFAULTS
        self.assertEqual(loaded["watch"]["interval_minutes"], defaults["watch"]["interval_minutes"])
        self.assertIs(loaded["watch"]["enabled"], False, "a valid value beside a damaged one is kept")
        self.assertEqual(loaded["ingest"], defaults["ingest"])
        self.assertEqual(loaded["publish"]["backup_keep_days"], defaults["publish"]["backup_keep_days"])
        self.assertEqual(loaded["publish"]["classify_threshold"], defaults["publish"]["classify_threshold"])
        self.assertEqual(loaded["scan"]["semantic_batch_size"], defaults["scan"]["semantic_batch_size"])

    def test_the_auto_import_interval_has_a_default(self):
        self.assertEqual(self.settings_config.load_settings()["ingest"]["interval_minutes"], 10)

    def test_the_settings_written_by_the_page_are_accepted(self):
        """What settings.js sends: a full copy including values it never edits."""
        page = self.settings_config.load_settings()
        page["watch"]["interval_minutes"] = 30
        page["publish"].update({"embed": True, "sidecar_types": {"raw": True}, "backup_keep_days": 0,
                                "backup_max_gb": 0})
        page["ingest"]["interval_minutes"] = 10

        self.settings_config.save_settings(page)

        self.assertEqual(self.settings_config.load_settings()["watch"]["interval_minutes"], 30)


class StartupUsesTheSavedIntervals(unittest.TestCase):
    def test_auto_import_is_started_with_its_configured_interval(self):
        import photo_search

        source = Path(photo_search.__file__).read_text(encoding="utf-8")
        start = source.index("pipeline = IngestPipeline(")
        self.assertIn('interval_minutes=int(ingest_cfg["interval_minutes"])', source[start:start + 500])


if __name__ == "__main__":
    unittest.main()
