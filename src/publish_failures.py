"""Plain-language reasons a photo could not be published, and what to do.

A failure reaches the Publish photos page as a raw message from ExifTool, the
operating system or LensLedger's own checks. Most are about the file system
(a leftover temporary file, something holding the photo open) and have
nothing to do with the picture, so re-saving the picture, which is what
Repair does, cannot help and only costs quality. Only damaged image data is
worth a Repair.
"""

from __future__ import annotations

import re

REPAIRABLE_SUFFIXES = (".jpg", ".jpeg")

_TEMP_FILE_IN_THE_WAY = re.compile(r"temporary file already exists|_exiftool_tmp", re.I)
_FILE_IN_USE = re.compile(
    r"error renaming temporary file|permission denied|errno 13|errno 32|access is denied"
    r"|being used by another process|read-only",
    re.I,
)
_PIXEL_CHECK = re.compile(r"pixel verification failed", re.I)
_DAMAGED_IMAGE = re.compile(
    r"not a valid (jpg|jpeg|image)|truncated|corrupt|premature end|bad format|format error"
    r"|invalid (jpg|jpeg|image)|cannot identify image|unknown image type",
    re.I,
)
_NOT_PUBLISHABLE = re.compile(r"not a publishable file type", re.I)

_HINTS = {
    "temp_file_in_the_way": (
        "A leftover temporary file (its name ends in _exiftool_tmp) from an interrupted write is "
        "next to this photo, and ExifTool will not overwrite it. Check the photo itself is still "
        "there, delete the temporary file, then publish again."
    ),
    "file_in_use": (
        "Windows would not let LensLedger replace this file. Something may have it open: file "
        "sync, a preview pane or antivirus. Close it or pause sync, check the file is not "
        "read-only, then publish again."
    ),
    "pixel_check": (
        "LensLedger wrote the tags, found the picture data had changed, and put the original "
        "back, so the photo is as it was. If it opens normally you can leave it, or use sidecar "
        "files in Settings. Repair re-saves the picture, which is lossy (a backup is kept)."
    ),
    "damaged_image": (
        "The picture data looks damaged. Repair re-saves it, which is lossy (a backup is kept), "
        "and then it can be published."
    ),
    "not_publishable": (
        "This file type cannot carry names inside the file. Use sidecar files in Settings."
    ),
}


def explain_failure(reason: str, path: str) -> dict[str, object]:
    """Classify a failure: its kind, what to do about it, and whether Repair can help."""
    text = reason or ""
    if _TEMP_FILE_IN_THE_WAY.search(text):
        kind = "temp_file_in_the_way"
    elif _FILE_IN_USE.search(text):
        kind = "file_in_use"
    elif _PIXEL_CHECK.search(text):
        kind = "pixel_check"
    elif _DAMAGED_IMAGE.search(text):
        kind = "damaged_image"
    elif _NOT_PUBLISHABLE.search(text):
        kind = "not_publishable"
    else:
        kind = "other"
    is_jpeg = (path or "").lower().endswith(REPAIRABLE_SUFFIXES)
    return {
        "kind": kind,
        "hint": _HINTS.get(kind, ""),
        "repairable": kind in ("pixel_check", "damaged_image") and is_jpeg,
    }
