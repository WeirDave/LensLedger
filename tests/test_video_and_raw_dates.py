from __future__ import annotations

import datetime as dt
import io
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

QUICKTIME_EPOCH = dt.datetime(1904, 1, 1, tzinfo=dt.timezone.utc)


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mvhd(utc: dt.datetime | None, version: int = 0) -> bytes:
    seconds = 0 if utc is None else int((utc - QUICKTIME_EPOCH).total_seconds())
    if version == 1:
        body = struct.pack(">B3xQQII", 1, seconds, seconds, 1000, 0)
    else:
        body = struct.pack(">B3xIIII", 0, seconds, seconds, 1000, 0)
    return box(b"mvhd", body + b"\0" * 80)


def apple_meta(stamp: str, full_box: bool) -> bytes:
    name = b"com.apple.quicktime.creationdate"
    keys = box(b"keys", struct.pack(">BxxxI", 0, 1) + struct.pack(">I4s", 8 + len(name), b"mdta") + name)
    value = struct.pack(">II", 1, 0) + stamp.encode()
    ilst = box(b"ilst", box(struct.pack(">I", 1), box(b"data", value)))
    hdlr = box(b"hdlr", b"\0" * 24)
    return box(b"meta", (b"\0\0\0\0" if full_box else b"") + hdlr + keys + ilst)


def movie(path: Path, utc: dt.datetime | None = None, apple: str | None = None,
          full_box: bool = False, version: int = 0, moov_last: bool = False) -> Path:
    moov_children = mvhd(utc, version) + (apple_meta(apple, full_box) if apple else b"")
    moov = box(b"moov", moov_children)
    mdat = box(b"mdat", b"\x11" * 4096)
    ftyp = box(b"ftyp", b"isom\0\0\2\0isomiso2")
    path.write_bytes(ftyp + (mdat + moov if moov_last else moov + mdat))
    return path


def ifd(order: str, entries: list[tuple[int, int, int, bytes]], base: int) -> bytes:
    """One TIFF directory at file offset `base`; long values are appended after it."""
    table = struct.pack(order + "H", len(entries))
    extra = b""
    position = base + 2 + 12 * len(entries) + 4
    for tag, kind, count, payload in entries:
        if len(payload) <= 4:
            value = payload.ljust(4, b"\0")
        else:
            value = struct.pack(order + "I", position + len(extra))
            extra += payload
        table += struct.pack(order + "HHI", tag, kind, count) + value
    return table + struct.pack(order + "I", 0) + extra


def ascii_entry(tag: int, text: str):
    data = text.encode("ascii") + b"\0"
    return (tag, 2, len(data), data)


def tiff(order: str = "<", magic: bytes = b"*\0", exif_date: str | None = None,
         main_date: str | None = None, exif_only: bool = False) -> bytes:
    mark = b"II" if order == "<" else b"MM"
    if order == ">" and magic == b"*\0":
        magic = b"\0*"
    exif_entries = [ascii_entry(0x9003, exif_date)] if exif_date else []
    if exif_only:  # a Canon CR3 CMT2 block: the Exif directory stands alone
        return mark + magic + struct.pack(order + "I", 8) + ifd(order, exif_entries, 8)
    main_entries = [ascii_entry(0x0132, main_date)] if main_date else []
    pointer = (0x8769, 4, 1, struct.pack(order + "I", 0))
    first = ifd(order, main_entries + [pointer], 8)
    second_at = 8 + len(first)
    pointer = (0x8769, 4, 1, struct.pack(order + "I", second_at))
    first = ifd(order, main_entries + [pointer], 8)
    return mark + magic + struct.pack(order + "I", 8) + first + ifd(order, exif_entries, second_at)


class VideoDateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def dates(self, path):
        from media_dates import video_dates

        return video_dates(path)

    def test_the_movie_header_gives_the_utc_date(self):
        path = movie(self.root / "a.mp4", utc=dt.datetime(2024, 7, 4, 12, 30, tzinfo=dt.timezone.utc))

        self.assertEqual(self.dates(path), (None, "2024-07-04"))

    def test_a_64_bit_header_and_a_movie_box_after_the_media_are_read(self):
        path = movie(self.root / "a.mov", utc=dt.datetime(2023, 1, 2, 3, 4, tzinfo=dt.timezone.utc),
                     version=1, moov_last=True)

        self.assertEqual(self.dates(path), (None, "2023-01-02"))

    def test_an_iphone_wall_clock_date_is_kept_apart_from_the_utc_one(self):
        # 9:15 pm on the 4th at UTC-7 is already the 5th in UTC.
        for full_box in (False, True):
            with self.subTest(full_box=full_box):
                path = movie(self.root / f"p{full_box}.mov", apple="2024-07-04T21:15:03-0700",
                             utc=dt.datetime(2024, 7, 5, 4, 15, 3, tzinfo=dt.timezone.utc), full_box=full_box)

                self.assertEqual(self.dates(path), ("2024-07-04", "2024-07-05"))

    def test_an_unset_clock_gives_no_date(self):
        unset = movie(self.root / "unset.mp4", utc=None)
        epoch = movie(self.root / "epoch.mp4", utc=QUICKTIME_EPOCH + dt.timedelta(days=1))

        self.assertEqual((self.dates(unset), self.dates(epoch)), ((None, None), (None, None)))

    def test_a_file_that_is_not_a_movie_gives_no_date_and_does_not_raise(self):
        junk = self.root / "junk.mp4"
        junk.write_bytes(b"\xff" * 64)
        empty = self.root / "empty.mp4"
        empty.write_bytes(b"")
        looping = self.root / "loop.mp4"
        looping.write_bytes(struct.pack(">I4s", 0, b"free") * 3)

        for path in (junk, empty, looping, self.root / "missing.mp4"):
            with self.subTest(path=path.name):
                self.assertEqual(self.dates(path), (None, None))

    def test_the_local_date_beats_a_file_name_which_beats_the_utc_date(self):
        from photo_index import capture_date_for

        utc = dt.datetime(2024, 7, 5, 4, 0, tzinfo=dt.timezone.utc)
        local = movie(self.root / "IMG_9999.mov", apple="2024-07-04T21:00:00-0700", utc=utc)
        named = movie(self.root / "2022-03-03 clip.mp4", utc=utc)
        bare = movie(self.root / "clip.mp4", utc=utc)

        self.assertEqual([capture_date_for(p) for p in (local, named, bare)],
                         ["2024-07-04", "2022-03-03", "2024-07-05"])


class RawDateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def date(self, name: str, data: bytes):
        from media_dates import raw_date

        path = self.root / name
        path.write_bytes(data)
        return raw_date(path)

    def test_tiff_based_raws_give_the_date_taken_in_either_byte_order(self):
        for name, order, magic in (
            ("a.dng", "<", b"*\0"), ("b.nef", ">", b"*\0"),
            ("c.orf", "<", b"RO"), ("d.rw2", "<", b"U\0"),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.date(name, tiff(order, magic, exif_date="2021:09:08 07:06:05")), "2021-09-08")

    def test_the_file_date_is_used_only_when_no_date_taken_exists(self):
        self.assertEqual(self.date("a.dng", tiff(main_date="2020:01:02 03:04:05")), "2020-01-02")
        self.assertEqual(
            self.date("b.dng", tiff(exif_date="2019:05:06 07:08:09", main_date="2024:01:02 03:04:05")), "2019-05-06")

    def test_a_fujifilm_raf_reads_the_jpeg_it_wraps(self):
        exif = Image.Exif()
        exif.get_ifd(0x8769)[0x9003] = "2018:11:12 13:14:15"
        jpeg = io.BytesIO()
        Image.new("RGB", (16, 16)).save(jpeg, format="JPEG", exif=exif)
        header = b"FUJIFILMCCD-RAW 0201FF129502".ljust(84, b"\0") + struct.pack(">II", 160, len(jpeg.getvalue()))
        data = header.ljust(160, b"\0") + jpeg.getvalue()

        self.assertEqual(self.date("a.raf", data), "2018-11-12")

    def test_a_canon_cr3_reads_its_cmt2_exif_block(self):
        blob = tiff(exif_date="2022:06:07 08:09:10", exif_only=True)
        cmt2 = struct.pack(">I4s", 8 + len(blob), b"CMT2") + blob
        data = box(b"ftyp", b"crx \0\0\0\1crx isom") + box(b"moov", box(b"uuid", b"\0" * 16 + cmt2)) + box(b"mdat", b"\0" * 64)

        self.assertEqual(self.date("a.cr3", data), "2022-06-07")

    def test_the_header_beats_a_date_in_a_raw_files_name(self):
        from photo_index import capture_date_for

        path = self.root / "2010-01-01 shoot.dng"
        path.write_bytes(tiff(exif_date="2021:09:08 07:06:05"))
        named_only = self.root / "2010-02-02 shoot.dng"
        named_only.write_bytes(b"\xff" * 40)

        self.assertEqual((capture_date_for(path), capture_date_for(named_only)), ("2021-09-08", "2010-02-02"))

    def test_damaged_raws_give_no_date_and_do_not_raise(self):
        truncated = tiff(exif_date="2021:09:08 07:06:05")[:20]
        self.assertEqual(
            [self.date("t.dng", truncated), self.date("e.dng", b""), self.date("j.nef", b"\xff" * 40),
             self.date("z.dng", tiff(exif_date="0000:00:00 00:00:00"))],
            [None, None, None, None])


class ScanDatesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "photos"
        self.library.mkdir()
        self.database = self.root / "library.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def dates(self):
        con = sqlite3.connect(self.database)
        try:
            return dict(con.execute("SELECT filename, capture_date FROM assets"))
        finally:
            con.close()

    def test_a_scan_dates_videos_and_raw_files_and_an_older_library_is_redated(self):
        from photo_index import CAPTURE_DATE_VERSION, scan_library

        movie(self.library / "MVI_0001.mov", utc=dt.datetime(2024, 7, 4, 12, tzinfo=dt.timezone.utc))
        (self.library / "RAW_0001.dng").write_bytes(tiff(exif_date="2021:09:08 07:06:05"))
        scan_library(self.library, self.database)
        expected = {"MVI_0001.mov": "2024-07-04", "RAW_0001.dng": "2021-09-08"}
        self.assertEqual(self.dates(), expected)

        # a library last scanned by 1.17, when these files had no date
        con = sqlite3.connect(self.database)
        con.execute("UPDATE assets SET capture_date=NULL, date_scanned=1")  # 1.17 stored version 1
        con.commit()
        con.close()
        scan_library(self.library, self.database)

        self.assertEqual(self.dates(), expected)
        con = sqlite3.connect(self.database)
        self.assertEqual(con.execute("SELECT MIN(date_scanned) FROM assets").fetchone()[0], CAPTURE_DATE_VERSION)
        con.close()


if __name__ == "__main__":
    unittest.main()
