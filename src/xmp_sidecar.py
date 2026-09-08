"""Write XMP sidecar (.xmp) files for photo metadata."""

from __future__ import annotations

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
) -> str:
    """Build a complete XMP sidecar XML string."""
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


def write_sidecar(
    photo_path: Path,
    *,
    title: str = "",
    description: str = "",
    keywords: list[str] | None = None,
    people: list[str] | None = None,
) -> Path:
    """Write an XMP sidecar file next to the photo. Returns the sidecar path."""
    xmp_path = photo_path.with_suffix(".xmp")
    content = build_xmp(
        title=title,
        description=description,
        keywords=keywords,
        people=people,
    )
    xmp_path.write_text(content, encoding="utf-8")
    return xmp_path


def sidecar_exists(photo_path: Path) -> bool:
    return photo_path.with_suffix(".xmp").is_file()
