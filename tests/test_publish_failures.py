from __future__ import annotations

import unittest

from publish_failures import explain_failure

# Messages have the same shape as the ones ExifTool, Windows and LensLedger produce,
# with invented paths.
TEMP_FILE = (
    "Error: Temporary file already exists: C:/Invented/Library/2020/sample.jpg_exiftool_tmp"
)
RENAMING = (
    "Error renaming temporary file to C:/Invented/Library/2020/sample.jpg Warning: "
    "Error updating file time - C:/Invented/Library/2020/sample.jpg"
)
RENAMING_ONLY = "Error renaming temporary file to C:/Invented/Library/2023/sample.jpg"
PERMISSION = "[Errno 13] Permission denied: 'C:\\\\Invented\\\\Library\\\\2010\\\\sample.tiff'"
IN_USE = "[WinError 32] The process cannot access the file because it is being used by another process"
PIXELS = "Pixel verification failed for sample.jpg"
DAMAGED = "Error: Not a valid JPG (looks more like a PNG)"
NOT_PUBLISHABLE = "Not a publishable file type"


class ExplainFailureTests(unittest.TestCase):
    def kind(self, reason, path="Library/sample.jpg"):
        return explain_failure(reason, path)["kind"]

    def test_each_kind_of_failure_is_recognised(self):
        self.assertEqual(self.kind(TEMP_FILE), "temp_file_in_the_way")
        self.assertEqual(self.kind(RENAMING), "file_in_use")
        self.assertEqual(self.kind(RENAMING_ONLY), "file_in_use")
        self.assertEqual(self.kind(PERMISSION, "Library/sample.tiff"), "file_in_use")
        self.assertEqual(self.kind(IN_USE), "file_in_use")
        self.assertEqual(self.kind(PIXELS), "pixel_check")
        self.assertEqual(self.kind(DAMAGED), "damaged_image")
        self.assertEqual(self.kind(NOT_PUBLISHABLE, "Library/sample.gif"), "not_publishable")
        self.assertEqual(self.kind("something nobody has seen before"), "other")
        self.assertEqual(self.kind(""), "other")

    def test_a_leftover_temporary_file_wins_over_other_words_in_the_message(self):
        self.assertEqual(self.kind(TEMP_FILE + " Permission denied"), "temp_file_in_the_way")

    def test_repair_is_offered_only_for_damaged_picture_data(self):
        for reason in (PIXELS, DAMAGED):
            self.assertTrue(explain_failure(reason, "Library/sample.jpg")["repairable"], reason)
        for reason in (TEMP_FILE, RENAMING, RENAMING_ONLY, PERMISSION, IN_USE, NOT_PUBLISHABLE,
                       "something nobody has seen before"):
            self.assertFalse(explain_failure(reason, "Library/sample.jpg")["repairable"], reason)

    def test_repair_is_never_offered_for_anything_but_a_jpeg(self):
        for path in ("Library/sample.heic", "Library/sample.HEIF", "Library/sample.tiff",
                     "Library/sample.png", "Library/sample"):
            self.assertFalse(explain_failure(PIXELS, path)["repairable"], path)
        for path in ("Library/sample.JPG", "Library/sample.jpeg", "Library/a b/sample.Jpg"):
            self.assertTrue(explain_failure(PIXELS, path)["repairable"], path)

    def test_every_recognised_kind_says_what_to_do(self):
        for reason in (TEMP_FILE, RENAMING, PIXELS, DAMAGED, NOT_PUBLISHABLE):
            self.assertGreater(len(explain_failure(reason, "Library/sample.jpg")["hint"]), 40, reason)
        self.assertEqual(explain_failure("something nobody has seen before", "x.jpg")["hint"], "")

    def test_the_hints_name_no_path_or_person(self):
        for reason in (TEMP_FILE, RENAMING, PIXELS, DAMAGED, NOT_PUBLISHABLE):
            hint = explain_failure(reason, "Library/Invented Person/sample.jpg")["hint"]
            self.assertNotIn("Invented", hint)
            self.assertNotIn("sample", hint)


if __name__ == "__main__":
    unittest.main()
