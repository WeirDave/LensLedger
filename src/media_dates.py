"""The date inside a video or a camera RAW file, read without a decoder.

Pillow cannot open these, and ExifTool is bundled for Windows only, so the
two containers that matter are read directly: the ISO/QuickTime box layout
(MP4, MOV, M4V, 3GP and Canon CR3) and TIFF with its camera variants (DNG,
CR2, NEF, ARW, ORF, RW2), plus Fujifilm RAF, which wraps a JPEG. Only box
headers and a few directory entries are read; nothing is decoded.

Every function returns an ISO date (YYYY-MM-DD) or None, and never raises
for a damaged or unfamiliar file.
"""

from __future__ import annotations

import datetime as dt
import io
import mmap
import re
import struct
from pathlib import Path

_QUICKTIME_EPOCH = dt.datetime(1904, 1, 1, tzinfo=dt.timezone.utc)
_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")
_EXIF_DATE = re.compile(r"^\s*(\d{4})[:-](\d{2})[:-](\d{2})")
_CREATION_DATE_KEY = b"com.apple.quicktime.creationdate"
_MAX_BOXES = 4096  # bounds a damaged file that loops


def _valid(year: int, month: int, day: int) -> str | None:
    try:
        value = dt.date(year, month, day)
    except ValueError:
        return None
    # A clock that was never set gives 0000, 1904 or 1970; a date in the
    # future is a wrong clock too. Neither is the day the media was made.
    if value.year < 1971 or value > dt.date.today() + dt.timedelta(days=2):
        return None
    return value.isoformat()


def _from_text(pattern: re.Pattern, text: str) -> str | None:
    match = pattern.match(text)
    return _valid(int(match[1]), int(match[2]), int(match[3])) if match else None


# --- ISO base media / QuickTime ------------------------------------------------

def _boxes(handle, start: int, end: int):
    """(type, body_start, box_end) for each box between start and end."""
    position = start
    for _ in range(_MAX_BOXES):
        if position + 8 > end:
            return
        handle.seek(position)
        header = handle.read(16)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header[:8])
        body = position + 8
        if size == 1:
            if len(header) < 16:
                return
            size = struct.unpack(">Q", header[8:16])[0]
            body = position + 16
        elif size == 0:
            size = end - position
        if size < body - position:
            return
        box_end = min(position + size, end)
        yield kind, body, box_end
        position = box_end


def _find_box(handle, start: int, end: int, kind: bytes):
    for found, body, box_end in _boxes(handle, start, end):
        if found == kind:
            return body, box_end
    return None


def _apple_creation_date(handle, moov_body: int, moov_end: int) -> str | None:
    """The local capture time iPhones (and anything using the same atoms) store.

    mvhd holds UTC, so a video shot late in the evening west of Greenwich is
    dated the next day there. This key carries the wall-clock time and its
    offset, which is the date the person would give.
    """
    meta = _find_box(handle, moov_body, moov_end, b"meta")
    if not meta:
        return None
    body, end = meta
    handle.seek(body)
    head = handle.read(8)
    # ISO says meta is a full box (four version/flags bytes first); QuickTime
    # writes it as a plain container. A real child box header tells which.
    if len(head) < 8 or head[4:8] not in (b"hdlr", b"keys", b"ilst", b"free"):
        body += 4
    keys = _find_box(handle, body, end, b"keys")
    ilst = _find_box(handle, body, end, b"ilst")
    if not keys or not ilst:
        return None
    handle.seek(keys[0] + 4)  # version/flags
    count = struct.unpack(">I", handle.read(4))[0]
    names: list[bytes] = []
    for _ in range(min(count, 256)):
        entry = handle.read(8)
        if len(entry) < 8:
            return None
        size = struct.unpack(">I", entry[:4])[0]
        if size < 8 or size > 1024:
            return None
        names.append(handle.read(size - 8))
    if _CREATION_DATE_KEY not in names:
        return None
    wanted = struct.pack(">I", names.index(_CREATION_DATE_KEY) + 1)
    for item, item_body, item_end in _boxes(handle, ilst[0], ilst[1]):
        if item != wanted:
            continue
        data = _find_box(handle, item_body, item_end, b"data")
        if not data:
            return None
        handle.seek(data[0] + 8)  # type indicator and locale
        text = handle.read(min(data[1] - data[0] - 8, 64)).decode("utf-8", "ignore")
        return _from_text(_ISO_DATE, text)
    return None


def video_dates(path: Path) -> tuple[str | None, str | None]:
    """(local, utc) capture dates of an MP4/MOV/M4V/3GP.

    `local` is the wall-clock date when the file records one (iPhone
    videos); `utc` is the movie header's creation time, which is UTC and so
    can be a day off for the person who shot it. A caller prefers `local`,
    then a date in the file name, then `utc`.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            moov = _find_box(handle, 0, size, b"moov")
            if not moov:
                return None, None
            local = _apple_creation_date(handle, *moov)
            mvhd = _find_box(handle, moov[0], moov[1], b"mvhd")
            if not mvhd:
                return local, None
            handle.seek(mvhd[0])
            raw = handle.read(12)
            if len(raw) < 8:
                return local, None
            # version 1 stores 64-bit times, version 0 stores 32-bit ones
            seconds = struct.unpack(">Q", raw[4:12])[0] if raw[0] == 1 and len(raw) == 12 \
                else struct.unpack(">I", raw[4:8])[0]
            if not seconds:
                return local, None
            created = _QUICKTIME_EPOCH + dt.timedelta(seconds=seconds)
            return local, _valid(created.year, created.month, created.day)
    except (OSError, ValueError, struct.error, OverflowError):
        return None, None


# --- TIFF and the camera formats built on it -----------------------------------

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def _ifd(data, order: str, offset: int) -> dict[int, tuple[int, int, int]]:
    """tag -> (type, count, position of its value) for one directory."""
    entries: dict[int, tuple[int, int, int]] = {}
    if offset < 8 or offset + 2 > len(data):
        return entries
    (count,) = struct.unpack_from(order + "H", data, offset)
    for index in range(min(count, 512)):
        at = offset + 2 + index * 12
        if at + 12 > len(data):
            break
        tag, kind, number = struct.unpack_from(order + "HHI", data, at)
        length = _TYPE_SIZE.get(kind, 1) * number
        entries[tag] = (kind, number, at + 8 if length <= 4 else struct.unpack_from(order + "I", data, at + 8)[0])
    return entries


def _long(data, order: str, entry: tuple[int, int, int]) -> int | None:
    kind, _count, position = entry
    if position + 4 > len(data) or kind not in (3, 4, 9):
        return None
    return struct.unpack_from(order + ("H" if kind == 3 else "I"), data, position)[0]


def _ascii(data, entry: tuple[int, int, int] | None) -> str:
    if not entry or entry[0] != 2:
        return ""
    _kind, count, position = entry
    return bytes(data[position:position + min(count, 64)]).split(b"\0")[0].decode("ascii", "ignore")


def _tiff_date(data, base: int = 0) -> str | None:
    """DateTimeOriginal (else digitized, else the file's DateTime) from a TIFF block.

    The magic number after the byte-order mark is not checked: ORF and RW2
    use their own and are otherwise ordinary TIFF directories.
    """
    block = memoryview(data)[base:]
    if len(block) < 8 or bytes(block[:2]) not in (b"II", b"MM"):
        return None
    order = "<" if bytes(block[:2]) == b"II" else ">"
    (first,) = struct.unpack_from(order + "I", block, 4)
    main = _ifd(block, order, first)
    exif: dict[int, tuple[int, int, int]] = {}
    if 0x8769 in main:
        pointer = _long(block, order, main[0x8769])
        exif = _ifd(block, order, pointer) if pointer else {}
    for source, tag in ((exif, 0x9003), (exif, 0x9004), (main, 0x0132)):
        found = _from_text(_EXIF_DATE, _ascii(block, source.get(tag)))
        if found:
            return found
    return None


def raw_date(path: Path) -> str | None:
    """Capture date of a camera RAW file, or None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(112)
            if head.startswith(b"FUJIFILMCCD-RAW"):
                offset, length = struct.unpack(">II", head[84:92])
                handle.seek(offset)
                return _jpeg_date(handle.read(min(length, 8_000_000)))
            if head[4:8] == b"ftyp":
                return _cr3_date(handle)
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
                return _tiff_date(view)
    except (OSError, ValueError, struct.error):
        return None


def _jpeg_date(blob: bytes) -> str | None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(blob)) as image:
            exif = image.getexif()
            details = exif.get_ifd(0x8769)
            for value in (details.get(0x9003), details.get(0x9004), exif.get(0x0132)):
                found = _from_text(_EXIF_DATE, str(value or ""))
                if found:
                    return found
    except (OSError, ValueError, SyntaxError):
        pass
    return None


def _cr3_date(handle) -> str | None:
    """Canon CR3 keeps an Exif directory in a 'CMT2' box inside moov."""
    handle.seek(0)
    head = handle.read(4_000_000)
    index = head.find(b"CMT2")
    if index < 4:
        return None
    size = struct.unpack(">I", head[index - 4:index])[0]
    blob = head[index + 4:index - 4 + size]
    if len(blob) < 8 or blob[:2] not in (b"II", b"MM"):
        return None
    order = "<" if blob[:2] == b"II" else ">"
    (first,) = struct.unpack_from(order + "I", blob, 4)
    exif = _ifd(blob, order, first)
    for tag in (0x9003, 0x9004):
        found = _from_text(_EXIF_DATE, _ascii(blob, exif.get(tag)))
        if found:
            return found
    return None
