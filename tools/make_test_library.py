#!/usr/bin/env python3
"""Generate a synthetic photo library for testing.

Running the app against a real library to try something out is how real photo
paths, folder names and face data end up somewhere they should not be. This
builds a library of generated images instead -- no real photos, no real places,
no real people -- so the test server has somewhere safe to point.

    python tools/make_test_library.py

Writes to .test-library-synthetic/ next to this repository, which .gitignore
excludes. Existing files are left alone; pass --fresh to start over.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / ".test-library-synthetic"

# Invented folders and subjects. Nothing here refers to a real place or person.
SCENES = [
    ("Holidays/Coast", "harbour wall at dusk", (196, 122, 74)),
    ("Holidays/Coast", "empty beach huts", (214, 176, 128)),
    ("Holidays/Mountains", "ridge under cloud", (108, 126, 142)),
    ("Holidays/Mountains", "pine slope", (74, 106, 82)),
    ("Garden", "seed trays on a bench", (126, 148, 92)),
    ("Garden", "hedge in low sun", (150, 160, 86)),
    ("Days out/Museum", "gallery staircase", (128, 120, 132)),
    ("Days out/Museum", "display case reflection", (96, 104, 124)),
    ("Documents", "NOTICE: CAR PARK CLOSED SUNDAY", (238, 238, 232)),
    ("Documents", "RECEIPT TOTAL 14.20", (240, 236, 226)),
]


def build(root: Path, fresh: bool = False) -> int:
    if fresh and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    written = 0
    for index, (folder, subject, colour) in enumerate(SCENES, 1):
        directory = root / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"2026-{index:02d}-{index + 10:02d} synthetic-{index:02d}.jpg"
        if path.exists():
            continue
        image = Image.new("RGB", (800, 600), colour)
        draw = ImageDraw.Draw(image)
        draw.rectangle([40, 40, 760, 560], outline=(255, 255, 255), width=4)
        draw.text((70, 270), f"SYNTHETIC TEST IMAGE {index:02d}", fill=(255, 255, 255))
        draw.text((70, 300), subject, fill=(255, 255, 255))
        image.save(path, quality=90)
        written += 1

    # A format that cannot carry embedded tags, so the sidecar fallback and the
    # "could not be written" reporting both have something to exercise.
    diagram = root / "Documents" / "2026-11-21 diagram.png"
    if not diagram.exists():
        Image.new("RGB", (400, 300), (205, 86, 86)).save(diagram)
        written += 1

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--fresh", action="store_true",
                        help="delete the existing synthetic library first")
    args = parser.parse_args()
    written = build(args.root.resolve(), fresh=args.fresh)
    print(f"Synthetic library ready at {args.root.resolve()} ({written} new file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
