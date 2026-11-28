from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

SOURCE = Path(__file__).resolve().parents[1] / "src"


def _picture() -> Image.Image:
    image = Image.new("RGB", (48, 32), (200, 30, 30))
    image.paste((30, 30, 200), (24, 0, 48, 32))
    return image


def _jpeg_with_odd_scan_header() -> bytes:
    """A sequential JPEG whose scan header holds values the standard disallows."""
    buffer = io.BytesIO()
    _picture().save(buffer, "JPEG")
    data = buffer.getvalue()
    start = data.index(b"\xff\xda")
    end = start + 2 + int.from_bytes(data[start + 2:start + 4], "big")
    return data[:end - 3] + b"\x00\x00\x00" + data[end:]


def _refuses_opencv(*_args, **_kwargs):
    raise AssertionError("OpenCV decoder was used")


# Hands back Pillow's array unchanged, so these tests need neither OpenCV nor NumPy.
_CV2 = SimpleNamespace(IMREAD_COLOR=1, COLOR_RGB2BGR=0, imdecode=_refuses_opencv,
                       cvtColor=lambda array, _code: array)
_NP = SimpleNamespace(uint8="uint8", array=lambda image: image.copy())


class QuietPhotoDecodingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def _read(self, name: str, cv2=_CV2, np=_NP):
        from face_locations import read_photo_bgr
        return read_photo_bgr(self.folder / name, cv2, np)

    def test_jpeg_with_a_decoder_quirk_prints_nothing(self):
        (self.folder / "odd.JPG").write_bytes(_jpeg_with_odd_scan_header())
        script = (
            "import sys; from pathlib import Path; from types import SimpleNamespace as N\n"
            "from face_locations import read_photo_bgr\n"
            "cv2 = N(COLOR_RGB2BGR=0, cvtColor=lambda a, _: a)\n"
            "print(read_photo_bgr(Path(sys.argv[1]), cv2, N(array=lambda i: i.copy())).size)\n"
        )
        # A separate process, because C libraries write past Python's sys.stderr.
        run = subprocess.run(
            [sys.executable, "-c", script, str(self.folder / "odd.JPG")],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(SOURCE)},
        )
        self.assertEqual((run.returncode, run.stdout.strip(), run.stderr), (0, "(48, 32)", ""))

    def test_camera_orientation_turns_the_picture_upright(self):
        for name in ("turned.jpg", "turned.png"):
            exif = Image.Exif()
            exif[274] = 6
            _picture().save(self.folder / name, exif=exif.tobytes())
            upright = self._read(name)
            self.assertEqual(upright.size, (32, 48), name)
            self.assertGreater(upright.getpixel((16, 4))[0], 150, name)
            self.assertGreater(upright.getpixel((16, 44))[2], 150, name)

    def test_orientation_held_only_in_xmp_is_ignored(self):
        xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
               b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
               b'<rdf:Description xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"/>'
               b'</rdf:RDF></x:xmpmeta>')
        _picture().save(self.folder / "xmp.jpg", xmp=xmp)
        self.assertEqual(self._read("xmp.jpg").size, (48, 32))

    def test_sixteen_bit_greyscale_is_left_to_opencv(self):
        Image.new("I;16", (8, 8), 30000).save(self.folder / "deep.png")
        cv2 = SimpleNamespace(IMREAD_COLOR=1, imdecode=lambda *_: "from opencv")
        np = SimpleNamespace(uint8="uint8", fromfile=lambda *_args, **_kwargs: b"")
        self.assertEqual(self._read("deep.png", cv2, np), "from opencv")

    def test_unreadable_photo_reports_why(self):
        (self.folder / "broken.jpg").write_bytes(b"not a picture")
        cv2 = SimpleNamespace(IMREAD_COLOR=1, imdecode=lambda *_: None)
        np = SimpleNamespace(uint8="uint8", fromfile=lambda *_args, **_kwargs: b"")
        with self.assertRaisesRegex(OSError, "cannot identify image file"):
            self._read("broken.jpg", cv2, np)


if __name__ == "__main__":
    unittest.main()
