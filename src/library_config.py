"""Persist and discover LensLedger photo-library locations."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
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
        mapped = config[root_key]
        if os.path.isabs(mapped):
            candidate = Path(mapped)
        else:
            candidate = LIBRARY_DATABASE_ROOT / mapped
        if candidate.is_file():
            return candidate
    label = re.sub(r'[<>:"/\\|?*]+', " ", root.name).strip() or "photo-library"
    return root / ".LensLedger" / f"LensLedger-{label}.sqlite3"


def library_db_path_appdata(root: Path) -> Path:
    """Legacy: return a database path inside the central AppData Libraries folder."""
    root = root.resolve()
    if root == DEFAULT_LIBRARY_ROOT.resolve():
        return LIBRARY_DATABASE_ROOT / "default.sqlite3"
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "photo-library"
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:12]
    return LIBRARY_DATABASE_ROOT / f"{label}-{digest}.sqlite3"


def associate_db_path(root: Path, db_path: Path) -> None:
    """Record that `root` uses `db_path`, so renaming root doesn't lose the database."""
    config = _load_db_mappings()
    config[str(root.resolve()).casefold()] = str(db_path.resolve())
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


def load_all_known_libraries() -> list[dict[str, object]]:
    """Return every library ever recorded, including ones whose folders no longer exist."""
    try:
        value = json.loads(LIBRARY_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            current_raw = str(value.get("current_root") or value.get("root") or "")
            try:
                current = str(Path(current_raw).resolve()).casefold() if current_raw else ""
            except OSError:
                current = current_raw.casefold() if current_raw else ""
            libraries = value.get("libraries", [])
            if not isinstance(libraries, list):
                libraries = []
            seen: set[str] = set()
            result: list[dict[str, object]] = []
            for item in libraries:
                if not isinstance(item, str) or not item.strip():
                    continue
                try:
                    resolved = str(Path(item).resolve())
                except OSError:
                    resolved = item
                key = resolved.casefold()
                if key in seen:
                    continue
                seen.add(key)
                accessible = Path(item).is_dir()
                result.append({
                    "path": resolved,
                    "label": Path(resolved).name or resolved,
                    "accessible": accessible,
                    "is_current": key == current if current else False,
                })
            return result
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return []


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
    if sys.platform == "win32":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{letter}:\\")
            try:
                if root.is_dir() and ctypes.windll.kernel32.GetDriveTypeW(str(root)) == 2:
                    candidates.append((f"Removable drive {letter}:", root))
            except (AttributeError, OSError):
                pass
    elif sys.platform == "linux":
        for mount_dir in [Path("/media") / os.getlogin(), Path("/run/media") / os.getlogin(), Path("/mnt")]:
            try:
                if mount_dir.is_dir():
                    for child in mount_dir.iterdir():
                        if child.is_dir():
                            candidates.append((f"Mount: {child.name}", child))
            except OSError:
                pass
    elif sys.platform == "darwin":
        volumes = Path("/Volumes")
        try:
            if volumes.is_dir():
                for child in volumes.iterdir():
                    if child.is_dir() and child.name != "Macintosh HD":
                        candidates.append((f"Volume: {child.name}", child))
        except OSError:
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
    if sys.platform == "win32":
        return _choose_folder_windows()
    if sys.platform == "darwin":
        return _choose_folder_macos()
    return _choose_folder_linux()


def _choose_folder_windows() -> str:
    script = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Name FgW -Namespace Util -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);'
$f = New-Object System.Windows.Forms.Form
$f.TopMost = $true
$f.ShowInTaskbar = $false
$f.WindowState = 'Minimized'
$f.Show()
[Util.FgW]::SetForegroundWindow($f.Handle) | Out-Null
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Choose a photo library folder'
$d.UseDescriptionForTitle = $true
if ($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $d.SelectedPath
}
$f.Dispose()
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )
    if result.returncode:
        raise ValueError((result.stderr or "The folder chooser could not open").strip())
    return result.stdout.strip()


def _choose_folder_macos() -> str:
    result = subprocess.run(
        ["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose a photo library folder")'],
        capture_output=True, text=True, timeout=600, check=False,
    )
    if result.returncode:
        return ""
    return result.stdout.strip().rstrip("/")


def _choose_folder_linux() -> str:
    for cmd, args in [
        ("zenity", ["zenity", "--file-selection", "--directory", "--title=Choose a photo library folder"]),
        ("kdialog", ["kdialog", "--getexistingdirectory", str(Path.home()), "--title", "Choose a photo library folder"]),
    ]:
        if shutil.which(cmd):
            result = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            return ""
    raise ValueError("No folder picker available — install zenity or kdialog")


def choose_file(title: str = "Choose a file", filter_label: str = "All files", filter_ext: str = "*") -> str:
    if sys.platform == "win32":
        return _choose_file_windows(title, filter_label, filter_ext)
    if sys.platform == "darwin":
        return _choose_file_macos(title, filter_ext)
    return _choose_file_linux(title, filter_ext)


def _choose_file_windows(title: str, filter_label: str, filter_ext: str) -> str:
    win_filter = f"{filter_label} (*.{filter_ext})|*.{filter_ext}" if filter_ext != "*" else "All files (*.*)|*.*"
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Name FgW -Namespace Util -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);'
$f = New-Object System.Windows.Forms.Form
$f.TopMost = $true
$f.ShowInTaskbar = $false
$f.WindowState = 'Minimized'
$f.Show()
[Util.FgW]::SetForegroundWindow($f.Handle) | Out-Null
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '{title}'
$d.Filter = '{win_filter}'
if ($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $d.FileName
}}
$f.Dispose()
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )
    if result.returncode:
        raise ValueError((result.stderr or "The file chooser could not open").strip())
    return result.stdout.strip()


def _choose_file_macos(title: str, filter_ext: str) -> str:
    type_clause = f' of type {{"{filter_ext}"}}' if filter_ext != "*" else ""
    script = f'POSIX path of (choose file with prompt "{title}"{type_clause})'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=600, check=False,
    )
    if result.returncode:
        return ""
    return result.stdout.strip()


def _choose_file_linux(title: str, filter_ext: str) -> str:
    file_filter = f"*.{filter_ext}" if filter_ext != "*" else "*"
    for cmd, args in [
        ("zenity", ["zenity", "--file-selection", f"--title={title}", f"--file-filter={file_filter}"]),
        ("kdialog", ["kdialog", "--getopenfilename", str(Path.home()), file_filter, "--title", title]),
    ]:
        if shutil.which(cmd):
            result = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            return ""
    raise ValueError("No file picker available — install zenity or kdialog")
