"""A still picture from a video, used as its thumbnail.

LensLedger has no video decoder of its own. On Windows the shell that draws
Explorer's thumbnails is asked first, because it ships with the system and
understands whatever codecs are installed. OpenCV, when the optional face
packages brought it along, is the fallback. When neither can produce a frame
the caller shows a plain label instead.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import Image

_SIIGBF_BIGGERSIZEOK = 0x1
_SIIGBF_THUMBNAILONLY = 0x8
_COINIT_APARTMENTTHREADED = 0x2


class _Guid(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


class _Size(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _Bitmap(ctypes.Structure):
    _fields_ = [("type", ctypes.c_long), ("width", ctypes.c_long), ("height", ctypes.c_long),
                ("widthBytes", ctypes.c_long), ("planes", ctypes.c_ushort), ("bitsPixel", ctypes.c_ushort),
                ("bits", ctypes.c_void_p)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("width", ctypes.c_long), ("height", ctypes.c_long),
                ("planes", ctypes.c_ushort), ("bitCount", ctypes.c_ushort), ("compression", ctypes.c_uint32),
                ("sizeImage", ctypes.c_uint32), ("xPels", ctypes.c_long), ("yPels", ctypes.c_long),
                ("used", ctypes.c_uint32), ("important", ctypes.c_uint32)]


# IShellItemImageFactory {BCC18B79-BA16-442F-80C4-8A59C30C463B}
_IID_IMAGE_FACTORY = _Guid(0xBCC18B79, 0xBA16, 0x442F, (ctypes.c_ubyte * 8)(0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B))


def _windows_frame(path: Path, size: int) -> Image.Image | None:
    ole32, shell32, gdi32 = ctypes.windll.ole32, ctypes.windll.shell32, ctypes.windll.gdi32
    initialised = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    factory = ctypes.c_void_p()
    bitmap = wintypes.HBITMAP()
    try:
        shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_Guid),
                                                        ctypes.POINTER(ctypes.c_void_p)]
        if shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(_IID_IMAGE_FACTORY), ctypes.byref(factory)) != 0:
            return None
        table = ctypes.cast(ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        get_image = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, _Size, ctypes.c_uint,
                                       ctypes.POINTER(wintypes.HBITMAP))(table[3])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])
        try:
            result = get_image(factory, _Size(size, size), _SIIGBF_BIGGERSIZEOK | _SIIGBF_THUMBNAILONLY, ctypes.byref(bitmap))
        finally:
            release(factory)
        if result != 0 or not bitmap:
            return None
        info = _Bitmap()
        gdi32.GetObjectW(bitmap, ctypes.sizeof(info), ctypes.byref(info))
        width, height = int(info.width), int(info.height)
        if width <= 0 or height <= 0:
            return None
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        context = ctypes.windll.user32.GetDC(None)
        try:
            lines = gdi32.GetDIBits(context, bitmap, 0, height, pixels, ctypes.byref(header), 0)
        finally:
            ctypes.windll.user32.ReleaseDC(None, context)
        if lines != height:
            return None
        return Image.frombuffer("RGB", (width, height), pixels.raw, "raw", "BGRX", 0, 1).copy()
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if initialised in (0, 1):
            ole32.CoUninitialize()


def _opencv_frame(path: Path) -> Image.Image | None:
    try:
        import cv2
    except ImportError:
        return None
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            return None
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if count > 20:
            capture.set(cv2.CAP_PROP_POS_FRAMES, count // 10)
        ok, frame = capture.read()
        if not ok or frame is None:
            return None
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        capture.release()


def video_frame(path: Path, size: int) -> Image.Image | None:
    """An RGB picture from the video, or None when this computer cannot read it."""
    if sys.platform == "win32":
        try:
            frame = _windows_frame(path, size)
        except Exception:
            frame = None
        if frame is not None:
            return frame
    try:
        return _opencv_frame(path)
    except Exception:
        return None
