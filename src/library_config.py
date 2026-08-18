"""Persist and discover LensLedger photo-library locations."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from app_paths import default_library_root, libraries_root, settings_path


LIBRARY_STATE_PATH = settings_path()
LIBRARY_DATABASE_ROOT = libraries_root()
DEFAULT_LIBRARY_ROOT = default_library_root()


def library_db_path(root: Path) -> Path:
    root = root.resolve()
    config = _load_db_mappings()
    root_key = str(root).casefold()
    if root_key in config:
        candidate = LIBRARY_DATABASE_ROOT / config[root_key]
        if candidate.is_file():
            return candidate
    if root == DEFAULT_LIBRARY_ROOT.resolve():
        return LIBRARY_DATABASE_ROOT / "default.sqlite3"
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "photo-library"
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:12]
    return LIBRARY_DATABASE_ROOT / f"{label}-{digest}.sqlite3"


def associate_db_path(root: Path, db_path: Path) -> None:
    """Record that `root` uses `db_path`, so renaming root doesn't lose the database."""
    config = _load_db_mappings()
    config[str(root.resolve()).casefold()] = db_path.name
    _save_db_mappings(config)


def _load_db_mappings() -> dict[str, str]:
    try:
        raw = json.loads(LIBRARY_STATE_PATH.read_text(encoding="utf-8"))
        return dict(raw.get("db_mappings", {})) if isinstance(raw, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _save_db_mappings(mappings: dict[str, str]) -> None:
    LIBRARY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(LIBRARY_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError, json.JSONDecodeError):
        raw = {}
    raw["db_mappings"] = mappings
    tmp = LIBRARY_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    tmp.replace(LIBRARY_STATE_PATH)


def load_library_config() -> dict[str, object]:
    try:
        value = json.loads(LIBRARY_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            current = str(value.get("current_root") or value.get("root") or "")
            libraries = value.get("libraries", [])
            if not isinstance(libraries, list):
                libraries = []
            normalized = []
            for item in libraries:
                if not isinstance(item, str) or not item.strip():
                    continue
                candidate = Path(item)
                if candidate.is_dir():
                    normalized.append(str(candidate.resolve()))
            if current and Path(current).is_dir() and str(Path(current).resolve()) not in normalized:
                normalized.insert(0, str(Path(current).resolve()))
            return {"current_root": current, "libraries": normalized}
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {"current_root": "", "libraries": []}


def load_library_state() -> Path:
    """Resolve the library to open at startup.

    Falls back through the recently-known libraries list before giving up
    and pointing at the OS Pictures folder -- a `current_root` that no
    longer exists (a deleted removable drive, a cleaned-up temp folder)
    should never silently swap the user's real library for an empty
    default with no indication anything changed.
    """
    config = load_library_config()
    value = config.get("current_root", "")
    if value:
        root = Path(str(value)).resolve()
        if root.is_dir():
            return root
    for candidate in config.get("libraries", []):
        root = Path(str(candidate)).resolve()
        if root.is_dir():
            return root
    return DEFAULT_LIBRARY_ROOT.resolve()


def save_library_state(root: Path) -> None:
    LIBRARY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = load_library_config()
    libraries = [str(Path(str(item)).resolve()) for item in config.get("libraries", [])]
    root_text = str(root.resolve())
    libraries = [item for item in libraries if item.casefold() != root_text.casefold()]
    libraries.insert(0, root_text)
    temporary = LIBRARY_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({"current_root": root_text, "libraries": libraries}, indent=2), encoding="utf-8")
    temporary.replace(LIBRARY_STATE_PATH)


def suggested_library_roots() -> list[dict[str, str]]:
    candidates: list[tuple[str, Path]] = [
        ("Pictures", Path.home() / "Pictures"),
        ("Dropbox Photos", Path.home() / "Dropbox" / "Photos"),
        ("Dropbox Camera Uploads", Path.home() / "Dropbox" / "Camera Uploads"),
    ]
    for variable, label in (("OneDrive", "OneDrive Pictures"), ("Dropbox", "Dropbox Photos")):
        value = os.environ.get(variable, "").strip()
        if value:
            candidates.append((label, Path(value) / "Pictures" if variable == "OneDrive" else Path(value) / "Photos"))
    if os.name == "nt":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{letter}:\\")
            try:
                if root.is_dir() and ctypes.windll.kernel32.GetDriveTypeW(str(root)) == 2:
                    candidates.append((f"Removable drive {letter}:", root))
            except (AttributeError, OSError):
                pass
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if resolved.is_dir() and key not in seen:
            result.append({"label": label, "path": str(resolved)})
            seen.add(key)
    return result


def choose_library_folder() -> str:
    script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Choose a photo library folder'
$dialog.UseDescriptionForTitle = $true
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )
    if result.returncode:
        raise ValueError((result.stderr or "The folder chooser could not open").strip())
    return result.stdout.strip()
