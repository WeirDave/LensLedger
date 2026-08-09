"""Per-user filesystem locations for LensLedger runtime data."""

from __future__ import annotations

import os
from pathlib import Path


APP_DIRECTORY_NAME = "LensLedger"


def data_root() -> Path:
    """Return the writable data directory without storing data beside the code."""
    override = os.environ.get("LENSLEDGER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (base / APP_DIRECTORY_NAME).resolve()


def default_library_root() -> Path:
    pictures = Path.home() / "Pictures"
    return pictures if pictures.is_dir() else Path.home()


def settings_path() -> Path:
    return data_root() / "library-state.json"


def libraries_root() -> Path:
    return data_root() / "Libraries"


def backup_root() -> Path:
    return data_root() / "Metadata Backups"


def review_bin_root() -> Path:
    return data_root() / "Review Bin"


def face_data_root() -> Path:
    return data_root() / "Face Data"


def database_backup_root() -> Path:
    return data_root() / "Database Backups"
