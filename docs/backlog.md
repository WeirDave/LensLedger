# LensLedger — Backlog

Items for future development, roughly prioritized.

## Global settings page

**Problem:** There is no central place for the user to configure LensLedger's behavior. Library switching, scan preferences, and any future configuration (auto-sort rules, default views) all need a home.

**What it enables:**
- Change the active library root folder
- Switch between multiple collections
- Configure auto-ingest rules (see below)
- Set scan preferences (OCR workers, batch sizes)
- Theme and display preferences

**Existing infrastructure:** `library-state.json` already stores `current_root` and `libraries`. `app_paths.py` defines a clean data directory. A `/settings` route with a simple JSON-backed config file would extend naturally.

**Impact:** Foundation for several other backlog items. Low complexity, high value.

---

## Folder rename / move resilience

**Problem:** When a library root folder or subfolder is renamed at the OS level, LensLedger loses all enrichment data (tags, people, face embeddings, OCR text, review history) for affected photos. The database filename is derived from a hash of the root path, so a renamed root creates an entirely new database. Subfolder renames cause files to appear as new entries while old-path records are cascade-deleted.

**Root cause:** Files are identified solely by `relative_path`. There is no content hashing, inode tracking, or any mechanism to recognize that a file at a new path is the same file from an old path.

**Possible approaches:**

1. **Content-hash matching** — compute a perceptual or file hash during scan; on next scan, match orphaned hashes to new paths before deleting old records. Trades disk/CPU at scan time for rename resilience.

2. **Path migration tool** — a UI or CLI command that takes an old root path and a new root path, updates `path` and `relative_path` columns in the database, and renames the `.sqlite3` file to match the new hash. Manual but zero-cost at scan time.

3. **Database filename stability** — decouple the database filename from the folder path (e.g., store a persistent library ID in the database itself and use that for the filename). Solves root-rename but not subfolder-rename.

4. **Hybrid** — stable DB filename (#3) for root renames + content-hash reconciliation (#1) for subfolder/file moves.

**Affected code:**
- `src/library_config.py` — `library_db_path()` (path-to-filename hashing)
- `src/photo_index.py` — `scan_library()` (relative_path-based change detection, orphan deletion)
- `src/photo_search.py` — `open_library()` (library open/switch flow)

**Impact:** High for users who reorganize their photo folders. All enrichment work (potentially hours of face training, tagging, OCR) is silently lost.

---

## Multiple collections / root folder switching

**Problem:** Users with more than one photo collection (e.g., personal + work, or multiple drives) need to switch between them. The infrastructure exists but has no UI.

**Existing infrastructure:**
- `library_config.py` already tracks a `libraries` list and `current_root`
- `suggested_library_roots()` discovers common locations (Pictures, Dropbox, OneDrive, removable drives)
- `load_library_config()` and `save_library_state()` handle persistence
- The library picker dialog (`choose_library_folder()`) already works

**What's missing:** A UI on the settings page (or scan page) to view known libraries, add new ones, and switch between them without restarting the server.

**Impact:** Medium. Users with multiple collections currently have to restart the server to switch.

---

## Database import / export

**Problem:** No way to back up or transfer the enrichment database (tags, people, face embeddings, OCR text, review history) independently of the photos. If the database is lost, all enrichment work must be redone from scratch.

**Existing infrastructure:**
- `app_paths.py` already defines `database_backup_root()` and the backup button on the scan page creates verified copies
- The database is a single SQLite file plus face data in a separate directory

**Export:** Copy the `.sqlite3` file + face data directory into a portable archive (ZIP). Include a manifest with the library root path, schema version, and photo count so import can verify compatibility.

**Import:** Place the database and face data back, update `library-state.json` to point at the new paths. The tricky part is path remapping — if the library root changed between export and import, all `path` and `relative_path` columns need updating.

**Impact:** High for disaster recovery and machine migration.

---

## Meaning search (optional model)

**Status:** Already implemented as of v0.42.0 and functional. The model is optional (large download). As of v0.47.0, failed files are properly tracked and reported.

**Future improvements:**
- Support additional/better models beyond ViT-B-32/openai
- Allow the user to choose a model in settings (trade quality vs. speed/disk)
- Re-index with a new model without losing the old index

---

## Growing collections — new folder handling

**Problem:** As a photo collection grows and new subfolders are added, the user must manually re-run the scan to pick them up. There is no live watching or automatic detection.

**Possible approaches:**

1. **Manual re-scan (current)** — user clicks "Scan for photo locations" or "Run all scans" to pick up new folders. Simple, predictable, zero background resource usage.

2. **Filesystem watching** — use `watchdog` or OS-level file system notifications to detect new files/folders and trigger an incremental scan automatically. More responsive but adds background resource usage and complexity (especially on Windows with cloud-synced folders).

3. **Scheduled scanning** — run a lightweight scan on a timer (e.g., every 30 minutes while the app is running). Middle ground between manual and live.

**Considerations:**
- Cloud-synced folders (OneDrive, Dropbox) generate filesystem events for placeholder files that aren't actually downloaded yet — the watcher needs the same `is_cloud_placeholder` logic the scan already uses
- Large libraries (50k+ files) make frequent full scans expensive; an incremental approach watching only new/changed files is essential

**Impact:** Medium. Currently a minor inconvenience — the scan is fast for unchanged files.

---

## Camera upload auto-ingest pipeline

**Problem:** Phone camera uploads (e.g., Dropbox Camera Uploads, OneDrive Camera Roll) dump unsorted photos into a flat folder. The user wants these automatically scanned, tagged, and moved into the organized collection folder.

**Proposed flow:**

1. **Watch folder** — monitor a configurable source folder (e.g., `Dropbox/Camera Uploads`) for new files
2. **Scan** — extract EXIF metadata, run face detection, OCR
3. **Tag** — auto-apply labels from recognized faces, detected text, EXIF data (location, date, camera)
4. **Sort** — move into the collection folder using configurable rules

**Sorting rules engine:**
- Template-based destination paths: `{year}/{year}_{month}_{day} - {event}` or `{year}/{month}/{location}`
- Override rules for recognized people: "Photos with [Person] go to `Family/[Person]`"
- Override rules for locations: "Photos near [Place] go to `Travel/[Place]`"
- Fallback rule for unrecognized photos: `{year}/{year}_{month}_{day} - Unsorted`
- Configurable via the settings page

**Prerequisites:**
- Settings page (to configure watch folder, destination, rules)
- Folder rename resilience (so moved files don't lose their enrichment)
- Growing collection handling (to detect the newly-sorted files)

**Considerations:**
- Must be safe — never delete originals, only move (with undo capability via review bin)
- Must handle duplicates (same photo uploaded twice)
- Must handle partial uploads (file still being synced)
- Should log every action for auditability
- Should allow manual review before moving (optional "review queue" mode)

**Impact:** High. This is the most transformative feature — turns LensLedger from a passive indexer into an active photo management tool.
