<p align="center">
  <img src="assets/lensledger-logo.png" alt="LensLedger" width="260">
</p>

<h3 align="center">Your photos, understood.</h3>

<p align="center">
  <a href="https://github.com/WeirDave/LensLedger/releases/latest"><img src="https://img.shields.io/github/v/release/WeirDave/LensLedger?style=flat-square&color=6f55b5" alt="Latest Release"></a>
  <a href="https://github.com/WeirDave/LensLedger/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/WeirDave/LensLedger/tests.yml?branch=main&style=flat-square&label=Windows%20tests" alt="Windows tests"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-Windows-informational?style=flat-square" alt="Windows">
  <img src="https://img.shields.io/github/license/WeirDave/LensLedger?style=flat-square" alt="License">
</p>

---

## What is LensLedger?

LensLedger is a local-first photo and video index, search, and metadata review
tool. Point it at a library and it builds a private SQLite catalog without
uploading your media or changing the originals. Browse by date, search across
visible subjects and event context, review people suggestions, inspect embedded
metadata, and explicitly publish approved descriptions, keywords, or people to
individual JPEG files.

The scanner is incremental: unchanged files are skipped, new files are added,
and missing files leave the index. Dropbox and other Windows cloud placeholders
can be inventoried without forcing a download; deeper metadata analysis waits
until those files are locally available.

## Highlights

- Read-only library discovery and incremental scanning
- Separate SQLite index for every selected photo library
- Full-text search across paths, dates, subjects, tags, people, and OCR
- Staged edits that remain in LensLedger until explicitly published
- Field-by-field metadata preview before JPEG writes
- Timestamped safety copies and decoded-pixel verification after publication
- Reversible review bin and people-review history
- Local face profiles that never confirm identities automatically
- Per-user data storage, separate from application source and photo folders

## Quick start

### Windows release

1. Download `LensLedger-vX.Y.Z.zip` from the [latest release](https://github.com/WeirDave/LensLedger/releases/latest).
2. Extract the ZIP to a permanent folder.
3. Install [Python 3.11 or newer](https://www.python.org/downloads/) and select **Add Python to PATH** during setup.
4. Open a terminal in the extracted folder and run `python -m pip install -r requirements.txt`.
5. Double-click **Start LensLedger.cmd**.
6. Open the menu, choose **Open photo library**, select a folder, and let the initial inventory finish.

LensLedger opens a localhost-only viewer at `http://127.0.0.1:5309`. Keep the
terminal window open while using the application.

### Run from source

```powershell
git clone https://github.com/WeirDave/LensLedger.git
cd LensLedger
python -m pip install -r requirements.txt
python photo_search.py
```

## First run and data location

The repository and release contain no database and no personal photo data.
LensLedger creates runtime files under:

```text
%LOCALAPPDATA%\LensLedger\
  library-state.json
  Libraries\
  Metadata Backups\
  Database Backups\
  Review Bin\
  Face Data\
```

Each selected library receives its own database. Photos remain in their
original folders. Set `LENSLEDGER_DATA_DIR` before launch to use a different
runtime-data directory.

## Database tools

Create an empty database, inspect it, verify it, back it up, migrate it, or
rebuild full-text search:

```powershell
python database_tools.py init
python database_tools.py status
python database_tools.py verify
python database_tools.py backup
python database_tools.py migrate
python database_tools.py rebuild-search
```

Use `--db C:\path\to\library.sqlite3` before the command to target a specific
database. Backups use SQLite's online backup API and are integrity-checked.

The indexer also remains available directly:

```powershell
python photo_index.py scan "C:\Users\you\Pictures"
python photo_index.py stats
python photo_index.py query "beach AND sunset"
python photo_index.py ocr --since 2025-01-01 --workers 4
```

## Safety and privacy

- The server binds only to `127.0.0.1`.
- Scanning and database edits do not alter media files.
- Metadata publication is explicit and limited to supported JPEG files.
- Every publication creates a safety copy and verifies that decoded pixels are unchanged.
- Face matching and OCR run locally; no photo is uploaded by LensLedger.
- Suggested identities remain review-only until a person confirms them.

## Development

```powershell
python -m unittest discover -s tests -v
python -m compileall -q .
```

Release tags matching `v*` run the Windows test suite and publish a versioned
ZIP through GitHub Actions. Release notes live under `docs/releases/`.

## Current scope

LensLedger indexes common image and video formats, file and folder dates,
existing XMP keywords, curated tags, Windows OCR, and locally reviewed people.
Semantic image embeddings, richer duplicate detection, and a map experience are
planned as independent workers so models can evolve without rewriting photos or
rebuilding the basic inventory.

## License

LensLedger is available under the [MIT License](LICENSE). Bundled ExifTool files
retain their upstream license; see [third-party notices](THIRD_PARTY_NOTICES.md).
