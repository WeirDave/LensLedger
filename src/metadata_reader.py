"""Read embedded photo metadata without coupling it to the web server."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import ExifTags, Image, IptcImagePlugin


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
        "title": "Title", "headline": "Headline", "description": "Description",
        "subject": "Embedded keywords", "creator": "Creator", "rights": "Copyright",
        "personinimage": "People shown", "event": "Event", "location": "Location",
        "city": "City", "state": "State / province", "country": "Country",
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
            # rdf:Description is an XML container, not dc:description.
            if local in wanted and "rdf-syntax-ns" not in namespace:
                values = [
                    _text_value(node.text) for node in element.iter()
                    if node.tag.rsplit("}", 1)[-1] == "li" and node.text
                ]
                if not values and element.text and element.text.strip():
                    values = [_text_value(element.text)]
                found.setdefault(wanted[local], []).extend(value for value in values if value)
            for name, value in element.attrib.items():
                attr_local = name.rsplit("}", 1)[-1].casefold()
                if attr_local in wanted and _text_value(value):
                    found.setdefault(wanted[attr_local], []).append(_text_value(value))
    return {label: ", ".join(dict.fromkeys(values)) for label, values in found.items() if values}


def read_embedded_metadata(path: Path) -> dict[str, object]:
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
            make = _text_value(main.get("Make", ""))
            model = _text_value(main.get("Model", ""))
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
                    gps_link = f"/map?lat={latitude:.6f}&lon={longitude:.6f}"
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


def pixel_hash(path: Path) -> str:
    """Hash decoded pixels so metadata-only publishing cannot alter the picture."""
    with Image.open(path) as image:
        image.load()
        digest = hashlib.sha256()
        digest.update(f"{image.mode}:{image.size[0]}x{image.size[1]}".encode("ascii"))
        digest.update(image.tobytes())
        return digest.hexdigest()
