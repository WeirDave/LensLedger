#!/usr/bin/env python3
"""Verified, rollback-capable updater for managed LensLedger installations."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


REPOSITORY = "WeirDave/LensLedger"
API_VERSION = "2022-11-28"
MARKER_NAME = ".lensledger-managed.json"
LEGACY_HANDOFF_REGISTRY = "legacy-launchers.json"
LEGACY_LAUNCHER_MARKER = "REM LensLedger managed-launcher handoff"
REQUIRED_FILES = {
    "src/app_paths.py", "assets/lensledger-logo.png", "assets/world-map.svg", "CHANGELOG.md",
    "src/database_tools.py", "src/library_config.py", "src/metadata_reader.py", "src/lensledger_updater.py",
    "src/generate_historical_folder_tags.py",
    "src/photo_index.py", "src/photo_search.py", "src/product.py", "requirements.txt",
    "requirements-semantic.txt", "Install LensLedger.cmd", "Start LensLedger.cmd",
    "THIRD_PARTY_NOTICES.md", "tools/ExifTool/ExifTool.exe", "src/windows_ocr.ps1",
    "LICENSE", "README.md",
    "web/css/onboarding.css", "web/js/onboarding.js", "web/css/viewer.css", "web/js/viewer.js",
    "web/css/map.css", "web/js/map.js", "web/css/people-review.css", "web/js/people-review.js",
}
FORBIDDEN_ROOT_NAMES = {
    ".git", "Face Data", "Libraries", "Metadata Backups", "Database Backups",
    "Review Bin", "library-state.json", "photo-index.sqlite3",
}


class UpdateError(RuntimeError):
    """A user-actionable update failure."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    name: str
    page_url: str
    asset_api_url: str
    asset_name: str
    asset_size: int
    digest: str


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise UpdateError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in match.groups())


def read_tree_version(root: Path) -> str:
    product = root / "src" / "product.py"
    try:
        text = product.read_text(encoding="utf-8")
    except OSError as exc:
        raise UpdateError(f"Cannot read {product}: {exc}") from exc
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise UpdateError(f"Cannot find APP_VERSION in {product}")
    version_tuple(match.group(1))
    return match.group(1)


def data_root() -> Path:
    override = os.environ.get("LENSLEDGER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (base / "LensLedger").resolve()


def updates_root() -> Path:
    return data_root() / "Updates"


def managed_install_root() -> Path:
    override = os.environ.get("LENSLEDGER_INSTALL_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs"
    else:
        base = Path.home() / ".local" / "opt"
    return (base / "LensLedger").resolve()


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def github_token() -> str:
    for name in ("LENSLEDGER_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=15,
                check=False, creationflags=_creation_flags(),
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def _headers(token: str = "", accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "LensLedger-Updater",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward a GitHub token to a cross-host signed asset URL."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlparse(request.full_url).netloc != urllib.parse.urlparse(newurl).netloc:
            redirected.headers.pop("Authorization", None)
            redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


def _opener():
    return urllib.request.build_opener(_SafeRedirectHandler())


def latest_release(token: str | None = None) -> ReleaseInfo:
    token = github_token() if token is None else token
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
    request = urllib.request.Request(url, headers=_headers(token))
    try:
        with _opener().open(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise UpdateError(
                "LensLedger could not access its private GitHub releases. "
                "Sign in with GitHub CLI (`gh auth login`) or set LENSLEDGER_GITHUB_TOKEN."
            ) from exc
        raise UpdateError(f"GitHub release check failed (HTTP {exc.code})") from exc
    except (OSError, ValueError) as exc:
        raise UpdateError(f"GitHub release check failed: {exc}") from exc

    tag = str(payload.get("tag_name", ""))
    version = tag.removeprefix("v")
    version_tuple(version)
    expected_name = f"LensLedger-{tag}.zip"
    assets = [asset for asset in payload.get("assets", []) if isinstance(asset, dict)]
    asset = next((item for item in assets if item.get("name") == expected_name), None)
    if asset is None:
        zip_assets = [item for item in assets if str(item.get("name", "")).lower().endswith(".zip")]
        asset = zip_assets[0] if len(zip_assets) == 1 else None
    if asset is None:
        raise UpdateError(f"Release {tag} does not contain one unambiguous LensLedger ZIP")
    digest = str(asset.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise UpdateError(f"Release {tag} does not provide a valid SHA-256 digest")
    return ReleaseInfo(
        version=version,
        tag=tag,
        name=str(payload.get("name") or tag),
        page_url=str(payload.get("html_url") or ""),
        asset_api_url=str(asset.get("url") or ""),
        asset_name=str(asset.get("name") or expected_name),
        asset_size=int(asset.get("size") or 0),
        digest=digest.lower(),
    )


def check_for_update(current_version: str, token: str | None = None) -> dict[str, object]:
    current = version_tuple(current_version)
    release = latest_release(token)
    return {
        "current_version": current_version,
        "available": version_tuple(release.version) > current,
        "release": asdict(release),
    }


def _download_release(release: ReleaseInfo, destination: Path, token: str,
                      progress: Callable[[int, int], None] | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(
        release.asset_api_url,
        headers=_headers(token, "application/octet-stream"),
    )
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with _opener().open(request, timeout=60) as response, partial.open("wb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, release.asset_size)
    except (OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Release download failed: {exc}") from exc
    actual = f"sha256:{digest.hexdigest()}"
    if actual != release.digest:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Downloaded release digest mismatch: expected {release.digest}, got {actual}")
    if release.asset_size and downloaded != release.asset_size:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Downloaded release size mismatch: expected {release.asset_size}, got {downloaded}")
    os.replace(partial, destination)
    return destination


def _assert_within(path: Path, parent: Path) -> None:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise UpdateError(f"Unsafe update path outside {parent}: {path}") from exc


def _remove_staging(path: Path, parent: Path) -> None:
    _assert_within(path, parent)
    if path.exists():
        shutil.rmtree(path)


def _extract_release(archive: Path, destination: Path) -> Path:
    parent = destination.parent.resolve()
    _remove_staging(destination, parent)
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                relative = Path(item.filename.replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise UpdateError(f"Release contains an unsafe path: {item.filename}")
                mode = (item.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise UpdateError(f"Release contains an unsupported symbolic link: {item.filename}")
                _assert_within(destination / relative, destination)
            package.extractall(destination)
    except (OSError, zipfile.BadZipFile) as exc:
        _remove_staging(destination, parent)
        raise UpdateError(f"Release ZIP could not be extracted: {exc}") from exc
    return destination


def validate_release_tree(root: Path, expected_version: str | None = None) -> str:
    root = root.resolve()
    missing = sorted(name for name in REQUIRED_FILES if not (root / name).is_file())
    if missing:
        raise UpdateError("Release is incomplete; missing: " + ", ".join(missing))
    if (root / ".git").exists() or any((child / ".git").exists() for child in root.iterdir() if child.is_dir()):
        raise UpdateError("Git working copies cannot be installed or replaced by the updater")
    present_forbidden = sorted(name for name in FORBIDDEN_ROOT_NAMES if name != ".git" and (root / name).exists())
    if present_forbidden:
        raise UpdateError("Release contains private runtime data: " + ", ".join(present_forbidden))
    version = read_tree_version(root)
    if expected_version and version != expected_version:
        raise UpdateError(f"Release version mismatch: expected {expected_version}, found {version}")
    return version


def stage_latest(current_version: str, token: str | None = None,
                 progress: Callable[[int, int], None] | None = None) -> tuple[ReleaseInfo, Path]:
    token = github_token() if token is None else token
    release = latest_release(token)
    if version_tuple(release.version) <= version_tuple(current_version):
        raise UpdateError(f"LensLedger {current_version} is already up to date")
    root = updates_root()
    archive = root / "Downloads" / release.asset_name
    _download_release(release, archive, token, progress)
    staged = root / f"Staged-{release.version}"
    _extract_release(archive, staged)
    validate_release_tree(staged, release.version)
    return release, staged


def _safe_install_target(path: Path) -> Path:
    path = path.resolve()
    if path == Path(path.anchor) or path == Path.home().resolve() or len(path.parts) < 3:
        raise UpdateError(f"Refusing unsafe installation target: {path}")
    return path


def is_managed_install(path: Path) -> bool:
    try:
        marker = json.loads((path / MARKER_NAME).read_text(encoding="utf-8"))
        return marker.get("managed_by") == "LensLedger updater"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _legacy_handoff_registry_path() -> Path:
    return data_root() / LEGACY_HANDOFF_REGISTRY


def _registered_legacy_roots() -> list[Path]:
    """Return the legacy shortcut locations explicitly adopted by this user."""
    registry = _legacy_handoff_registry_path()
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
        roots = payload.get("roots", [])
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(roots, list):
        return []
    result: list[Path] = []
    for value in roots:
        if not isinstance(value, str) or not value.strip():
            continue
        root = Path(value).expanduser().resolve()
        if root not in result:
            result.append(root)
    return result


def _register_legacy_root(legacy_root: Path) -> None:
    registry = _legacy_handoff_registry_path()
    roots = _registered_legacy_roots()
    if legacy_root not in roots:
        roots.append(legacy_root)
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_name(f".{registry.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({
            "roots": sorted(str(root) for root in roots),
        }, indent=2), encoding="utf-8")
        os.replace(temporary, registry)
    finally:
        temporary.unlink(missing_ok=True)


def _legacy_launcher_backup_path(legacy_root: Path) -> Path:
    return legacy_root / "Start LensLedger.pre-managed.cmd"


def _managed_handoff_script(install_root: Path) -> str:
    target = install_root / "Start LensLedger.cmd"
    return "\r\n".join((
        "@echo off",
        LEGACY_LAUNCHER_MARKER,
        "REM This preserves older shortcuts while always starting the managed, updated app.",
        "setlocal",
        f'set "LENSLEDGER_MANAGED_START={target}"',
        'if not exist "%LENSLEDGER_MANAGED_START%" (',
        "    echo.",
        "    echo LensLedger's managed installation is missing.",
        "    echo Run Install LensLedger.cmd from a current LensLedger release to repair it.",
        "    echo.",
        "    pause",
        "    exit /b 1",
        ")",
        'call "%LENSLEDGER_MANAGED_START%"',
        "exit /b %ERRORLEVEL%",
        "",
    ))


def handoff_legacy_launcher(legacy_root: Path, install_root: Path,
                             register: bool = True) -> dict[str, str]:
    """Replace an explicitly adopted pre-managed launcher with a reversible handoff."""
    legacy_root = legacy_root.resolve()
    install_root = install_root.resolve()
    start_script = legacy_root / "Start LensLedger.cmd"
    if legacy_root == install_root:
        raise UpdateError("A managed LensLedger installation cannot be adopted as a legacy launcher")
    if is_managed_install(legacy_root) or (legacy_root / ".git").exists():
        raise UpdateError(f"Refusing to replace a managed installation or Git working copy: {legacy_root}")
    if not (legacy_root / "photo_search.py").is_file() or not start_script.is_file():
        raise UpdateError(f"Not a recognizable legacy LensLedger installation: {legacy_root}")
    if not is_managed_install(install_root) or not (install_root / "Start LensLedger.cmd").is_file():
        raise UpdateError(f"Managed LensLedger installation is unavailable: {install_root}")
    try:
        current = start_script.read_text(encoding="utf-8")
    except OSError as exc:
        raise UpdateError(f"Cannot read legacy launcher {start_script}: {exc}") from exc

    backup = _legacy_launcher_backup_path(legacy_root)
    status = "already_handed_off" if LEGACY_LAUNCHER_MARKER in current else "handed_off"
    if status == "handed_off":
        try:
            if not backup.exists():
                shutil.copy2(start_script, backup)
            temporary = start_script.with_name(f".{start_script.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(_managed_handoff_script(install_root), encoding="utf-8")
                os.replace(temporary, start_script)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise UpdateError(f"Cannot create managed launcher handoff in {legacy_root}: {exc}") from exc
    if register:
        _register_legacy_root(legacy_root)
    return {
        "legacy_root": str(legacy_root),
        "launcher": str(start_script),
        "backup_launcher": str(backup),
        "status": status,
    }


def refresh_legacy_launcher_handoffs(install_root: Path) -> list[dict[str, str]]:
    """Reapply known shortcut handoffs without letting an unavailable old folder block an update."""
    results: list[dict[str, str]] = []
    for legacy_root in _registered_legacy_roots():
        try:
            results.append(handoff_legacy_launcher(legacy_root, install_root, register=False))
        except UpdateError as exc:
            results.append({
                "legacy_root": str(legacy_root),
                "status": "unavailable",
                "message": str(exc),
            })
    return results


def install_tree(source: Path, target: Path) -> dict[str, str]:
    source = source.resolve()
    version = validate_release_tree(source)
    target = _safe_install_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / f".{target.name}.stage-{uuid.uuid4().hex}"
    rollback = None
    try:
        shutil.copytree(source, stage)
        marker = {
            "managed_by": "LensLedger updater",
            "version": version,
            "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        (stage / MARKER_NAME).write_text(json.dumps(marker, indent=2), encoding="utf-8")
        validate_release_tree(stage, version)
        if target.exists():
            if not is_managed_install(target):
                raise UpdateError(
                    f"The existing target is not a managed LensLedger installation: {target}. "
                    "Choose an empty managed-install folder."
                )
            old_version = read_tree_version(target)
            timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            rollback = target.parent / f"{target.name}.previous-{old_version}-{timestamp}"
            if rollback.exists():
                raise UpdateError(f"Rollback destination already exists: {rollback}")
            target.rename(rollback)
        stage.rename(target)
    except Exception:
        if rollback and rollback.exists() and not target.exists():
            rollback.rename(target)
        if stage.exists():
            _remove_staging(stage, target.parent)
        raise
    return {
        "version": version,
        "install_root": str(target),
        "rollback_root": str(rollback) if rollback else "",
    }


def _library_database_path(root: Path, runtime_root: Path) -> Path:
    pictures = Path.home() / "Pictures"
    default = pictures if pictures.is_dir() else Path.home()
    if root.resolve() == default.resolve():
        return runtime_root / "Libraries" / "default.sqlite3"
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "photo-library"
    digest = hashlib.sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest()[:12]
    return runtime_root / "Libraries" / f"{label}-{digest}.sqlite3"


def _legacy_library_root(database: Path) -> Path:
    source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        paths = [row[0] for row in source.execute(
            "SELECT path FROM assets WHERE path<>'' AND COALESCE(in_review_bin, 0)=0"
        )]
        if not paths:
            paths = [row[0] for row in source.execute(
                "SELECT original_path FROM review_bin WHERE restored_at IS NULL AND original_path<>''"
            )]
        if not paths:
            raise UpdateError("Cannot determine the legacy photo-library root: the catalog has no photo paths")
        common = os.path.commonpath(paths)
    except (sqlite3.DatabaseError, ValueError) as exc:
        raise UpdateError(f"Cannot determine the legacy photo-library root: {exc}") from exc
    finally:
        source.close()
    root = Path(common).resolve()
    if root.is_file() or (len(paths) == 1 and Path(paths[0]).suffix):
        root = root.parent
    if not root.is_dir():
        raise UpdateError(f"Legacy photo-library root is unavailable: {root}")
    return root


def _remap_path(value: str, old_root: Path, new_root: Path) -> str:
    try:
        relative = Path(value).resolve().relative_to(old_root.resolve())
    except (OSError, ValueError):
        return value
    return str((new_root / relative).resolve())


def _remap_migrated_paths(database: Path, legacy_root: Path, runtime: Path) -> None:
    mappings = [
        ("review_bin", "review_path", legacy_root / "Review Bin", runtime / "Review Bin"),
        ("metadata_publications", "backup_path", legacy_root / "Metadata Backups", runtime / "Metadata Backups"),
    ]
    con = sqlite3.connect(database)
    try:
        for table, column, old_root, new_root in mappings:
            rows = con.execute(f"SELECT id,{column} FROM {table} WHERE {column}<>''").fetchall()
            con.executemany(
                f"UPDATE {table} SET {column}=? WHERE id=?",
                [(_remap_path(value, old_root, new_root), row_id) for row_id, value in rows],
            )
        rows = con.execute("SELECT id,path FROM assets WHERE in_review_bin=1").fetchall()
        con.executemany(
            "UPDATE assets SET path=? WHERE id=?",
            [
                (_remap_path(value, legacy_root / "Review Bin", runtime / "Review Bin"), asset_id)
                for asset_id, value in rows
            ],
        )
        if str(con.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise UpdateError("Migrated database failed verification after recovery-path remapping")
        con.commit()
    finally:
        con.close()


def migrate_legacy_data(legacy_root: Path, install_root: Path) -> dict[str, str]:
    legacy_root = legacy_root.resolve()
    source_database = legacy_root / "photo-index.sqlite3"
    if not source_database.is_file():
        return {}
    runtime = data_root()
    library_root = _legacy_library_root(source_database)
    target_database = _library_database_path(library_root, runtime)
    if target_database.exists():
        raise UpdateError(
            f"A managed catalog already exists at {target_database}; the legacy catalog was not overwritten"
        )
    target_database.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_database.with_suffix(".migrating")
    temporary.unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{source_database.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    staged_directories: list[tuple[Path, Path]] = []
    created_directories: list[Path] = []
    try:
        for command in ("migrate", "verify"):
            result = subprocess.run(
                [sys.executable, str(install_root / "src" / "database_tools.py"), "--db", str(temporary), command],
                capture_output=True, text=True, timeout=180, check=False,
                creationflags=_creation_flags(),
            )
            if result.returncode:
                raise UpdateError((result.stderr or result.stdout or f"Database {command} failed").strip())
        runtime.mkdir(parents=True, exist_ok=True)
        for name in ("Face Data", "Metadata Backups", "Review Bin"):
            source_directory = legacy_root / name
            target_directory = runtime / name
            if source_directory.is_dir() and not target_directory.exists():
                staged_directory = runtime / f".{name}.migrating-{uuid.uuid4().hex}"
                shutil.copytree(source_directory, staged_directory)
                staged_directories.append((staged_directory, target_directory))
        _remap_migrated_paths(temporary, legacy_root, runtime)
        state = runtime / "library-state.json"
        state_temp = state.with_suffix(".tmp")
        state_temp.write_text(json.dumps({
            "current_root": str(library_root),
            "libraries": [str(library_root)],
        }, indent=2), encoding="utf-8")
        for staged_directory, target_directory in staged_directories:
            staged_directory.rename(target_directory)
            created_directories.append(target_directory)
        os.replace(temporary, target_database)
        os.replace(state_temp, state)
    except Exception:
        temporary.unlink(missing_ok=True)
        for staged_directory, _target_directory in staged_directories:
            if staged_directory.exists():
                shutil.rmtree(staged_directory)
        if not target_database.exists():
            for created_directory in created_directories:
                if created_directory.exists():
                    shutil.rmtree(created_directory)
        raise
    return {
        "legacy_database": str(source_database),
        "database": str(target_database),
        "library_root": str(library_root),
    }


def wait_for_process(process_id: int, timeout_seconds: int = 180) -> None:
    if process_id <= 0 or process_id == os.getpid():
        return
    if os.name == "nt":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, process_id)
        if not handle:
            return
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, timeout_seconds * 1000)
            if result == 0x00000102:
                raise UpdateError("LensLedger did not close in time; the update was not installed")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except OSError:
            return
        time.sleep(.2)
    raise UpdateError("LensLedger did not close in time; the update was not installed")


def launch_lensledger(install_root: Path) -> None:
    if os.name == "nt":
        subprocess.Popen(
            ["cmd.exe", "/c", str(install_root / "Start LensLedger.cmd")], cwd=install_root,
            stdin=subprocess.DEVNULL, close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    else:
        subprocess.Popen(
            [sys.executable, str(install_root / "src" / "photo_search.py")], cwd=install_root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )


def install_current(source: Path, target: Path | None = None, legacy_root: Path | None = None,
                    launch: bool = True) -> dict[str, object]:
    source = source.resolve()
    validate_release_tree(source)
    target = (target or managed_install_root()).resolve()
    result: dict[str, object] = install_tree(source, target)
    if legacy_root:
        result["migration"] = migrate_legacy_data(legacy_root, target)
        result["legacy_launcher"] = handoff_legacy_launcher(legacy_root, target)
    handoffs = refresh_legacy_launcher_handoffs(target)
    if handoffs:
        result["legacy_launchers"] = handoffs
    if launch:
        launch_lensledger(target)
    return result


def install_latest(current_root: Path, current_version: str, wait_pid: int = 0,
                   legacy_root: Path | None = None, launch: bool = True,
                   token: str | None = None) -> dict[str, object]:
    release, staged = stage_latest(current_version, token)
    target = current_root.resolve() if is_managed_install(current_root) else managed_install_root()
    wait_for_process(wait_pid)
    result: dict[str, object] = install_tree(staged, target)
    if legacy_root:
        result["migration"] = migrate_legacy_data(legacy_root, target)
        result["legacy_launcher"] = handoff_legacy_launcher(legacy_root, target)
    handoffs = refresh_legacy_launcher_handoffs(target)
    if handoffs:
        result["legacy_launchers"] = handoffs
    if launch:
        launch_lensledger(target)
    result["release"] = asdict(release)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="check GitHub for a newer verified release")
    check.add_argument("--current", required=True)
    current = subparsers.add_parser("install-current", help="install this extracted release into the managed app folder")
    current.add_argument("--source", type=Path, default=Path(__file__).parent.parent)
    current.add_argument("--target", type=Path)
    current.add_argument("--legacy-root", type=Path)
    current.add_argument("--no-launch", action="store_true")
    latest = subparsers.add_parser("install-latest", help="download, verify, and install the latest release")
    latest.add_argument("--current-root", type=Path, required=True)
    latest.add_argument("--current", required=True)
    latest.add_argument("--wait-pid", type=int, default=0)
    latest.add_argument("--legacy-root", type=Path)
    latest.add_argument("--no-launch", action="store_true")
    handoff = subparsers.add_parser(
        "handoff-legacy-launcher",
        help="redirect one legacy LensLedger shortcut to the managed installation",
    )
    handoff.add_argument("--legacy-root", type=Path, required=True)
    handoff.add_argument("--target", type=Path, default=managed_install_root())
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = check_for_update(args.current)
        elif args.command == "install-current":
            result = install_current(args.source, args.target, args.legacy_root, not args.no_launch)
        elif args.command == "handoff-legacy-launcher":
            result = handoff_legacy_launcher(args.legacy_root, args.target)
        else:
            result = install_latest(
                args.current_root, args.current, args.wait_pid, args.legacy_root,
                not args.no_launch,
            )
        print(json.dumps(result, indent=2))
        return 0
    except UpdateError as exc:
        print(f"Update failed: {exc}", file=sys.stderr)
        if args.command == "install-latest" and args.wait_pid:
            current_root = args.current_root.resolve()
            if (current_root / "src" / "photo_search.py").is_file():
                try:
                    launch_lensledger(current_root)
                    print("The previous LensLedger copy was restarted.", file=sys.stderr)
                except OSError as restart_error:
                    print(f"The previous copy could not restart: {restart_error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
