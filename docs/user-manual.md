# LensLedger User Manual

LensLedger is a local-first photo and video indexing tool for Windows. It builds a private, searchable inventory of your photo collection without moving, renaming, uploading, or changing your files. Everything runs on your computer — nothing is sent to the cloud.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Library Management](#library-management)
3. [Scanning Your Photos](#scanning-your-photos)
4. [Searching and Browsing](#searching-and-browsing)
5. [Viewing and Editing Photo Metadata](#viewing-and-editing-photo-metadata)
6. [People and Faces](#people-and-faces)
7. [Photo Map](#photo-map)
8. [Auto-import Photos](#auto-import-photos)
9. [Publishing Metadata](#publishing-metadata)
10. [Review Bin (Trash)](#review-bin-trash)
11. [Batch Editing](#batch-editing)
12. [Database and Backups](#database-and-backups)
13. [Settings](#settings)
14. [Keyboard and Mouse Shortcuts](#keyboard-and-mouse-shortcuts)
15. [Supported File Formats](#supported-file-formats)
16. [Advanced Configuration](#advanced-configuration)

---

## Getting Started

When you first launch LensLedger, the setup page walks you through creating your first library.

### Step 1: Choose a photo folder

Select the folder that contains your photos. LensLedger shows suggested locations (Pictures, Dropbox Photos, Camera Uploads, OneDrive, removable drives) or you can click **Browse** to pick any folder.

### How libraries work

A **library = one root folder**. Everything inside that folder (all subfolders, any depth) belongs to the library. Each library has its own separate database — there is no shared master database.

- Add more subfolders, reorganize within the root — the next scan picks up the changes automatically.
- Files outside the root folder are not tracked. If you move a photo out, the next scan marks it as removed.
- You can have multiple libraries and switch between them from **Settings**.

### Step 2: Where the database is stored

By default, the database is stored in a hidden `.LensLedger` folder inside your photo library (e.g. `Photos\.LensLedger\LensLedger-Photos.sqlite3`). This means the index travels with your photos — copy the folder to another drive and everything comes with it.

You can also choose to store the database in the application data folder (`%LOCALAPPDATA%\LensLedger\Libraries\`) if you prefer to keep the photo folder completely clean.

### Step 3: Build your library

Click **Build my library** to start an initial scan. LensLedger discovers all photos and videos, records their locations, types, dates, and any embedded metadata (EXIF, IPTC, XMP). Cloud-only files (e.g. Dropbox Smart Sync placeholders) are counted without forcing a download.

The scan can be paused and resumed at any time.

### Step 4: Optional text scanning

After the initial scan completes, LensLedger offers to scan your photos for visible text (signs, screenshots, receipts) using local OCR. This runs in the background and can be started later from the **Scan your photos** page.

### Step 5: Open your library

Click **Open my library** to enter the main photo browser.

---

## Library Management

LensLedger supports multiple libraries. You can switch between them, add new ones, and relocate existing ones. All library management happens on the **Settings** page.

### Adding a library

Go to **Settings** and click **Add library**. Browse to the folder, then choose where to store the database. By default, the database goes in a hidden `.LensLedger` folder inside your photo library. The library is registered without starting a scan immediately, so you can add libraries even while another scan is running. Switch to it and scan when ready.

### Switching libraries

In **Settings**, click **Switch** next to any library in the list. Your current library remains in the list and its index is preserved.

### Relocating a library

If you moved your photos from one location to another (for example, from a USB drive to your hard drive), click **Relocate** next to the library in **Settings**. Browse to the new folder location. LensLedger updates all file paths in the database so your existing tags, people, OCR results, and scan data carry over. Your photos must already be at the new location before you relocate.

### Removing a library

Click **Remove** to take a library out of the list. This does not delete any photos or the database — it only removes the entry from the library list. You cannot remove the currently active library.

---

## Scanning Your Photos

The **Scan your photos** page is the dashboard for all scanning operations. Each scan runs in the background and can be paused and resumed.

### Run all scans

Runs the scans below back to back — photo locations, then OCR, then meaning search and face detection (if set up) — so you don't have to start each one manually.

### Photo locations (GPS)

An incremental library scan that discovers new and changed files and extracts embedded GPS coordinates. These coordinates power the **Photo map**. Safe to run any time — it picks up any new or changed files.

### Local text recognition (OCR)

Reads visible text in your photos — signs, screenshots, receipts, documents — and makes it searchable. You can configure:

- **OCR worker threads** (1–16, default 4) — more workers scan faster but use more CPU
- **OCR batch size** (10–500, default 50) — photos processed per commit
- **Only since** date filter — skip photos taken before a specific date, useful for scanning only recent additions

### Meaning search (optional)

Uses a local AI vision model (CLIP) to let you search photos by natural language descriptions like "a birthday cake" or "sunset over water." This is optional because the model software is a separate download (400 MB to 1.8 GB depending on the model chosen).

To set up meaning search, go to **Settings > Meaning search model** and click **Set up meaning search**. Three models are available:

| Model | Size | Quality |
|-------|------|---------|
| ViT-B-32 | ~400 MB | Good general quality, fast |
| ViT-B-16 | ~600 MB | Better quality, slower |
| ViT-L-14 | ~1.8 GB | High quality, significantly slower |

Changing the model re-indexes your photos on the next meaning search run.

### Face detection (optional)

Finds faces in your photos so they can be identified in the **Name faces** and **People review** pages. This is optional and a separate download (~500 MB) because the face-detection model's license does not allow LensLedger to bundle it.

To set up face detection, go to **Scan your photos** and click **Set up face detection**.

### Backups

Click **Create verified database backup** to make a verified copy of your database with an integrity check.

---

## Searching and Browsing

The home page is the main photo browser with a search toolbar and scrollable filmstrip.

### Search scopes

Use the scope selector next to the search box to choose what to search:

- **Everything** (default) — searches all of the below combined
- **Visible image tags** — matches subjects, objects, people, and OCR text
- **Day/event context** — matches folder-derived tags
- **People** — browse and filter by recognized people (shows a card grid)
- **Meaning** — semantic search with natural language queries (requires meaning search setup)

### Sorting

- **Best match** — relevance ranking (available when searching with "Everything" scope)
- **Newest first** — by capture date, newest on the left
- **Oldest first** — by capture date, oldest on the left
- **Filename A–Z** — alphabetical by filename

### Date filtering

Click the date filter button to open a calendar picker. Select a specific date to show only photos from that day. Use the **Previous day** / **Next day** buttons to navigate between days with photos.

### Filmstrip

The filmstrip at the bottom shows photo thumbnails. Scroll horizontally or drag to browse. More photos load automatically as you scroll. Click a thumbnail to view the full photo above.

---

## Viewing and Editing Photo Metadata

Click a photo in the filmstrip to see it in the main viewer area. The sidebar on the right shows editable metadata.

### Primary subject

A short phrase describing the main thing in the photo (e.g. "Golden Gate Bridge at sunset"). Stored as the IPTC/XMP Title and Headline when published.

### Photo tags

Comma-separated searchable tags for other visible things in the photo (e.g. "bridge, fog, bay, cars"). Stored as IPTC/XMP Keywords when published.

### People in this photo

Shows confirmed people and face-recognition suggestions. You can:

- Accept or reject suggested people
- Manually add a person using the person picker (with autocomplete)
- Click a person's name to see all their photos

### Event / folder tags

Reusable tags applied to every photo in the same folder (e.g. "Christmas 2025", "Beach vacation"). Useful for providing shared context.

### Capture details

Expandable section showing read-only embedded EXIF, IPTC, and XMP metadata. If the photo has GPS coordinates, links are provided to view the location on the Photo map or OpenStreetMap.

### Hidden tags

You can hide specific tags for individual photos (e.g. if an auto-generated tag is wrong). Hidden tags can be restored.

### Zooming

- Scroll the mouse wheel on the main image to zoom in/out (up to 20x)
- Triple-click the image to toggle 3x zoom
- Click and drag to pan while zoomed

---

## People and Faces

LensLedger has a two-stage workflow for identifying people in your photos.

### Stage 1: Name faces

Go to **Name faces** from the navigation menu. This page shows a grid of unidentified face crops, diversity-sampled to show variety. For each face, you can:

- **Name it** — type a name (with autocomplete) to identify the person. After naming, LensLedger shows similar unidentified faces that likely match, so you can confirm them in one click.
- **Not a person** — mark false detections (statues, posters, etc.)
- **Unknown person** — mark as a real person you don't want to name yet

Click any face crop to see the full photo for context.

### Stage 2: Review people

Go to **Review people** from the navigation menu. This page shows batches of face-match suggestions for each person. For each suggested photo, you can:

- **Confirm** — yes, this is the right person
- **Wrong** — this is not the right person
- **Correct** — this is a different person (reassign)

Use **Save & publish this group** to confirm all decisions and write people names into the JPEG metadata. **Undo last batch** rolls back decisions including any metadata changes.

You can **defer** a person's suggestions for 1–30 days if you're unsure.

### Managing people

From the People search scope on the home page:

- **Edit name** — change a person's primary name. The rename propagates to all confirmed photos' JPEG metadata.
- **Aliases** — add alternate names (nicknames, maiden names) that also match in search.
- **Merge** — combine duplicate person records. Aliases are preserved, and JPEG metadata is updated.

---

## Photo Map

The **Photo map** page shows an interactive world map with markers at every GPS-tagged photo location.

- **Scroll** to zoom in and out
- **Drag** to pan
- **Click a cluster marker** to zoom into that area
- **Click a single marker** to see details: photo count, date range, coordinates, and a representative photo

From the marker details, you can:

- Open the representative photo in the viewer
- View all photos from this location
- Open the location in OpenStreetMap

GPS coordinates are extracted during the Photo locations scan. They are read from embedded EXIF data and never written back.

---

## Auto-import Photos

The **Auto-import photos** page sets up an automatic pipeline for sorting new photos from a camera upload folder into your collection.

### How it works

1. Set a **source folder** (e.g. `C:\Users\you\Dropbox\Camera Uploads`)
2. Set a **destination folder** (e.g. `C:\Users\you\Pictures\Sorted`)
3. Configure a **date sorting template** using placeholders:
   - `{year}` — four-digit year (e.g. 2026)
   - `{month}` — two-digit month (01–12)
   - `{day}` — two-digit day (01–31)
   - `{hour}` — two-digit hour (00–23)
   - `{minute}` — two-digit minute (00–59)
4. The default template `{year}/{year}_{month}_{day}` creates folders like `2026/2026_08_28`

### Override rules

You can add rules that route specific files to different destinations based on filename matching. For example, photos with "Screenshot" in the name could go to a Screenshots folder instead of the date-sorted structure. Rules are checked in order; the first match wins.

### Controls

- **Enable/disable toggle** — when enabled, the pipeline checks at the configured interval
- **Check interval** — how often to check for new photos (5 minutes to 24 hours, default: 10 minutes)
- **Run now** — trigger an immediate pipeline run
- **Activity log** — shows every file processed, with duplicates and errors

Duplicate files are detected by content hash and skipped automatically.

---

## Publishing Metadata

Publishing writes your subjects, people, tags, and descriptions back into the photo file's embedded metadata (IPTC/XMP). Only JPEG and HEIC/HEIF files are publishable.

### How to publish

1. Open a photo and fill in the metadata you want to save
2. Click **Preview & publish** in the sidebar
3. Review the before/after comparison showing exactly what will change
4. Click **Publish** to write the metadata

### Safety features

- A **safety backup** is created before every write
- After writing, LensLedger verifies the image pixels haven't changed (hash comparison)
- Click **Restore last publish** to revert from the safety backup

### Auto-publishing

When you confirm people in the **Review people** page, their names are automatically published to the JPEG metadata.

---

## Review Bin (Trash)

The review bin is a safe staging area for photos you want to remove. Photos are moved to a separate Review Bin folder — they are not permanently deleted.

### Moving photos to the review bin

- Click the trash icon on any photo
- Use batch selection (Ctrl+click or Shift+click thumbnails) and click **Trash selected**
- An undo toast appears for 12 seconds after trashing

### Managing the review bin

Open the review bin from the hamburger menu (**Trash & restore**):

- **Restore** — move a photo back to its original location
- **Delete** — permanently remove a single photo
- **Empty trash** — permanently delete all items in the review bin

---

## Batch Editing

You can select multiple photos and apply changes to all of them at once.

### Selecting photos

- **Ctrl+click** (or Cmd+click) a thumbnail to toggle its selection
- **Shift+click** a thumbnail to select a range from the last clicked thumbnail

A batch bar appears at the bottom showing the selection count.

### Batch actions

- **Add tags** — add the same tags to all selected photos
- **Trash** — move all selected photos to the review bin
- **Clear selection** — deselect all

---

## Database and Backups

### Database location

By default, databases are stored in `%LOCALAPPDATA%\LensLedger\Libraries\`. When adding a library, you can choose to store the database inside the photo library folder instead.

### Verified backups

From **Scan your photos**, click **Create verified database backup**. This creates a SQLite online backup with an integrity check, stored in `%LOCALAPPDATA%\LensLedger\Database Backups\`.

### Export and import

From **Settings > Database**:

- **Export database** — creates a portable ZIP containing the database, face data, and a manifest. Useful for backups or moving to a new machine.
- **Import database** — restores from a previous export ZIP. File paths are automatically remapped to the current library location.

Your photos are not included in exports — only the LensLedger index is transferred.

---

## Settings

Access settings from the navigation menu or hamburger menu.

### Photo libraries

Manage your library list: add, switch, relocate, or remove libraries. See [Library Management](#library-management).

### Scan preferences

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| OCR worker threads | 1–16 | 4 | More workers scan faster but use more CPU |
| OCR batch size | 10–500 | 50 | Photos processed per OCR commit |
| Meaning search batch | 1–128 | 16 | Images per CLIP encoding batch |

### Meaning search model

Choose the CLIP model for meaning search. See [Meaning search](#meaning-search-optional).

### Display preferences

| Setting | Options | Default | Description |
|---------|---------|---------|-------------|
| Photos per page | 50–1000 | 250 | Number of photos loaded per filmstrip page |
| Default sort order | Newest / Oldest / Name | Newest first | Initial sort when opening the library |
| Filmstrip thumbnail size | Small / Medium / Large | Medium | Size of thumbnails in the filmstrip |

### Folder watching

Automatically detect new and changed photos without manually running a scan.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Enable watching | On/Off | Off | Toggle automatic folder watching |
| Check interval | 5–1440 minutes | 30 | How often to check for new files |

---

## Keyboard and Mouse Shortcuts

| Action | Shortcut |
|--------|----------|
| Next / previous photo | Left / Right arrow keys |
| Open photo in file explorer | Double-click the main image |
| Toggle 3x zoom | Triple-click the main image |
| Zoom in / out | Scroll wheel on the main image |
| Pan while zoomed | Click and drag |
| Select photo for batch editing | Ctrl+click (or Cmd+click) a thumbnail |
| Select a range of photos | Shift+click a thumbnail |
| Close any panel or dialog | Escape |

---

## Supported File Formats

| Format | Metadata | Faces | Viewable | Publishable |
|--------|----------|-------|----------|-------------|
| JPEG (.jpg, .jpeg) | Yes | Yes | Yes | Yes |
| PNG (.png) | Yes | Yes | Yes | No |
| WebP (.webp) | Yes | Yes | Yes | No |
| TIFF (.tif, .tiff) | Yes | Yes | Yes | No |
| GIF (.gif) | No | Yes | Yes | No |
| BMP (.bmp) | No | Yes | Yes | No |
| HEIC / HEIF | No | Yes | Yes * | Yes |
| RAW (.dng, .cr2, .cr3, .nef, .arw, .orf, .rw2, .raf) | No | No | No | No |
| Video (.mp4, .mov, .avi, .wmv, .mpg, .mpeg, .mkv) | No | No | No | No |

\* HEIC/HEIF files are converted to JPEG on the fly for viewing. RAW and video files are indexed and searchable but not viewable or face-scanned.

**Publishable** means LensLedger can write people names, tags, and subjects back into the file's embedded metadata.

---

## Advanced Configuration

### Data directory

All application data is stored at `%LOCALAPPDATA%\LensLedger` by default. This includes:

| Folder | Contents |
|--------|----------|
| `Libraries\` | Per-library SQLite databases |
| `Metadata Backups\` | Pre-publish safety backups |
| `Database Backups\` | Verified database backups |
| `Review Bin\` | Trashed photos |
| `Face Data\` | Face detection data |
| `Exports\` | Database export ZIPs |
| `library-state.json` | Library list and current library |
| `settings.json` | Application settings |

Override the data directory by setting the `LENSLEDGER_DATA_DIR` environment variable.

### Command-line options

| Option | Description |
|--------|-------------|
| `--version` | Print version and exit |
| `--port N` | Set the HTTP port (default: 5309) |
| `--root PATH` | Override the library root path |
| `--db PATH` | Override the database path |
| `--no-open` | Don't auto-open the browser on startup |

### Updates

LensLedger checks for updates from GitHub automatically. When a new version is available, an update badge appears in the navigation menu. If you're running from a managed installation, updates can be downloaded and installed automatically. If you're running from source code, a banner appears when the on-disk code is newer than the running server, with a **Restart** button to load the new version.
