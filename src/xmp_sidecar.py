"""Write XMP sidecar (.xmp) files for photo metadata."""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


_XMP_HEADER = '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
_XMP_FOOTER = '<?xpacket end="w"?>'

_NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "Iptc4xmpExt": "http://iptc.org/std/Iptc4xmpExt/2008-02-29/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
}


def _register_namespaces():
    for prefix, uri in _NS.items():
        ET.register_namespace(prefix, uri)


def build_xmp(
    *,
    title: str = "",
    description: str = "",
    keywords: list[str] | None = None,
    people: list[str] | None = None,
    rating: int = 0,
) -> str:
    """Build a complete XMP sidecar XML string.

    A rating outside 1-5 writes no xmp:Rating element at all: "not rated in
    LensLedger" means leave the rating out, never write 0.
    """
    _register_namespaces()
    keywords = keywords or []
    people = people or []

    lines = [
        _XMP_HEADER,
        f'<x:xmpmeta xmlns:x="{_NS["x"]}">',
        f'  <rdf:RDF xmlns:rdf="{_NS["rdf"]}">',
        '    <rdf:Description rdf:about=""',
        f'      xmlns:dc="{_NS["dc"]}"',
        f'      xmlns:photoshop="{_NS["photoshop"]}"',
        f'      xmlns:Iptc4xmpExt="{_NS["Iptc4xmpExt"]}"',
        f'      xmlns:lr="{_NS["lr"]}">',
    ]

    if title:
        lines.extend([
            '      <dc:title>',
            '        <rdf:Alt>',
            f'          <rdf:li xml:lang="x-default">{_escape(title)}</rdf:li>',
            '        </rdf:Alt>',
            '      </dc:title>',
            f'      <photoshop:Headline>{_escape(title)}</photoshop:Headline>',
        ])

    if description:
        lines.extend([
            '      <dc:description>',
            '        <rdf:Alt>',
            f'          <rdf:li xml:lang="x-default">{_escape(description)}</rdf:li>',
            '        </rdf:Alt>',
            '      </dc:description>',
        ])

    if keywords:
        lines.append('      <dc:subject>')
        lines.append('        <rdf:Bag>')
        for kw in keywords:
            lines.append(f'          <rdf:li>{_escape(kw)}</rdf:li>')
        lines.append('        </rdf:Bag>')
        lines.append('      </dc:subject>')
        lines.append('      <lr:hierarchicalSubject>')
        lines.append('        <rdf:Bag>')
        for kw in keywords:
            lines.append(f'          <rdf:li>{_escape(kw)}</rdf:li>')
        lines.append('        </rdf:Bag>')
        lines.append('      </lr:hierarchicalSubject>')

    if 1 <= rating <= 5:
        # Declared on the element, not on rdf:Description: is_lensledger_sidecar
        # recognises our own files by the opening lines of the block.
        lines.append(f'      <xmp:Rating xmlns:xmp="{_NS["xmp"]}">{rating}</xmp:Rating>')

    if people:
        lines.append('      <Iptc4xmpExt:PersonInImage>')
        lines.append('        <rdf:Bag>')
        for person in people:
            lines.append(f'          <rdf:li>{_escape(person)}</rdf:li>')
        lines.append('        </rdf:Bag>')
        lines.append('      </Iptc4xmpExt:PersonInImage>')

    lines.extend([
        '    </rdf:Description>',
        '  </rdf:RDF>',
        '</x:xmpmeta>',
        _XMP_FOOTER,
    ])
    return '\n'.join(lines)


def _escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


class SidecarRefused(Exception):
    """A sidecar was deliberately not written; the message says why."""


def write_sidecar(
    photo_path: Path,
    *,
    title: str = "",
    description: str = "",
    keywords: list[str] | None = None,
    people: list[str] | None = None,
    rating: int = 0,
) -> Path:
    """Write an XMP sidecar file next to the photo. Returns the sidecar path.

    An .xmp file another program wrote under that name is never replaced: it
    holds that program's own settings, and this file would carry only tags.
    The new file is written beside the old one and swapped in whole, so an
    interruption leaves either the old sidecar or the new one.
    """
    xmp_path = photo_path.with_suffix(".xmp")
    if xmp_path.is_file():
        try:
            existing = xmp_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            existing = None
        # An empty file is what an interrupted write used to leave behind.
        if existing is None or (existing.strip() and not is_lensledger_sidecar(existing)):
            raise SidecarRefused(
                f"{xmp_path.name} was written by another program and was left as it is")
    content = build_xmp(
        title=title,
        description=description,
        keywords=keywords,
        people=people,
        rating=rating,
    )
    handle, temp_name = tempfile.mkstemp(prefix=f".{xmp_path.stem}-", suffix=".tmp", dir=xmp_path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as temp:
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, xmp_path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return xmp_path


def sidecar_exists(photo_path: Path) -> bool:
    return photo_path.with_suffix(".xmp").is_file()


def is_lensledger_sidecar(text: str) -> bool:
    """True when a sidecar's opening block is exactly what write_sidecar writes.

    Sidecars share a name with every file of the same stem, and Lightroom and
    other programs write theirs under that same name; only LensLedger's own
    may ever be moved aside.
    """
    opening = build_xmp().split("\n")[:8]
    return text.lstrip("﻿").replace("\r\n", "\n").split("\n")[:8] == opening
