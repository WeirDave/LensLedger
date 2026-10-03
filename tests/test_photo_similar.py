from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


def edited(image: Image.Image) -> Image.Image:
    """The same picture with a small patch painted over it, as an edit would."""
    image = image.copy()
    ImageDraw.Draw(image).rectangle([0, 0, image.width // 5, image.height // 5], fill=(255, 255, 255))
    return image


def picture(seed: int, size=(640, 480)) -> Image.Image:
    """A coarse random block pattern: stable under resizing, unlike any other seed."""
    rng = random.Random(seed)
    blocks = Image.new("L", (16, 12))
    blocks.putdata([rng.randrange(256) for _ in range(16 * 12)])
    return blocks.resize(size, Image.BICUBIC).convert("RGB")


class VisualHashTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def distance(self, a: Path, b: Path) -> int:
        from photo_index import visual_hash

        return (int(visual_hash(a), 16) ^ int(visual_hash(b), 16)).bit_count()

    def test_a_resized_recompressed_copy_stays_close_and_another_photo_does_not(self):
        original = self.root / "a.jpg"
        picture(1).save(original, quality=92)
        small = self.root / "a-small.jpg"
        picture(1).resize((320, 240)).save(small, quality=60)
        other = self.root / "b.jpg"
        picture(2).save(other, quality=92)

        self.assertLessEqual(self.distance(original, small), 5)
        self.assertGreater(self.distance(original, other), 12)

    def test_a_photo_rotated_by_its_orientation_flag_hashes_like_the_upright_one(self):
        upright = self.root / "up.jpg"
        picture(3, (600, 400)).save(upright, quality=92)
        exif = Image.Exif()
        exif[0x0112] = 6  # stored sideways, shown rotated a quarter turn
        sideways = self.root / "side.jpg"
        picture(3, (600, 400)).rotate(90, expand=True).save(sideways, quality=92, exif=exif)

        self.assertLessEqual(self.distance(upright, sideways), 8)

    def test_what_cannot_be_hashed_gets_no_hash(self):
        from photo_index import visual_hash

        broken = self.root / "broken.jpg"
        broken.write_bytes(b"not a jpeg")
        video = self.root / "clip.mp4"
        video.write_bytes(b"\x00" * 64)

        self.assertEqual((visual_hash(broken), visual_hash(video), visual_hash(self.root / "missing.jpg")), ("", "", ""))


class SimilarPhotoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "photos"
        (self.library / "Trip").mkdir(parents=True)
        self.database = self.root / "library.sqlite3"
        picture(10).save(self.library / "Trip" / "IMG_0001.jpg", quality=92)
        edited(picture(10).resize((320, 240))).save(self.library / "Trip" / "IMG_0001-small.jpg", quality=60)
        picture(11).save(self.library / "Trip" / "IMG_0002.jpg", quality=92)
        # An exact pair on its own: Exact duplicates covers it, Similar must not repeat it.
        picture(12).save(self.library / "Trip" / "IMG_0003.jpg", quality=92)
        (self.library / "Trip" / "IMG_0003 (copy).jpg").write_bytes((self.library / "Trip" / "IMG_0003.jpg").read_bytes())
        from photo_index import scan_library

        scan_library(self.library, self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def groups(self, **kwargs):
        from photo_index import connect
        from photo_similar import find_similar_groups

        con = connect(self.database)
        try:
            return [[item["filename"] for item in group] for group in find_similar_groups(con, **kwargs)]
        finally:
            con.close()

    def test_the_fixture_copy_differs_a_little_so_the_threshold_is_exercised(self):
        from photo_index import visual_hash

        a = int(visual_hash(self.library / "Trip" / "IMG_0001.jpg"), 16)
        b = int(visual_hash(self.library / "Trip" / "IMG_0001-small.jpg"), 16)
        self.assertGreater((a ^ b).bit_count(), 0)

    def test_a_smaller_copy_groups_with_its_original_largest_file_first(self):
        self.assertEqual(self.groups(), [["IMG_0001.jpg", "IMG_0001-small.jpg"]])

    def test_a_flat_picture_matches_nothing(self):
        from photo_index import scan_library

        # Different bytes, so only the flat-picture rule can keep them apart.
        for name, grey in (("blank-a.jpg", 250), ("blank-b.jpg", 248)):
            Image.new("RGB", (400, 300), (grey, grey, grey)).save(self.library / name, quality=92)
        scan_library(self.library, self.database)

        self.assertEqual(self.groups(), [["IMG_0001.jpg", "IMG_0001-small.jpg"]])

    def test_a_path_filter_keeps_the_whole_group(self):
        self.assertEqual(self.groups(tokens=["small"]), [["IMG_0001.jpg", "IMG_0001-small.jpg"]])
        self.assertEqual(self.groups(tokens=["nowhere"]), [])

    def test_a_photo_in_the_review_bin_leaves_its_group(self):
        con = sqlite3.connect(self.database)
        con.execute("UPDATE assets SET in_review_bin=1 WHERE filename='IMG_0001-small.jpg'")
        con.commit()
        con.close()

        self.assertEqual(self.groups(), [])

    def test_a_library_indexed_before_visual_hashes_gets_them_on_the_next_scan(self):
        from photo_index import VISUAL_HASH_VERSION, scan_library

        con = sqlite3.connect(self.database)
        con.execute("UPDATE assets SET visual_hash='', visual_scanned=0")
        con.commit()
        con.close()
        self.assertEqual(self.groups(), [])

        scan_library(self.library, self.database)

        self.assertEqual(self.groups(), [["IMG_0001.jpg", "IMG_0001-small.jpg"]])
        con = sqlite3.connect(self.database)
        self.assertEqual(con.execute("SELECT MIN(visual_scanned) FROM assets").fetchone()[0], VISUAL_HASH_VERSION)
        con.close()


if __name__ == "__main__":
    unittest.main()
