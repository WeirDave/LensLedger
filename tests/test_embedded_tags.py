from __future__ import annotations

import io
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

import photo_index
from photo_index import EMBEDDED_TAGS_VERSION, extract_embedded_tags, scan_library


XMP = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:MicrosoftPhoto="http://ns.microsoft.com/photo/1.0/"'
    ' xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/">'
    '<dc:subject><rdf:Bag><rdf:li>Harbour</rdf:li><rdf:li>Rock &amp; Roll</rdf:li></rdf:Bag></dc:subject>'
    '<MicrosoftPhoto:LastKeywordXMP><rdf:Bag><rdf:li>harbour</rdf:li><rdf:li>Ferry</rdf:li></rdf:Bag>'
    '</MicrosoftPhoto:LastKeywordXMP>'
    '<Iptc4xmpExt:PersonInImage><rdf:Bag><rdf:li>Otto Brandt</rdf:li><rdf:li>Lena Vos</rdf:li>'
    '<rdf:li>otto brandt</rdf:li></rdf:Bag></Iptc4xmpExt:PersonInImage>'
    '</rdf:Description></rdf:RDF></x:xmpmeta>'
).encode("utf-8")


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def _iptc_app13(keywords: list[bytes], utf8_marker: bool) -> bytes:
    iim = b""
    if utf8_marker:
        iim += b"\x1c\x01\x5a" + (3).to_bytes(2, "big") + b"\x1b%G"
    for keyword in keywords:
        iim += b"\x1c\x02\x19" + len(keyword).to_bytes(2, "big") + keyword
    resource = b"8BIM" + (0x0404).to_bytes(2, "big") + b"\x00\x00" + len(iim).to_bytes(4, "big") + iim
    if len(iim) % 2:
        resource += b"\x00"
    return _segment(0xED, b"Photoshop 3.0\x00" + resource)


def _jpeg(extra_segments: bytes) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), (40, 90, 150)).save(buffer, "JPEG", quality=90)
    data = buffer.getvalue()
    return data[:2] + extra_segments + data[2:]


class TestEmbeddedTagReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_every_keyword_field_and_people(self):
        photo = self.root / "a.jpg"
        photo.write_bytes(_jpeg(
            _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + XMP)
            + _iptc_app13(["Café".encode("utf-8"), b"Ferry"], utf8_marker=True)
        ))
        keywords, people = extract_embedded_tags(photo)
        self.assertEqual(keywords, ["Harbour", "Rock & Roll", "Café", "Ferry"])
        self.assertEqual(people, ["Otto Brandt", "Lena Vos"])

    def test_iptc_without_a_charset_marker_is_read_as_latin1(self):
        photo = self.root / "b.jpg"
        photo.write_bytes(_jpeg(_iptc_app13(["Café".encode("cp1252")], utf8_marker=False)))
        self.assertEqual(extract_embedded_tags(photo), (["Café"], []))

    def test_heic_xmp_is_found_anywhere_in_the_file(self):
        photo = self.root / "c.heic"
        photo.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 50_000 + XMP + b"\x00" * 64)
        keywords, people = extract_embedded_tags(photo)
        self.assertEqual(keywords, ["Harbour", "Rock & Roll", "Ferry"])
        self.assertEqual(people, ["Otto Brandt", "Lena Vos"])

    def test_files_without_tags_and_other_formats_return_nothing(self):
        plain = self.root / "d.jpg"
        plain.write_bytes(_jpeg(b""))
        other = self.root / "e.png"
        Image.new("RGB", (4, 4)).save(other)
        self.assertEqual(extract_embedded_tags(plain), ([], []))
        self.assertEqual(extract_embedded_tags(other), ([], []))


class TestEmbeddedTagBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = Path(self.tmp.name) / "photos"
        self.library.mkdir()
        self.photo = self.library / "2026-08-09 harbour.jpg"
        self.photo.write_bytes(_jpeg(
            _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + XMP)
            + _iptc_app13([b"Pier"], utf8_marker=True)
        ))
        self.database = Path(self.tmp.name) / "library.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def _embedded(self, con):
        rows = con.execute(
            """SELECT at.source, t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
               WHERE at.source IN ('embedded_xmp','embedded_people') ORDER BY at.source, t.name"""
        ).fetchall()
        return {(source, name) for source, name in rows}

    def test_a_new_file_stores_keywords_and_people(self):
        scan_library(self.library, self.database, quiet=True)
        con = sqlite3.connect(self.database)
        try:
            embedded = self._embedded(con)
            self.assertIn(("embedded_xmp", "Pier"), embedded)
            self.assertIn(("embedded_xmp", "Ferry"), embedded)
            self.assertIn(("embedded_people", "Lena Vos"), embedded)
            self.assertEqual(con.execute("SELECT tags_scanned FROM assets").fetchone()[0],
                             EMBEDDED_TAGS_VERSION)
        finally:
            con.close()

    def test_an_unchanged_file_indexed_by_the_older_reader_is_read_again_once(self):
        scan_library(self.library, self.database, quiet=True)
        con = sqlite3.connect(self.database)
        con.execute("DELETE FROM asset_tags WHERE source='embedded_people'")
        con.execute("UPDATE assets SET tags_scanned=0")
        con.commit()
        con.close()
        before = self.photo.read_bytes()

        scan_library(self.library, self.database, quiet=True)

        con = sqlite3.connect(self.database)
        try:
            self.assertIn(("embedded_people", "Otto Brandt"), self._embedded(con))
            self.assertEqual(con.execute("SELECT tags_scanned FROM assets").fetchone()[0],
                             EMBEDDED_TAGS_VERSION)
        finally:
            con.close()
        self.assertEqual(self.photo.read_bytes(), before)


class TestEmbeddedTagReaderStaysFast(unittest.TestCase):
    """A damaged or crafted file must not make the reader's time grow with the
    square of its size, and a large file must not be held in memory whole."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_thousands_of_unclosed_tags_are_read_in_linear_time(self):
        hostile = (b"<x:xmpmeta>" + b"<dc:subject>" * 60_000
                   + b"<Iptc4xmpExt:PersonInImage>" * 20_000
                   + b"<MicrosoftPhoto:LastKeywordXMP>" * 20_000
                   + b"<rdf:li>" * 60_000 + b"</x:xmpmeta>")
        photo = self.root / "hostile.png"
        photo.write_bytes(hostile)
        started = time.monotonic()
        keywords, people = extract_embedded_tags(photo)
        self.assertEqual((keywords, people), ([], []))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_an_unclosed_tag_before_a_good_one_does_not_hide_it(self):
        data = (b"<x:xmpmeta><dc:subject><dc:subject><rdf:Bag><rdf:li>"
                b"Harbour</rdf:li></rdf:Bag></dc:subject></x:xmpmeta>")
        photo = self.root / "damaged.webp"
        photo.write_bytes(b"RIFF" + data)
        self.assertEqual(extract_embedded_tags(photo)[0], ["Harbour"])

    def test_a_prefix_without_a_closing_tag_does_not_hide_another_prefix(self):
        data = (b"<x:xmpmeta><a:PersonInImage><b:PersonInImage><rdf:Bag>"
                b"<rdf:li>Otto Brandt</rdf:li></rdf:Bag></b:PersonInImage></x:xmpmeta>")
        photo = self.root / "prefixes.png"
        photo.write_bytes(b"\x89PNG" + data)
        self.assertEqual(extract_embedded_tags(photo)[1], ["Otto Brandt"])

    def test_the_packet_is_found_across_read_boundaries(self):
        chunk = photo_index.XMP_READ_CHUNK
        for padding in (chunk - 5, chunk - 1, chunk, chunk + 3):
            with self.subTest(padding=padding):
                photo = self.root / f"edge{padding}.tif"
                photo.write_bytes(b"\x00" * padding + XMP + b"\x00" * 10)
                keywords, people = extract_embedded_tags(photo)
                self.assertEqual(keywords, ["Harbour", "Rock & Roll", "Ferry"])
                self.assertEqual(people, ["Otto Brandt", "Lena Vos"])

    def test_a_packet_that_never_ends_is_given_up_on(self):
        photo = self.root / "endless.tif"
        original = photo_index.XMP_PACKET_LIMIT
        photo_index.XMP_PACKET_LIMIT = 2 * photo_index.XMP_READ_CHUNK
        try:
            photo.write_bytes(b"<x:xmpmeta><dc:subject>" + b"x" * (5 * photo_index.XMP_READ_CHUNK))
            self.assertEqual(extract_embedded_tags(photo), ([], []))
        finally:
            photo_index.XMP_PACKET_LIMIT = original


if __name__ == "__main__":
    unittest.main()
