#!/usr/bin/env python3
"""LensLedger localhost-only photo review and metadata staging interface."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import ExifTags, Image, IptcImagePlugin

from app_paths import (
    backup_root, default_library_root, libraries_root, review_bin_root, settings_path,
)
from face_learning import learn as learn_faces
from photo_index import (
    connect, extract_xmp_keywords, rebuild_search_row, scan_library,
    set_source_tags, sync_person_tags, utc_now,
)
from product import APP_NAME, APP_TAGLINE, APP_VERSION


TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
PAGE_SIZE = 250
PUBLISHABLE_EXTENSIONS = {".jpg", ".jpeg"}
EXIFTOOL_PATH = Path(__file__).parent / "tools" / "ExifTool" / "ExifTool.exe"
BACKUP_ROOT = backup_root()
LIBRARY_STATE_PATH = settings_path()
LIBRARY_DATABASE_ROOT = libraries_root()
DEFAULT_LIBRARY_ROOT = default_library_root()


def clean_tag(value: str) -> str:
    return " ".join(value.strip().split())[:120]


def split_tags(value: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in value.split(";"):
        tag = clean_tag(part)
        if tag and tag.casefold() not in seen:
            result.append(tag)
            seen.add(tag.casefold())
    return result


def _text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace").strip("\x00")
    return str(value).strip()


def _gps_decimal(values, reference: str) -> float | None:
    try:
        degrees, minutes, seconds = (float(part) for part in values)
        result = degrees + minutes / 60 + seconds / 3600
        return -result if reference.upper() in {"S", "W"} else result
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _xmp_fields(image: Image.Image) -> dict[str, str]:
    wanted = {
        "title": "Title", "headline": "Headline", "description": "Description", "subject": "Embedded keywords",
        "creator": "Creator", "rights": "Copyright", "personinimage": "People shown",
        "event": "Event", "location": "Location", "city": "City",
        "state": "State / province", "country": "Country",
    }
    found: dict[str, list[str]] = {}
    for marker, payload in getattr(image, "applist", []):
        if marker != "APP1" or b"ns.adobe.com/xap" not in payload:
            continue
        start = payload.find(b"<")
        if start < 0:
            continue
        try:
            root = ET.fromstring(payload[start:])
        except ET.ParseError:
            continue
        for element in root.iter():
            local = element.tag.rsplit("}", 1)[-1].casefold()
            namespace = element.tag[1:].split("}", 1)[0] if element.tag.startswith("{") else ""
            # rdf:Description is only an XML container. Treating it as
            # dc:description incorrectly copied descendant keywords into the
            # photo description (for example, the old "Fast food" tag).
            if local in wanted and "rdf-syntax-ns" not in namespace:
                values = [_text_value(node.text) for node in element.iter() if node.tag.rsplit("}", 1)[-1] == "li" and node.text]
                if not values and element.text and element.text.strip():
                    values = [_text_value(element.text)]
                found.setdefault(wanted[local], []).extend(value for value in values if value)
            for name, value in element.attrib.items():
                attr_local = name.rsplit("}", 1)[-1].casefold()
                if attr_local in wanted and _text_value(value):
                    found.setdefault(wanted[attr_local], []).append(_text_value(value))
    return {label: ", ".join(dict.fromkeys(values)) for label, values in found.items() if values}


def read_embedded_metadata(path: Path) -> dict[str, list[dict[str, str]]]:
    descriptive: dict[str, str] = {}
    capture: dict[str, str] = {}
    gps_link = ""
    if path.suffix.lower() not in {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp"}:
        return {"descriptive": [], "capture": [], "description": ""}
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            main = {ExifTags.TAGS.get(key, key): value for key, value in exif.items()}
            for source, label in (("ImageDescription", "Description"), ("Artist", "Creator"), ("Copyright", "Copyright")):
                if source in main and _text_value(main[source]):
                    descriptive[label] = _text_value(main[source])
            make = _text_value(main.get("Make", "")); model = _text_value(main.get("Model", ""))
            if make or model:
                capture["Camera"] = " ".join(part for part in (make, model) if part)
            if _text_value(main.get("Software", "")):
                capture["Software"] = _text_value(main["Software"])

            try:
                details = {ExifTags.TAGS.get(key, key): value for key, value in exif.get_ifd(ExifTags.IFD.Exif).items()}
            except (KeyError, TypeError, ValueError):
                details = {}
            capture_date = details.get("DateTimeOriginal") or main.get("DateTime")
            if capture_date:
                date_text = _text_value(capture_date)
                capture["Date taken"] = date_text[:10].replace(":", "-") + date_text[10:]
            for source, label in (("LensModel", "Lens"), ("ISOSpeedRatings", "ISO"), ("PhotographicSensitivity", "ISO")):
                if source in details and label not in capture:
                    capture[label] = _text_value(details[source])
            if "ExposureTime" in details:
                exposure = float(details["ExposureTime"])
                capture["Exposure"] = f"1/{round(1 / exposure)} s" if 0 < exposure < 1 else f"{exposure:g} s"
            if "FNumber" in details:
                capture["Aperture"] = f"f/{float(details['FNumber']):g}"
            if "FocalLength" in details:
                capture["Focal length"] = f"{float(details['FocalLength']):g} mm"

            try:
                gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
                latitude = _gps_decimal(gps.get(2), _text_value(gps.get(1, "")))
                longitude = _gps_decimal(gps.get(4), _text_value(gps.get(3, "")))
                if latitude is not None and longitude is not None:
                    capture["GPS"] = f"{latitude:.6f}, {longitude:.6f}"
                    gps_link = f"https://www.openstreetmap.org/?mlat={latitude:.6f}&mlon={longitude:.6f}#map=16/{latitude:.6f}/{longitude:.6f}"
            except (KeyError, TypeError, ValueError):
                pass

            iptc = IptcImagePlugin.getiptcinfo(image) or {}
            iptc_map = {
                (2, 5): "Title", (2, 80): "Creator", (2, 90): "City",
                (2, 92): "Location", (2, 95): "State / province", (2, 101): "Country",
                (2, 105): "Headline", (2, 116): "Copyright", (2, 120): "Description",
            }
            for key, label in iptc_map.items():
                value = iptc.get(key)
                if value:
                    values = value if isinstance(value, list) else [value]
                    descriptive[label] = ", ".join(_text_value(item) for item in values if _text_value(item))
            keywords = iptc.get((2, 25))
            if keywords:
                values = keywords if isinstance(keywords, list) else [keywords]
                descriptive["Embedded keywords"] = ", ".join(_text_value(item) for item in values if _text_value(item))
            descriptive.update(_xmp_fields(image))
    except (OSError, ValueError):
        pass
    return {
        "descriptive": [{"label": label, "value": value} for label, value in descriptive.items()],
        "capture": [
            {"label": label, "value": value, **({"href": gps_link} if label == "GPS" and gps_link else {})}
            for label, value in capture.items()
        ],
        "description": descriptive.get("Description", ""),
    }


def _pixel_hash(path: Path) -> str:
    """Hash decoded pixels so metadata-only publishing cannot alter the picture."""
    with Image.open(path) as image:
        image.load()
        digest = hashlib.sha256()
        digest.update(f"{image.mode}:{image.size[0]}x{image.size[1]}".encode("ascii"))
        digest.update(image.tobytes())
        return digest.hexdigest()


def _run_exiftool(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    if not EXIFTOOL_PATH.is_file():
        raise ValueError("The metadata publishing tool is not installed")
    result = subprocess.run(
        [str(EXIFTOOL_PATH), *arguments], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, check=False,
    )
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or "Metadata publishing failed").strip())
    return result


def _exiftool_values(path: Path) -> dict[str, object]:
    result = _run_exiftool([
        "-j", "-G1", "-struct", "-EXIF:ImageDescription", "-XMP-dc:Description",
        "-IPTC:Caption-Abstract", "-XMP-dc:Title", "-IPTC:ObjectName",
        "-XMP-photoshop:Headline", "-XMP-dc:Subject", "-IPTC:Keywords",
        "-XMP-microsoft:LastKeywordXMP", "-XMP-iptcExt:PersonInImage", str(path),
    ])
    rows = json.loads(result.stdout)
    return rows[0] if rows else {}


def library_db_path(root: Path) -> Path:
    root = root.resolve()
    if root == DEFAULT_LIBRARY_ROOT.resolve():
        return LIBRARY_DATABASE_ROOT / "default.sqlite3"
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "photo-library"
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:12]
    return LIBRARY_DATABASE_ROOT / f"{label}-{digest}.sqlite3"


def load_library_state() -> Path:
    config = load_library_config()
    value = config.get("current_root", "")
    if value:
        root = Path(value).resolve()
        if root.is_dir():
            return root
    return DEFAULT_LIBRARY_ROOT.resolve()


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


def save_library_state(root: Path) -> None:
    LIBRARY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = load_library_config()
    libraries = [str(Path(item).resolve()) for item in config.get("libraries", [])]
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


class SearchHandler(BaseHTTPRequestHandler):
    db_path: Path
    library_root: Path
    csrf_token: str
    library_lock = threading.Lock()
    library_job: dict[str, object] = {"state": "idle", "message": ""}
    library_cancel = threading.Event()

    def db(self):
        return connect(self.db_path)

    def send_bytes(self, data: bytes, content_type: str, status: int = 200, cache: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, body: str, status: int = 200):
        self.send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status)

    def send_json(self, value, status: int = 200):
        self.send_bytes(json.dumps(value).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(url.query)
        if url.path == "/logo.png":
            return self.serve_logo()
        if url.path == "/world-map.svg":
            return self.serve_world_map()
        if url.path == "/media":
            return self.serve_media(params)
        if url.path == "/api/asset":
            return self.asset_detail(params)
        if url.path == "/api/trash":
            return self.trash_history()
        if url.path == "/api/library/status":
            return self.library_status()
        if url.path == "/api/library/options":
            return self.library_options()
        if url.path == "/api/map/points":
            return self.map_points()
        if url.path == "/api/people/review/queue":
            return self.people_review_queue(params)
        if url.path == "/people-review":
            return self.people_review_page(params)
        if url.path == "/map":
            return self.map_page()
        if url.path != "/":
            return self.send_error(404)
        return self.viewer_page(params)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 100_000:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if body.get("csrf") != self.csrf_token:
                return self.send_json({"error": "invalid request token"}, 403)
            route = urllib.parse.urlparse(self.path).path
            if route == "/api/subject":
                return self.update_subject(body)
            if route == "/api/tag/add":
                return self.add_tag(body)
            if route == "/api/folder-tag/add":
                return self.add_folder_tag(body)
            if route == "/api/tag/remove":
                return self.remove_tag(body)
            if route == "/api/tag/restore":
                return self.restore_tag(body)
            if route == "/api/person/add":
                return self.add_person(body)
            if route == "/api/person/state":
                return self.set_person_state(body)
            if route == "/api/person/aliases":
                return self.set_person_aliases(body)
            if route == "/api/person/names":
                return self.set_person_names(body)
            if route == "/api/people/review/decision":
                return self.people_review_decision(body)
            if route == "/api/people/review/batch":
                return self.people_review_batch_decision(body)
            if route == "/api/people/review/undo":
                return self.undo_people_review(body)
            if route == "/api/people/review/batch-undo":
                return self.undo_people_review_batch(body)
            if route == "/api/people/learn":
                return self.learn_people(body)
            if route == "/api/review-bin":
                return self.move_to_review_bin(body)
            if route == "/api/review-bin/restore":
                return self.restore_from_review_bin(body)
            if route == "/api/publish/preview":
                return self.preview_publish(body)
            if route == "/api/publish":
                return self.publish_metadata(body)
            if route == "/api/publish/restore":
                return self.restore_published_metadata(body)
            if route == "/api/library/browse":
                return self.browse_library(body)
            if route == "/api/library/open":
                return self.open_library(body)
            if route == "/api/library/cancel":
                return self.cancel_library_scan(body)
            return self.send_json({"error": "not found"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, 500)

    def get_active_asset(self, con: sqlite3.Connection, asset_id: int):
        row = con.execute(
            "SELECT * FROM assets WHERE id=? AND in_review_bin=0", (asset_id,)
        ).fetchone()
        if not row:
            raise ValueError("photo is no longer available")
        return row

    def onboarding_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Set up {APP_NAME}</title><link rel="icon" href="/logo.png"><style>
:root{{--bg:#0d1018;--panel:#171b26;--panel2:#202638;--line:#35405a;--text:#f4f6fb;--muted:#9ba8bd;--cyan:#16bde9;--violet:#8b55ff;--green:#58b76b;--danger:#e25c70}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:radial-gradient(circle at 50% -20%,#273358 0,#111622 42%,var(--bg) 72%);color:var(--text);font:15px system-ui,Segoe UI,sans-serif}}button,input{{font:inherit}}button{{border:0;border-radius:8px;padding:11px 15px;background:#3d6fe8;color:white;font-weight:750;cursor:pointer}}button.secondary{{background:#30384b}}button.danger{{background:#793746}}button:disabled{{opacity:.45;cursor:default}}
.shell{{width:min(1040px,calc(100% - 36px));margin:auto;padding:34px 0 50px}}.brand{{display:flex;align-items:center;justify-content:center;gap:14px;margin-bottom:26px}}.brand img{{width:62px;height:62px;object-fit:contain}}.brand h1{{margin:0;font-size:30px}}.brand p{{margin:3px 0 0;color:var(--muted)}}.version{{color:#c5b7ff;background:#292341;border:1px solid #4a3f75;border-radius:999px;padding:4px 9px;font-size:11px;font-weight:800}}
.card{{background:#171b26ed;border:1px solid var(--line);border-radius:16px;box-shadow:0 22px 70px #0008;padding:28px}}.intro{{text-align:center;max-width:720px;margin:0 auto 25px}}.intro h2{{font-size:30px;margin:0 0 9px}}.intro p{{color:#c3ccda;line-height:1.55;margin:0}}.steps{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:22px 0}}.step{{background:#111722;border:1px solid #2f3a51;border-radius:11px;padding:14px}}.step strong{{display:block;color:var(--cyan);margin-bottom:5px}}.step span{{color:var(--muted);font-size:13px;line-height:1.4}}
.chooser{{border-top:1px solid var(--line);padding-top:21px}}.chooser h3{{margin:0 0 5px;font-size:18px}}.chooser>p{{margin:0 0 14px;color:var(--muted)}}.suggestions{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:9px;margin-bottom:14px}}.suggestion{{text-align:left;background:#222a3b;border:1px solid #3c4964;overflow:hidden}}.suggestion strong,.suggestion small{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.suggestion small{{font-weight:400;color:var(--muted);margin-top:3px}}.path-row{{display:flex;gap:8px}}.path-row input{{min-width:0;flex:1;border:1px solid #46506a;border-radius:8px;background:#232a3a;color:white;padding:11px 12px}}.actions{{display:flex;align-items:center;gap:9px;margin-top:13px}}.actions .spacer{{flex:1}}.privacy{{color:var(--muted);font-size:12px;line-height:1.45;max-width:600px}}
.progress-panel{{display:none;margin-top:20px;padding:20px;background:#111722;border:1px solid #35435f;border-radius:12px}}.progress-panel.open{{display:block}}.progress-head{{display:flex;align-items:start;gap:12px}}.progress-head h3{{margin:0 0 4px}}.progress-head p{{margin:0;color:var(--cyan)}}.progress-head .spacer{{flex:1}}.bar{{height:8px;background:#252d3d;border-radius:999px;overflow:hidden;margin:17px 0}}.bar span{{display:block;width:35%;height:100%;background:linear-gradient(90deg,var(--violet),var(--cyan));border-radius:999px;animation:move 1.25s infinite alternate ease-in-out}}@keyframes move{{from{{transform:translateX(-80%)}}to{{transform:translateX(265%)}}}}.metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}}.metric{{background:#1c2331;border-radius:8px;padding:10px;text-align:center}}.metric strong{{display:block;font-size:20px}}.metric span{{color:var(--muted);font-size:11px}}.complete .bar{{display:none}}.complete .progress-head p{{color:var(--green)}}.complete-grid{{display:none;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:15px}}.complete .complete-grid{{display:grid}}.complete-grid div{{background:#1d2632;border:1px solid #344456;border-radius:9px;padding:12px}}.complete-grid strong{{font-size:23px;display:block}}.complete-grid span{{color:var(--muted);font-size:12px}}.completion-actions{{display:none;justify-content:flex-end;margin-top:15px}}.complete .completion-actions{{display:flex}}.error{{color:var(--danger)!important}}
@media(max-width:720px){{.steps,.metrics,.complete-grid{{grid-template-columns:1fr 1fr}}.path-row{{flex-wrap:wrap}}.path-row input{{flex-basis:100%}}.card{{padding:20px}}}}
</style></head><body><main class="shell"><header class="brand"><img src="/logo.png" alt=""><div><h1>{APP_NAME}</h1><p>{APP_TAGLINE}</p></div><span class="version">v{APP_VERSION}</span></header><section class="card">
<div class="intro"><h2>Let’s find your photo library</h2><p>Choose a folder that contains photos or videos. LensLedger will build a private, searchable inventory without moving, renaming, uploading, or changing your files.</p></div>
<div class="steps"><div class="step"><strong>1 · Discover</strong><span>Record file locations, types, dates, and locally available metadata.</span></div><div class="step"><strong>2 · Review</strong><span>See exactly what was found, including cloud files that are not downloaded.</span></div><div class="step"><strong>3 · Enrich</strong><span>Add subjects, people, OCR, and approved metadata at your pace.</span></div></div>
<section class="chooser"><h3>Choose your first library</h3><p>You can add and switch between more libraries later. Start with the folder that best represents one photo collection.</p><div class="suggestions" id="suggestions"></div><div class="path-row"><input id="libraryPath" aria-label="Photo library folder" placeholder="C:\\Users\\you\\Pictures"><button type="button" class="secondary" id="browse">Browse…</button></div><div class="actions"><span class="privacy">🔒 The index stays on this computer. Cloud placeholders are counted without forcing a download.</span><span class="spacer"></span><button type="button" id="start">Build my library</button></div></section>
<section class="progress-panel" id="progressPanel" aria-live="polite"><div class="progress-head"><div><h3 id="progressTitle">Building your library</h3><p id="progressMessage">Preparing scan…</p></div><span class="spacer"></span><button type="button" class="danger" id="cancel">Pause scan</button></div><div class="bar"><span></span></div><div class="metrics"><div class="metric"><strong id="scanned">0</strong><span>discovered</span></div><div class="metric"><strong id="changed">0</strong><span>indexed</span></div><div class="metric"><strong id="unchanged">0</strong><span>unchanged</span></div><div class="metric"><strong id="placeholders">0</strong><span>cloud-only</span></div><div class="metric"><strong id="errors">0</strong><span>errors</span></div></div><div class="complete-grid" id="completeGrid"></div><div class="completion-actions"><button type="button" id="enterLibrary">Open my library</button></div></section>
</section></main><script>
const csrf={json.dumps(self.csrf_token)};const $=id=>document.getElementById(id);let polling=false;
async function api(path,payload){{const response=await fetch(path,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{...payload,csrf}})}});const data=await response.json();if(!response.ok)throw new Error(data.error||'Request failed');return data}}
function choice(item,labelSuffix=''){{const button=document.createElement('button');button.type='button';button.className='suggestion';const strong=document.createElement('strong');strong.textContent=item.label+labelSuffix;const small=document.createElement('small');small.textContent=item.path;button.append(strong,small);button.onclick=()=>{{$('libraryPath').value=item.path;$('start').focus()}};return button}}
async function loadOptions(){{const response=await fetch('/api/library/options');const data=await response.json();const seen=new Set();const buttons=[];for(const item of data.known||[]){{seen.add(item.path.toLowerCase());buttons.push(choice(item,' · indexed before'))}}for(const item of data.suggestions||[])if(!seen.has(item.path.toLowerCase()))buttons.push(choice(item));$('suggestions').replaceChildren(...buttons);if(!$('libraryPath').value&&buttons.length)buttons[0].click()}}
function number(value){{return Number(value||0).toLocaleString()}}
function showProgress(job){{$('progressPanel').classList.add('open');for(const key of ['scanned','changed','unchanged','placeholders','errors'])$(key).textContent=number(job[key]);$('progressMessage').textContent=job.message||job.state;if(job.state==='error'){{$('progressMessage').className='error';$('cancel').hidden=true;$('start').textContent='Try again';$('start').disabled=false;polling=false;return}}if(job.state==='cancelled'){{$('progressTitle').textContent='Scan paused safely';$('cancel').hidden=true;$('start').textContent='Resume scan';$('start').disabled=false;polling=false;return}}if(job.state==='complete'){{polling=false;$('progressPanel').classList.add('complete');$('progressTitle').textContent='Your library is ready';$('cancel').hidden=true;$('start').textContent='Scan complete';$('start').disabled=true;const summary=job.summary||{{}};const values=[['Media files',summary.assets],['Images',summary.images],['Videos',summary.videos],['RAW files',summary.raw_files],['Metadata ready',summary.metadata_ready],['Cloud-only',summary.placeholders]];$('completeGrid').replaceChildren(...values.map(([label,value])=>{{const box=document.createElement('div');const strong=document.createElement('strong');strong.textContent=number(value);const span=document.createElement('span');span.textContent=label;box.append(strong,span);return box}}));return}}if(polling)setTimeout(poll,350)}}
async function poll(){{try{{const response=await fetch('/api/library/status');showProgress(await response.json())}}catch(error){{$('progressMessage').textContent=error.message;$('progressMessage').className='error';polling=false}}}}
async function startScan(){{const path=$('libraryPath').value.trim();if(!path)return;$('start').disabled=true;$('start').textContent='Scanning…';$('cancel').hidden=false;$('progressPanel').className='progress-panel open';$('progressTitle').textContent='Building your library';$('progressMessage').className='';try{{await api('/api/library/open',{{path}});polling=true;poll()}}catch(error){{$('progressPanel').classList.add('open');$('progressMessage').textContent=error.message;$('progressMessage').className='error';$('start').disabled=false}}}}
$('browse').onclick=async()=>{{$('browse').disabled=true;try{{const result=await api('/api/library/browse',{{}});if(result.path)$('libraryPath').value=result.path}}catch(error){{$('progressPanel').classList.add('open');$('progressMessage').textContent=error.message;$('progressMessage').className='error'}}finally{{$('browse').disabled=false}}}};$('start').onclick=startScan;$('cancel').onclick=async()=>{{$('cancel').disabled=true;try{{await api('/api/library/cancel',{{}})}}catch(error){{$('progressMessage').textContent=error.message}}}};$('enterLibrary').onclick=()=>location.href='/?sort=newest';loadOptions();fetch('/api/library/status').then(r=>r.json()).then(job=>{{if(job.state==='scanning'){{polling=true;showProgress(job)}}}});
</script></body></html>"""
        self.send_html(page)

    def map_points(self):
        with self.db() as con:
            located = int(con.execute(
                """SELECT COUNT(*) FROM assets WHERE in_review_bin=0
                   AND gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL"""
            ).fetchone()[0])
            pending = int(con.execute(
                "SELECT COUNT(*) FROM assets WHERE in_review_bin=0 AND location_scanned=0"
            ).fetchone()[0])
            rows = con.execute(
                """SELECT MIN(id) AS asset_id, AVG(gps_latitude) AS latitude,
                          AVG(gps_longitude) AS longitude, COUNT(*) AS photo_count,
                          MIN(capture_date) AS first_date, MAX(capture_date) AS last_date,
                          MIN(filename) AS filename
                   FROM assets
                   WHERE in_review_bin=0 AND gps_latitude IS NOT NULL
                         AND gps_longitude IS NOT NULL
                   GROUP BY ROUND(gps_latitude, 1), ROUND(gps_longitude, 1)
                   ORDER BY photo_count DESC, first_date DESC
                   LIMIT 5000"""
            ).fetchall()
        self.send_json({
            "located": located,
            "pending": pending,
            "clusters": [dict(row) for row in rows],
        })

    def map_page(self):
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Photo map — {APP_NAME}</title><link rel="icon" href="/logo.png"><style>
:root{{--bg:#0c1119;--panel:#171d29;--line:#35415a;--text:#f3f6fb;--muted:#9aa8bc;--cyan:#16bde9;--violet:#8b55ff}}
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--text);font:14px system-ui,Segoe UI,sans-serif}}button,a{{font:inherit}}button,.button{{border:0;border-radius:8px;padding:9px 12px;background:#30394d;color:white;font-weight:750;cursor:pointer;text-decoration:none}}button:hover,.button:hover{{background:#3c4861}}
body{{display:grid;grid-template-rows:auto minmax(0,1fr)}}header{{display:flex;align-items:center;gap:11px;padding:10px 14px;background:#151b27;border-bottom:1px solid var(--line);z-index:4}}header img{{width:36px;height:36px;object-fit:contain}}header h1{{font-size:18px;margin:0}}header p{{color:var(--muted);margin:2px 0 0;font-size:12px}}.spacer{{flex:1}}.count{{color:#c9baff;background:#2a2442;border:1px solid #463d6c;border-radius:999px;padding:6px 10px}}.map-shell{{position:relative;min-height:0;overflow:hidden;background:#09101a;cursor:grab}}.map-shell.dragging{{cursor:grabbing}}#world{{position:absolute;width:1440px;height:720px;transform-origin:0 0;background:url('/world-map.svg') center/100% 100% no-repeat;box-shadow:0 25px 80px #0009}}.marker{{position:absolute;transform:translate(-50%,-50%);min-width:18px;height:18px;border:2px solid #dff9ff;border-radius:999px;background:#08a9d7;color:white;box-shadow:0 0 0 5px #0cbde52e,0 4px 14px #000;padding:0 5px;font-size:9px;font-weight:900;line-height:14px;cursor:pointer}}.marker.multi{{background:#754de2;border-color:#eee8ff;box-shadow:0 0 0 6px #8b55ff30,0 4px 14px #000}}.marker:hover,.marker:focus{{z-index:2;outline:2px solid white;outline-offset:2px}}
.controls{{position:absolute;left:14px;top:14px;display:grid;gap:6px;z-index:3}}.controls button{{width:38px;height:38px;padding:0;font-size:20px;background:#202a3be8;border:1px solid #43506a}}.legend{{position:absolute;left:14px;bottom:14px;background:#151c29e8;border:1px solid var(--line);border-radius:9px;padding:10px 12px;color:var(--muted);line-height:1.45;z-index:3;max-width:310px}}.legend strong{{display:block;color:var(--text)}}.details{{position:absolute;right:14px;top:14px;width:min(340px,calc(100% - 28px));display:none;background:#171e2ceF;border:1px solid #46536e;border-radius:12px;overflow:hidden;box-shadow:0 18px 50px #000b;z-index:4}}.details.open{{display:block}}.details img{{display:block;width:100%;height:210px;object-fit:cover;background:#090c12}}.details-body{{padding:14px}}.details h2{{margin:0 0 5px;font-size:17px}}.details p{{margin:4px 0;color:var(--muted)}}.details-actions{{display:flex;gap:8px;margin-top:12px}}.details-actions .button{{background:#3d6fe8}}.empty{{position:absolute;inset:0;display:none;place-items:center;text-align:center;padding:30px;z-index:2;background:#0c1119d9}}.empty.open{{display:grid}}.empty h2{{margin:0 0 8px}}.empty p{{color:var(--muted);max-width:560px;line-height:1.5}}
@media(max-width:700px){{header p{{display:none}}.count{{display:none}}.details{{top:auto;bottom:14px}}.details img{{height:150px}}}}
</style></head><body><header><img src="/logo.png" alt=""><div><h1>Photo map</h1><p>Embedded locations from the current library · read-only and kept local</p></div><span class="spacer"></span><span class="count" id="count">Loading locations…</span><a class="button" href="/">Back to library</a></header>
<main class="map-shell" id="viewport"><div id="world"></div><div class="controls"><button type="button" id="zoomIn" aria-label="Zoom in">+</button><button type="button" id="zoomOut" aria-label="Zoom out">−</button><button type="button" id="reset" aria-label="Reset map">⌂</button></div><div class="legend"><strong>Photo locations</strong>Scroll to zoom and drag to pan. Nearby coordinates are grouped; select a marker to open its representative photo.</div><aside class="details" id="details"><img id="preview" alt="Representative photo from this location"><div class="details-body"><h2 id="placeTitle"></h2><p id="placeDates"></p><p id="placeCoords"></p><div class="details-actions"><a class="button" id="openPhoto">Open photo</a><button type="button" id="closeDetails">Close</button></div></div></aside><section class="empty" id="empty"><div><h2>No mapped photos yet</h2><p id="emptyText">Run an incremental library scan to collect embedded GPS coordinates. LensLedger reads them locally and never writes location data back to your files.</p><a class="button" href="/">Return to library</a></div></section></main>
<script>
const viewport=document.getElementById('viewport'),world=document.getElementById('world'),details=document.getElementById('details');let zoom=1,panX=0,panY=0,drag=null,clusters=[];
function baseScale(){{return Math.min(viewport.clientWidth/1440,viewport.clientHeight/720)}}
function transform(){{const scale=baseScale()*zoom;const x=(viewport.clientWidth-1440*scale)/2+panX;const y=(viewport.clientHeight-720*scale)/2+panY;world.style.transform=`translate(${{x}}px,${{y}}px) scale(${{scale}})`}}
function marker(point){{const button=document.createElement('button');button.type='button';button.className='marker'+(point.photo_count>1?' multi':'');button.style.left=((point.longitude+180)/360*1440)+'px';button.style.top=((90-point.latitude)/180*720)+'px';button.textContent=point.photo_count>1?point.photo_count:'';button.title=point.photo_count.toLocaleString()+' photo'+(point.photo_count===1?'':'s');button.setAttribute('aria-label',button.title+' at '+point.latitude.toFixed(3)+', '+point.longitude.toFixed(3));button.onclick=event=>{{event.stopPropagation();show(point)}};return button}}
function show(point){{document.getElementById('preview').src='/media?id='+point.asset_id;document.getElementById('placeTitle').textContent=point.photo_count.toLocaleString()+' photo'+(point.photo_count===1?'':'s')+' near this location';document.getElementById('placeDates').textContent=point.first_date===point.last_date?(point.first_date||'Date unknown'):(point.first_date||'Unknown')+' – '+(point.last_date||'Unknown');document.getElementById('placeCoords').textContent=point.latitude.toFixed(5)+', '+point.longitude.toFixed(5);document.getElementById('openPhoto').href='/?date='+(point.first_date||'')+'&selected='+point.asset_id;details.classList.add('open')}}
function setZoom(next,cx=viewport.clientWidth/2,cy=viewport.clientHeight/2){{const prior=zoom;zoom=Math.max(1,Math.min(8,next));if(zoom===prior)return;panX=(panX-cx)*(zoom/prior)+cx;panY=(panY-cy)*(zoom/prior)+cy;transform()}}
viewport.onwheel=event=>{{event.preventDefault();const box=viewport.getBoundingClientRect();setZoom(zoom*(event.deltaY<0?1.25:.8),event.clientX-box.left,event.clientY-box.top)}};viewport.onpointerdown=event=>{{if(event.target.closest('button,a,.details'))return;drag={{x:event.clientX,y:event.clientY,px:panX,py:panY}};viewport.setPointerCapture(event.pointerId);viewport.classList.add('dragging')}};viewport.onpointermove=event=>{{if(!drag)return;panX=drag.px+event.clientX-drag.x;panY=drag.py+event.clientY-drag.y;transform()}};viewport.onpointerup=()=>{{drag=null;viewport.classList.remove('dragging')}};
document.getElementById('zoomIn').onclick=()=>setZoom(zoom*1.4);document.getElementById('zoomOut').onclick=()=>setZoom(zoom/1.4);document.getElementById('reset').onclick=()=>{{zoom=1;panX=panY=0;transform()}};document.getElementById('closeDetails').onclick=()=>details.classList.remove('open');window.onresize=transform;
fetch('/api/map/points').then(response=>response.json()).then(data=>{{clusters=data.clusters||[];document.getElementById('count').textContent=Number(data.located||0).toLocaleString()+' located photos · '+clusters.length.toLocaleString()+' places';world.append(...clusters.map(marker));if(!clusters.length){{document.getElementById('empty').classList.add('open');if(data.pending)document.getElementById('emptyText').textContent=Number(data.pending).toLocaleString()+' cataloged files still need a location scan. Rescan this library from the library menu, then return here.'}}transform()}}).catch(error=>{{document.getElementById('empty').classList.add('open');document.getElementById('emptyText').textContent=error.message}});transform();
</script></body></html>"""
        self.send_html(page)

    def viewer_page(self, params):
        with self.db() as con:
            if int(con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0]) == 0:
                return self.onboarding_page()
        query = params.get("q", [""])[0].strip()
        selected_date = params.get("date", [""])[0]
        scope = params.get("scope", ["all"])[0]
        requested_person = params.get("person", [""])[0]
        person_id = int(requested_person) if requested_person.isdigit() else None
        sort = params.get("sort", ["oldest" if selected_date else "newest"])[0]
        if scope not in {"image", "context", "people", "all"}:
            scope = "image"
        if sort not in {"newest", "oldest", "name"}:
            sort = "oldest"
        try:
            page_number = max(1, int(params.get("page", ["1"])[0]))
        except ValueError:
            page_number = 1

        clauses = ["a.in_review_bin=0"]
        values: list[object] = []
        if scope == "people" and person_id:
            clauses.append("""EXISTS (
                SELECT 1 FROM asset_people ap
                WHERE ap.asset_id=a.id AND ap.person_id=? AND ap.state='confirmed'
            )""")
            values.append(person_id)
        if selected_date:
            try:
                dt.date.fromisoformat(selected_date)
                clauses.append("a.capture_date=?")
                values.append(selected_date)
            except ValueError:
                selected_date = ""
        tokens = TOKEN_RE.findall(query)
        if tokens and scope == "all":
            clauses.append("""(search_fts MATCH ? OR EXISTS (
                SELECT 1 FROM asset_people ap JOIN people p ON p.id=ap.person_id
                LEFT JOIN person_aliases pa ON pa.person_id=p.id
                WHERE ap.asset_id=a.id AND ap.state='confirmed'
                  AND (p.name LIKE ? OR pa.alias LIKE ?)
            ))""")
            person_pattern = f"%{query}%"
            values.extend([" AND ".join(f'\"{token}\"' for token in tokens), person_pattern, person_pattern])
        elif tokens and scope != "people":
            sources = "('subject','asset_rule','embedded_xmp','person')" if scope == "image" else "('folder_rule')"
            for token in tokens:
                pattern = f"%{token}%"
                tag_clause = f"""EXISTS (
                    SELECT 1 FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                    WHERE at.asset_id=a.id AND at.source IN {sources} AND t.name LIKE ?
                      AND NOT EXISTS (
                          SELECT 1 FROM asset_tag_exclusions e
                          WHERE e.relative_path=a.relative_path AND e.tag=t.name
                      )
                )"""
                if scope == "image":
                    clauses.append(f"({tag_clause} OR EXISTS (SELECT 1 FROM text_data x WHERE x.asset_id=a.id AND (x.ocr_text LIKE ? OR x.caption LIKE ?)))")
                    values.extend([pattern, pattern, pattern])
                else:
                    clauses.append(f"({tag_clause} OR a.folder LIKE ?)")
                    values.extend([pattern, pattern])

        where = "WHERE " + " AND ".join(clauses)
        order = {
            "newest": "a.capture_date DESC, a.relative_path DESC",
            "oldest": "a.capture_date ASC, a.relative_path ASC",
            "name": "a.filename COLLATE NOCASE ASC, a.relative_path ASC",
        }[sort]
        error = ""
        rows = []
        people_cards = []
        selected_person_name = ""
        total = 0
        trash_count = 0
        review_count = 0
        try:
            with self.db() as con:
                trash_count = int(con.execute(
                    "SELECT COUNT(*) FROM review_bin WHERE restored_at IS NULL"
                ).fetchone()[0])
                review_count = int(con.execute(
                    "SELECT COUNT(*) FROM asset_people WHERE state='suggested'"
                ).fetchone()[0])
                if scope == "people" and not person_id:
                    people_query = f"%{query}%"
                    people_rows = con.execute(
                        """SELECT p.id,p.name,
                               (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0) confirmed_count,
                               (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0) suggested_count,
                               COALESCE(
                                 (SELECT ap.asset_id FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                  WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0 AND a.media_type='image'
                                  ORDER BY a.capture_date DESC,a.id DESC LIMIT 1),
                                 (SELECT ap.asset_id FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                                  WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0 AND a.media_type='image'
                                  ORDER BY ap.confidence DESC,a.id DESC LIMIT 1)
                               ) representative_id
                            FROM people p
                            WHERE ?='' OR p.name LIKE ? OR EXISTS (
                                SELECT 1 FROM person_aliases pa WHERE pa.person_id=p.id AND pa.alias LIKE ?
                            ) ORDER BY p.name COLLATE NOCASE""",
                        (query, people_query, people_query),
                    ).fetchall()
                    for person in people_rows:
                        aliases = [row[0] for row in con.execute(
                            "SELECT alias FROM person_aliases WHERE person_id=? ORDER BY alias COLLATE NOCASE",
                            (person["id"],),
                        )]
                        people_cards.append(dict(person) | {"aliases": aliases})
                    total = len(people_cards)
                else:
                    if scope == "people" and person_id:
                        person_row = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
                        if not person_row:
                            person_id = None
                            raise ValueError("That person is no longer available")
                        selected_person_name = person_row[0]
                    total = int(con.execute(
                        f"SELECT COUNT(*) FROM search_fts JOIN assets a ON a.id=search_fts.asset_id {where}", values
                    ).fetchone()[0])
                    rows = con.execute(
                        f"""SELECT a.id,a.filename,a.folder,a.capture_date,a.media_type
                            FROM search_fts JOIN assets a ON a.id=search_fts.asset_id
                            {where} ORDER BY {order} LIMIT ? OFFSET ?""",
                        values + [PAGE_SIZE, (page_number - 1) * PAGE_SIZE],
                    ).fetchall()
        except sqlite3.Error as exc:
            error = str(exc)

        items = [dict(row) for row in rows]
        requested_id = params.get("selected", [""])[0]
        selected_id = int(requested_id) if requested_id.isdigit() and any(int(x["id"]) == int(requested_id) for x in items) else (int(items[0]["id"]) if items else None)
        first = (page_number - 1) * PAGE_SIZE + 1 if total else 0
        last = min(page_number * PAGE_SIZE, total)
        if scope == "people" and not person_id:
            view_label = "People"
        elif scope == "people" and selected_person_name:
            view_label = f"Photos of {selected_person_name}"
        elif query:
            view_label = f'Search: “{query}”'
        elif selected_date:
            view_label = selected_date
        else:
            view_label = {
                "newest": "Newest photos",
                "oldest": "Oldest photos",
                "name": "Filename order",
            }.get(sort, "Photos")
        summary = f"{view_label} • {total:,}" if scope == "people" and not person_id else f"{view_label} • {first:,}–{last:,} of {total:,}"
        data_json = json.dumps(items).replace("</", "<\\/")
        base = {"q": query, "date": selected_date, "scope": scope, "sort": sort}
        if person_id:
            base["person"] = person_id
        previous = f'/?{urllib.parse.urlencode(base | {"page": page_number - 1})}' if page_number > 1 else ""
        following = f'/?{urllib.parse.urlencode(base | {"page": page_number + 1})}' if last < total else ""

        scope_options = "".join(
            f'<option value="{value}"{" selected" if scope == value else ""}>{label}</option>'
            for value, label in (("image", "Visible image tags"), ("context", "Day/event context"), ("people", "People"), ("all", "Everything"))
        )
        sort_options = "".join(
            f'<option value="{value}"{" selected" if sort == value else ""}>{label}</option>'
            for value, label in (("oldest", "Oldest first"), ("newest", "Newest first"), ("name", "Filename A–Z"))
        )
        gallery_mode = scope == "people" and not person_id
        gallery_cards = []
        for person in people_cards:
            aliases = person["aliases"]
            alias_text = ", ".join(aliases) if aliases else "No alternate names yet"
            picture = (
                f'<img src="/media?id={int(person["representative_id"])}" alt="Representative photo of {html.escape(person["name"], quote=True)}">'
                if person["representative_id"] else '<div class="person-placeholder">👤</div>'
            )
            person_url = "/?" + urllib.parse.urlencode({"scope": "people", "person": person["id"], "sort": "newest"})
            gallery_cards.append(
                f'<article class="person-card"><a href="{person_url}">{picture}'
                f'<div class="person-card-info"><strong>{html.escape(person["name"])}</strong>'
                f'<small>{int(person["confirmed_count"]):,} confirmed photo{"s" if int(person["confirmed_count"]) != 1 else ""}'
                f' · {int(person["suggested_count"]):,} to review</small>'
                f'<span>{html.escape(alias_text)}</span></div></a>'
                f'<button type="button" class="edit-aliases" data-person-id="{int(person["id"])}" '
                f'data-person-name="{html.escape(person["name"], quote=True)}" '
                f'data-aliases="{html.escape(json.dumps(aliases), quote=True)}">Edit names</button></article>'
            )
        people_gallery_html = (
            '<main class="people-browser"><div class="people-browser-head"><div><h2>People</h2>'
            '<p>Choose a person to see confirmed photos. Edit names to add nicknames, maiden names, or other aliases.</p></div>'
            f'<div class="people-head-actions"><span>{len(people_cards):,} {"person" if len(people_cards) == 1 else "people"}</span>'
            f'<button type="button" id="reviewPeopleGallery">Review people ({review_count:,})</button></div></div><section class="people-grid">'
            + ("".join(gallery_cards) if gallery_cards else '<p class="people-empty">No people match that name.</p>')
            + '</section></main>'
        ) if gallery_mode else ""
        people_result_bar = (
            f'<div class="people-result-bar"><a href="/?scope=people">← All people</a><strong>{html.escape(selected_person_name)}</strong></div>'
            if scope == "people" and person_id else ""
        )
        person_hidden = f'<input type="hidden" name="person" value="{person_id}">' if person_id else ""
        body_class = "people-gallery-mode" if gallery_mode else ""
        search_placeholder = "Filter people by name or alias" if gallery_mode else "Subject, person, object, or visible text"
        viewer_style = ' style="display:none"' if gallery_mode else ""
        previous_page_js = (
            "const prev=document.createElement('a');prev.className='button secondary';"
            f"prev.href={json.dumps(previous)};prev.textContent='Previous page';f.append(prev);"
            if previous else ""
        )
        next_page_js = (
            "const next=document.createElement('a');next.className='button secondary';"
            f"next.href={json.dumps(following)};next.textContent='Next page';f.append(next);"
            if following else ""
        )
        pager_js = previous_page_js + next_page_js
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{APP_NAME} — {APP_TAGLINE}</title><link rel="icon" href="/logo.png">
<style>
:root{{--bg:#0d1018;--panel:#171b26;--panel2:#202638;--line:#30384d;--text:#f4f6fb;--muted:#9ba8bd;--cyan:#16bde9;--violet:#8b55ff;--danger:#e25c70;--selection:#ffd84d}}
*{{box-sizing:border-box}}html,body{{width:100%;height:100%}}body{{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,Segoe UI,sans-serif;overflow:hidden;display:grid;grid-template-rows:auto minmax(0,1fr)}}button,input,select,textarea{{font:inherit}}header{{min-height:106px;background:#141925;border-bottom:1px solid var(--line);padding:12px 18px}}.top{{display:flex;align-items:center;gap:10px;margin-bottom:10px}}.top img{{width:38px;height:38px;object-fit:contain}}.identity{{display:grid}}h1{{font-size:20px;margin:0}}.tagline{{font-size:11px;color:#8ea5bd}}.version{{color:#b5a0ff;background:#292341;border:1px solid #4a3f75;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:700}}.summary{{margin-left:auto;color:var(--muted)}}
form.toolbar{{display:grid;grid-template-columns:minmax(260px,1fr) 190px auto auto 160px auto auto;gap:8px;align-items:end}}label{{display:grid;gap:3px;color:var(--muted);font-size:11px}}input,select,textarea{{min-width:0;border:1px solid #46506a;border-radius:7px;background:#232a3a;color:white;padding:8px 9px}}textarea{{resize:vertical;min-height:64px}}button,.button{{border:0;border-radius:7px;padding:8px 12px;background:#3d6fe8;color:white;font-weight:700;cursor:pointer;text-decoration:none;text-align:center}}button.secondary,.button.secondary{{background:#30384b}}button.danger{{background:#7e3340}}button:disabled{{opacity:.4;cursor:default}}
.viewer{{min-height:0;display:grid;grid-template-rows:minmax(0,1fr) 158px}}.upper{{min-height:0;overflow:hidden;display:grid;grid-template-columns:minmax(0,1fr) 430px}}.stage{{position:relative;min-width:0;min-height:0;height:100%;overflow:hidden;display:grid;place-items:center;background:#090b10;padding:14px}}.stage img,.stage video{{display:block;width:100%;height:100%;min-width:0;min-height:0;object-fit:contain;box-shadow:0 10px 35px #000}}.empty{{color:var(--muted);font-size:18px}}.raw-preview{{text-align:center;color:var(--muted);line-height:1.5}}.raw-preview strong{{display:block;color:var(--cyan);font-size:28px;margin-bottom:7px}}.stage-nav{{position:absolute;top:50%;transform:translateY(-50%);border-radius:50%;width:42px;height:42px;padding:0;background:#202638cc;font-size:25px}}#previousPhoto{{left:18px}}#nextPhoto{{right:18px}}
.sidebar{{min-height:0;overflow:auto;background:var(--panel);border-left:1px solid var(--line);padding:16px}}.file-date{{color:var(--cyan);font-weight:800}}.file-name{{font-size:16px;font-weight:750;margin-top:4px;word-break:break-word}}.folder{{margin-top:5px;color:var(--muted);font-size:12px;word-break:break-word}}.editor-guide{{margin-top:14px;padding:11px 12px;border:1px solid #3b4864;border-left:3px solid var(--cyan);border-radius:7px;background:#1d2433;color:#dbe4f2;font-size:12px;line-height:1.45}}.section{{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}}.section h2{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#d2daea;margin:0 0 6px}}.section-description{{color:var(--muted);font-size:12px;line-height:1.45;margin:0 0 10px}}.row{{display:flex;gap:7px}}.row input{{flex:1}}.chips{{display:flex;flex-wrap:wrap;gap:7px}}.chip{{display:inline-flex;align-items:center;gap:7px;background:#282f42;border:1px solid #424d68;border-radius:999px;padding:6px 8px 6px 10px;color:#e9edff}}.chip.subject{{border-color:#168fb0;background:#173543;color:#e8fbff}}.chip.context{{color:#b8c1cf;background:#202633}}.chip.hidden{{opacity:.65;border-style:dashed}}.chip button{{padding:0;width:19px;height:19px;border-radius:50%;background:#4b5570;line-height:1}}.subject-editor{{margin-top:9px}}.hint{{color:var(--muted);font-size:12px;line-height:1.4;margin-top:8px}}.status{{min-height:20px;margin-top:10px;color:var(--cyan)}}
.filmstrip{{display:flex;gap:8px;overflow-x:auto;overflow-y:hidden;padding:10px 14px;background:#111621;border-top:1px solid var(--line);scrollbar-color:#4a5570 #181d29;cursor:grab;user-select:none;touch-action:pan-y}}.filmstrip.dragging{{cursor:grabbing;scroll-behavior:auto}}.filmstrip.dragging .thumb{{pointer-events:none}}.thumb{{position:relative;flex:0 0 170px;height:132px;padding:0;border:4px solid transparent;border-radius:8px;overflow:hidden;background:#090b10}}.thumb.active{{border-color:var(--selection);box-shadow:inset 0 0 0 2px #080a0f,0 0 0 1px var(--selection)}}.thumb img{{width:100%;height:100%;object-fit:cover;pointer-events:none}}.thumb .video-label{{height:100%;display:grid;place-items:center;color:#adb8c9}}.thumb span{{position:absolute;left:0;right:0;bottom:0;background:#090b10d9;padding:4px 6px;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left;pointer-events:none}}.pager{{margin-left:auto;display:flex;align-items:center;gap:8px}}.toast{{position:fixed;left:50%;bottom:180px;transform:translateX(-50%);background:#292f3f;border:1px solid #57627d;border-radius:9px;padding:12px 16px;display:none;z-index:5;box-shadow:0 10px 30px #000}}
header{{min-height:88px;padding:8px 14px}}.top{{margin-bottom:6px}}.top img{{width:30px;height:30px}}.menu-toggle{{width:34px;height:34px;padding:0;background:#252c3d;font-size:20px}}.summary{{margin-left:auto}}#moveToTrash{{margin-left:8px;background:#7e3340;padding:7px 12px}}form.toolbar{{display:flex;gap:8px;align-items:end}}.search-field{{flex:0 1 480px;min-width:260px}}.scope-field{{flex:0 0 175px}}.date-field{{flex:0 0 145px}}.sort-field{{flex:0 0 145px}}.menu-panel{{display:none;position:fixed;z-index:20;top:50px;left:12px;width:250px;padding:8px;background:#202638;border:1px solid #46506a;border-radius:10px;box-shadow:0 16px 45px #000}}.menu-panel.open{{display:grid;gap:4px}}.menu-panel button{{background:transparent;text-align:left;color:var(--text);padding:10px}}.menu-panel button:hover{{background:#30384b}}.section{{position:relative;padding-top:10px;margin-top:10px}}.section-title{{display:flex;align-items:center;gap:7px;margin-bottom:8px}}.section-title h2{{margin:0}}.info-button{{width:21px;height:21px;padding:0;border-radius:50%;background:#30384b;color:#b9c7da;font-size:13px}}.help-popover{{display:none;position:absolute;z-index:10;right:0;top:34px;width:min(350px,100%);padding:11px 12px;background:#252c3d;border:1px solid #56627d;border-radius:8px;color:#dce4f0;font-size:12px;line-height:1.45;box-shadow:0 12px 30px #000}}.help-popover.open{{display:block}}.editor-compact{{display:flex;align-items:center;gap:7px;margin-top:10px;color:#b8c5d7;font-size:12px}}.editor-compact .info-button{{margin-left:auto}}.metadata-details{{border-top:1px solid var(--line);margin-top:12px;padding-top:12px}}.metadata-details summary{{cursor:pointer;color:#d2daea;font-weight:800;text-transform:uppercase;letter-spacing:.07em;font-size:12px}}.metadata-grid{{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:11px}}.metadata-grid .wide{{grid-column:1/-1}}.metadata-note{{color:var(--muted);font-size:11px;line-height:1.4;margin:8px 0}}.metadata-actions{{display:flex;justify-content:flex-end;margin-top:10px}}.status{{margin-top:8px}}.modal-backdrop{{display:none;position:fixed;z-index:30;inset:0;background:#05070bc9;place-items:center;padding:30px}}.modal-backdrop.open{{display:grid}}.modal{{width:min(680px,100%);max-height:80vh;overflow:auto;background:#1b2030;border:1px solid #46506a;border-radius:12px;padding:20px;box-shadow:0 20px 60px #000}}.modal-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}}.modal h2{{margin:0;font-size:19px}}.modal-close{{background:#30384b}}.modal p,.modal li{{color:#c5cede;line-height:1.5}}.trash-list{{display:grid;gap:8px}}.trash-item{{display:flex;align-items:center;gap:10px;padding:10px;background:#22293a;border-radius:8px}}.trash-item .trash-meta{{min-width:0;flex:1}}.trash-item strong,.trash-item small{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.trash-item small{{color:var(--muted);margin-top:3px}}.trash-empty{{color:var(--muted);padding:15px 0}}
.metadata-readout{{display:grid;gap:10px;margin-top:10px}}.metadata-group{{display:grid;gap:5px}}.metadata-group h3{{margin:0;color:var(--cyan);font-size:11px;text-transform:uppercase;letter-spacing:.06em}}.metadata-item{{display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px;padding:5px 0;border-bottom:1px solid #292f40;font-size:12px}}.metadata-item dt{{color:var(--muted)}}.metadata-item dd{{margin:0;word-break:break-word}}.metadata-item a{{color:var(--cyan);text-decoration:none}}.metadata-item a:hover{{text-decoration:underline}}.metadata-empty{{color:var(--muted);font-size:12px;padding:4px 0}}
.publish-section{{border:1px solid #465d49;border-left:3px solid #58b76b;border-radius:8px;background:#1b2822;padding:11px;margin-top:12px}}.publish-section .section-title{{margin-bottom:6px}}.publish-section label{{font-size:12px;color:#dce7df}}.publish-section textarea{{width:100%;margin-top:5px;min-height:58px}}.publish-actions{{display:flex;gap:8px;align-items:center;margin-top:9px}}.publish-actions .secondary{{margin-left:auto}}.publish-note{{margin:6px 0 0;color:#aebdaf;font-size:11px;line-height:1.4}}.modal.publish-modal{{width:min(1280px,calc(100vw - 40px))}}.preview-table{{width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed}}.preview-table th,.preview-table td{{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #343c50;word-break:normal}}.preview-table th{{color:#9facc0}}.preview-table th:first-child,.preview-table td:first-child{{color:var(--cyan);width:220px;white-space:nowrap}}.confirm-bar{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:15px}}.confirm-bar small{{color:var(--muted);flex:1}}.confirm-buttons{{display:flex;gap:8px;white-space:nowrap}}
.chip.person{{border-color:#7656b7;background:#302646}}.chip.suggestion{{border-style:dashed;border-color:#a57b43;background:#3a3022}}.suggestion-label{{width:100%;color:#d6b77d;font-size:11px;margin:2px 0 6px}}.chip .accept-person{{background:#39764b}}.library-panel{{display:grid;gap:10px}}.library-panel .row input{{flex:1}}.library-status{{color:var(--cyan);min-height:20px}}.library-choices{{display:grid;gap:6px}}.library-choice{{display:grid;text-align:left;background:#293247}}.library-choice small{{color:var(--muted);font-weight:400;overflow:hidden;text-overflow:ellipsis}}.scan-metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}}.scan-metrics span{{padding:8px;background:#222a3a;border-radius:7px;color:var(--muted)}}.scan-metrics strong{{display:block;color:var(--text);font-size:18px}}
.people-browser{{min-height:0;overflow:auto;padding:24px;background:#10141e}}.people-browser-head{{display:flex;align-items:start;justify-content:space-between;gap:20px;margin-bottom:18px}}.people-browser-head h2{{font-size:25px;margin:0}}.people-browser-head p{{color:var(--muted);margin:5px 0 0}}.people-head-actions{{display:flex;align-items:center;gap:9px}}.people-head-actions>span{{color:#c5b7ff;background:#2b2544;border-radius:999px;padding:7px 11px;white-space:nowrap}}.people-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px;padding-bottom:30px}}.person-card{{position:relative;overflow:hidden;background:#1b2030;border:1px solid #343d53;border-radius:12px}}.person-card:hover{{border-color:#7656b7;transform:translateY(-1px)}}.person-card a{{display:block;color:inherit;text-decoration:none}}.person-card img,.person-placeholder{{display:block;width:100%;height:180px;object-fit:cover;background:#090b10}}.person-placeholder{{display:grid;place-items:center;font-size:58px;color:#5f6880}}.person-card-info{{display:grid;gap:5px;padding:12px 12px 48px}}.person-card-info strong{{font-size:16px}}.person-card-info small{{color:var(--cyan)}}.person-card-info span{{color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.edit-aliases{{position:absolute;left:12px;bottom:10px;padding:6px 9px;background:#30384b;font-size:12px}}.people-empty{{color:var(--muted)}}.people-result-bar{{position:fixed;z-index:4;top:88px;left:12px;background:#202638;border:1px solid #46506a;border-radius:9px;padding:8px 12px;display:flex;align-items:center;gap:12px;box-shadow:0 8px 25px #000}}.people-result-bar a{{color:var(--cyan);text-decoration:none}}.alias-editor{{display:grid;gap:8px}}.alias-editor input{{width:100%}}.people-gallery-mode #moveToTrash,.people-gallery-mode #previousDay,.people-gallery-mode #nextDay,.people-gallery-mode .date-field,.people-gallery-mode .sort-field{{display:none}}.modal.people-review-modal{{width:min(1180px,calc(100vw - 36px));height:min(780px,calc(100vh - 36px));max-height:none;overflow:hidden;display:grid;grid-template-rows:auto minmax(0,1fr)}}.modal.people-review-modal #modalBody{{min-height:0;overflow:hidden}}.people-review{{height:100%;min-height:0;display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:12px}}.review-progress{{display:flex;justify-content:space-between;gap:12px;color:var(--muted)}}.review-main{{min-height:0;overflow:hidden;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(300px,.7fr);gap:16px}}.review-image-wrap{{min-width:0;min-height:0;display:grid;place-items:center;background:#090b10;border-radius:10px;overflow:hidden}}.review-image-wrap img{{display:block;width:100%;height:100%;min-width:0;min-height:0;object-fit:contain}}.review-controls{{min-height:0;overflow:auto;display:flex;flex-direction:column;gap:12px;padding:5px}}.review-person{{font-size:25px;font-weight:800}}.review-confidence{{color:#d6b77d}}.review-file{{color:var(--muted);font-size:12px;word-break:break-word}}.review-question{{font-size:18px;margin-top:8px}}.review-decisions{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}.review-decisions button{{padding:13px}}.review-yes{{background:#39764b}}.review-no{{background:#873d4b}}.review-correction{{display:grid;gap:7px;border-top:1px solid var(--line);padding-top:12px}}.review-correction .row input{{flex:1}}.review-footer{{display:flex;align-items:center;gap:9px}}.review-footer .review-spacer{{flex:1}}.review-complete{{height:100%;display:grid;place-items:center;text-align:center;color:#cdd6e5}}.review-complete strong{{font-size:25px;display:block;margin-bottom:8px}}
@media(max-width:950px){{.upper{{grid-template-columns:1fr 320px}}form.toolbar{{grid-template-columns:1fr 170px auto auto}}.optional{{display:none}}}}
.menu-panel a{{color:var(--text);padding:10px;text-decoration:none;border-radius:7px;font-weight:700}}.menu-panel a:hover{{background:#30384b}}
</style></head><body class="{body_class}">
<header><div class="top"><button type="button" class="menu-toggle" id="menuToggle" aria-label="Open menu">☰</button><img src="/logo.png" alt=""><div class="identity"><h1>{APP_NAME}</h1><div class="tagline">{APP_TAGLINE}</div></div><span class="version">v{APP_VERSION}</span><span class="summary">{html.escape(summary)} <span style="color:#e25c70">{html.escape(error)}</span></span><button type="button" class="danger" id="moveToTrash">🗑 Move to Trash</button></div>
<form class="toolbar">{person_hidden}<label class="search-field">Search<input name="q" value="{html.escape(query, quote=True)}" placeholder="{search_placeholder}"></label><label class="scope-field">Search scope<select name="scope" id="scopePicker">{scope_options}</select></label><button type="button" class="secondary" id="previousDay">◀ Day</button><label class="date-field">Date<input type="date" name="date" id="datePicker" value="{html.escape(selected_date, quote=True)}"></label><button type="button" class="secondary" id="nextDay">Day ▶</button><label class="sort-field optional">Sort<select name="sort">{sort_options}</select></label><button>View</button></form></header>
<nav class="menu-panel" id="menuPanel"><a href="/people-review">👥 Review people ({review_count:,})</a><button type="button" data-panel="library">📁 Open photo library</button><a href="/map">🌍 Photo map</a><button type="button" data-panel="trash">Trash &amp; restore ({trash_count})</button><button type="button" data-panel="guide">Quick guide</button><button type="button" data-panel="about">About LensLedger</button></nav>
{people_gallery_html}{people_result_bar}<main class="viewer"{viewer_style}><section class="upper"><div class="stage" id="stage"><div class="empty">Choose a photo from the filmstrip</div><button class="stage-nav" id="previousPhoto">‹</button><button class="stage-nav" id="nextPhoto">›</button></div><aside class="sidebar">
<div class="file-date" id="assetDate"></div><div class="file-name" id="assetName"></div><div class="folder" id="assetFolder"></div>
<div class="editor-compact"><strong>Metadata for this photo</strong><button type="button" class="info-button" data-help="editorHelp" aria-label="About metadata editing">ⓘ</button><div class="help-popover" id="editorHelp">Your edits stay in LensLedger until you click Preview &amp; publish for this photo. Nothing is written automatically.</div></div>
<div class="section"><div class="section-title"><h2>1. Primary subject</h2><button type="button" class="info-button" data-help="subjectHelp" aria-label="About primary subjects">ⓘ</button></div><div class="help-popover" id="subjectHelp">One short phrase naming the main thing in this photo. Stored as IPTC/XMP Title/Headline metadata.</div><div class="chips" id="subjectChip"></div><div class="row subject-editor"><input id="subjectInput" placeholder="Example: Formula 1 race cars"><button id="saveSubject">Save subject</button></div></div>
<div class="section"><div class="section-title"><h2>2. Photo tags</h2><button type="button" class="info-button" data-help="photoTagHelp" aria-label="About photo tags">ⓘ</button></div><div class="help-popover" id="photoTagHelp">Searchable people, objects, places, or activities visible in this photo. Use lowercase for ordinary things and activities; capitalize people, places, brands, and acronyms normally. Search ignores capitalization. These are stored as IPTC/XMP Keywords. Enter one or several separated by commas.</div><div class="chips" id="imageTags"></div><div class="row" style="margin-top:9px"><input id="newTag" placeholder="Formula 1, race car, Honda, McLaren"><button id="addTag">Add tags</button></div></div>
<div class="section"><div class="section-title"><h2>3. People in this photo</h2><button type="button" class="info-button" data-help="peopleHelp" aria-label="About people in this photo">ⓘ</button></div><div class="help-popover" id="peopleHelp">Confirmed people become searchable and publish to the standard XMP People Shown field and Keywords. Face matches are suggestions only until you approve them.</div><div class="chips" id="confirmedPeople"></div><div id="suggestionLabel" class="suggestion-label">Face recognition suggestions — confirm or reject</div><div class="chips" id="suggestedPeople"></div><div class="row" style="margin-top:9px"><input id="newPerson" list="peopleOptions" placeholder="Person's name"><datalist id="peopleOptions"></datalist><button id="addPerson">Add person</button></div></div>
<div class="section"><div class="section-title"><h2>4. Event / folder tags</h2><button type="button" class="info-button" data-help="eventTagHelp" aria-label="About event and folder tags">ⓘ</button></div><div class="help-popover" id="eventTagHelp">Reusable context applied to every photo in this folder. These also map to Keywords; use Event name and Location below when a more precise standard field applies.</div><div class="chips" id="contextTags"></div><div class="row" style="margin-top:9px"><input id="newContextTag" placeholder="Car show, family vacation, birthday"><button id="addContextTag">Add to event</button></div></div>
<section class="publish-section" id="publishSection"><div class="section-title"><h2>Publish to this photo</h2><button type="button" class="info-button" data-help="publishHelp" aria-label="About publishing">ⓘ</button></div><div class="help-popover" id="publishHelp">Writes the primary subject as Title/Headline, confirmed people as People Shown, all visible Photo, People, and Event tags as Keywords, and the description below into this JPEG. A safety backup is made first and the picture pixels are verified afterward.</div><label>Photo description<textarea id="publishDescription" placeholder="Describe what is actually in this photo"></textarea></label><div class="publish-actions"><button type="button" id="previewPublish">Preview &amp; publish</button><button type="button" class="secondary" id="restorePublish">Restore last publish</button></div><p class="publish-note" id="publishNote">Only this selected photo will be changed, and only after you approve the preview.</p></section>
<details class="metadata-details" id="embeddedMetadata"><summary>Information already in this photo</summary><p class="metadata-note">Read directly from the photo. Nothing here changes the file.</p><div class="metadata-readout" id="metadataReadout"></div></details>
<div class="section" id="hiddenSection"><div class="section-title"><h2>Hidden tags</h2><button type="button" class="info-button" data-help="hiddenTagHelp" aria-label="About hidden tags">ⓘ</button></div><div class="help-popover" id="hiddenTagHelp">These tags are ignored only for this photo. Click one to restore it.</div><div class="chips" id="hiddenTags"></div></div><div class="status" id="status"></div>
</aside></section><section class="filmstrip" id="filmstrip"></section></main><div class="toast" id="toast"></div>
<div class="modal-backdrop" id="modalBackdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle"><div class="modal-head"><h2 id="modalTitle"></h2><button type="button" class="modal-close" id="modalClose">Close</button></div><div id="modalBody"></div></section></div>
<script>
const items={data_json}; const csrf={json.dumps(self.csrf_token)}; const currentLibrary={json.dumps(str(self.library_root))}; let selectedId={json.dumps(selected_id)}; let currentDetail=null;
const $=id=>document.getElementById(id); const stage=$('stage');
function esc(s){{return String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[c]))}}
async function api(path,payload){{const r=await fetch(path,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{...payload,csrf}})}});const data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');return data}}
function setStatus(text,error=false){{$('status').textContent=text;$('status').style.color=error?'#e25c70':'#16bde9'}}
function chip(tag,kind){{const el=document.createElement('span');el.className='chip '+(kind==='context'?'context':'');el.append(document.createTextNode(tag.name));const b=document.createElement('button');b.textContent='×';b.title='Hide this tag for this photo';b.onclick=()=>removeTag(tag);el.append(b);return el}}
function renderSubject(detail){{const holder=$('subjectChip');if(!detail.subject){{holder.replaceChildren();$('subjectInput').placeholder='Example: Formula 1 race cars';return}}const el=document.createElement('span');el.className='chip subject';el.append(document.createTextNode(detail.subject));const b=document.createElement('button');b.textContent='×';b.title='Clear the primary subject';b.onclick=clearSubject;el.append(b);holder.replaceChildren(el);$('subjectInput').placeholder='Replace the current subject'}}
function renderChips(detail){{renderSubject(detail);$('imageTags').replaceChildren(...detail.image_tags.map(t=>chip(t,'image')));$('contextTags').replaceChildren(...detail.context_tags.map(t=>chip(t,'context')));const hidden=detail.hidden_tags.map(name=>{{const e=document.createElement('button');e.className='chip hidden';e.textContent='Restore '+name;e.onclick=()=>restoreTag(name);return e}});$('hiddenTags').replaceChildren(...hidden);$('hiddenSection').style.display=hidden.length?'block':'none'}}
function renderPeople(detail){{const confirmed=detail.confirmed_people.map(person=>{{const el=document.createElement('span');el.className='chip person';el.append(document.createTextNode(person.name));const remove=document.createElement('button');remove.textContent='×';remove.title='Remove this person from the photo';remove.onclick=()=>changePerson(person.name,'rejected');el.append(remove);return el}});$('confirmedPeople').replaceChildren(...confirmed);const suggested=detail.suggested_people.map(person=>{{const el=document.createElement('span');el.className='chip suggestion';el.append(document.createTextNode(person.name+' '+Math.round(person.confidence*100)+'%'));const accept=document.createElement('button');accept.className='accept-person';accept.textContent='✓';accept.title='Confirm this person';accept.onclick=()=>changePerson(person.name,'confirmed');const reject=document.createElement('button');reject.textContent='×';reject.title='Reject this suggestion';reject.onclick=()=>changePerson(person.name,'rejected');el.append(accept,reject);return el}});$('suggestedPeople').replaceChildren(...suggested);$('suggestionLabel').style.display=suggested.length?'block':'none';$('peopleOptions').replaceChildren(...detail.people_options.map(name=>{{const option=document.createElement('option');option.value=name;return option}}))}}
function renderEmbeddedMetadata(meta){{const holder=$('metadataReadout');holder.replaceChildren();let count=0;for(const [key,title] of [['descriptive','Description and ownership'],['capture','Capture details']]){{const items=meta?.[key]||[];if(!items.length)continue;count+=items.length;const group=document.createElement('section');group.className='metadata-group';const heading=document.createElement('h3');heading.textContent=title;const list=document.createElement('dl');for(const item of items){{const row=document.createElement('div');row.className='metadata-item';const term=document.createElement('dt');term.textContent=item.label;const value=document.createElement('dd');if(item.href){{const link=document.createElement('a');link.href=item.href;link.target='_blank';link.rel='noopener';link.title='Open this location on a map';link.textContent=item.value+'  ↗';value.append(link)}}else value.textContent=item.value;row.append(term,value);list.append(row)}}group.append(heading,list);holder.append(group)}}if(!count){{const empty=document.createElement('div');empty.className='metadata-empty';empty.textContent='No readable embedded details were found in this file.';holder.append(empty)}}}}
async function selectAsset(id){{selectedId=Number(id);document.querySelectorAll('.thumb').forEach(x=>{{const isActive=Number(x.dataset.id)===selectedId;x.classList.toggle('active',isActive);if(isActive)x.setAttribute('aria-current','true');else x.removeAttribute('aria-current')}});const active=document.querySelector('.thumb.active');active?.scrollIntoView({{block:'nearest',inline:'center'}});try{{const r=await fetch('/api/asset?id='+selectedId);if(!r.ok)throw new Error('Could not load photo');const d=await r.json();currentDetail=d;stage.querySelectorAll('img,video,.empty,.raw-preview').forEach(x=>x.remove());if(d.media_type==='raw'){{const raw=document.createElement('div');raw.className='raw-preview';raw.innerHTML='<strong>RAW photo</strong>LensLedger indexed this original file.<br>A browser preview is not available yet.';stage.prepend(raw)}}else{{const media=document.createElement(d.media_type==='video'?'video':'img');media.src='/media?id='+d.id;if(d.media_type==='video')media.controls=true;stage.prepend(media)}}$('assetDate').textContent=d.capture_date||'Date unknown';$('assetName').textContent=d.filename;$('assetFolder').textContent=d.folder;$('subjectInput').value='';$('newPerson').value='';$('publishDescription').value=d.embedded_metadata?.description||'';$('previewPublish').disabled=!d.publishable;$('restorePublish').disabled=!d.can_restore_publish;$('publishNote').textContent=d.publishable?'Only this selected photo will be changed, and only after you approve the preview.':'Publishing is currently available for JPEG photos.';renderChips(d);renderPeople(d);renderEmbeddedMetadata(d.embedded_metadata);setStatus('')}}catch(e){{setStatus(e.message,true)}}updateNav()}}
function updateNav(){{const i=items.findIndex(x=>Number(x.id)===selectedId);$('previousPhoto').disabled=i<=0;$('nextPhoto').disabled=i<0||i>=items.length-1}}
function step(delta){{const i=items.findIndex(x=>Number(x.id)===selectedId);const next=items[i+delta];if(next)selectAsset(next.id)}}
async function saveSubject(){{const value=$('subjectInput').value.trim();if(!value){{setStatus('Enter a primary subject first',true);return}}try{{await api('/api/subject',{{id:selectedId,subject:value}});await selectAsset(selectedId);setStatus('Primary subject saved')}}catch(e){{setStatus(e.message,true)}}}}
async function clearSubject(){{try{{await api('/api/subject',{{id:selectedId,subject:''}});await selectAsset(selectedId);setStatus('Primary subject cleared')}}catch(e){{setStatus(e.message,true)}}}}
async function addTag(){{const value=$('newTag').value.trim();if(!value)return;try{{const result=await api('/api/tag/add',{{id:selectedId,tag:value}});$('newTag').value='';await selectAsset(selectedId);setStatus(result.added===1?'1 photo tag added':result.added+' photo tags added')}}catch(e){{setStatus(e.message,true)}}}}
async function addPerson(){{const value=$('newPerson').value.trim();if(!value)return;try{{await api('/api/person/add',{{id:selectedId,name:value}});await selectAsset(selectedId);setStatus(value+' confirmed in this photo')}}catch(e){{setStatus(e.message,true)}}}}
async function changePerson(name,state){{try{{await api('/api/person/state',{{id:selectedId,name,state}});await selectAsset(selectedId);setStatus(state==='confirmed'?name+' confirmed':name+' removed from this photo')}}catch(e){{setStatus(e.message,true)}}}}
async function addContextTag(){{const value=$('newContextTag').value.trim();if(!value)return;try{{const result=await api('/api/folder-tag/add',{{id:selectedId,tag:value}});$('newContextTag').value='';await selectAsset(selectedId);if(result.added===0)setStatus('Those tags are already on this event');else setStatus(result.added===1?'Event tag added to '+result.assets+' photos':result.added+' event tags added to '+result.assets+' photos')}}catch(e){{setStatus(e.message,true)}}}}
async function removeTag(tag){{try{{await api('/api/tag/remove',{{id:selectedId,tag:tag.name,source:tag.source}});await selectAsset(selectedId);setStatus('Tag hidden for this photo')}}catch(e){{setStatus(e.message,true)}}}}
async function restoreTag(name){{try{{await api('/api/tag/restore',{{id:selectedId,tag:name}});await selectAsset(selectedId);setStatus('Tag restored')}}catch(e){{setStatus(e.message,true)}}}}
async function moveToBin(){{if(!currentDetail||!confirm('Move “'+currentDetail.filename+'” to Trash?\\n\\nIt will leave the photo library and disappear from search. You can undo immediately or restore it later from ☰ → Trash & restore.'))return;try{{const oldId=selectedId;const result=await api('/api/review-bin',{{id:selectedId}});const i=items.findIndex(x=>Number(x.id)===oldId);items.splice(i,1);document.querySelector('.thumb[data-id="'+oldId+'"]').remove();showUndo(result.review_id,currentDetail.filename);if(items.length)selectAsset(items[Math.min(i,items.length-1)].id);else location.reload()}}catch(e){{setStatus(e.message,true)}}}}
function showUndo(reviewId,name){{const t=$('toast');t.replaceChildren(document.createTextNode('Moved '+name+' to Trash. '));const b=document.createElement('button');b.textContent='Undo';b.onclick=async()=>{{try{{await api('/api/review-bin/restore',{{review_id:reviewId}});location.reload()}}catch(e){{setStatus(e.message,true)}}}};t.append(b);t.style.display='block';setTimeout(()=>t.style.display='none',12000)}}
function closeHelp(){{document.querySelectorAll('.help-popover.open').forEach(x=>x.classList.remove('open'))}}
function openModal(title,content){{document.querySelector('.modal').classList.remove('publish-modal','people-review-modal');$('modalTitle').textContent=title;$('modalBody').replaceChildren(content);$('modalBackdrop').classList.add('open')}}
function textPanel(paragraphs){{const box=document.createElement('div');paragraphs.forEach(text=>{{const p=document.createElement('p');p.textContent=text;box.append(p)}});return box}}
function editAliases(button){{const personId=Number(button.dataset.personId);const personName=button.dataset.personName;const aliases=JSON.parse(button.dataset.aliases);const box=document.createElement('div');box.className='alias-editor';const note=document.createElement('p');note.textContent='Change the primary name everywhere in LensLedger. Alternate names are optional and only kept when you enter them below.';const primaryLabel=document.createElement('label');primaryLabel.textContent='Primary name';const primary=document.createElement('input');primary.value=personName;primary.placeholder='Full name';primaryLabel.append(primary);const aliasLabel=document.createElement('label');aliasLabel.textContent='Other names for this same person (optional)';const aliasInput=document.createElement('input');aliasInput.value=aliases.join(', ');aliasInput.placeholder='Nickname, maiden name, alternate spelling';aliasLabel.append(aliasInput);const actions=document.createElement('div');actions.className='publish-actions';const cancel=document.createElement('button');cancel.className='secondary';cancel.textContent='Cancel';cancel.onclick=()=>$('modalBackdrop').classList.remove('open');const save=document.createElement('button');save.textContent='Save names';save.onclick=async()=>{{save.disabled=true;try{{await api('/api/person/names',{{person_id:personId,name:primary.value,aliases:aliasInput.value.split(',')}});location.reload()}}catch(e){{save.disabled=false;note.textContent=e.message;note.style.color='#e25c70'}}}};actions.append(cancel,save);box.append(note,primaryLabel,aliasLabel,actions);openModal('Names for '+personName,box);primary.focus();primary.select()}}
let peopleReview=null;let reviewHistory=[];const reviewSkipped=new Set();
function buildPeopleReview(){{const box=document.createElement('div');box.className='people-review';box.innerHTML='<div class="review-progress"><span id="reviewProgress"></span><span id="reviewRemaining"></span></div><div class="review-main"><div class="review-image-wrap"><img id="reviewImage" alt="Photo being reviewed"></div><div class="review-controls"><div class="review-person" id="reviewPerson"></div><div class="review-confidence" id="reviewConfidence"></div><div class="review-file" id="reviewFile"></div><div class="review-question">Is this person correct?</div><div class="review-decisions"><button type="button" class="review-yes" id="reviewYes">Yes, confirm</button><button type="button" class="review-no" id="reviewNo">No, not them</button></div><div class="review-correction"><strong>It is someone else</strong><div class="row"><input id="reviewCorrectName" list="reviewPeopleOptions" placeholder="Enter or choose the correct name"><datalist id="reviewPeopleOptions"></datalist><button type="button" id="reviewCorrect">Correct name</button></div></div><div class="status" id="reviewStatus"></div></div></div><div class="review-footer"><button type="button" class="secondary" id="reviewSkip">Skip for now</button><button type="button" class="secondary" id="reviewUndo" disabled>Undo last decision</button><span class="review-spacer"></span><button type="button" class="secondary" id="reviewNextPerson">Next person</button></div>';return box}}
function currentReviewItem(){{return peopleReview?.suggestions.find(item=>!reviewSkipped.has(peopleReview.person.id+':'+item.id))||null}}
function showReviewComplete(title,message){{const main=document.querySelector('.people-review');main.innerHTML='<div class="review-complete"><div><strong></strong><span></span></div></div>';main.querySelector('strong').textContent=title;main.querySelector('span').textContent=message}}
async function loadPeopleReview(personId=null,advance=false){{let url='/api/people/review/queue';const params=new URLSearchParams();if(personId)params.set('person_id',personId);if(advance)params.set('advance','1');if(params.size)url+='?'+params;const r=await fetch(url);const data=await r.json();if(!r.ok)throw new Error(data.error||'Could not load People review');peopleReview=data;if(!data.person){{showReviewComplete('People review complete','There are no face suggestions waiting for review.');return}}if(!currentReviewItem()){{showReviewComplete('Nothing else to review in this session','The remaining suggestions were skipped. Close this window to pause, or reopen Review people later.');return}}renderPeopleReview()}}
function renderPeopleReview(){{const item=currentReviewItem();if(!peopleReview?.person||!item)return;$('reviewPerson').textContent=peopleReview.person.name;$('reviewConfidence').textContent=Math.round((item.confidence||0)*100)+'% confidence';$('reviewFile').textContent=(item.capture_date||'Date unknown')+' · '+item.folder+' / '+item.filename;$('reviewImage').src='/media?id='+item.id;$('reviewProgress').textContent=peopleReview.suggestions.length+' suggestion'+(peopleReview.suggestions.length===1?'':'s')+' for '+peopleReview.person.name;$('reviewRemaining').textContent=peopleReview.remaining_total+' photos · '+peopleReview.people_remaining+' people remaining';$('reviewCorrectName').value='';$('reviewPeopleOptions').replaceChildren(...peopleReview.people_options.map(name=>{{const option=document.createElement('option');option.value=name;return option}}));$('reviewStatus').textContent='';document.querySelectorAll('.review-decisions button,#reviewCorrect').forEach(button=>button.disabled=false);$('reviewUndo').disabled=!reviewHistory.length;$('reviewYes').onclick=()=>submitPeopleReview('confirmed');$('reviewNo').onclick=()=>submitPeopleReview('rejected');$('reviewCorrect').onclick=()=>submitPeopleReview('corrected');$('reviewSkip').onclick=()=>{{reviewSkipped.add(peopleReview.person.id+':'+item.id);if(currentReviewItem())renderPeopleReview();else loadPeopleReview(peopleReview.person.id,true)}};$('reviewNextPerson').onclick=()=>loadPeopleReview(peopleReview.person.id,true);$('reviewUndo').onclick=undoPeopleReview}}
async function submitPeopleReview(action){{const item=currentReviewItem();if(!item)return;const correctedName=$('reviewCorrectName').value.trim();if(action==='corrected'&&!correctedName){{$('reviewStatus').textContent='Enter the correct name first';return}}document.querySelectorAll('.review-decisions button,#reviewCorrect').forEach(button=>button.disabled=true);try{{const result=await api('/api/people/review/decision',{{asset_id:item.id,person_id:peopleReview.person.id,action,corrected_name:correctedName}});reviewHistory.push({{action_id:result.action_id,person_id:peopleReview.person.id}});reviewSkipped.delete(peopleReview.person.id+':'+item.id);await loadPeopleReview(peopleReview.person.id)}}catch(e){{$('reviewStatus').textContent=e.message;document.querySelectorAll('.review-decisions button,#reviewCorrect').forEach(button=>button.disabled=false)}}}}
async function undoPeopleReview(){{const previous=reviewHistory.pop();if(!previous)return;try{{await api('/api/people/review/undo',{{action_id:previous.action_id}});await loadPeopleReview(previous.person_id)}}catch(e){{$('reviewStatus').textContent=e.message}}}}
async function openPeopleReview(personId=null){{reviewSkipped.clear();reviewHistory=[];const body=buildPeopleReview();openModal('Review people',body);document.querySelector('.modal').classList.add('people-review-modal');try{{await loadPeopleReview(personId)}}catch(e){{showReviewComplete('Could not open People review',e.message)}}}}
async function openLibraryPanel(){{const body=document.createElement('div');body.className='library-panel';const intro=document.createElement('p');intro.textContent='Each photo library keeps its own index. Choose any folder containing photos or videos.';const row=document.createElement('div');row.className='row';const input=document.createElement('input');input.value=currentLibrary;input.placeholder='C:\\\\Path\\\\To\\\\Photos';const browse=document.createElement('button');browse.className='secondary';browse.textContent='Browse…';const status=document.createElement('div');status.className='library-status';browse.onclick=async()=>{{browse.disabled=true;try{{const result=await api('/api/library/browse',{{}});if(result.path)input.value=result.path}}catch(e){{status.textContent=e.message}}finally{{browse.disabled=false}}}};row.append(input,browse);const actions=document.createElement('div');actions.className='publish-actions';const open=document.createElement('button');open.textContent='Open & index library';open.onclick=async()=>{{open.disabled=true;status.textContent='Starting library index…';try{{await api('/api/library/open',{{path:input.value}});while(true){{await new Promise(resolve=>setTimeout(resolve,1000));const response=await fetch('/api/library/status');const job=await response.json();status.textContent=job.message||job.state;if(job.state==='complete'){{location.href='/?sort=newest';return}}if(job.state==='error')throw new Error(job.message)}}}}catch(e){{status.textContent=e.message;open.disabled=false}}}};actions.append(open);body.append(intro,row,actions,status);openModal('Open photo library',body)}}
async function openLibraryPanelV2(){{
 const body=document.createElement('div');body.className='library-panel';
 const intro=document.createElement('p');intro.textContent='Each folder keeps its own independent index. Choose a previous library, a suggested photo location, or another folder.';
 const choices=document.createElement('div');choices.className='library-choices';
 const row=document.createElement('div');row.className='row';const input=document.createElement('input');input.value=currentLibrary;input.placeholder='Choose a folder';
 const browse=document.createElement('button');browse.className='secondary';browse.textContent='Browse…';row.append(input,browse);
 const status=document.createElement('div');status.className='library-status';const metrics=document.createElement('div');metrics.className='scan-metrics';
 const actions=document.createElement('div');actions.className='publish-actions';const cancel=document.createElement('button');cancel.className='danger';cancel.textContent='Pause scan';cancel.hidden=true;const open=document.createElement('button');open.textContent='Open & index library';actions.append(cancel,open);
 function renderMetrics(job){{metrics.replaceChildren(...[['Discovered',job.scanned],['Indexed',job.changed],['Cloud-only',job.placeholders],['Unchanged',job.unchanged],['Removed',job.removed],['Errors',job.errors]].map(([label,value])=>{{const box=document.createElement('span');const strong=document.createElement('strong');strong.textContent=Number(value||0).toLocaleString();box.append(strong,document.createTextNode(label));return box}}))}}
 async function poll(){{const response=await fetch('/api/library/status');const job=await response.json();status.textContent=job.message||job.state;renderMetrics(job);if(job.state==='complete'){{open.disabled=false;open.textContent='Open library';open.onclick=()=>location.href='/?sort=newest';cancel.hidden=true;return}}if(job.state==='cancelled'){{open.disabled=false;open.textContent='Resume scan';cancel.hidden=true;return}}if(job.state==='error'){{open.disabled=false;cancel.hidden=true;return}}setTimeout(poll,350)}}
 browse.onclick=async()=>{{browse.disabled=true;try{{const result=await api('/api/library/browse',{{}});if(result.path)input.value=result.path}}catch(e){{status.textContent=e.message}}finally{{browse.disabled=false}}}};
 open.onclick=async()=>{{open.disabled=true;cancel.hidden=false;status.textContent='Starting library index…';try{{await api('/api/library/open',{{path:input.value}});poll()}}catch(e){{status.textContent=e.message;open.disabled=false;cancel.hidden=true}}}};
 cancel.onclick=async()=>{{cancel.disabled=true;try{{await api('/api/library/cancel',{{}})}}catch(e){{status.textContent=e.message}}}};
 body.append(intro,choices,row,actions,status,metrics);openModal('Photo libraries',body);
 try{{const response=await fetch('/api/library/options');const data=await response.json();const entries=[...(data.known||[]).map(x=>({{...x,group:'Previous'}})),...(data.suggestions||[]).map(x=>({{...x,group:'Suggested'}}))];const seen=new Set();for(const item of entries){{if(seen.has(item.path.toLowerCase()))continue;seen.add(item.path.toLowerCase());const button=document.createElement('button');button.className='library-choice';const strong=document.createElement('strong');strong.textContent=item.group+' · '+item.label;const small=document.createElement('small');small.textContent=item.path;button.append(strong,small);button.onclick=()=>input.value=item.path;choices.append(button)}}}}catch(e){{status.textContent=e.message}}
}}
function metadataText(value){{if(Array.isArray(value))return value.join(', ');if(value&&typeof value==='object')return JSON.stringify(value);return value==null?'':String(value)}}
async function previewPublish(){{if(!currentDetail?.publishable)return;try{{const preview=await api('/api/publish/preview',{{id:selectedId,description:$('publishDescription').value}});const box=document.createElement('div');const intro=document.createElement('p');intro.textContent='Review every destination below. Nothing has been written yet.';const table=document.createElement('table');table.className='preview-table';const head=document.createElement('tr');['File field','Before','After'].forEach(label=>{{const th=document.createElement('th');th.textContent=label;head.append(th)}});table.append(head);const keys=[...new Set([...Object.keys(preview.before),...Object.keys(preview.after)])];keys.forEach(key=>{{const row=document.createElement('tr');[key,metadataText(preview.before[key]),metadataText(preview.after[key])].forEach(value=>{{const cell=document.createElement('td');cell.textContent=value||'—';row.append(cell)}});table.append(row)}});const bar=document.createElement('div');bar.className='confirm-bar';const note=document.createElement('small');note.textContent='A full safety copy is created before writing, and decoded picture pixels must match afterward.';const buttons=document.createElement('div');buttons.className='confirm-buttons';const cancelButton=document.createElement('button');cancelButton.className='secondary';cancelButton.textContent='Cancel';cancelButton.onclick=()=>$('modalBackdrop').classList.remove('open');const confirmButton=document.createElement('button');confirmButton.textContent='Publish this photo';confirmButton.onclick=async()=>{{confirmButton.disabled=true;cancelButton.disabled=true;try{{const result=await api('/api/publish',{{id:selectedId,description:$('publishDescription').value,expected_after:preview.after}});$('modalBackdrop').classList.remove('open');await selectAsset(selectedId);setStatus(result.message)}}catch(e){{confirmButton.disabled=false;cancelButton.disabled=false;setStatus(e.message,true)}}}};buttons.append(cancelButton,confirmButton);bar.append(note,buttons);box.append(intro,table,bar);openModal('Publish metadata to '+currentDetail.filename,box);document.querySelector('.modal').classList.add('publish-modal')}}catch(e){{setStatus(e.message,true)}}}}
async function restorePublished(){{if(!currentDetail?.can_restore_publish||!confirm('Restore “'+currentDetail.filename+'” from the safety backup made before its last publish?'))return;try{{const result=await api('/api/publish/restore',{{id:selectedId}});await selectAsset(selectedId);setStatus(result.message)}}catch(e){{setStatus(e.message,true)}}}}
async function openMenuPanel(name){{$('menuPanel').classList.remove('open');if(name==='review-people')return openPeopleReview();if(name==='library')return openLibraryPanelV2();if(name==='guide')return openModal('Quick guide',textPanel(['Use Primary subject for the main thing in one photo, Photo tags for other visible things, People for confirmed identities, and Event / folder tags for context shared by the whole batch. Search uses Everything by default so all four contribute to normal results.','Face matches are suggestions until you confirm them. Open Information already in this photo to see readable EXIF, IPTC, and XMP details already stored in the file.']));if(name==='about')return openModal('About LensLedger',textPanel(['LensLedger v{APP_VERSION} — {APP_TAGLINE}','Current photo library: '+currentLibrary,'Each library has a separate index. Approved People-review decisions publish immediately; other edits use Preview & Publish. Every write creates a safety backup and verifies the picture pixels.']));if(name==='trash'){{const body=document.createElement('div');body.className='trash-list';openModal('Trash & restore',body);try{{const r=await fetch('/api/trash');const data=await r.json();if(!data.items.length){{const empty=document.createElement('div');empty.className='trash-empty';empty.textContent='Trash is empty.';body.append(empty);return}}data.items.forEach(item=>{{const row=document.createElement('div');row.className='trash-item';const meta=document.createElement('div');meta.className='trash-meta';const strong=document.createElement('strong');strong.textContent=item.name;const small=document.createElement('small');small.textContent=item.path;meta.append(strong,small);const restore=document.createElement('button');restore.textContent='Restore';restore.onclick=async()=>{{await api('/api/review-bin/restore',{{review_id:item.id}});location.reload()}};row.append(meta,restore);body.append(row)}})}}catch(e){{body.textContent=e.message}}}}}}
function renderFilmstrip(){{const f=$('filmstrip');items.forEach(item=>{{const b=document.createElement('button');b.className='thumb';b.dataset.id=item.id;b.title=item.filename;b.onclick=()=>selectAsset(item.id);if(item.media_type==='image'){{const img=document.createElement('img');img.loading='lazy';img.src='/media?id='+item.id;b.append(img)}}else{{const v=document.createElement('div');v.className='video-label';v.textContent=item.media_type==='raw'?'RAW':'▶ VIDEO';b.append(v)}}const s=document.createElement('span');s.textContent=(item.capture_date||'')+'  '+item.filename;b.append(s);f.append(b)}});{pager_js}}}
function enableFilmstripDrag(){{const f=$('filmstrip');let active=false,startX=0,startScroll=0,moved=false,suppressClick=false,pointerId=null;f.addEventListener('pointerdown',e=>{{if(e.button!==0||e.target.closest('a'))return;active=true;moved=false;pointerId=e.pointerId;startX=e.clientX;startScroll=f.scrollLeft}});f.addEventListener('pointermove',e=>{{if(!active||e.pointerId!==pointerId)return;const delta=e.clientX-startX;if(!moved&&Math.abs(delta)>5){{moved=true;f.classList.add('dragging');f.setPointerCapture(pointerId)}}if(moved){{f.scrollLeft=startScroll-delta;e.preventDefault()}}}});f.addEventListener('pointerup',e=>{{if(!active||e.pointerId!==pointerId)return;suppressClick=moved;active=false;f.classList.remove('dragging');if(f.hasPointerCapture(pointerId))f.releasePointerCapture(pointerId);pointerId=null;if(suppressClick)setTimeout(()=>suppressClick=false,0)}});f.addEventListener('pointercancel',()=>{{active=false;moved=false;suppressClick=false;f.classList.remove('dragging');pointerId=null}});f.addEventListener('click',e=>{{if(!suppressClick)return;suppressClick=false;e.preventDefault();e.stopPropagation()}},true)}}
function changeDay(delta){{const input=$('datePicker');let d=input.value?new Date(input.value+'T12:00:00'):new Date();d.setDate(d.getDate()+delta);input.value=d.toISOString().slice(0,10);input.form.submit()}}
function submitOnEnter(event,action){{if(event.key==='Enter'&&!event.isComposing){{event.preventDefault();action()}}}}
$('reviewPeopleGallery')?.addEventListener('click',()=>location.href='/people-review');
$('previousPhoto').onclick=()=>step(-1);$('nextPhoto').onclick=()=>step(1);$('previousDay').onclick=()=>changeDay(-1);$('nextDay').onclick=()=>changeDay(1);$('saveSubject').onclick=saveSubject;$('subjectInput').onkeydown=e=>submitOnEnter(e,saveSubject);$('addTag').onclick=addTag;$('newTag').onkeydown=e=>submitOnEnter(e,addTag);$('addPerson').onclick=addPerson;$('newPerson').onkeydown=e=>submitOnEnter(e,addPerson);$('addContextTag').onclick=addContextTag;$('newContextTag').onkeydown=e=>submitOnEnter(e,addContextTag);$('previewPublish').onclick=previewPublish;$('restorePublish').onclick=restorePublished;$('moveToTrash').onclick=moveToBin;$('scopePicker').onchange=e=>{{if(e.target.value==='people'){{e.target.form.querySelector('[name=q]').value='';e.target.form.querySelector('[name=date]').value=''}}e.target.form.submit()}};document.querySelectorAll('.edit-aliases').forEach(button=>button.onclick=()=>editAliases(button));$('menuToggle').onclick=e=>{{e.stopPropagation();closeHelp();$('menuPanel').classList.toggle('open')}};document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>openMenuPanel(b.dataset.panel));document.querySelectorAll('[data-help]').forEach(b=>b.onclick=e=>{{e.stopPropagation();const target=$(b.dataset.help);const opening=!target.classList.contains('open');closeHelp();if(opening)target.classList.add('open')}});$('modalClose').onclick=()=>$('modalBackdrop').classList.remove('open');$('modalBackdrop').onclick=e=>{{if(e.target===$('modalBackdrop'))$('modalBackdrop').classList.remove('open')}};document.addEventListener('click',e=>{{if(!e.target.closest('.menu-panel')&&!e.target.closest('.menu-toggle'))$('menuPanel').classList.remove('open');if(!e.target.closest('.help-popover')&&!e.target.closest('.info-button'))closeHelp()}});document.addEventListener('keydown',e=>{{if(e.key==='Escape'){{$('menuPanel').classList.remove('open');$('modalBackdrop').classList.remove('open');closeHelp()}}if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='ArrowLeft')step(-1);if(e.key==='ArrowRight')step(1)}});renderFilmstrip();enableFilmstripDrag();if(selectedId)selectAsset(selectedId);else{{document.querySelectorAll('.sidebar input,.sidebar textarea,.sidebar button').forEach(control=>control.disabled=true);$('moveToTrash').disabled=true;updateNav()}}
</script></body></html>"""
        self.send_html(page)

    def people_review_page(self, params):
        requested = params.get("person", [""])[0]
        initial_person_id = int(requested) if requested.isdigit() else None
        template = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review people — __APP_NAME__</title><link rel="icon" href="/logo.png">
<style>
:root{--bg:#0d1018;--panel:#171b26;--panel2:#202638;--line:#30384d;--text:#f4f6fb;--muted:#9ba8bd;--cyan:#16bde9;--danger:#e25c70;--green:#4eb46a;--yellow:#ffd84d}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:14px system-ui,Segoe UI,sans-serif}button,input{font:inherit}button,.button{border:0;border-radius:8px;padding:10px 14px;background:#3d6fe8;color:white;font-weight:750;cursor:pointer;text-decoration:none}button.secondary,.button.secondary{background:#30384b}button:disabled{opacity:.45;cursor:default}
body{display:grid;grid-template-rows:auto minmax(0,1fr)}header{position:sticky;top:0;z-index:5;background:#141925;border-bottom:1px solid var(--line);padding:12px 20px}.topbar{display:flex;align-items:center;gap:12px;max-width:1800px;margin:auto}.topbar img{width:34px;height:34px;object-fit:contain}.identity{display:grid}.identity strong{font-size:18px}.identity small{color:var(--muted)}.version{color:#b5a0ff;background:#292341;border:1px solid #4a3f75;border-radius:999px;padding:3px 8px;font-size:11px}.top-spacer{flex:1}.progress{color:var(--muted);white-space:nowrap}
main{width:min(1800px,100%);margin:auto;padding:18px 20px 110px}.review-head{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:14px}.review-head h1{font-size:30px;margin:0 0 4px}.review-head p{margin:0;color:var(--muted);line-height:1.45}.person-count{color:#d6b77d;font-weight:750;text-align:right}.photo-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.review-card{min-width:0;background:var(--panel);border:3px solid var(--green);border-radius:12px;overflow:hidden;box-shadow:0 8px 24px #0006}.review-card.wrong{border-color:var(--danger);opacity:.78}.photo-box{position:relative;height:clamp(230px,31vh,370px);min-height:0;overflow:hidden;display:block;background:#07090e}.photo-box img{position:absolute;inset:0;display:block;width:100%;height:100%;min-width:0;min-height:0;object-fit:contain}.state-badge{position:absolute;z-index:1;left:10px;top:10px;border-radius:999px;padding:6px 9px;background:#28623a;color:white;font-size:12px;font-weight:800;box-shadow:0 2px 10px #000}.wrong .state-badge{background:#873d4b}.expand{position:absolute;z-index:1;right:8px;top:8px;background:#202638dd;padding:7px 9px}.card-info{display:grid;gap:8px;padding:10px}.file-line{display:flex;gap:8px;align-items:start}.file-line div{min-width:0;flex:1}.file-line strong,.file-line small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.file-line small{color:var(--muted);margin-top:3px}.confidence{color:#d6b77d;font-weight:750;white-space:nowrap}.toggle-wrong{width:100%;background:#28623a}.wrong .toggle-wrong{background:#873d4b}.correction{display:none;grid-template-columns:minmax(0,1fr);gap:5px}.wrong .correction{display:grid}.correction label{font-size:11px;color:#c8d0dd}.correction input{width:100%;border:1px solid #4b5670;border-radius:7px;background:#22293a;color:white;padding:8px}
.actionbar{position:fixed;z-index:6;left:0;right:0;bottom:0;background:#141925f5;border-top:1px solid var(--line);padding:12px 20px;box-shadow:0 -8px 26px #0008}.actions{max-width:1800px;margin:auto;display:flex;align-items:center;gap:10px}.actions .spacer{flex:1}.selection-summary{color:#cdd6e5}.status{color:var(--cyan);min-height:20px}.primary-action{background:#39764b;padding:12px 18px}.empty{min-height:55vh;display:grid;place-items:center;text-align:center}.empty h2{font-size:30px;margin:0 0 8px}.empty p{color:var(--muted)}
.lightbox{display:none;position:fixed;z-index:20;inset:0;background:#05070bef;padding:24px;grid-template-rows:auto minmax(0,1fr)}.lightbox.open{display:grid}.lightbox-head{display:flex;justify-content:flex-end;margin-bottom:10px}.lightbox img{width:100%;height:100%;object-fit:contain;min-height:0}
@media(max-width:1250px){.photo-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.photo-box{height:360px}}@media(max-width:700px){.photo-grid{grid-template-columns:1fr}.photo-box{height:420px}.review-head{align-items:start;flex-direction:column}.actions{flex-wrap:wrap}.actions .spacer{display:none}.progress{display:none}}
</style></head><body>
<header><div class="topbar"><a class="button secondary" href="/">← Photo library</a><img src="/logo.png" alt=""><div class="identity"><strong>__APP_NAME__</strong><small>People review</small></div><span class="version">v__APP_VERSION__</span><span class="top-spacer"></span><span class="progress" id="globalProgress">Loading suggestions…</span><button type="button" class="secondary" id="learnMore">Find more matches</button></div></header>
<main><section id="reviewArea"><div class="empty"><div><h2>Loading people…</h2><p>Preparing the next group of photos.</p></div></div></section></main>
<div class="actionbar" id="actionbar" hidden><div class="actions"><button type="button" class="secondary" id="skipBatch">Skip these for now</button><button type="button" class="secondary" id="nextPerson">Next person</button><button type="button" class="secondary" id="undoBatch" disabled>Undo last batch</button><span class="spacer"></span><span><span class="selection-summary" id="selectionSummary"></span><span class="status" id="status"></span></span><button type="button" class="primary-action" id="confirmBatch">Save &amp; publish this group</button></div></div>
<div class="lightbox" id="lightbox"><div class="lightbox-head"><button type="button" class="secondary" id="closeLightbox">Close</button></div><img id="largePhoto" alt="Enlarged photo"></div>
<datalist id="peopleOptions"></datalist>
<script>
const csrf=__CSRF__;const initialPersonId=__INITIAL_PERSON__;const batchSize=8;
let queue=null;let batch=[];let rejected=new Set();let skipped=new Set();let corrections=new Map();let history=[];
const $=id=>document.getElementById(id);
async function api(path,data){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,csrf})});const value=await response.json();if(!response.ok)throw new Error(value.error||'Request failed');return value}
function availableSuggestions(){return (queue?.suggestions||[]).filter(item=>!skipped.has(queue.person.id+':'+item.id))}
async function loadQueue(personId=null,advance=false){let url='/api/people/review/queue';const query=new URLSearchParams();if(personId)query.set('person_id',personId);if(advance)query.set('advance','1');if(query.size)url+='?'+query;const response=await fetch(url);queue=await response.json();if(!response.ok)throw new Error(queue.error||'Could not load People review');render()}
function render(){rejected.clear();corrections.clear();$('confirmBatch').disabled=false;if(!queue?.person){$('reviewArea').innerHTML='<div class="empty"><div><h2>People review complete</h2><p>There are no face suggestions waiting for review.</p><a class="button" href="/">Return to the photo library</a></div></div>';$('actionbar').hidden=true;$('globalProgress').textContent='No suggestions remaining';return}const available=availableSuggestions();if(!available.length){loadQueue(queue.person.id,true).catch(showError);return}batch=available.slice(0,batchSize);$('globalProgress').textContent=queue.remaining_total.toLocaleString()+' photos · '+queue.people_remaining.toLocaleString()+' people remaining';$('peopleOptions').replaceChildren(...queue.people_options.map(name=>{const option=document.createElement('option');option.value=name;return option}));const section=document.createElement('section');section.innerHTML='<div class="review-head"><div><h1></h1><p>These photos may contain this person. Mark the incorrect ones, then save and publish the group.</p></div><div class="person-count"></div></div><div class="photo-grid"></div>';section.querySelector('h1').textContent='Does this photo contain '+queue.person.name+'?';section.querySelector('.person-count').textContent=batch.length+' shown · '+queue.suggestions.length+' suggestions for '+queue.person.name;const grid=section.querySelector('.photo-grid');batch.forEach(item=>grid.append(buildCard(item)));$('reviewArea').replaceChildren(section);$('actionbar').hidden=false;$('undoBatch').disabled=!history.length;$('status').textContent='';updateSummary()}
function buildCard(item){const card=document.createElement('article');card.className='review-card';card.dataset.id=item.id;card.innerHTML='<div class="photo-box"><img loading="lazy" alt="Suggested photo"><span class="state-badge">✓ Contains person</span><button type="button" class="expand" title="Show the full photo larger">⛶ Enlarge</button></div><div class="card-info"><div class="file-line"><div><strong></strong><small></small></div><span class="confidence"></span></div><button type="button" class="toggle-wrong">This photo contains '+escapeText(queue.person.name)+'</button><div class="correction"><label>If you know who it is, enter the correct name (optional)</label><input list="peopleOptions" placeholder="Correct name"></div></div>';card.querySelector('img').src='/media?id='+item.id;card.querySelector('strong').textContent=item.filename;card.querySelector('small').textContent=(item.capture_date||'Date unknown')+' · '+item.folder;card.querySelector('.confidence').textContent=Math.round((item.confidence||0)*100)+'%';card.querySelector('.toggle-wrong').onclick=()=>toggleCard(card,item);card.querySelector('.expand').onclick=()=>openLarge(item.id);const input=card.querySelector('input');input.oninput=()=>corrections.set(item.id,input.value.trim());input.onclick=e=>e.stopPropagation();return card}
function escapeText(value){const span=document.createElement('span');span.textContent=value;return span.innerHTML}
function toggleCard(card,item){const key=item.id;if(rejected.has(key)){rejected.delete(key);corrections.delete(key);card.querySelector('input').value='';card.classList.remove('wrong');card.querySelector('.state-badge').textContent='✓ Contains person';card.querySelector('.toggle-wrong').textContent='This photo contains '+queue.person.name}else{rejected.add(key);card.classList.add('wrong');card.querySelector('.state-badge').textContent='✕ Does not contain '+queue.person.name;card.querySelector('.toggle-wrong').textContent='Marked as not containing this person'}updateSummary()}
function updateSummary(){const wrong=rejected.size;const matches=batch.length-wrong;$('selectionSummary').textContent=matches+' will be confirmed and published'+(wrong?' · '+wrong+' marked incorrect':'');$('confirmBatch').textContent='Save & publish '+batch.length+' decision'+(batch.length===1?'':'s')}
function openLarge(id){$('largePhoto').src='/media?id='+id;$('lightbox').classList.add('open')}
function closeLarge(){$('lightbox').classList.remove('open');$('largePhoto').removeAttribute('src')}
async function submitBatch(){const button=$('confirmBatch');button.disabled=true;$('status').textContent='Saving decisions and publishing metadata…';try{const decisions=batch.map(item=>{const corrected=(corrections.get(item.id)||'').trim();return{asset_id:item.id,action:rejected.has(item.id)?(corrected?'corrected':'rejected'):'confirmed',corrected_name:corrected}});const result=await api('/api/people/review/batch',{person_id:queue.person.id,decisions});history.push({person_id:queue.person.id,action_ids:result.action_ids});batch.forEach(item=>skipped.delete(queue.person.id+':'+item.id));await loadQueue(queue.person.id)}catch(error){showError(error);button.disabled=false}}
function skipBatch(){batch.forEach(item=>skipped.add(queue.person.id+':'+item.id));render()}
async function undoBatch(){const prior=history.pop();if(!prior)return;$('undoBatch').disabled=true;try{await api('/api/people/review/batch-undo',{action_ids:prior.action_ids});await loadQueue(prior.person_id)}catch(error){history.push(prior);showError(error);$('undoBatch').disabled=false}}
function showError(error){$('status').textContent=error.message||String(error)}
async function runLearning(){const button=$('learnMore');button.disabled=true;$('globalProgress').textContent='Learning from confirmed faces…';try{const result=await api('/api/people/learn',{});skipped.clear();history=[];await loadQueue();if(!result.suggestions)$('globalProgress').textContent='No additional strong matches found'}catch(error){$('globalProgress').textContent=error.message||String(error)}finally{button.disabled=false}}
$('confirmBatch').onclick=submitBatch;$('skipBatch').onclick=skipBatch;$('nextPerson').onclick=()=>loadQueue(queue?.person?.id,true).catch(showError);$('undoBatch').onclick=undoBatch;$('learnMore').onclick=runLearning;$('closeLightbox').onclick=closeLarge;$('lightbox').onclick=event=>{if(event.target===$('lightbox'))closeLarge()};document.addEventListener('keydown',event=>{if(event.key==='Escape')closeLarge()});loadQueue(initialPersonId).catch(error=>{$('reviewArea').innerHTML='<div class="empty"><div><h2>People review could not open</h2><p></p></div></div>';$('reviewArea').querySelector('p').textContent=error.message});
</script></body></html>"""
        page = (template.replace("__APP_NAME__", html.escape(APP_NAME))
                .replace("__APP_VERSION__", html.escape(APP_VERSION))
                .replace("__CSRF__", json.dumps(self.csrf_token))
                .replace("__INITIAL_PERSON__", json.dumps(initial_person_id)))
        self.send_html(page)

    def asset_detail(self, params):
        try:
            asset_id = int(params.get("id", [""])[0])
            with self.db() as con:
                asset = self.get_active_asset(con, asset_id)
                source_path = self.library_root / Path(asset["relative_path"])
                embedded_keywords = extract_xmp_keywords(source_path)
                stored_keywords = [row[0] for row in con.execute(
                    """SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                       WHERE at.asset_id=? AND at.source='embedded_xmp' ORDER BY t.name""", (asset_id,)
                )]
                if [value.casefold() for value in stored_keywords] != [value.casefold() for value in embedded_keywords]:
                    set_source_tags(con, asset_id, "embedded_xmp", embedded_keywords)
                    rebuild_search_row(con, asset_id)
                annotation = con.execute(
                    "SELECT subject FROM asset_annotations WHERE relative_path=?", (asset["relative_path"],)
                ).fetchone()
                excluded = {row[0].casefold() for row in con.execute(
                    "SELECT tag FROM asset_tag_exclusions WHERE relative_path=?", (asset["relative_path"],)
                )}
                tag_rows = con.execute(
                    """SELECT t.name,at.source FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                       WHERE at.asset_id=? ORDER BY t.name""", (asset_id,)
                ).fetchall()
                context_keys = {
                    row["name"].casefold() for row in tag_rows
                    if row["source"] == "folder_rule" and row["name"].casefold() not in excluded
                }
                seen: set[tuple[str, bool]] = set()
                image_tags = []
                context_tags = []
                for row in tag_rows:
                    key = row["name"].casefold()
                    if key in excluded or row["source"] in {"subject", "person"}:
                        continue
                    if row["source"] != "folder_rule" and key in context_keys:
                        continue
                    target = context_tags if row["source"] == "folder_rule" else image_tags
                    if (key, target is context_tags) not in seen:
                        target.append({"name": row["name"], "source": row["source"]})
                        seen.add((key, target is context_tags))
                confirmed_people = [dict(row) for row in con.execute(
                    """SELECT p.name,ap.source FROM asset_people ap JOIN people p ON p.id=ap.person_id
                       WHERE ap.asset_id=? AND ap.state='confirmed' ORDER BY p.name""", (asset_id,)
                )]
                suggested_people = [dict(row) for row in con.execute(
                    """SELECT p.name,ap.confidence FROM asset_people ap JOIN people p ON p.id=ap.person_id
                       WHERE ap.asset_id=? AND ap.state='suggested'
                       ORDER BY ap.confidence DESC,p.name""", (asset_id,)
                )]
                confirmed_keys = {person["name"].casefold() for person in confirmed_people}
                image_tags = [tag for tag in image_tags if tag["name"].casefold() not in confirmed_keys]
                return self.send_json({
                    "id": asset_id, "filename": asset["filename"], "folder": asset["folder"],
                    "capture_date": asset["capture_date"], "media_type": asset["media_type"],
                    "subject": annotation["subject"] if annotation else "",
                    "image_tags": image_tags, "context_tags": context_tags,
                    "confirmed_people": confirmed_people, "suggested_people": suggested_people,
                    "people_options": [row[0] for row in con.execute(
                        "SELECT name FROM people UNION SELECT alias FROM person_aliases ORDER BY 1 COLLATE NOCASE"
                    )],
                    "embedded_metadata": read_embedded_metadata(source_path),
                    "publishable": asset["media_type"] == "image" and source_path.suffix.lower() in PUBLISHABLE_EXTENSIONS,
                    "can_restore_publish": bool(con.execute(
                        """SELECT 1 FROM metadata_publications WHERE relative_path=? AND restored_at IS NULL
                           ORDER BY id DESC LIMIT 1""", (asset["relative_path"],)
                    ).fetchone()),
                    "hidden_tags": [row[0] for row in con.execute(
                        "SELECT tag FROM asset_tag_exclusions WHERE relative_path=? ORDER BY tag", (asset["relative_path"],)
                    )],
                })
        except (ValueError, sqlite3.Error) as exc:
            return self.send_json({"error": str(exc)}, 404)

    def _publication_values(self, con: sqlite3.Connection, asset_id: int, description: str):
        asset = self.get_active_asset(con, asset_id)
        path = (self.library_root / Path(asset["relative_path"])).resolve()
        path.relative_to(self.library_root)
        if path.suffix.lower() not in PUBLISHABLE_EXTENSIONS or not path.is_file():
            raise ValueError("Publishing is currently available for JPEG photos")
        annotation = con.execute(
            "SELECT subject FROM asset_annotations WHERE relative_path=?", (asset["relative_path"],)
        ).fetchone()
        excluded = {row[0].casefold() for row in con.execute(
            "SELECT tag FROM asset_tag_exclusions WHERE relative_path=?", (asset["relative_path"],)
        )}
        keywords: list[str] = []
        seen: set[str] = set()
        for row in con.execute(
            """SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id
               WHERE at.asset_id=? AND at.source<>'subject' ORDER BY t.name""", (asset_id,)
        ):
            key = row["name"].casefold()
            if key not in excluded and key not in seen:
                keywords.append(row["name"]); seen.add(key)
        subject = annotation["subject"] if annotation else ""
        people = [row[0] for row in con.execute(
            """SELECT p.name FROM asset_people ap JOIN people p ON p.id=ap.person_id
               WHERE ap.asset_id=? AND ap.state='confirmed' ORDER BY p.name""", (asset_id,)
        )]
        before_fields = _exiftool_values(path)
        before_fields.pop("SourceFile", None)
        after_fields = {
            "IFD0:ImageDescription": description,
            "XMP-dc:Description": description,
            "IPTC:Caption-Abstract": description,
            "XMP-dc:Title": subject,
            "IPTC:ObjectName": subject,
            "XMP-photoshop:Headline": subject,
            "XMP-dc:Subject": keywords,
            "IPTC:Keywords": keywords,
            "XMP-microsoft:LastKeywordXMP": keywords,
            "XMP-iptcExt:PersonInImage": people,
        }
        return asset, path, {
            "before": before_fields,
            "after": after_fields,
            "summary": {"subject": subject, "description": description, "keywords": keywords, "people": people},
        }

    @staticmethod
    def _metadata_values(value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    def _publish_people_metadata(self, con: sqlite3.Connection, asset_id: int,
                                 remove_names=(), review_action_id=None,
                                 operation="people"):
        asset = self.get_active_asset(con, int(asset_id))
        path = (self.library_root / Path(asset["relative_path"])).resolve()
        path.relative_to(self.library_root)
        if path.suffix.lower() not in PUBLISHABLE_EXTENSIONS or not path.is_file():
            raise ValueError(f"People metadata publishing requires a JPEG: {asset['filename']}")

        before = _exiftool_values(path)
        before.pop("SourceFile", None)
        excluded = {row[0].casefold() for row in con.execute(
            "SELECT tag FROM asset_tag_exclusions WHERE relative_path=?", (asset["relative_path"],)
        )}
        removed = {clean_tag(str(name)).casefold() for name in remove_names if clean_tag(str(name))}
        blocked = excluded | removed

        keywords: list[str] = []
        keyword_keys: set[str] = set()
        for field in ("XMP-dc:Subject", "IPTC:Keywords", "XMP-microsoft:LastKeywordXMP"):
            for value in self._metadata_values(before.get(field)):
                key = value.casefold()
                if key not in blocked and key not in keyword_keys:
                    keywords.append(value); keyword_keys.add(key)

        people: list[str] = []
        people_keys: set[str] = set()
        for value in self._metadata_values(before.get("XMP-iptcExt:PersonInImage")):
            key = value.casefold()
            if key not in blocked and key not in people_keys:
                people.append(value); people_keys.add(key)
        for row in con.execute(
            """SELECT p.name FROM asset_people ap JOIN people p ON p.id=ap.person_id
               WHERE ap.asset_id=? AND ap.state='confirmed' ORDER BY p.name COLLATE NOCASE""",
            (asset_id,),
        ):
            key = row["name"].casefold()
            if key not in people_keys:
                people.append(row["name"]); people_keys.add(key)
            if key not in keyword_keys:
                keywords.append(row["name"]); keyword_keys.add(key)

        after = {
            "XMP-dc:Subject": keywords,
            "IPTC:Keywords": keywords,
            "XMP-microsoft:LastKeywordXMP": keywords,
            "XMP-iptcExt:PersonInImage": people,
        }
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        relative = Path(asset["relative_path"])
        backup = (BACKUP_ROOT / relative.parent /
                  f"{relative.stem}.before-people-{timestamp}{relative.suffix}").resolve()
        backup.relative_to(BACKUP_ROOT.resolve())
        backup.parent.mkdir(parents=True, exist_ok=True)
        before_pixels = _pixel_hash(path)
        shutil.copy2(path, backup)
        if backup.stat().st_size != path.stat().st_size:
            backup.unlink(missing_ok=True)
            raise ValueError(f"The safety backup could not be verified for {asset['filename']}")

        clear_arguments = [
            "-overwrite_original", "-charset", "iptc=UTF8",
            "-XMP-dc:Subject=", "-IPTC:Keywords=", "-XMP-microsoft:LastKeywordXMP=",
            "-XMP-iptcExt:PersonInImage=", str(path),
        ]
        add_arguments = ["-overwrite_original", "-charset", "iptc=UTF8"]
        for keyword in keywords:
            add_arguments.extend([
                f"-XMP-dc:Subject+={keyword}", f"-IPTC:Keywords+={keyword}",
                f"-XMP-microsoft:LastKeywordXMP+={keyword}",
            ])
        for person in people:
            add_arguments.append(f"-XMP-iptcExt:PersonInImage+={person}")
        add_arguments.append(str(path))
        try:
            _run_exiftool(clear_arguments)
            if keywords or people:
                _run_exiftool(add_arguments)
            if _pixel_hash(path) != before_pixels:
                raise ValueError(f"Pixel verification failed for {asset['filename']}")
        except Exception:
            shutil.copy2(backup, path)
            raise

        stat = path.stat()
        con.execute(
            """INSERT INTO metadata_publications
               (asset_id,relative_path,backup_path,before_json,after_json,
                operation,review_action_id,published_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (asset_id, asset["relative_path"], str(backup), json.dumps(before),
             json.dumps(after), operation, review_action_id, utc_now()),
        )
        con.execute(
            "UPDATE assets SET size_bytes=?,mtime_ns=?,metadata_scanned=1,indexed_at=? WHERE id=?",
            (stat.st_size, stat.st_mtime_ns, utc_now(), asset_id),
        )
        set_source_tags(con, asset_id, "embedded_xmp", keywords)
        rebuild_search_row(con, asset_id)
        return {"path": path, "backup": backup, "filename": asset["filename"]}

    @staticmethod
    def _restore_people_batch(published):
        for item in reversed(published):
            try:
                shutil.copy2(item["backup"], item["path"])
                item["backup"].unlink(missing_ok=True)
            except OSError:
                pass

    def preview_publish(self, body):
        asset_id = int(body["id"])
        description = str(body.get("description", "")).strip()[:2000]
        with self.db() as con:
            _asset, _path, preview = self._publication_values(con, asset_id, description)
        self.send_json(preview)

    def publish_metadata(self, body):
        asset_id = int(body["id"])
        description = str(body.get("description", "")).strip()[:2000]
        with self.db() as con:
            asset, path, preview = self._publication_values(con, asset_id, description)
            if body.get("expected_after") != preview["after"]:
                raise ValueError("The metadata changed after the preview. Please preview it again.")
            timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            relative = Path(asset["relative_path"])
            backup = (BACKUP_ROOT / relative.parent / f"{relative.stem}.before-{timestamp}{relative.suffix}").resolve()
            backup.relative_to(BACKUP_ROOT.resolve())
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                raise ValueError("A backup with this timestamp already exists; wait one second and try again")
            before_pixels = _pixel_hash(path)
            shutil.copy2(path, backup)
            if backup.stat().st_size != path.stat().st_size:
                backup.unlink(missing_ok=True)
                raise ValueError("The safety backup could not be verified")
            after = preview["summary"]
            arguments = [
                "-overwrite_original", "-charset", "iptc=UTF8",
                f"-EXIF:ImageDescription={after['description']}",
                f"-XMP-dc:Description={after['description']}",
                f"-IPTC:Caption-Abstract={after['description']}",
                f"-XMP-dc:Title={after['subject']}", f"-IPTC:ObjectName={after['subject']}",
                f"-XMP-photoshop:Headline={after['subject']}",
                "-XMP-dc:Subject=", "-IPTC:Keywords=", "-XMP-microsoft:LastKeywordXMP=",
                "-XMP-iptcExt:PersonInImage=",
            ]
            keyword_arguments = ["-overwrite_original", "-charset", "iptc=UTF8"]
            for keyword in after["keywords"]:
                keyword_arguments.extend([
                    f"-XMP-dc:Subject+={keyword}", f"-IPTC:Keywords+={keyword}",
                    f"-XMP-microsoft:LastKeywordXMP+={keyword}",
                ])
            for person in after["people"]:
                keyword_arguments.append(f"-XMP-iptcExt:PersonInImage+={person}")
            try:
                _run_exiftool([*arguments, str(path)])
                if after["keywords"] or after["people"]:
                    _run_exiftool([*keyword_arguments, str(path)])
                if _pixel_hash(path) != before_pixels:
                    raise ValueError("Pixel verification failed")
            except Exception:
                shutil.copy2(backup, path)
                raise
            stat = path.stat()
            con.execute(
                """INSERT INTO metadata_publications
                   (asset_id,relative_path,backup_path,before_json,after_json,published_at)
                   VALUES (?,?,?,?,?,?)""",
                (asset_id, asset["relative_path"], str(backup), json.dumps(preview["before"]),
                 json.dumps(preview["after"]), utc_now()),
            )
            con.execute(
                "UPDATE assets SET size_bytes=?,mtime_ns=?,metadata_scanned=1,indexed_at=? WHERE id=?",
                (stat.st_size, stat.st_mtime_ns, utc_now(), asset_id),
            )
            set_source_tags(con, asset_id, "embedded_xmp", after["keywords"])
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True, "backup": str(backup), "message": "Metadata published and picture pixels verified"})

    def restore_published_metadata(self, body):
        asset_id = int(body["id"])
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            record = con.execute(
                """SELECT * FROM metadata_publications WHERE relative_path=? AND restored_at IS NULL
                   ORDER BY id DESC LIMIT 1""", (asset["relative_path"],)
            ).fetchone()
            if not record:
                raise ValueError("There is no published version to restore")
            source = (self.library_root / Path(asset["relative_path"])).resolve()
            source.relative_to(self.library_root)
            backup = Path(record["backup_path"]).resolve()
            backup.relative_to(BACKUP_ROOT.resolve())
            if not backup.is_file():
                raise ValueError("The safety backup is missing")
            shutil.copy2(backup, source)
            stat = source.stat()
            con.execute("UPDATE metadata_publications SET restored_at=? WHERE id=?", (utc_now(), record["id"]))
            con.execute(
                "UPDATE assets SET size_bytes=?,mtime_ns=?,metadata_scanned=1,indexed_at=? WHERE id=?",
                (stat.st_size, stat.st_mtime_ns, utc_now(), asset_id),
            )
            set_source_tags(con, asset_id, "embedded_xmp", extract_xmp_keywords(source))
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True, "message": "The photo was restored from its safety backup"})

    def trash_history(self):
        with self.db() as con:
            rows = con.execute(
                """SELECT id,original_relative_path,moved_at FROM review_bin
                   WHERE restored_at IS NULL ORDER BY moved_at DESC"""
            ).fetchall()
        self.send_json({"items": [
            {
                "id": int(row["id"]),
                "name": Path(row["original_relative_path"]).name,
                "path": row["original_relative_path"],
                "moved_at": row["moved_at"],
            }
            for row in rows
        ]})

    @staticmethod
    def resolve_or_create_person(con: sqlite3.Connection, name: str) -> int:
        row = con.execute(
            """SELECT id FROM people WHERE name=? COLLATE NOCASE
               UNION SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE LIMIT 1""",
            (name, name),
        ).fetchone()
        if row:
            return int(row[0])
        con.execute("INSERT INTO people(name) VALUES (?)", (name,))
        return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    def people_review_queue(self, params):
        requested = params.get("person_id", [""])[0]
        requested_id = int(requested) if requested.isdigit() else None
        advance = params.get("advance", [""])[0] == "1"
        with self.db() as con:
            remaining_total = int(con.execute(
                """SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                   WHERE ap.state='suggested' AND a.in_review_bin=0"""
            ).fetchone()[0])
            people_remaining = int(con.execute(
                """SELECT COUNT(DISTINCT ap.person_id) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                   WHERE ap.state='suggested' AND a.in_review_bin=0"""
            ).fetchone()[0])
            person = None
            if requested_id and not advance:
                person = con.execute(
                    """SELECT p.id,p.name FROM people p WHERE p.id=? AND EXISTS (
                           SELECT 1 FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                           WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0
                       )""", (requested_id,)
                ).fetchone()
            if not person and remaining_total:
                previous_name = ""
                if requested_id:
                    row = con.execute("SELECT name FROM people WHERE id=?", (requested_id,)).fetchone()
                    previous_name = row[0] if row else ""
                person = con.execute(
                    """SELECT p.id,p.name FROM people p WHERE p.name>? COLLATE NOCASE AND EXISTS (
                           SELECT 1 FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                           WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0
                       ) ORDER BY p.name COLLATE NOCASE LIMIT 1""", (previous_name,)
                ).fetchone()
                if not person:
                    person = con.execute(
                        """SELECT p.id,p.name FROM people p WHERE EXISTS (
                               SELECT 1 FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                               WHERE ap.person_id=p.id AND ap.state='suggested' AND a.in_review_bin=0
                           ) ORDER BY p.name COLLATE NOCASE LIMIT 1"""
                    ).fetchone()
            suggestions = []
            if person:
                suggestions = [dict(row) for row in con.execute(
                    """SELECT a.id,a.filename,a.folder,a.capture_date,ap.confidence
                       FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                       WHERE ap.person_id=? AND ap.state='suggested' AND a.in_review_bin=0
                       ORDER BY ap.confidence DESC,a.capture_date,a.relative_path""",
                    (person["id"],),
                )]
            people_options = [row[0] for row in con.execute(
                "SELECT name FROM people UNION SELECT alias FROM person_aliases ORDER BY 1 COLLATE NOCASE"
            )]
        self.send_json({
            "person": dict(person) if person else None,
            "suggestions": suggestions,
            "remaining_total": remaining_total,
            "people_remaining": people_remaining,
            "people_options": people_options,
        })

    def learn_people(self, body):
        result = learn_faces(self.db_path, apply=True)
        self.send_json({
            "ok": True,
            "profiles": result["profiles"],
            "eligible_profiles": result["eligible_profiles"],
            "suggestions": result["suggestions"],
        })

    def _apply_people_review_decision(self, con, asset_id, person_id, action, corrected_name=""):
        asset_id = int(asset_id); person_id = int(person_id); action = str(action)
        if action not in {"confirmed", "rejected", "corrected"}:
            raise ValueError("invalid review decision")
        corrected_name = clean_tag(str(corrected_name))
        if action == "corrected" and not corrected_name:
            raise ValueError("enter the correct person's name")
        self.get_active_asset(con, asset_id)
        original = con.execute(
            "SELECT state,confidence,face_id,source,updated_at FROM asset_people WHERE asset_id=? AND person_id=?",
            (asset_id, person_id),
        ).fetchone()
        if not original or original["state"] != "suggested":
            raise ValueError("that suggestion has already been reviewed")
        previous = dict(original)
        corrected_person_id = None
        corrected_previous = None
        final_action = action
        if action == "corrected":
            corrected_person_id = self.resolve_or_create_person(con, corrected_name)
            if corrected_person_id == person_id:
                final_action = "confirmed"
                corrected_person_id = None
            else:
                prior = con.execute(
                    "SELECT state,confidence,face_id,source,updated_at FROM asset_people WHERE asset_id=? AND person_id=?",
                    (asset_id, corrected_person_id),
                ).fetchone()
                corrected_previous = dict(prior) if prior else None
        new_state = "confirmed" if final_action == "confirmed" else "rejected"
        con.execute(
            "UPDATE asset_people SET state=?,updated_at=? WHERE asset_id=? AND person_id=?",
            (new_state, utc_now(), asset_id, person_id),
        )
        if corrected_person_id:
            con.execute(
                """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
                   VALUES (?,?,'confirmed',NULL,?,'manual-correction',?)
                   ON CONFLICT(asset_id,person_id) DO UPDATE SET
                       state='confirmed',face_id=excluded.face_id,
                       source='manual-correction',updated_at=excluded.updated_at""",
                (asset_id, corrected_person_id, original["face_id"], utc_now()),
            )
        cursor = con.execute(
            """INSERT INTO people_review_actions(
                   asset_id,person_id,action,previous_json,corrected_person_id,
                   corrected_previous_json,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (asset_id, person_id, "corrected" if corrected_person_id else final_action,
             json.dumps(previous), corrected_person_id,
             json.dumps(corrected_previous) if corrected_person_id else None, utc_now()),
        )
        sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)
        return int(cursor.lastrowid)

    def people_review_decision(self, body):
        published = []
        try:
            with self.db() as con:
                person = con.execute("SELECT name FROM people WHERE id=?", (int(body["person_id"]),)).fetchone()
                if not person:
                    raise ValueError("person is no longer available")
                action = body.get("action", "")
                action_id = self._apply_people_review_decision(
                    con, body["asset_id"], body["person_id"], action,
                    body.get("corrected_name", ""),
                )
                removed = [person["name"]] if action in {"rejected", "corrected"} else []
                published.append(self._publish_people_metadata(
                    con, int(body["asset_id"]), removed, action_id
                ))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({"ok": True, "action_id": action_id, "published": 1})

    def people_review_batch_decision(self, body):
        person_id = int(body["person_id"])
        decisions = body.get("decisions")
        if not isinstance(decisions, list) or not 1 <= len(decisions) <= 8:
            raise ValueError("choose between one and eight photos")
        asset_ids = [int(item["asset_id"]) for item in decisions]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("the same photo cannot appear twice")
        published = []
        try:
            with self.db() as con:
                person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
                if not person:
                    raise ValueError("person is no longer available")
                action_ids = []
                removals: dict[int, set[str]] = {}
                for item in decisions:
                    action = item.get("action", "")
                    action_ids.append(self._apply_people_review_decision(
                        con, item["asset_id"], person_id, action,
                        item.get("corrected_name", ""),
                    ))
                    if action in {"rejected", "corrected"}:
                        removals.setdefault(int(item["asset_id"]), set()).add(person["name"])
                actions_by_asset = dict(zip(asset_ids, action_ids))
                for asset_id in asset_ids:
                    published.append(self._publish_people_metadata(
                        con, asset_id, removals.get(asset_id, set()), actions_by_asset[asset_id]
                    ))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({"ok": True, "action_ids": action_ids, "published": len(published)})

    def _undo_people_review_action(self, con, action_id):
        action = con.execute(
            "SELECT * FROM people_review_actions WHERE id=? AND undone_at IS NULL", (int(action_id),)
        ).fetchone()
        if not action:
            raise ValueError("that review action can no longer be undone")
        previous = json.loads(action["previous_json"])
        con.execute(
                """UPDATE asset_people SET state=?,confidence=?,face_id=?,source=?,updated_at=?
                   WHERE asset_id=? AND person_id=?""",
                (previous["state"], previous["confidence"], previous["face_id"], previous["source"],
                 previous["updated_at"], action["asset_id"], action["person_id"]),
        )
        corrected_id = action["corrected_person_id"]
        if corrected_id:
            corrected_previous = json.loads(action["corrected_previous_json"])
            if corrected_previous:
                con.execute(
                        """UPDATE asset_people SET state=?,confidence=?,face_id=?,source=?,updated_at=?
                           WHERE asset_id=? AND person_id=?""",
                        (corrected_previous["state"], corrected_previous["confidence"],
                         corrected_previous["face_id"], corrected_previous["source"], corrected_previous["updated_at"],
                         action["asset_id"], corrected_id),
                )
            else:
                con.execute(
                    "DELETE FROM asset_people WHERE asset_id=? AND person_id=?",
                    (action["asset_id"], corrected_id),
                )
        con.execute("UPDATE people_review_actions SET undone_at=? WHERE id=?", (utc_now(), int(action_id)))
        asset_id = int(action["asset_id"])
        publication = con.execute(
            """SELECT * FROM metadata_publications
               WHERE review_action_id=? AND restored_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (int(action_id),),
        ).fetchone()
        if publication:
            backup = Path(publication["backup_path"]).resolve()
            backup.relative_to(BACKUP_ROOT.resolve())
            source = (self.library_root / Path(publication["relative_path"])).resolve()
            source.relative_to(self.library_root)
            if not backup.is_file():
                raise ValueError("The people-metadata safety backup is missing")
            expected_pixels = _pixel_hash(backup)
            shutil.copy2(backup, source)
            if _pixel_hash(source) != expected_pixels:
                raise ValueError("Pixel verification failed while undoing people metadata")
            stat = source.stat()
            con.execute(
                "UPDATE metadata_publications SET restored_at=? WHERE id=?",
                (utc_now(), publication["id"]),
            )
            con.execute(
                "UPDATE assets SET size_bytes=?,mtime_ns=?,metadata_scanned=1,indexed_at=? WHERE id=?",
                (stat.st_size, stat.st_mtime_ns, utc_now(), asset_id),
            )
            set_source_tags(con, asset_id, "embedded_xmp", extract_xmp_keywords(source))
        sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)

    def undo_people_review(self, body):
        with self.db() as con:
            self._undo_people_review_action(con, body["action_id"])
        self.send_json({"ok": True})

    def undo_people_review_batch(self, body):
        action_ids = body.get("action_ids")
        if not isinstance(action_ids, list) or not 1 <= len(action_ids) <= 8:
            raise ValueError("choose between one and eight review decisions")
        if len(action_ids) != len({int(value) for value in action_ids}):
            raise ValueError("the same review decision cannot appear twice")
        with self.db() as con:
            for action_id in reversed(action_ids):
                self._undo_people_review_action(con, action_id)
        self.send_json({"ok": True})

    def add_person(self, body):
        asset_id = int(body["id"]); name = clean_tag(str(body.get("name", "")))
        if not name:
            raise ValueError("enter a person's name")
        published = []
        try:
            with self.db() as con:
                self.get_active_asset(con, asset_id)
                person_id = self.resolve_or_create_person(con, name)
                con.execute(
                    """INSERT INTO asset_people(asset_id,person_id,state,confidence,source,updated_at)
                       VALUES (?,?,'confirmed',NULL,'manual',?)
                       ON CONFLICT(asset_id,person_id) DO UPDATE SET
                           state='confirmed',source='manual',updated_at=excluded.updated_at""",
                    (asset_id, person_id, utc_now()),
                )
                sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)
                published.append(self._publish_people_metadata(con, asset_id))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({"ok": True, "published": 1})

    def set_person_aliases(self, body):
        person_id = int(body["person_id"])
        raw_aliases = body.get("aliases", [])
        if not isinstance(raw_aliases, list):
            raise ValueError("aliases must be a list")
        aliases: list[str] = []
        seen: set[str] = set()
        for value in raw_aliases:
            alias = clean_tag(str(value))
            key = alias.casefold()
            if alias and key not in seen:
                aliases.append(alias); seen.add(key)
        with self.db() as con:
            person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
            if not person:
                raise ValueError("person is no longer available")
            primary_key = person["name"].casefold()
            aliases = [alias for alias in aliases if alias.casefold() != primary_key]
            for alias in aliases:
                conflict = con.execute(
                    "SELECT id FROM people WHERE name=? COLLATE NOCASE AND id<>?", (alias, person_id)
                ).fetchone()
                alias_conflict = con.execute(
                    "SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE AND person_id<>?",
                    (alias, person_id),
                ).fetchone()
                if conflict or alias_conflict:
                    raise ValueError(f"“{alias}” already belongs to another person")
            con.execute("DELETE FROM person_aliases WHERE person_id=?", (person_id,))
            con.executemany(
                "INSERT INTO person_aliases(person_id,alias) VALUES (?,?)",
                [(person_id, alias) for alias in aliases],
            )
        self.send_json({"ok": True, "aliases": aliases})

    def set_person_names(self, body):
        person_id = int(body["person_id"])
        primary_name = clean_tag(str(body.get("name", "")))
        if not primary_name:
            raise ValueError("enter the person's primary name")
        raw_aliases = body.get("aliases", [])
        if not isinstance(raw_aliases, list):
            raise ValueError("alternate names must be a list")
        aliases: list[str] = []
        seen = {primary_name.casefold()}
        for value in raw_aliases:
            alias = clean_tag(str(value))
            key = alias.casefold()
            if alias and key not in seen:
                aliases.append(alias); seen.add(key)

        published = []
        try:
            with self.db() as con:
                person = con.execute("SELECT name FROM people WHERE id=?", (person_id,)).fetchone()
                if not person:
                    raise ValueError("person is no longer available")
                old_name = str(person["name"])
                primary_conflict = con.execute(
                    "SELECT id FROM people WHERE name=? COLLATE NOCASE AND id<>?",
                    (primary_name, person_id),
                ).fetchone()
                primary_alias_conflict = con.execute(
                    "SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE AND person_id<>?",
                    (primary_name, person_id),
                ).fetchone()
                if primary_conflict or primary_alias_conflict:
                    raise ValueError(f"“{primary_name}” already belongs to another person")
                for alias in aliases:
                    name_conflict = con.execute(
                        "SELECT id FROM people WHERE name=? COLLATE NOCASE AND id<>?", (alias, person_id)
                    ).fetchone()
                    alias_conflict = con.execute(
                        "SELECT person_id FROM person_aliases WHERE alias=? COLLATE NOCASE AND person_id<>?",
                        (alias, person_id),
                    ).fetchone()
                    if name_conflict or alias_conflict:
                        raise ValueError(f"“{alias}” already belongs to another person")

                affected = con.execute(
                    """SELECT DISTINCT a.id,a.relative_path FROM asset_people ap
                       JOIN assets a ON a.id=ap.asset_id
                       WHERE ap.person_id=? AND ap.state='confirmed'""",
                    (person_id,),
                ).fetchall()
                embedded_old_assets: set[int] = set()
                if old_name.casefold() != primary_name.casefold():
                    embedded_old_assets = {
                        int(row[0]) for row in con.execute(
                            """SELECT at.asset_id FROM asset_tags at JOIN tags t ON t.id=at.tag_id
                               WHERE at.asset_id IN (
                                   SELECT asset_id FROM asset_people WHERE person_id=? AND state='confirmed'
                               ) AND at.source='embedded_xmp' AND t.name=? COLLATE NOCASE""",
                            (person_id, old_name),
                        )
                    }

                con.execute("DELETE FROM person_aliases WHERE person_id=?", (person_id,))
                con.execute("UPDATE people SET name=? WHERE id=?", (primary_name, person_id))
                con.executemany(
                    "INSERT INTO person_aliases(person_id,alias) VALUES (?,?)",
                    [(person_id, alias) for alias in aliases],
                )
                name_changed = old_name != primary_name
                for asset in affected:
                    asset_id = int(asset["id"])
                    if asset_id in embedded_old_assets:
                        con.execute(
                            "INSERT OR IGNORE INTO asset_tag_exclusions(relative_path,tag) VALUES (?,?)",
                            (asset["relative_path"], old_name),
                        )
                    sync_person_tags(con, asset_id)
                    rebuild_search_row(con, asset_id)
                    if name_changed:
                        published.append(self._publish_people_metadata(
                            con, asset_id, [old_name], operation="people-rename"
                        ))
                if old_name.casefold() != primary_name.casefold():
                    con.execute(
                        """DELETE FROM tags WHERE name=? COLLATE NOCASE AND NOT EXISTS (
                               SELECT 1 FROM asset_tags at WHERE at.tag_id=tags.id
                           )""",
                        (old_name,),
                    )
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({
            "ok": True, "name": primary_name, "aliases": aliases,
            "updated_photos": len(affected), "published": len(published),
        })

    def set_person_state(self, body):
        asset_id = int(body["id"]); name = clean_tag(str(body.get("name", "")))
        state = str(body.get("state", ""))
        if state not in {"confirmed", "rejected"}:
            raise ValueError("invalid person state")
        published = []
        try:
            with self.db() as con:
                self.get_active_asset(con, asset_id)
                row = con.execute("SELECT id FROM people WHERE name=? COLLATE NOCASE", (name,)).fetchone()
                if not row:
                    raise ValueError("person is no longer available")
                changed = con.execute(
                    "UPDATE asset_people SET state=?,updated_at=? WHERE asset_id=? AND person_id=?",
                    (state, utc_now(), asset_id, int(row[0])),
                ).rowcount
                if not changed:
                    raise ValueError("person is no longer associated with this photo")
                sync_person_tags(con, asset_id); rebuild_search_row(con, asset_id)
                published.append(self._publish_people_metadata(
                    con, asset_id, [name] if state == "rejected" else []
                ))
        except Exception:
            self._restore_people_batch(published)
            raise
        self.send_json({"ok": True, "published": 1})

    def library_status(self):
        with type(self).library_lock:
            job = dict(type(self).library_job)
        job["current_root"] = str(type(self).library_root)
        self.send_json(job)

    def library_options(self):
        config = load_library_config()
        known = []
        for value in config.get("libraries", []):
            root = Path(str(value))
            if root.is_dir():
                known.append({"label": root.name or str(root), "path": str(root.resolve())})
        self.send_json({
            "suggestions": suggested_library_roots(),
            "known": known,
            "current_root": str(type(self).library_root),
        })

    def browse_library(self, _body):
        self.send_json({"path": choose_library_folder()})

    def open_library(self, body):
        value = str(body.get("path", "")).strip()
        if not value:
            raise ValueError("choose a photo library folder")
        root = Path(value).resolve()
        if not root.is_dir():
            raise ValueError("that photo library folder does not exist")
        database = library_db_path(root).resolve()
        with type(self).library_lock:
            if type(self).library_job.get("state") == "scanning":
                raise ValueError("another photo library is currently being indexed")
            type(self).library_job = {
                "state": "scanning", "message": "Discovering photos and videos…",
                "target_root": str(root), "scanned": 0, "changed": 0,
                "unchanged": 0, "removed": 0, "errors": 0, "placeholders": 0,
            }
            type(self).library_cancel.clear()
        handler_class = type(self)

        def worker():
            try:
                def update_progress(counts):
                    with handler_class.library_lock:
                        handler_class.library_job = {
                            "state": "scanning", "message": f"Discovered {int(counts['scanned']):,} media files…",
                            "target_root": str(root), **counts,
                        }

                result = scan_library(
                    root, database, progress=update_progress,
                    should_cancel=handler_class.library_cancel.is_set,
                )
                if result not in {0, 2, 3}:
                    raise ValueError("the library index did not complete")
                handler_class.library_root = root
                handler_class.db_path = database
                save_library_state(root)
                with connect(database) as con:
                    summary = {
                        "assets": int(con.execute("SELECT COUNT(*) FROM assets WHERE in_review_bin=0").fetchone()[0]),
                        "images": int(con.execute("SELECT COUNT(*) FROM assets WHERE media_type='image' AND in_review_bin=0").fetchone()[0]),
                        "videos": int(con.execute("SELECT COUNT(*) FROM assets WHERE media_type='video' AND in_review_bin=0").fetchone()[0]),
                        "raw_files": int(con.execute("SELECT COUNT(*) FROM assets WHERE media_type='raw' AND in_review_bin=0").fetchone()[0]),
                        "metadata_ready": int(con.execute("SELECT COUNT(*) FROM assets WHERE metadata_scanned=1 AND in_review_bin=0").fetchone()[0]),
                        "placeholders": int(con.execute("SELECT COUNT(*) FROM assets WHERE metadata_scanned=0 AND in_review_bin=0").fetchone()[0]),
                    }
                with handler_class.library_lock:
                    progress_counts = {key: handler_class.library_job.get(key, 0) for key in (
                        "scanned", "changed", "unchanged", "removed", "errors", "placeholders"
                    )}
                    state = "cancelled" if result == 3 else "complete"
                    handler_class.library_job = {
                        "state": state,
                        "message": "Scan paused. Run it again to resume." if result == 3 else f"Library ready: {root}",
                        "target_root": str(root), **progress_counts, "summary": summary,
                    }
            except Exception as exc:
                with handler_class.library_lock:
                    handler_class.library_job = {
                        "state": "error", "message": str(exc), "target_root": str(root),
                    }

        threading.Thread(target=worker, name="LensLedger-library-index", daemon=True).start()
        self.send_json({"ok": True, "state": "scanning", "path": str(root)}, 202)

    def cancel_library_scan(self, _body):
        with type(self).library_lock:
            if type(self).library_job.get("state") != "scanning":
                raise ValueError("there is no scan running")
            type(self).library_cancel.set()
            type(self).library_job["message"] = "Pausing after the current file…"
        self.send_json({"ok": True, "state": "cancelling"}, 202)

    def update_subject(self, body):
        asset_id = int(body["id"]); subject = clean_tag(str(body.get("subject", "")))
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            con.execute("""INSERT INTO asset_annotations(relative_path,subject,tags) VALUES (?,?,'')
                ON CONFLICT(relative_path) DO UPDATE SET subject=excluded.subject""", (asset["relative_path"], subject))
            set_source_tags(con, asset_id, "subject", [subject] if subject else [])
            if subject:
                con.execute("DELETE FROM asset_tag_exclusions WHERE relative_path=? AND tag=? COLLATE NOCASE", (asset["relative_path"], subject))
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True})

    def add_tag(self, body):
        asset_id = int(body["id"])
        incoming = [
            clean_tag(part) for part in re.split(r"[,;\n]+", str(body.get("tag", "")))
            if clean_tag(part)
        ]
        if not incoming: raise ValueError("enter at least one tag")
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            row = con.execute("SELECT subject,tags FROM asset_annotations WHERE relative_path=?", (asset["relative_path"],)).fetchone()
            names = split_tags(row["tags"] if row else "")
            known = {name.casefold() for name in names}
            for tag in incoming:
                if tag.casefold() not in known:
                    names.append(tag); known.add(tag.casefold())
            con.execute("""INSERT INTO asset_annotations(relative_path,subject,tags) VALUES (?,?,?)
                ON CONFLICT(relative_path) DO UPDATE SET tags=excluded.tags""", (asset["relative_path"], row["subject"] if row else "", ";".join(names)))
            for tag in incoming:
                con.execute("DELETE FROM asset_tag_exclusions WHERE relative_path=? AND tag=? COLLATE NOCASE", (asset["relative_path"], tag))
            set_source_tags(con, asset_id, "asset_rule", names); rebuild_search_row(con, asset_id)
        self.send_json({"ok": True, "added": len(incoming)})

    def add_folder_tag(self, body):
        asset_id = int(body["id"])
        incoming = [
            clean_tag(part) for part in re.split(r"[,;\n]+", str(body.get("tag", "")))
            if clean_tag(part)
        ]
        if not incoming:
            raise ValueError("enter at least one event tag")
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            folder = asset["folder"]
            existing = {
                row[0].casefold() for row in con.execute(
                    "SELECT tag FROM folder_tags WHERE folder=?", (folder,)
                )
            }
            added = 0
            for tag in incoming:
                if tag.casefold() not in existing:
                    con.execute("INSERT INTO folder_tags(folder,tag) VALUES (?,?)", (folder, tag))
                    existing.add(tag.casefold())
                    added += 1
            folder_names = [
                row[0] for row in con.execute(
                    "SELECT tag FROM folder_tags WHERE folder=? ORDER BY tag", (folder,)
                )
            ]
            folder_assets = con.execute(
                "SELECT id FROM assets WHERE folder=?", (folder,)
            ).fetchall()
            for row in folder_assets:
                set_source_tags(con, int(row["id"]), "folder_rule", folder_names)
                rebuild_search_row(con, int(row["id"]))
        self.send_json({"ok": True, "added": added, "assets": len(folder_assets)})

    def remove_tag(self, body):
        asset_id = int(body["id"]); tag = clean_tag(str(body.get("tag", "")))
        if not tag: raise ValueError("tag cannot be empty")
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            con.execute("INSERT OR IGNORE INTO asset_tag_exclusions(relative_path,tag) VALUES (?,?)", (asset["relative_path"], tag))
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True})

    def restore_tag(self, body):
        asset_id = int(body["id"]); tag = clean_tag(str(body.get("tag", "")))
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            con.execute("DELETE FROM asset_tag_exclusions WHERE relative_path=? AND tag=? COLLATE NOCASE", (asset["relative_path"], tag))
            rebuild_search_row(con, asset_id)
        self.send_json({"ok": True})

    def move_to_review_bin(self, body):
        asset_id = int(body["id"])
        with self.db() as con:
            asset = self.get_active_asset(con, asset_id)
            source = Path(asset["path"]).resolve(); source.relative_to(self.library_root)
            if any(part in {"!LensLedger", "_PhotoIndex", "_FaceData"} for part in source.relative_to(self.library_root).parts) or not source.is_file():
                raise ValueError("refusing to move this path")
            review_root = review_bin_root().resolve()
            destination = review_root / Path(asset["relative_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination = destination.with_name(f"{destination.stem}-{dt.datetime.now():%Y%m%d-%H%M%S}{destination.suffix}")
            destination.resolve().relative_to(review_root)
            shutil.move(str(source), str(destination))
            review_id = con.execute("""INSERT INTO review_bin(asset_id,original_path,original_relative_path,review_path,moved_at)
                VALUES (?,?,?,?,?)""", (asset_id, str(source), asset["relative_path"], str(destination), utc_now())).lastrowid
            con.execute("UPDATE assets SET path=?,in_review_bin=1 WHERE id=?", (str(destination), asset_id))
        self.send_json({"ok": True, "review_id": review_id})

    def restore_from_review_bin(self, body):
        review_id = int(body["review_id"])
        with self.db() as con:
            row = con.execute("SELECT * FROM review_bin WHERE id=? AND restored_at IS NULL", (review_id,)).fetchone()
            if not row: raise ValueError("review item is no longer available")
            source = Path(row["review_path"]).resolve(); destination = Path(row["original_path"]).resolve()
            destination.relative_to(self.library_root)
            if not source.is_file() or destination.exists(): raise ValueError("cannot restore because the source is missing or destination exists")
            destination.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(source), str(destination))
            con.execute("UPDATE assets SET path=?,in_review_bin=0 WHERE id=?", (str(destination), row["asset_id"]))
            con.execute("UPDATE review_bin SET restored_at=? WHERE id=?", (utc_now(), review_id))
        self.send_json({"ok": True})

    def serve_logo(self):
        path = Path(__file__).with_name("assets") / "lensledger-logo.png"
        if not path.is_file(): return self.send_error(404)
        self.send_bytes(path.read_bytes(), "image/png", cache="private, max-age=86400")

    def serve_world_map(self):
        path = Path(__file__).with_name("assets") / "world-map.svg"
        if not path.is_file(): return self.send_error(404)
        self.send_bytes(path.read_bytes(), "image/svg+xml", cache="private, max-age=86400")

    def serve_media(self, params):
        try: asset_id = int(params.get("id", [""])[0])
        except ValueError: return self.send_error(400)
        with self.db() as con:
            row = con.execute("SELECT path FROM assets WHERE id=? AND in_review_bin=0", (asset_id,)).fetchone()
        if not row: return self.send_error(404)
        path = Path(row["path"]).resolve()
        try: path.relative_to(self.library_root)
        except ValueError: return self.send_error(403)
        if not path.is_file(): return self.send_error(404)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(path.stat().st_size)); self.send_header("Cache-Control", "private, max-age=3600"); self.end_headers()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024): self.wfile.write(chunk)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_NAME} {APP_VERSION}")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--port", type=int, default=5309)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    root = (args.root or load_library_state()).resolve()
    database = (args.db or library_db_path(root)).resolve()
    SearchHandler.db_path = database; SearchHandler.library_root = root; SearchHandler.csrf_token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), SearchHandler); url = f"http://127.0.0.1:{args.port}/"
    print("\n" + "=" * 62, flush=True); print(f"  {APP_NAME} v{APP_VERSION}\n  {APP_TAGLINE}\n", flush=True); print(f"  Local library: {url}\n  Press Ctrl+C in this window to stop LensLedger.", flush=True); print("=" * 62 + "\n", flush=True)
    if not args.no_open:
        browser_timer = threading.Timer(1.0, webbrowser.open, args=(url,))
        browser_timer.daemon = True
        browser_timer.start()
    try: server.serve_forever()
    except KeyboardInterrupt: print(f"\n{APP_NAME} is stopping...", flush=True)
    finally: server.server_close(); print(f"{APP_NAME} stopped.", flush=True)


if __name__ == "__main__":
    main()
