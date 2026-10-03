from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image


def save_with_exif(path: Path, original: str | None = None, modified: str | None = None) -> None:
    exif = Image.Exif()
    if modified:
        exif[0x0132] = modified
    if original:
        exif.get_ifd(0x8769)[0x9003] = original
    Image.new("RGB", (24, 16), (90, 120, 150)).save(path, quality=90, exif=exif)


class CaptureDateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "photos"
        self.library.mkdir()
        self.database = self.root / "library.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def dates(self) -> dict[str, str | None]:
        con = sqlite3.connect(self.database)
        try:
            return dict(con.execute("SELECT filename, capture_date FROM assets"))
        finally:
            con.close()

    def test_a_camera_file_name_with_no_date_takes_its_date_from_exif(self):
        from photo_index import scan_library

        save_with_exif(self.library / "IMG_1234.jpg", original="2021:05:06 07:08:09")

        self.assertEqual(scan_library(self.library, self.database), 0)

        self.assertEqual(self.dates(), {"IMG_1234.jpg": "2021-05-06"})

    def test_the_date_taken_beats_a_date_in_the_folder_name(self):
        from photo_index import scan_library

        folder = self.library / "2023-01-01 imported"
        folder.mkdir()
        save_with_exif(folder / "IMG_0001.jpg", original="2019:12:31 23:00:00")

        scan_library(self.library, self.database)

        self.assertEqual(self.dates(), {"IMG_0001.jpg": "2019-12-31"})

    def test_a_date_in_the_name_beats_the_date_an_editor_last_saved(self):
        from photo_index import scan_library

        save_with_exif(self.library / "2018-03-04 walk.jpg", modified="2024:09:09 10:00:00")
        save_with_exif(self.library / "edited.jpg", modified="2024:09:09 10:00:00")

        scan_library(self.library, self.database)

        self.assertEqual(self.dates(), {"2018-03-04 walk.jpg": "2018-03-04", "edited.jpg": "2024-09-09"})

    def test_an_unset_camera_clock_is_not_a_date(self):
        from photo_index import scan_library

        save_with_exif(self.library / "IMG_0002.jpg", original="0000:00:00 00:00:00")

        scan_library(self.library, self.database)

        self.assertEqual(self.dates(), {"IMG_0002.jpg": None})

    def test_phone_file_names_without_separators_carry_a_date(self):
        from photo_index import capture_date_from_path

        for name, expected in (
            ("IMG_20240102_101010.jpg", "2024-01-02"),
            ("PXL_20231231_235959123.jpg", "2023-12-31"),
            ("20190704_120000.mp4", "2019-07-04"),
            ("Screenshot_20220305-101112.png", "2022-03-05"),
            ("IMG_1234.jpg", None),
            ("scan_123456789012.jpg", None),
            ("IMG_20241399_000000.jpg", None),
        ):
            with self.subTest(name=name):
                self.assertEqual(capture_date_from_path(Path("misc") / name), expected)

    def test_a_library_indexed_before_exif_dates_gets_them_on_the_next_scan(self):
        from photo_index import scan_library

        save_with_exif(self.library / "IMG_5555.jpg", original="2020:02:29 12:00:00")
        scan_library(self.library, self.database)
        con = sqlite3.connect(self.database)
        con.execute("UPDATE assets SET capture_date=NULL, date_scanned=0")
        con.commit()
        con.close()

        scan_library(self.library, self.database)

        self.assertEqual(self.dates(), {"IMG_5555.jpg": "2020-02-29"})
        con = sqlite3.connect(self.database)
        self.assertEqual(con.execute("SELECT date_scanned FROM assets").fetchone()[0], 1)
        con.close()


if __name__ == "__main__":
    unittest.main()
