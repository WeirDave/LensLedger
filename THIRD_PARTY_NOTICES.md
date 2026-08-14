# Third-party software

LensLedger bundles the Windows ExifTool distribution so approved metadata can
be read and written locally. ExifTool is copyright Phil Harvey and is provided
under its own license. The complete upstream notices are retained in
`tools/ExifTool/exiftool_files/LICENSE` and
`tools/ExifTool/exiftool_files/README`.

The LensLedger MIT license does not replace or modify those third-party terms.

LensLedger also supports an optional InsightFace-based utility for recovering
bounding boxes that were absent from legacy face indexes. InsightFace's Python
code is separately licensed, and its published pretrained model packs have
separate non-commercial research terms. Neither the optional Python runtime nor
any face model is bundled or redistributed by LensLedger.

The world coastline outlines in `assets/world-map.svg` (used by the local
Photo map) are adapted from "World location map (equirectangular 180).svg"
by TUBS, via Wikimedia Commons, licensed under CC BY-SA 3.0 / GFDL. Colors
and styling were changed and unrelated map elements (rivers, borders, the
template legend marker) were removed; the coastline geometry is otherwise
unmodified. Original: https://commons.wikimedia.org/wiki/File:World_location_map_(equirectangular_180).svg
