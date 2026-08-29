# LensLedger changelog

LensLedger uses semantic versioning: `MAJOR.MINOR.PATCH`.

- **MAJOR** — incompatible database, metadata, or workflow changes
- **MINOR** — new backward-compatible features
- **PATCH** — corrections and small backward-compatible improvements

## 0.76.1 — 2026-08-28

- Fixed accuracy tracking for People Review confirmations — naming a face via the single-face endpoint now correctly records a review action, so accuracy percentages display properly instead of showing -1%.

## 0.76.0 — 2026-08-28

- Renamed "Name faces" to "People review" throughout the app — nav, page title, header, manual, log messages.
- Removed "Review people" from the navigation menu (page still accessible by URL).
- Changed "Confirm all remaining" threshold from 25 confirmed + 75% cohesion to 10+ confirmed with 100% accuracy — based on analysis of real review data showing the system is correct from the very first suggestion for 55 out of 58 people.
- Added progress feedback in match group headers: "5/10 confirmed — keep going" → "ready to confirm all" so users know when to stop reviewing and let the system take over.
- Updated user manual: consolidated two-stage workflow into a single "People review" section with "When to stop" guidance.

## 0.75.0 — 2026-08-28

- Added recognition accuracy percentage to People gallery cards on the search page, calculated from review history (confirmed vs. rejected/corrected suggestions).
- Added "Recognition accuracy" and "Confirm all remaining" sections to the user manual explaining how face recognition works, what accuracy means, and the matching thresholds.
- Removed cohesion-based "confidence" display from Name Faces match group headers — the internal threshold is still used for the "Confirm all remaining" button, but the user-facing label was misleading.
- Fixed cycling bug where unchecked faces in "Also looks like" groups reappeared indefinitely after confirming a batch.

## 0.74.0 — 2026-08-28

- Added recognition confidence percentage to match groups (e.g. "87% confidence").
- Added "Confirm all remaining" button that appears when confidence is 75%+ and at least 25 faces are confirmed — one click confirms every remaining match server-side.
- Match group headers now show confidence alongside auto-confirmed counts.

## 0.73.0 — 2026-08-28

- Added dedicated "Publish photos" page for controlling when JPEG metadata is written.
- Moved metadata publishing out of the Name faces flow — confirming faces now only updates the database.
- Added `published_at` tracking to `asset_people` so the publish page knows what needs writing.
- Added nav link for Publish photos between Review people and Photo map.

## 0.72.1 — 2026-08-28

- Metadata publishing now logs progress every 25 photos (e.g. "Published 'Name' — 25/199") instead of staying silent until all photos are done.
- Publishing no longer holds the database connection for the entire run — each photo opens and closes its own connection, so naming a new person or other operations aren't blocked while a large batch publishes.

## 0.72.0 — 2026-08-28

- High-confidence matches (similarity ≥0.75) are now auto-confirmed without review; only borderline matches (0.65–0.75) are shown for manual review. The match group header shows how many were auto-confirmed alongside the review count.
- Metadata publishing (ExifTool JPEG writes) is now deferred until the matching chain is exhausted, so the interactive confirm-and-find-more loop runs at database speed instead of waiting for file I/O on every batch.

## 0.71.0 — 2026-08-28

- Match groups now show up to 200 faces per round instead of 50, and confirm the entire batch in a single server request instead of one HTTP call per face. Combined with continuous replenishing, this makes it practical to clear thousands of photos of one person in minutes.

## 0.70.1 — 2026-08-28

- Name Faces log messages now show the full relative path instead of just the filename, making it easier to locate photos in the library.

## 0.70.0 — 2026-08-28

- "Also looks like" match groups on Name Faces now continuously replenish: after confirming a batch, the system searches for more matches using a centroid built from all confirmed faces for that person, and keeps going until no more are found. This lets you clear out hundreds of photos of one person in a single flow without leaving the page.

## 0.69.0 — 2026-08-28

- Added "Trash photo" button to the enlarged photo lightbox on Name Faces, so photos can be trashed directly while reviewing them full-size.

## 0.68.1 — 2026-08-28

- Name Faces now randomly samples from all unidentified faces instead of always showing the most recently scanned ones, so each visit surfaces different people from across the entire library.
- Increased the diversity sampling pool from 500 to 2,000 faces for better variety in large libraries (small libraries still load everything).

## 0.68.0 — 2026-08-28

- Added scroll-wheel zoom and drag-to-pan to the enlarged photo lightbox on both the Name Faces and People Review pages, making it possible to zoom in on background faces before deciding who they are.
- Zoom controls (level indicator and reset button) appear automatically when zoomed past 1x.
- Triple-click toggles between 1x and 3x zoom, matching the main viewer behavior.

## 0.67.0 — 2026-08-28

- Lowered face suggestion threshold from 0.76 to 0.70 and margin from 0.04 to 0.02, generating significantly more People Review suggestions from named faces.
- "Also looks like" matches when naming a face now searches 50,000 faces (was 4,000) with a lower similarity bar (0.65 vs 0.76), surfacing up to 50 matches instead of 16.
- Name Faces page now excludes faces that already have pending suggestions in People Review, avoiding duplicate work.
- Unidentified face counts now exclude faces with pending suggestions, giving a more accurate picture of remaining work.

## 0.66.1 — 2026-08-28

- "Checking for more matches" screen now shows a spinner animation and explains what's happening, instead of looking frozen.
- After learning completes, the header briefly shows what was found (e.g. "Found 42 new suggestions, 3 auto-confirmed") before loading the next batch.
- The face learning process now logs to the terminal: profile count, suggestion count, and auto-confirmed count.

## 0.66.0 — 2026-08-28

- Review People header now shows how many unidentified faces remain alongside the existing suggestion counts.
- When People Review finishes but unidentified faces remain, a large "Name faces (N)" button routes to the Name Faces page instead of sending users back to the home page.

## 0.65.4 — 2026-08-28

- Terminal log labels now match their source page: `[People review]` for Review People decisions, `[Name faces]` for Name Faces naming.

## 0.65.3 — 2026-08-28

- Name Faces page now logs to the terminal when a face is named, including "Also looks like" match confirmations.

## 0.65.2 — 2026-08-28

- Review People terminal logging now prints each entry in real time as each photo's metadata is written, instead of batching all log entries at the end.

## 0.65.1 — 2026-08-28

- Review People thumbnails now fall back to the full photo when a face crop is unavailable (faces imported without bounding box data), instead of showing a dark empty box.

## 0.65.0 — 2026-08-28

- Redesigned Review People page with compact face crop thumbnails instead of large full-photo cards, fitting ~60 faces on screen at once for much faster bulk review.
- Click any thumbnail to toggle confirmed/rejected. Enlarge button opens a lightbox with the full photo, filename, date, and correction options.
- Raised batch limit from 8 to 200 suggestions per person.
- Added terminal logging for all people review decisions, labeled `[Name faces]`, showing the action and filename for each confirm, reject, correction, or disposition.

## 0.64.3 — 2026-08-28

- Updated the Name faces intro text, user manual, and Quick guide to explain the recommended two-stage workflow: name a handful of people (5–10), then go to Review people where LensLedger suggests matches across the whole library.
- Face detection completion message now directs users to "Name a few people" then "Review people" instead of the unclear "Find more matches" phrasing.

## 0.64.2 — 2026-08-28

- "Also looks like" match thumbnails now have an Enlarge button below each photo, so you can view the full image in the lightbox before deciding whether to confirm or reject a blurry or ambiguous match.

## 0.64.1 — 2026-08-28

- Name faces page now shows more diverse faces instead of repeating the same person. Raised the diversity sampling threshold from 0.72 to 0.78 to close a gap where similar faces would slip past the diversity filter but not be similar enough for the "Also looks like" auto-grouping.

## 0.64.0 — 2026-08-28

- Auto-import now triggers a library scan 5 seconds after importing new photos, so they get indexed immediately instead of waiting for the next folder watcher cycle.
- "Run all scans" button now resets to its original text after the scan completes (was stuck on "Starting scan...").
- "Run all scans" now logs start and completion to the console with "(manual)" label to distinguish from automatic background scans.
- Overview stats on the Scan page now have tooltips explaining what each number means.
- Face detection model loading no longer dumps raw debug output to the console (Applied providers, model ignore, find model, set det-size lines).
- Renamed "Ingested" to "Imported" in auto-import stats, status page, and console output.

## 0.63.6 — 2026-08-28

- Auto-import now matches existing folders by date prefix — if `2026_08_22 - Beach Day` exists, new photos for that date go there instead of creating a bare `2026_08_22` folder.
- Auto-import now skips files that already exist at the destination with the same name and size instead of creating `_1` copies.

## 0.63.5 — 2026-08-28

- Auto-import no longer scans the entire destination library for duplicates — it just imports files and handles filename collisions with a numeric suffix. Startup is now instant instead of spending minutes hashing 50K+ photos.
- Auto-import errors during processing are now logged to the console instead of being silently swallowed.
- Auto-import now logs cloud-only placeholder files that were skipped (Dropbox smart sync files not yet downloaded).
- Auto-import now logs files still uploading and reports candidate count before processing.
- Removed duplicate-tracking stats from the auto-import status page.

## 0.63.4 — 2026-08-28 (superseded)

- Auto-import now caches the destination file index across cycles instead of rehashing all library photos every check — first run logs progress, subsequent checks are near-instant.
- Auto-import now logs cloud-only placeholder files that were skipped (Dropbox smart sync files not yet downloaded).
- Auto-import now logs files skipped because they are still uploading.
- Auto-import errors during processing are now logged to the console instead of being silently swallowed.
- Auto-import now reports how many candidate photos it found before processing.

## 0.63.3 — 2026-08-28

- Auto-import now fires first on startup (5 seconds) before folder watcher (30 seconds) so new photos are imported before the scan runs.
- Suppressed raw diagnostic output from scans and OCR in the console window — only the clean timestamped `console_log` lines appear now.

## 0.63.2 — 2026-08-28

- Meaning search errors now show the actual reason an image failed (e.g. "cannot identify image file", "truncated JPEG") instead of the generic "Image could not be encoded by the meaning-search model."
- Unindexed images with no stored error now show "Not indexed — no error recorded. Try rebuilding the meaning index." instead of the same generic message.

## 0.63.1 — 2026-08-28

- Settings TOC links now scroll sections to the correct position below the sticky header.

## 0.63.0 — 2026-08-28

- Settings page now has a sticky left-hand table of contents with scroll-spy highlighting.
- Added auto-import toggle and check interval to the Settings page, with a link to the full auto-import configuration page.
- Folder watching now defaults to enabled with a 5-minute interval (was disabled, 30 minutes).
- Both folder watching and auto-import now fire their first check immediately on startup instead of waiting for the full interval.
- Toast notifications are now readable in light mode (was white text on pale beige).
- Nav menu now shows the full library path instead of just the folder name.

## 0.62.2 — 2026-08-28

- Auto-import now logs each file individually as it's imported, skipped as duplicate, or errored — plus "no new photos found" when a check comes up empty.

## 0.62.1 — 2026-08-28

- Console timestamps now use 12-hour format with date (e.g. `[2026-08-28 01:14:08 PM]`) instead of 24-hour time only.

## 0.62.0 — 2026-08-28

- Console window now shows timestamped activity: scan progress, OCR, face detection, meaning search, folder watcher checks, and auto-import results.
- Startup banner now shows current library, database path, and active background services.

## 0.61.0 — 2026-08-28

- Renamed "Auto-ingest" to "Auto-import photos" across the entire application (nav menu, page title, manual, docs).
- Added configurable check interval (5 minutes to 24 hours) to the auto-import page — previously hardcoded at 10 minutes.
- Old `/auto-ingest` URL redirects to `/auto-import` so bookmarks still work.

## 0.60.0 — 2026-08-28

- Settings page now shows the actual database path for the current library, not just the general application data folder.

## 0.59.2 — 2026-08-28

- User Manual now opens in a new browser tab so it can be viewed side-by-side with LensLedger.

## 0.59.1 — 2026-08-28

- Fixed CI test failure caused by Windows short path name mismatch in library path assertions.

## 0.59.0 — 2026-08-28

- Changed default database location: databases now live inside the library in a hidden `.LensLedger` folder (e.g. `Photos\.LensLedger\LensLedger-Photos.sqlite3`). The index travels with the photos — copy the folder to another drive and everything comes with it.
- Database filename convention is `LensLedger-{folder name}.sqlite3` so displaced files are self-identifying.
- Added "How libraries work" explanation to the User Manual, Quick guide, and onboarding page: one library = one root folder, each library has its own separate database, no shared master database.
- Existing libraries with databases in AppData continue to work unchanged.

## 0.58.0 — 2026-08-28

- Added in-app User Manual page at `/manual`, accessible from the navigation menu under Help & Support.
- Covers all 16 sections: getting started, library management, scanning, searching, viewing metadata, people and faces, photo map, auto-ingest, publishing, review bin, batch editing, database and backups, settings, keyboard shortcuts, supported formats, and advanced configuration.

## 0.57.2 — 2026-08-28

- Added comprehensive user manual at `docs/user-manual.md` covering all features: library management, scanning, search, metadata editing, people and faces, photo map, auto-ingest, publishing, review bin, batch editing, database operations, settings, keyboard shortcuts, supported formats, and advanced configuration.

## 0.57.1 — 2026-08-28

- Added library management section to the Quick guide covering adding libraries, switching, relocating, and database location choices.

## 0.57.0 — 2026-08-28

- Fixed folder browse dialog appearing behind the browser window by making it open as a topmost window.
- Added ability to add a new library without immediately starting a scan, so libraries can be registered even while another scan is running.
- Added database location choice: users can now store the database inside the photo library folder (useful for external drives) instead of the default application data folder.
- Added "Relocate" feature for libraries, allowing users to move a library from one location to another (e.g. USB drive to hard drive) while keeping the existing database and index.

## 0.56.0 — 2026-08-18

- Fixed auto-ingest page layout so Browse buttons align inline with their Source/Destination folder input fields instead of wrapping below them.
- Added a "Run now" button to the auto-ingest page that triggers an immediate pipeline run without needing to enable the periodic auto-ingest timer.

## 0.55.1 — 2026-08-18

- Added the "restart now" version banner to all pages — previously only the viewer detected when a newer version was on disk and offered a one-click restart; now every page (Settings, Scan, Map, Auto-ingest, Name faces, Review people) shows the same banner and restart button.

## 0.55.0 — 2026-08-18

- Added a consistent About panel across all pages — version, tagline, current library path, and developer project links are now shown in a unified overlay accessible from the navigation menu on every page.
- Added keyboard and mouse shortcuts table to the viewer's Quick Guide panel, covering arrow-key navigation, double-click to reveal, triple-click to zoom, click-drag to pan, Ctrl/Shift-click for batch and range selection, and Escape to close.

## 0.54.0 — 2026-08-17

- Added active-page highlighting in the navigation menu — the current page is visually marked with an accent border and bold text.
- Added current library name and full path to the bottom of the navigation menu on every page.
- Settings library list now shows the full folder path instead of just the leaf folder name.
- Added confirmation dialogs before switching or removing a library in Settings.
- Added explanatory text to the database export/import section in Settings.
- Added tooltip to the OCR "Only since" date field explaining its purpose.
- Fixed map empty-state link to point to the scan page instead of the home page.

## 0.53.0 — 2026-08-17

- Moved camera upload auto-ingest from a settings section to its own dedicated page (`/auto-ingest`) with status dashboard, activity log, and clear documentation.
- Added configurable date sorting template with live preview and placeholder reference (`{year}`, `{month}`, `{day}`, `{hour}`, `{minute}`).
- Override rules are now clearly documented as optional filename-based overrides, with help text explaining how they interact with the date template.
- Added Auto-ingest link to the navigation menu.
- Added dedicated `/api/ingest/save` endpoint for saving ingest configuration independently from general settings.

## 0.52.0 — 2026-08-17

- Fixed settings page CSS: nav menu panel was unstyled, rendering the logo at full native size and links as raw text.
- Moved meaning search setup from the scan page to the settings page: the "Set up meaning search" button on the scan page now links to Settings, where users can install the model software with progress feedback and choose their preferred CLIP model.
- Added install status polling, progress bar, and install button to the Settings page "Meaning search model" section.

## 0.51.1 — 2026-08-17

- Updated backlog document: all 7 planned features are now implemented and shipped (v0.48.0–v0.51.0).

## 0.51.0 — 2026-08-17

- Added camera upload auto-ingest pipeline: automatically scan, tag, and sort new photos from a camera upload folder (e.g. Dropbox Camera Uploads) into an organized collection.
- Sorting uses configurable rules with template-based destination paths (`{year}/{month}/{day}` etc.). Unmatched photos use a default date-based pattern.
- Duplicate detection using content hashing prevents the same photo from being ingested twice.
- Partial upload protection: files are checked for stability (size unchanged after delay) before processing.
- All actions are logged to `ingest-log.json` for auditability.
- Never deletes originals — files are moved from source to destination.
- Added `/api/ingest/status` and `/api/ingest/log` endpoints for monitoring.
- Pipeline state is saved in settings and restored on server restart.

## 0.50.0 — 2026-08-17

- Added automatic folder watching: LensLedger can now periodically check for new, changed, or moved files without a manual scan. Configurable via the Settings page with an adjustable interval (minimum 5 minutes, default 30).
- The watcher uses scheduled incremental scans rather than filesystem notifications, avoiding false events from cloud-synced folders (OneDrive, Dropbox placeholders).
- Watcher state is saved in settings and restored on server restart.
- Added `/api/watcher/status` endpoint for monitoring watcher state.

## 0.49.0 — 2026-08-17

- Added folder rename and move resilience using content-hash reconciliation. When files are moved or renamed within a library, LensLedger now matches them by content fingerprint instead of deleting the old record and creating a new one, preserving all enrichment data (tags, people, face embeddings, OCR text, review history).
- Added content hashing during library scans: a fast fingerprint (SHA-256 of the first 8 KB + file size) is computed for each file and stored in the database.
- Added stable database filenames: library root paths are now mapped to database files in `library-state.json`, so renaming a library root folder no longer creates a new empty database.
- Added a `library_metadata` table to the database schema for persistent library identification.
- Bumped database schema to version 12 with the new `content_hash` column and `library_metadata` table.

## 0.48.0 — 2026-08-17

- Added a global Settings page accessible from the navigation menu, providing a central place to configure all LensLedger behavior.
- Added photo library management UI: view known libraries, switch between collections without restarting, add new libraries via folder browser, and remove libraries from the list.
- Added configurable scan preferences: OCR worker threads, OCR batch size, and meaning search batch size.
- Added meaning search model selection: choose between ViT-B-32, ViT-B-16, and ViT-L-14 CLIP models with different quality/speed tradeoffs. Changing the model re-indexes on the next meaning search run.
- Added display preferences: configurable photos per page, default sort order, and filmstrip thumbnail size.
- Added folder watching configuration: toggle automatic file detection and set the check interval.
- Added camera upload auto-ingest configuration: enable/disable, set source and destination folders, and define sorting rules.
- Added database export: creates a portable ZIP archive containing the SQLite database, face data, and a manifest with library metadata.
- Added database import: restore from a LensLedger export ZIP, with automatic path remapping if the library root changed and a pre-import backup of the existing database.
- Extended semantic_index.py to support multiple CLIP models (ViT-B-32, ViT-B-16, ViT-L-14) instead of only ViT-B-32.

## 0.47.1 — 2026-08-17

- Added comprehensive backlog document covering planned features: global settings page, folder rename resilience, multiple collections, database import/export, meaning search improvements, growing collection handling, and camera upload auto-ingest pipeline.

## 0.47.0 — 2026-08-17

- Made scan error counts clickable across all scan types — clicking the Errors metric now opens a modal showing which files failed and why.
- Added error visibility to the photo locations scan: errors are now stored per-file in the database, retrievable via a new `/api/library/errors` endpoint, and clickable in the UI.
- Added error visibility to the meaning search scan: failed files are now tracked with a `semantic_error` column, retrievable via a new `/api/semantic/errors` endpoint, and clickable in the UI.
- Fixed the "66 remaining" mystery in meaning search: files that the model cannot encode are now correctly counted as errors rather than remaining, so the Remaining count reaches 0 when the scan is truly done.
- Improved completion messages across all scan types to mention error counts and direct the user to click the Errors metric for details.
- Improved the "Run all scans" completion message to report total errors across all sub-scans.

## 0.46.0 — 2026-08-17

- Unified the navigation menu across all five pages — every page now shows the identical hamburger menu with the same items in the same order.
- Menu order now reflects the user workflow: Home, Scan photos, Name faces, Review people, Photo map.
- Added the hamburger menu to the Scan photos and Photo map pages, replacing the old quick-nav link bar.
- Added Quick guide, Check for updates, and Copy diagnostics to sub-page menus (redirects to the home page for panels that live there).
- Slightly enlarged the menu logo from 32px to 40px.

## 0.45.7 — 2026-08-17

- Renamed "Information already in this photo" to "Capture details" in the sidebar and quick guide.
- Thickened the filmstrip selection border from 4px to 5px for better visibility.

## 0.45.6 — 2026-08-17

- Left-aligned header and content on People Review and Name Faces pages to match the main viewer layout — menu, logo, version, and theme toggle are now in the same position on every page.
- Added "Home" link to the main viewer menu for consistency with sub-pages.
- People Review photo grid now uses auto-fill columns, fitting more review cards on wider screens instead of a fixed 4-column layout.

## 0.45.5 — 2026-08-17

- Replaced small text chevrons (‹ ›) on photo navigation buttons with properly sized SVG arrows, centered inside larger 48px circular buttons.

## 0.45.4 — 2026-08-17

- Renamed "Open photo library" to "Open photo library on disk" for clarity.
- Moved "Check for updates" from the Tools section to Help & Support.
- Fixed small emoji icons for trash (🗑) and update (⬆) buttons by adding variation selectors for consistent sizing across platforms.

## 0.45.3 — 2026-08-17

- Removed the "Find more matches" button from both Name Faces and People Review pages — learning now happens automatically when the review queue empties, so the manual button was redundant and confusing.
- Updated the Name Faces intro text to explain the automatic workflow instead of referencing the removed button.

## 0.45.2 — 2026-08-17

- Renamed "Photo library" to "Home" in the sub-page menu navigation, matching WaxFrame's convention and reflecting that the photo library page is the app's main landing page.

## 0.45.1 — 2026-08-17

- Trash button on review cards now uses a red danger background with a clean SVG trash can icon, making it visually distinct as a destructive action in both light and dark modes.
- Brightened the "Face being checked" label in light mode — uses the same golden yellow as dark mode instead of a muted olive tone.

## 0.45.0 — 2026-08-17

- Name Faces now shows a diverse variety of people instead of repeating the same person — uses greedy diversity sampling to pick one representative face per visual cluster.
- Auto-learns after the review queue empties on both Name Faces and People Review pages, so the system finds more matches without needing to click "Find more matches" manually.
- Name Faces transitions to a "Ready for People Review" prompt when learning generates new suggestions, guiding the user to the next step in the workflow.
- People Review automatically re-learns and reloads when the last suggestion is reviewed, continuing the review cycle until no more matches are found.

## 0.44.1 — 2026-08-17

- Fixed enlarge and trash button text color in light mode — now uses theme text color so buttons are readable against both light and dark backgrounds.
- Replaced the 🗑 emoji trash icon with "✕ Trash" text label to match the "⛶ Enlarge" button style.

## 0.44.0 — 2026-08-17

- Added consistent single-click enlarge button across all review pages — faces review now has the same ⛶ Enlarge button that people review already had.
- Double-click on any photo now reveals the file in Explorer (Windows), Finder (macOS), or the file manager (Linux) instead of enlarging or zooming.
- Triple-click on the main viewer photo toggles the 3× zoom that double-click previously controlled.
- Added trash button (🗑) to both people review and faces review cards, with confirmation prompt and undo toast.

## 0.43.23 — 2026-08-17

- Fixed About button on people review and name faces pages showing "undefined" version and tagline by adding missing bootstrap data.
- Converted menu sections to collapsible `<details>` groups matching the WaxFrame Professional pattern — Navigation opens by default, other sections are collapsed.
- Widened menu panel from 300px to 320px to match WaxFrame.

## 0.43.22 — 2026-08-17

- Fixed review card text layout: filenames, folder descriptions, and confidence percentages now all display fully instead of being truncated or squeezed out on narrow cards.

## 0.43.21 — 2026-08-17

- Fixed "This photo contains ..." button text being cut off on people review cards — long person names now wrap to a second line instead of being truncated.

## 0.43.20 — 2026-08-17

- Fixed "invalid request token" error after server restart — pages now auto-reload to get a fresh session token instead of showing a cryptic error message.

## 0.43.19 — 2026-08-17

- Redesigned main menu as a full-height slide-out panel with branded header, section labels, dividers, and badge counts — consistent with the navigation pattern used in WaxFrame Professional.
- Menu now includes a dimmed backdrop overlay, close button, and smooth slide animation.
- Applied the new menu layout to all three pages that use it: photo library, people review, and name faces.

## 0.43.18 — 2026-08-17

- Fixed people review "Save & publish" failing when the batch contained non-publishable file types (PNG, etc.) — review decisions are now recorded for all photos, and metadata publishing is skipped gracefully for unsupported formats.
- Fixed "This photo contains ..." button label being cut off on narrow review cards — long names now truncate with an ellipsis.

## 0.43.17 — 2026-08-17

- Removed the "Report on GitHub" link from the scan error modal — known errors are explained in-place and don't need a bug report.
- Added a "Help & support" link to the main menu on all pages (photo library, people review, name faces) that opens the GitHub issues page for general support and bug reporting.

## 0.43.16 — 2026-08-17

- Enhanced the scan error modal with human-readable explanations for common errors, a "Copy all to clipboard" button, and a "Report on GitHub" link that pre-fills an issue with version and error details.
- Added the app version to the scan page bootstrap data so error reports include the version number.

## 0.43.15 — 2026-08-17

- Added a centered loading indicator with spinner on the Name faces page so users see "Loading faces..." while the initial batch loads, instead of a seemingly empty page.

## 0.43.14 — 2026-08-17

- Fixed HEIC/HEIF photos not displaying in the lightbox preview on Name faces, Review people, Photo map, and asset detail pages. The server now converts HEIC/HEIF images to JPEG before serving them to the browser.

## 0.43.13 — 2026-08-17

- Cleaned up restart experience: no more "Starting LensLedger..." preamble; console shows the normal banner on fresh start and "new version loaded" on restart.
- Restart no longer opens a duplicate browser tab — the existing tab auto-reloads to the new version.

## 0.43.12 — 2026-08-17

- Standardized headers on the Name faces and People review pages to match the main photo library layout.
- Added hamburger menu button for navigation instead of inline quick-nav links.
- Moved version badge next to the logo/title, theme toggle to the far right, and unidentified count next to it.

## 0.43.11 — 2026-08-17

- Removed the duplicate "Loading LensLedger..." message — the .cmd "Starting LensLedger..." is sufficient.
- Fixed the `--restarted` flag being concatenated into the file path instead of passed as a separate argument, which broke restart on paths with spaces.
- The browser-tab close reminder now appears inside the startup banner only on restart, not as a separate line.

## 0.43.10 — 2026-08-17

- Metadata publishing (people tags and descriptions) now works for HEIC and HEIF images, not just JPEGs.

## 0.43.9 — 2026-08-17

- The "close the previous browser tab" reminder now only appears on restart, not on every normal startup.

## 0.43.8 — 2026-08-17

- Fixed blank console window on restart: the server's stdout was being sent to the updater's log file instead of the console because `stdin=subprocess.DEVNULL` forced Python to wire all three standard handles through `STARTUPINFO`, bypassing the new console. Removed the explicit stdin override so `CREATE_NEW_CONSOLE` works as intended.

## 0.43.7 — 2026-08-17

- Added an early "Loading LensLedger..." message that prints before heavy Python imports begin, so the console window is never blank during startup or restart.
- Added a reminder to close the previous browser tab when restarting.

## 0.43.6 — 2026-08-17

- Moved the Trash button from the top toolbar into the sidebar card and renamed it to "Trash this photo" for clarity.

## 0.43.5 — 2026-08-17

- Updated all third-party dependency version floors to the latest releases: Pillow 12.3, pillow-heif 1.5, onnxruntime 1.28, open_clip_torch 3.3, torch 2.13.

## 0.43.4 — 2026-08-17

- Patched insightface's deprecated `SimilarityTransform.estimate()` call with the newer `from_estimate()` class constructor, preventing a FutureWarning on scikit-image >= 0.26 and a hard error on >= 2.2. Falls back to the old API on older scikit-image versions.

## 0.43.3 — 2026-08-17

- Moved the "Starting LensLedger..." message to the very top of the launcher script so it appears instantly when the console window opens, before the stale-server cleanup and Python loading.

## 0.43.2 — 2026-08-17

- Fixed "Empty trash" and single-item trash deletion hanging: both referenced a nonexistent `faces` table instead of the correct `face_embeddings` table, causing a silent server error with no user feedback.
- Added `search_fts` cleanup when permanently deleting trashed items, preventing orphan search-index rows.
- Added error handling to the trash panel's "Empty trash" and "Delete" buttons so failures now show a status message instead of silently disabling the button.

## 0.43.1 — 2026-08-17

- Fixed HEIC support for face detection and face location recovery: OpenCV cannot decode HEIC natively, so the face scanner now falls back to Pillow when `cv2.imdecode` returns `None`.

## 0.43.0 — 2026-08-17

- Added HEIC/HEIF image support: iPhone photos in HEIC format are now decoded for scanning, meaning search, face detection, and thumbnails. Added `pillow-heif` to base requirements.
- Added `.heif` to recognized media extensions alongside the existing `.heic`.
- Fixed scan-photos page info popovers appearing in the top-left corner instead of next to their info button.

## 0.42.2 — 2026-08-17

- Added "Starting LensLedger..." message to the launcher script so the console window is no longer blank while Python loads — especially noticeable after a version restart.

## 0.42.1 — 2026-08-17

- Suppressed noisy third-party warnings from Hugging Face Hub, OpenCLIP, and Pillow that appeared during semantic search — no data is sent externally; the CLIP model runs entirely locally once downloaded.

## 0.42.0 — 2026-08-17

- Added stale-server detection: the app now checks every 30 seconds whether the running server version matches the on-disk version, and shows a warning banner with a one-click restart button when they differ.
- Added `/api/version` endpoint exposing running version, startup time, on-disk version, and restart readiness.
- Changed server URL from `127.0.0.1` to `localhost` for consistency.

## 0.41.4 — 2026-08-17

- Refined info button styling: uses `currentColor` border to inherit the surrounding text color, standard 16px size, `cursor:help`, and system font — looks like a native part of the page in both themes.

## 0.41.3 — 2026-08-17

- Restyled all info buttons across the app: replaced the hard-to-read ⓘ Unicode character with a clean serif italic "i", added a visible accent-colored border, transparent background, and hover state that fills with the accent color.
- Info buttons are now consistent across the viewer and scan-photos pages.

## 0.41.2 — 2026-08-17

- Added security policy (SECURITY.md) with private vulnerability reporting instructions.
- Added GitHub issue templates for bug reports with structured fields.

## 0.41.1 — 2026-08-17

- Added "Empty trash" button to permanently delete all trashed items at once.

## 0.41.0 — 2026-08-17

- Compacted sidebar layout: smaller chips, tighter section spacing, and smaller action buttons to reduce scrolling.
- Standardized the theme toggle button across all pages: consistent 32px round style and placement.
- Moved the theme toggle to the right side of the viewer header for consistency with other pages.
- Relocated the "Move to Trash" button from the header into the toolbar, after the "View" button.
- Added permanent delete to Trash & Restore: each trashed item now has a "Delete" button for irreversible removal.
- Enriched the About panel with links to the developer's other projects.
- Added cache-busting version parameters to logo and favicon URLs to prevent stale images after updates.
- Fixed tooltip text that referenced "Preview & publish" to match the actual "Publish to this photo" section heading.

## 0.40.2 — 2026-08-17

- Updated logo to new version with "LensLedger" text.
- Added dedicated favicon (shutter with "LL" initials), separate from the header logo.
- All pages now use the new favicon in the browser tab.

## 0.40.1 — 2026-08-17

- Doubled the logo size in the header across all pages.

## 0.40.0 — 2026-08-17

- Switched to vintage monochrome logo (camera shutter in muted grays).
- Redesigned dark theme with warm espresso/leather tones and antique brass accents.
- Redesigned light theme with aged parchment surfaces and antique gold accents.
- Both themes now have a cohesive retro/vintage aesthetic matching the new logo.

## 0.39.1 — 2026-08-17

- Fixed blank space between photo and filmstrip on desktop caused by sidebar backdrop taking a grid row.
- Fixed filmstrip thumbnails being compressed into an auto-sized implicit grid row.
- Redesigned light theme with warm tones and a dark photo stage for better contrast and depth.

## 0.39.0 — 2026-08-17

- Added light mode and theme switching across all pages.
- Theme toggle button in every page header (sun/moon icon).
- Preference saved in localStorage and restored on page load.
- Follows the OS color scheme by default via `prefers-color-scheme`.
- All hardcoded colors replaced with CSS custom properties in a shared theme file.

## 0.38.1 — 2026-08-17

- Fixed desktop layout regression where the sidebar appeared below the photo instead of beside it.

## 0.38.0 — 2026-08-16

- Photo map now uses dynamic clustering: markers re-group automatically as you zoom in and out.
- Clicking a multi-photo cluster zooms into it, splitting it into smaller clusters or individual pins.
- Server returns finer-grained location data (100m grid instead of 11km), enabling much better detail when zoomed in.
- Maximum zoom increased from 8x to 32x for close-range exploration.
- Only markers visible in the current viewport are rendered, improving performance with large libraries.
- Panning and zooming trigger re-clustering with a debounced update for smooth interaction.

## 0.37.0 — 2026-08-16

- Photo locations (GPS) scan now shows a progress bar with percentage and ETA on rescans.
- On first-time scans (empty database), the progress bar shows an indeterminate animation.
- Onboarding library scan now shows a determinate progress bar with percentage and ETA when rescanning, replacing the always-animated indeterminate bar.

## 0.36.0 — 2026-08-16

- Responsive mobile layout: on viewports under 768px, the sidebar becomes a slide-in drawer from the right edge.
- Added an ⓘ toggle button on the photo stage to open/close the sidebar on mobile.
- Tapping the backdrop overlay or pressing Escape closes the mobile sidebar.
- Toolbar controls wrap on narrow screens for better usability.
- Header, filmstrip, and thumbnails use compact spacing on mobile.

## 0.35.0 — 2026-08-16

- Added multi-select in the filmstrip: Ctrl+click to toggle individual photos, Shift+click to select a range.
- Selected photos show a blue border and checkmark badge.
- A floating batch action bar appears when photos are selected, with a count, tag input, "Add tags", "Trash selected", and "Clear" buttons.
- Batch tagging applies tags to all selected photos at once (up to 500).
- Batch trash moves all selected photos to the review bin in one operation.
- Escape key clears the selection. Clicking a photo without Ctrl/Shift also clears the selection and navigates normally.

## 0.34.0 — 2026-08-16

- Added photo zoom and pan to the main viewer stage.
- Scroll wheel zooms in and out on the photo (up to 20x).
- Click-and-drag pans around the photo when zoomed in.
- Double-click zooms to 300%; double-click again resets to fit.
- Zoom level indicator and "Reset zoom" button appear in the bottom-left corner when zoomed.
- Zoom resets automatically when navigating to a different photo.
- Face bounding box overlay repositions correctly when zooming and panning.

## 0.33.2 — 2026-08-14

- Removed a redundant comment above `progressSuffix()` in scan-photos.js
  that described what the function does (the function name and signature
  already make that clear).
- Added AI assistant convention files (CLAUDE.md, CONVENTIONS.md) so
  Claude Code and Aider follow the same release ceremony and commit
  format rules.

## 0.33.1 — 2026-08-14

- "Start Scan All" button now shows "Starting scan..." while the request
  is in flight, so there's visible feedback that the click registered.

## 0.33.0 — 2026-08-14

- Scan photos page: OCR and face-detection errors are now recorded
  per-photo, and clicking an "Errors" count opens a modal listing which
  files failed and why, instead of only showing an opaque total.
- Scan photos page: job descriptions moved from always-on paragraphs to
  compact "i" info popovers, and the database/folder path details are
  now tucked behind a collapsible disclosure — trims the page down to
  what you need at a glance.
- Fixed the face-detection "Errors" tile showing 0 and being unclickable
  whenever the recorded errors came from a previous session (it only
  counted errors from the in-memory job just run, unlike the equivalent
  OCR tile, which already merged in the persisted count).

## 0.32.1 — 2026-08-13

- Tightened the Scan photos page's visual density -- smaller card padding
  and corner radii, a consistent spacing/radius scale, and trimmed
  typography -- it was reported as feeling "chunky and clunky" next to
  the rest of the app.

## 0.32.0 — 2026-08-13

- Name faces: double-click (or the portrait itself) now opens the full
  photo in a lightbox with the detected face boxed, so a turned, blurry,
  or dark face can be identified from its surroundings instead of only
  the small crop.
- Name faces: added an "Unknown person" button alongside "Not a person" —
  for a real face you just can't identify (a stranger in a crowd shot,
  say), distinct from "the detector was wrong, this isn't a face."
- Name faces: "Confirm all" on a match group now shows real per-item
  progress and an honest summary ("2 confirmed, 1 failed") instead of a
  static "Confirming…" that could flip to a stale error message while
  other items were still quietly succeeding in the background.
- Any action that hits "the catalog is busy" (most often while a scan is
  running in the background) now explains why, retries automatically
  once, and no longer shows a merge-specific message for unrelated
  actions like naming a face.

## 0.31.3 — 2026-08-13

- Face detection is dramatically faster on photos with several people in
  them. It was loading and running InsightFace's full model pack —
  including gender/age and 3D/2D landmark models that LensLedger never
  reads — for every face found. Restricting it to just the detection and
  recognition models it actually uses cut a 19-face group photo from ~13s
  to ~1.5s, with identical detections and embeddings.

## 0.31.2 — 2026-08-13

- Replaced the Photo map's placeholder world map with a real one adapted
  from a Wikimedia Commons equirectangular world map (coastlines by TUBS,
  CC BY-SA 3.0 / GFDL), with attribution in the map legend and
  `THIRD_PARTY_NOTICES.md`.
- Added an "Open in OpenStreetMap" link next to marker details on the
  Photo map, and next to any GPS coordinate shown in a photo's embedded
  metadata in the viewer.

## 0.31.1 — 2026-08-13

- The "Run all scans" summary on the Scan photos page now shows the same
  percentage and time-remaining estimate as the individual job cards below
  it (e.g. "step 4 of 4 · 9% · ~45h remaining"), instead of just a step
  count with no sense of progress within that step.

## 0.31.0 — 2026-08-12

- On People review, double-clicking a suggestion's photo now opens the same
  full-size lightbox as the existing "Enlarge" button, for inspecting faces
  that are small, blurry, or in the background before deciding.
- Added two new outcomes for a face suggestion, on both the review card and
  the lightbox: **"Not a person"** (the detector was wrong — this isn't a
  face at all) and **"Unknown person"** (a real face, but not someone you
  can name). Both permanently exclude that exact detected face from ever
  being suggested again for anyone, not just the person currently under
  review, and both are fully undoable like every other review decision.
  "Not a person" reuses the same mechanism the Name-faces page already had
  (`ignored_at` on the face record); "Unknown person" is a new, parallel
  flag (`unknown_at`) since it means something different — a real face,
  deliberately left unidentified rather than a bad detection.

## 0.30.2 — 2026-08-12

- Fixed the update panel misreporting a source checkout run from a git
  worktree (rather than a plain clone) as not being a source checkout at
  all — `.git` is a file in a worktree, not a directory, and the checks
  in `src/photo_search.py` only accepted a directory. `restart_ready`
  now correctly detects on-disk code changes, and "Restart to apply" no
  longer 400s, from a worktree checkout.
- Removed an embedded "people review" modal in the main viewer
  (`web/js/viewer.js`, `web/css/viewer.css`) that had been fully
  unreachable since the real "Review people" entry points were switched
  to link straight to the standalone `/people-review` page — nothing in
  the app ever set the menu-panel key that would have opened it. Also
  removed a handful of smaller strays found in the same sweep: an
  `openLibraryPanel()` left behind after `openLibraryPanelV2()`
  superseded it, an unused HTML-escaping helper, and several CSS rules
  (a leftover pre-`/scan-photos`-split diagnostics-panel block, an old
  sidebar hint style, an unused metadata-grid layout, an unused
  filmstrip pager) with no remaining caller or template anywhere in the
  app. No user-facing behavior changes — everything removed here was
  confirmed dead first.

## 0.30.1 — 2026-08-12

- Fixed the new person picker (v0.30.0) being invisible in practice: its
  dropdown was `position:absolute` nested inside a narrow card that uses
  `overflow:hidden` for its rounded photo corners, so the moment the
  dropdown extended past the card's edge it was silently clipped away —
  the button opened, but nothing showed. The dropdown now renders as its
  own element appended straight to `<body>` and positioned with JS against
  the trigger's actual screen coordinates, so no ancestor's `overflow`
  can clip it, on any of the three pages that use it.

## 0.30.0 — 2026-08-12

- Every "who is this?" field in LensLedger — Name faces, People review's
  correction field, and the main viewer's "Person's name" — was still a
  plain text `<input>`, and `autocomplete="off"` turned out not to reliably
  stop the browser's own address-autofill from popping up over it (Firefox's
  Form Autofill largely ignores `autocomplete="off"`). These are now a
  custom dropdown (`web/js/person-picker.js`, shared by all three pages):
  a `<button>` trigger — never an autofill target — that opens a
  searchable list of known people with type-to-filter, plus a pinned
  "+ New person" entry that reveals a plain text field for typing a brand
  -new name. Choosing an existing name or confirming a new one applies
  immediately; no separate Save button needed.
- Fixed the update panel's "Checking for updates…" getting stuck
  indefinitely for a source checkout or extracted-ZIP copy — clicking
  "Check again" would show "Checking…" and then just sit there instead of
  ever resolving to "current" or "available", because that code path never
  re-polled while the background check was still running (the managed
  -install path already did; this one didn't).

## 0.29.1 — 2026-08-11

- Fixed a restart (whether the update panel's "Restart to apply"/"Download,
  install & restart", or manually re-running `Start LensLedger.cmd` while an
  old instance is still up) leaving the old console window behind. That
  script always ends with `pause`, so its window never closed itself once
  Python exited — every restart left one more dead "Press any key to
  continue" window stacking up. The new copy now starts first, and only
  once it's confirmed running is the old window closed — never the other
  way around, so a failed relaunch can't leave you with no LensLedger
  window at all. The old window is only ever closed when it's positively
  identified as that exact disposable launcher (a `cmd.exe` whose own
  command line names `Start LensLedger.cmd`); anything else, including an
  interactive shell you happened to launch LensLedger from directly, is
  left alone.

## 0.29.0 — 2026-08-11

- Replaced the toolbar's native "Date" filter — a plain `<input type="date">`
  — with a hand-built calendar dropdown. Every browser renders that native
  control's popup differently (and Firefox's, in particular, buries year
  navigation behind clicking the month label), so it's now plain HTML/CSS/JS
  LensLedger fully controls: identical appearance and behavior in every
  browser, plus an explicit Year dropdown for jumping straight to any year
  back to 1900 in one click, alongside a Month dropdown and the usual
  day grid, Today, and Clear.
- Fixed the "Who is this?" field on Name faces (and the equivalent fields on
  People review and the main photo viewer) triggering the browser's own
  address-autofill suggestions — typing a person's name could pop up a
  saved street address instead of, or on top of, the list of known people.
  These fields now set `autocomplete="off"`.
- Fixed the Save button on Name faces silently disappearing on narrower face
  cards. The card's info panel is a CSS grid with an implicit column that
  was sizing itself to its widest content (the un-shrinkable input+button
  row) rather than the card's actual width, and the overflow was being
  clipped rather than shown — a `grid-template-columns:minmax(0,1fr)` fix
  lets the row shrink to fit instead.
- Changed the small caption under each Name faces card to lead with the
  filename instead of date + folder — the filename is what actually
  distinguishes one photo from another (several faces from the same burst
  share a date and folder), so it's now shown first and preserved when the
  caption is too long to fit, with the full date/folder/filename available
  as a hover tooltip.

## 0.28.0 — 2026-08-11

- The update panel now detects when a source checkout's running process is
  stale relative to the code already on disk — the case where `git pull`
  (or any other edit) changed the `.py` files but nobody restarted the
  server yet, which previously showed the confusing "run from an extracted
  download, run Install LensLedger.cmd" message even for a real git
  checkout, because the running process's own APP_VERSION was baked in at
  startup and had no way to notice the file on disk had moved on. It's
  now compared fresh against `product.py` on every status check. When
  they disagree, the panel offers a one-click "Restart to apply" that
  restarts this process in place — no download, no file changes, nothing
  git-related — instead of pointing at `git pull`, which the user had
  usually already run.

## 0.27.0 — 2026-08-11

- Naming a face on "Name faces" now groups other still-unnamed faces that
  look like the same person right under it, with that name pre-filled, so
  confirming a repeat is one click ("Confirm all") instead of picking the
  same name from the dropdown over and over — matching the "is this also
  X?" grouping Google Photos does. Matching runs on direct face-to-face
  embedding similarity (cosine, threshold 0.76, the same value People
  review's "Find more matches" already uses for suggestions), so it works
  from the very first photo named for someone, not just after enough
  confirmed faces exist to build a person profile. Any face in a group can
  be unchecked before confirming, or the whole group dismissed with "Not
  these" to review those faces normally instead.

## 0.26.5 — 2026-08-11

- Documented 0.26.4's install-vs-extracted-ZIP distinction in the README:
  Quick start now flags that step 4 must be `Install LensLedger.cmd`, not
  `Start LensLedger.cmd`, for self-updates to ever work; Updates and
  rollback spells out the two unmanaged cases (extracted ZIP run
  directly, vs. a `git clone` checkout) and what to do about each; and
  Run from source states plainly that a checkout needs `git pull` plus a
  restart for backend changes, while front-end-only changes just need a
  browser refresh.

## 0.26.4 — 2026-08-11

- Clarified the "Check for updates" dead end for anyone who runs LensLedger
  directly from an extracted release ZIP instead of through `Install
  LensLedger.cmd` — previously indistinguishable from a deliberate `git`
  source checkout, and told to `git pull`, which doesn't apply to them.
  The update panel now tells the two cases apart and, for an extracted
  copy, says plainly: run `Install LensLedger.cmd` once from this same
  folder to get automatic updates; your library and catalog are
  unaffected. `Start LensLedger.cmd` also now prints the same heads-up at
  launch, before the browser opens, so it's not something you'd only find
  by opening the update menu.

## 0.26.3 — 2026-08-11

- Fixed 0.26.2's dropdown-autosave fix not actually firing on a real
  datalist pick: it listened for `change`, which only fires on blur —
  clicking a suggestion fires `input` immediately, per the HTML spec.
  Confirmed live: picking a name now saves in well under a second with
  no need to click elsewhere first, while typing a brand-new name
  character by character still correctly waits for Enter or Save (it
  never exactly matches an existing name mid-keystroke).

## 0.26.2 — 2026-08-11

- Fixed "Name faces" requiring an extra Enter press after picking a name
  from the dropdown suggestions. Selecting an existing name from the
  datalist now saves immediately, since the browser fires a `change`
  event the moment a suggestion is picked; typing a brand-new name still
  requires an explicit Enter or Save click, since creating a new person is
  a more deliberate action worth confirming.

## 0.26.1 — 2026-08-11

- Fixed same-day feedback on 0.26.0's new "Name faces" page: a single
  crowd or burst-mode photo (a Grand Prix crowd shot alone had 37
  detections) could flood the entire review batch with crops of itself —
  confirmed live against the real library, a "July 4th Boat and Fireworks"
  burst was supplying 4 of the first 6 cards. `/api/faces/unidentified`
  now caps results to one face per photo per batch (via `ROW_NUMBER() ...
  PARTITION BY asset_id`); a photo's other faces surface on later batches
  once its first face is named or ignored. Verified against the real
  library: a batch of 30 now spans 30 distinct photos instead of a handful
  repeated up to 4 times each.
- Fixed "Name faces" being unreachable from the page most people actually
  land on: the new page was only linked from Scan your photos, People
  review, and Photo map's header nav — never from the main viewer's
  hamburger menu, which is where most sessions start. Added "🙂 Name
  faces (N)" there, next to "Review people," with a live unnamed-face
  count.

## 0.26.0 — 2026-08-11

- Added a real "Name faces" page (`/faces-review`) — a grid of cropped
  thumbnails for every detected face nobody has named yet, with an inline
  name field per face. Face detection previously only filled a database
  table with unlabeled embeddings; there was no screen to browse them, so
  the only way to teach LensLedger a face was to already know who was in a
  photo and type their name onto it elsewhere first. Naming a face here
  links the name directly to that exact face (not just the whole photo),
  which "Find more matches" on People review can then use to train a
  profile even from group photos with several unnamed faces in them.
  Faces can also be marked "Not a person" (pets, statues, reflections) so
  they stop showing up and stop being considered for matching.
- Added real progress: OCR, meaning search, and face detection now show a
  percentage bar and an estimated time remaining on the Scan your photos
  page, computed from the done/total counts each job already reported
  every poll but the UI was discarding. Install buttons for the optional
  face/meaning-search downloads show an animated indeterminate bar while
  installing instead of a bare spinner.
- Fixed "Run all scans" reporting "All scans complete" identically whether
  it ran all four steps or silently skipped meaning search and face
  detection because they were not installed. It now reports exactly which
  steps ran and which were skipped, and the meaning-search/face-detection
  cards state their download size and explain why they're not bundled
  (large downloads, and the face model's license does not permit
  redistribution).
- Fixed several stale/dead-end navigation issues: replaced leftover
  references to the removed "Library health" modal (renamed to "Scan your
  photos" in 0.24.2) across onboarding, error messages, and README with
  the current name, several as real links; added a small nav (Scan
  photos / Review people / Name faces / Photo map) to the header of
  every non-viewer page, since Scan your photos and People review
  previously had no way to reach each other except through the main
  viewer's hamburger menu.
- Renamed the per-person "Edit names" button (plural) to "Edit name" —
  it only ever edits one person's name, and the plural read like a bulk
  action across the whole People grid.

## 0.25.3 — 2026-08-11

- Fixed `database is locked` errors flooding the console and breaking live
  status updates during any real scan. `connect()` unconditionally wrote
  `PRAGMA user_version` and committed on every single connection, including
  the lightweight status checks the Scan your photos page polls every
  700ms — a real write competing for SQLite's single writer lock even when
  nothing needed writing. Invisible before because scans used to finish in
  seconds (thanks to the placeholder bug fixed in 0.25.2 skipping most of
  the library); now that scans do real, sustained work, the five
  concurrently-polling status endpoints collided with the active scan
  writer constantly. The version write is now skipped whenever the schema
  is already current, which is true for nearly every connection.
- Confirmed live: mid-scan, 2,200 files in, the fixed placeholder
  detection was correctly recognizing 1,766 of them (80%) as real,
  previously wrongly-skipped files — direct field confirmation the 0.25.2
  fix works as intended.

## 0.25.2 — 2026-08-10

- Fixed a major face/OCR/meaning-search coverage bug: `is_cloud_placeholder()`
  treated any file carrying OneDrive/Dropbox's cloud-management attribute
  bits as unavailable, even when the file was fully downloaded. Dropbox
  sets those bits on hydrated files too, not just true placeholders, so
  LensLedger had been silently skipping content scans on files that were
  sitting right there on disk — about 48,900 of this library's 50,909
  files. It now checks actual on-disk allocation vs. logical file size
  (queried without ever triggering a download) to tell a genuinely
  un-downloaded placeholder from a fully-synced file. Confirmed against
  real files: a 400MB `.tif` and a 119KB `.jpg` that were both being
  wrongly skipped are now correctly recognized as fully present.
- Added an honest scope notice to the Scan your photos page: it now says
  up front how many files are actually downloaded and scannable vs. still
  cloud-only, so "scan complete" can no longer be misread as "your whole
  library is done."
- Fixed the Photo map's photo preview cropping tall/wide photos to a fixed
  box; it now shows the complete photo, letterboxed if needed.
- Fixed clicking a map marker giving no indication which one you picked;
  the selected marker now stays visibly highlighted until you pick another
  or close the panel.
- Fixed the Photo map's "Back to library" link living in the top-right as
  a button, inconsistent with every other page's top-left "← Photo
  library" link — moved it to match.
- New app logo (transparent background) and a site-wide color pass:
  replaced every remaining blue/purple accent (badges, chips, marker
  colors, background glows) with warm orange/red tones to match.

## 0.25.1 — 2026-08-10

- Clarified the "Scan for text now" message on the setup page: it now
  says the OCR scan runs on your computer and keeps going even if you
  close the browser tab or move on to your library, instead of leaving
  that ambiguous.

## 0.25.0 — 2026-08-10

- Added "Run all scans": one button on the Scan your photos page now runs
  photo locations, OCR, meaning search, and face detection back to back,
  skipping any optional scan you have not set up yet. Previously each scan
  had to be started by hand after the one before it finished. Verified
  live against the real 50,909-file library — all four steps ran and
  completed correctly in sequence.
- Fixed the "Mapped photos" and "People to review" links on Scan your
  photos doing nothing when clicked. A local status variable named
  `location` was shadowing the browser's own `window.location`, so the
  click handler was setting `.href` on the wrong object entirely.
- Fixed a library-loading bug where a leftover path (from a deleted temp
  folder) that no longer existed on disk silently fell back to the OS
  Pictures folder instead of the real, still-known library — with no
  indication anything had changed. `load_library_state()` now falls back
  through every other recently-used library before giving up.
- Replaced the app's blue accent color with a warm amber across every
  page, at the user's request.

## 0.24.2 — 2026-08-10

- Replace the cramped Library health modal with a real, full-width "Scan
  your photos" page (up to 1366px, no forced scrolling). Every background
  job (installs, OCR, meaning search, face detection) now shows a real
  animated spinner and live elapsed time instead of a static message that
  looked frozen. Added a dedicated "Photo locations (GPS)" scan card,
  previously an invisible side effect of "Rescan library" with no control
  of its own.
- Fixed the photo map showing "no mapped photos yet": location scanning
  had never actually been run against the real library. Ran it for real —
  845 photos now located across 58 places.

## 0.24.1 — 2026-08-09

- Fix face detection permanently re-scanning photos with no one in them.
  A photo with zero detected faces never got marked as scanned, so it
  would be reprocessed on every future pass forever, and "remaining"
  would never reach zero even after a fully complete scan. Added
  `assets.face_scanned` (schema v8), tracked independently of whether a
  face was actually found — the same convention `ocr_scanned` already
  uses. Caught live during the first real run against the full library.

## 0.24.0 — 2026-08-09

- Add real full-library face detection. Previously LensLedger could only
  match faces against a fixed, one-time historical import (2,210 faces
  total); it had no way to find a face in a photo outside that original
  set. Library health now has a "Face detection" job that runs actual
  detection (InsightFace, optional, same install pattern as meaning search)
  against every photo that has never been face-scanned, feeding directly
  into the existing suggestion/auto-confirm pipeline with no extra steps.

## 0.23.8 — 2026-08-09

- Documented every top-level file and folder in README.md, and pointed the
  meaning-search instructions at the new in-app one-click setup.

## 0.23.7 — 2026-08-09

- A photo's embedded GPS field now opens LensLedger's own map, centered on
  that photo's location, instead of a generic raw openstreetmap.org page.
- The map's location details panel gained a "View all photos here" action,
  so a multi-photo cluster is no longer limited to a single representative
  photo.

## 0.23.6 — 2026-08-09

- Meaning search's Library health panel now explains in plain language what
  it does and why it's optional, and adds a real one-click "Set up meaning
  search" that installs the required software as a monitored background
  job instead of pointing at a README command.

## 0.23.5 — 2026-08-09

- The first-run setup wizard now plainly states where LensLedger's data
  (index, backups, everything) actually lives, and offers a one-click
  "Scan for text now" right after the initial library scan completes,
  instead of leaving OCR to be discovered later in Library health.

## 0.23.4 — 2026-08-09

- Rename `static/` to `web/` (Python modules already live under `src/`, so
  this finishes the layout cleanup: code, web assets, and launchers are now
  each in their own clearly-named place). Internal-only change with no
  effect on how LensLedger looks or runs.

## 0.23.3 — 2026-08-09

- Fix folder-name tagging silently stopping after 2025 (`generate_historical_folder_tags.py`
  hardcoded a `year <= 2025` cutoff). Scans now infer tags for any folder
  automatically, forever, and fall back to the folder's own descriptive text
  when no curated category matches. Added `database_tools.py
  backfill-folder-tags` to retroactively fix already-indexed folders.

## 0.23.2 — 2026-08-09

- Fix the "Check for updates" flow silently creating a separate managed
  install when triggered from an unmanaged copy (like a source checkout) —
  the install button is now hidden with a clear explanation instead, and
  the backend refuses the request as a backstop.
- Fix the update dialog getting stuck on "Installing and restarting…"
  forever after a real managed-install update — it now polls until the new
  version responds and reloads automatically.
- Show a clear "No matches" message when a search comes up empty, instead
  of a blank filmstrip and a confusing "0–0 of 0" count.

## 0.23.1 — 2026-08-09

- Fix Windows OCR failing on large images ("Image dimensions are too large")
  by downscaling to fit the OCR engine's maximum before recognition, instead
  of erroring out and leaving the photo untagged.
- Reorganize the project layout: all Python modules now live under `src/`,
  and `static/` is split into `static/css/` and `static/js/`. Internal-only
  change with no effect on how LensLedger looks or runs.

## 0.23.0 — 2026-08-09

- Fix "Everything" search: any text search in the default scope raised a
  database error and silently returned zero results, because the query
  combined SQLite FTS5's `MATCH` with an `OR`, which FTS5 refuses to
  evaluate. Everything-scope search now works, and matches are ranked by
  relevance ("Best match") instead of only by date.
- Replace the filmstrip's fixed 250-photo page limit with infinite scroll: a
  new pagination endpoint feeds the filmstrip more photos automatically as
  you scroll, using the same query logic as the initial page so results can
  never drift out of sync.
- Confirm near-certain face matches automatically instead of always waiting
  for a review click. A match at least 90% confident with a wide margin over
  the runner-up is confirmed and published to the photo's metadata right
  away, the same safety-backed way a manual confirmation is; anything less
  certain still goes to the review queue exactly as before.
- Make the Library health panel's "People to review", "Mapped photos", and
  "Review Bin" counts clickable, taking you directly to that screen instead
  of just reporting a number.
- Close a clickjacking gap (missing `X-Frame-Options`/`Content-Security-
  Policy`), switch the CSRF check to a constant-time comparison, and fix a
  narrow race condition where a concurrent request could observe a library
  switch's new root paired with the old catalog.
- Move the viewer, onboarding, people-review, and map pages' CSS and
  JavaScript out of `photo_search.py` into served static files, and add the
  Content-Security-Policy this now makes possible.
- Escape SQL `LIKE` wildcards in the people-name search and standardize
  metadata-backup filename timestamps on microsecond precision.

## 0.22.5 — 2026-08-09

- Preserve the original pre-managed `Start LensLedger.cmd` and replace it with
  a managed-launcher handoff after a successful catalog migration. Existing
  old shortcuts now open the current managed installation instead of running
  an obsolete bundled copy.
- Remember adopted legacy shortcuts and recheck their handoff on every managed
  update, without allowing an unavailable old folder to block the update.
- Make the release workflow reject a tag whose version does not match
  `product.py`, or whose release notes are missing.

## 0.22.4 — 2026-08-09

- Make each person's photo view explicit about confirmed photos, pending face
  matches, and the confirmed photos that retain an exact face location.
- Add a direct, person-specific route into face review and highlight the known
  face in the normal photo viewer when a precise face box is available.
- Preserve all confirmed people on group photos; each remains visible and
  searchable through that person's own photo view.

## 0.22.3 — 2026-08-09

- State explicitly in the People page, Edit names dialog, and README that
  commas separate alternate names.

## 0.22.2 — 2026-08-09

- Make People merges resilient to transient SQLite locks by waiting safely for
  the catalog, taking the write reservation up front, and closing any failed
  connection immediately.
- Prevent a second People merge from running concurrently and require library
  scan, OCR, and meaning-indexing jobs to finish first.
- Explain a genuine catalog conflict clearly and confirm that no names were
  merged, instead of showing an opaque database error.

## 0.22.1 — 2026-08-09

- Create and verify a catalog backup automatically before every People merge,
  providing a recovery point for this intentionally non-automatic-undo action.

## 0.22.0 — 2026-08-09

- Add a People-library merge tool for duplicate names and categories.
- Keep the selected primary name, convert merged primary names and aliases into
  alternate searchable names, and combine each photo's person decision safely.
- Preserve the strongest decision when duplicate records overlap on a photo:
  confirmed, then suggested, then rejected; retain the exact face reference
  when it belongs to the winning record.
- Update confirmed JPEG People Shown metadata through the existing safety-backup
  and pixel-verification path, then rebuild local face profiles and suggestions.
- Preserve prior review decisions as closed history instead of deleting them.

## 0.21.0 — 2026-08-09

- Highlight the exact face being evaluated in People review with a responsive
  bounding box in both the gallery card and enlarged-photo view.
- Store normalized face rectangles with the exact face embedding and expose
  them through the review queue API.
- Recover rectangles for legacy embeddings by comparing regenerated vectors
  and accepting only strong, unambiguous matches.
- Prevent photo-level names on multi-person photos from training a face profile
  until a review decision identifies the exact face.
- Quarantine highly similar profiles from suggestion generation so duplicate or
  historically misassigned names cannot fan out into new false matches.
- Keep the optional face runtime and separately licensed pretrained model out
  of the standard installation and release archive.
- Show an explicit unavailable message for any legacy match that cannot be
  localized safely instead of highlighting a guessed face.

## 0.20.0 — 2026-08-09

- Add a managed per-user installation flow separate from photo and catalog data.
- Check GitHub for updates on launch and at most once every six hours, while
  requiring explicit approval before any installation.
- Authenticate to private releases through an existing GitHub CLI login or an
  explicitly supplied token without storing credentials in LensLedger.
- Verify release asset size and GitHub's SHA-256 digest, reject unsafe ZIP
  entries, and validate required files and versions before installation.
- Replace only updater-managed application folders and preserve the previous
  version as a rollback directory.
- Refuse to overwrite Git working copies, mixed source folders, unsafe target
  paths, or unmanaged existing installations.
- Migrate pre-v0.16 catalogs with SQLite's online backup API, schema migration,
  integrity verification, library registration, and source preservation.
- Add automated coverage for update discovery, verification, traversal defense,
  managed replacement, rollback, unmanaged-folder safety, and legacy migration.

## 0.19.0 — 2026-08-09

- Add opt-in, local-only natural-language image search using OpenCLIP.
- Keep the large machine-learning dependencies out of the standard installation
  and load them only when a user explicitly builds a meaning index.
- Build the meaning index incrementally in pausable background batches and
  expose coverage and progress in Library Health.
- Add a Meaning search scope that ranks locally indexed photos from ordinary
  scene descriptions without uploading photos or search text.
- Allow a person in the face-review queue to be deferred for seven days so the
  same difficult identity does not block later people across sessions.
- Add deterministic semantic-index and background-worker tests that do not
  download a model during CI.
- Separate embedded-metadata reading, pixel-integrity checks, and per-library
  configuration from the HTTP server into independently tested service modules.

## 0.18.0 — 2026-08-09

- Add a read-only, fully local Photo Map built from embedded EXIF GPS coordinates.
- Extract locations during incremental scans and cluster nearby photos without
  contacting an external map or tile service.
- Add a Library Health & OCR panel with database integrity, catalog, map,
  people-review, Review Bin, and text-recognition status.
- Make Windows OCR pausable and resumable, and remember completed images even
  when no visible text was found.
- Create verified database backups directly from the viewer.
- Expand automated coverage to include HTTP endpoints, CSRF rejection, path
  confinement, metadata publish/restore, Review Bin recovery, background OCR,
  GPS migration, mapping, diagnostics, and database backup verification.
- Rename the search scope label from “Search in” to the clearer “Search scope.”

## 0.17.0 — 2026-08-09

- Add a guided first-run experience that suggests likely photo folders, explains
  the private local inventory, and opens LensLedger only after the first scan.
- Show live discovered, indexed, unchanged, cloud-only, and error counts, with a
  safe pause/resume path and a clear completion inventory.
- Remember multiple libraries and provide the same scan controls when switching
  or adding a library later.
- Inventory common camera RAW formats separately from browser-previewable images,
  and stop treating WAV audio as a photo-library asset.
- Make the Windows launcher check for Python and install required packages for a
  new user before starting LensLedger.
- Extend scanner coverage for empty databases, incremental rescans, verified
  backups, cancelled/resumed scans, missing files, RAW files, and ignored audio.

## 0.16.0 — 2026-08-09

- Separate application source from personal library data and store new-user
  databases, settings, safety copies, face data, and review-bin files under the
  per-user LensLedger data directory.
- Add database commands to initialize, inspect, verify, migrate, back up, and
  rebuild search indexes without shipping a populated database.
- Add a clean public-source layout, privacy-focused ignore rules, MIT license,
  third-party notices, automated Windows tests, and versioned ZIP releases.
- Document fresh installation, first-library setup, safety guarantees, database
  maintenance, and source development in a branded README.

## 0.15.0 — 2026-08-08

- Publish every saved People-review decision immediately into the affected JPEG's XMP People Shown and keyword metadata.
- Preserve existing embedded people and keywords while adding all confirmed LensLedger identities and removing rejected or renamed identities.
- Make each batch atomic: create safety copies, verify decoded pixels, and restore already-written files if any photo fails.
- Extend People-review Undo so it restores both the database decision and the exact pre-publish photo file.
- Publish primary-name changes across every confirmed photo so the old name is fully replaced.
- Consolidate the application, database, face data, safety copies, launchers, and notes under `!LensLedger`.

## 0.14.0 — 2026-08-08

- Allow a person's primary display name to be changed from the visual People gallery.
- Rebuild every confirmed photo's local person tags and search data under the replacement name.
- Do not retain the previous primary name unless the user explicitly enters it as an alternate name.
- Hide an outdated embedded name locally when it exists in a confirmed photo; the file itself still changes only through Preview & Publish.
- Reject renames and alternate names that already belong to another identity rather than merging people silently.

## 0.13.0 — 2026-08-08

- Learn conservative face profiles from confirmed person/photo decisions without installing another model runtime.
- Reconstruct the most likely face from older photo-level confirmations and preserve exact face IDs for new decisions.
- Suppress profiles that collide with another named identity and require a strong best-match margin before suggesting a photo.
- Add Find more matches to the full-page People review workflow.
- Keep every learned result review-only; learning never confirms a person automatically.
- Make repeat learning idempotent so replacing prior learned suggestions preserves the same pending queue.

## 0.12.0 — 2026-08-08

- Replace the People review modal with a dedicated full-page review workspace.
- Show up to eight large, fully fitted photos at once with click-to-enlarge viewing.
- Phrase review prompts around whether the complete photo contains the proposed person.
- Confirm likely matches, reject incorrect matches, and optionally correct names in one atomic batch.
- Add whole-batch Undo, Skip group, and Next person controls while preserving individual review APIs.
- Fall back to complete photos because the recovered face vectors do not contain reliable face-position coordinates.
- Re-enable batch saving after each successful group and keep portrait previews inside their card boundaries.

## 0.11.2 — 2026-08-08

- Make the People review modal allocate its fixed height between its header, photo, controls, and footer.
- Scale portrait and landscape review photos fully into the available image area instead of clipping their lower edge.

## 0.11.1 — 2026-08-08

- Re-enable People review decision buttons after each successful save advances to the next suggestion.

## 0.11.0 — 2026-08-08

- Add a continuous People review session that advances through every pending face suggestion person by person.
- Provide Confirm, Reject, Correct name, Skip, Next person, and Undo controls in a focused photo-review interface.
- Persist every review decision immediately and automatically continue with the next person when a queue is completed.
- Record reversible review history so Undo restores confirmed, rejected, and corrected identities exactly.
- Show remaining-photo and remaining-person progress from both the main menu and People gallery.

## 0.10.3 — 2026-08-08

- Make every Windows launcher run stop the existing LensLedger server on port 5309 before starting the current code.
- Reopen the same local address after the clean restart so the browser loads the replacement server.

## 0.10.2 — 2026-08-08

- Make the Windows launcher open the existing LensLedger page when the server is already running.
- Delay automatic browser opening briefly during a fresh launch so the local server is ready first.

## 0.10.1 — 2026-08-08

- Move LensLedger's default local port to 5309 so it does not compete with Wireless Tools.

## 0.10.0 — 2026-08-08

- Add People as a dedicated search scope with an alphabetical visual gallery.
- Show a representative photo, confirmed-photo count, and pending-review count for every known person.
- Open all confirmed photos for a person by clicking their gallery card.
- Support multiple searchable alternate names per person without duplicating identities or publishing aliases as metadata.
- Resolve manual People entries through primary names and aliases while preserving the existing confirmation workflow.

## 0.9.0 — 2026-08-08

- Add a dedicated People in this photo editor with manual entry, autocomplete, and Enter-to-submit.
- Convert recovered face vectors and 55 named references into 522 review suggestions across 408 individual photos.
- Keep face matches non-searchable and non-publishable until explicitly confirmed; provide approve and reject controls with confidence percentages.
- Make confirmed people searchable and publish them to XMP People Shown and Keywords.
- Add Open photo library with a native Windows folder chooser, background indexing, remembered selection, and a separate SQLite index for every library root.
- Preserve the existing default library database and all subject, photo-tag, event-tag, trash, and publishing workflows.

## 0.8.3 — 2026-08-08

- Add an explicit Cancel button beside Publish this photo in the metadata preview footer.
- Keep the existing top-right Close control as an additional way to leave the preview without writing.

## 0.8.2 — 2026-08-08

- Submit Primary Subject, Photo Tags, and Event / Folder Tags with Enter as well as their existing buttons.
- Ignore Enter while an input method editor is still composing text.

## 0.8.1 — 2026-08-08

- Widen the metadata publishing preview without changing the size of other dialogs.
- Keep metadata destination names on one line and devote more table width to descriptions and keywords.

## 0.8.0 — 2026-08-08

- Add explicit, single-photo JPEG metadata publishing with an exact field-by-field before/after preview.
- Write approved descriptions, primary subjects, and visible photo/event tags to standard EXIF, IPTC, and XMP destinations.
- Create a timestamped safety copy before every publish and provide an in-app restore action.
- Verify decoded picture pixels after every metadata write and automatically restore the backup if verification fails.
- Import older Microsoft XMP keyword blocks into removable Photo Tags, including the previously missed `Fast food` example.
- Bundle ExifTool 13.59 for local metadata reading and writing.

## 0.7.0 — 2026-08-08

- Preserve the existing primary-subject, photo-tag, and event/folder-tag workflows while documenting their intended standard metadata destinations.
- Display readable descriptive, ownership, camera, capture, and GPS metadata already embedded in the selected photo.
- Link embedded GPS coordinates to their location on OpenStreetMap.
- Make Everything the default search scope while preserving the narrower visible-image and day/event scopes.
- Add a visible Photo map entry point describing the planned GPS-based world map.

## 0.6.3 — 2026-08-08

- Add a high-contrast bounding box around the filmstrip thumbnail for the photo currently shown in the viewer.
- Mark the current thumbnail semantically for assistive technology.

## 0.6.2 — 2026-08-07

- Preserve real mouse clicks on filmstrip thumbnails by starting pointer capture only after drag movement begins.
- Expire drag-click suppression immediately after the completed drag gesture.

## 0.6.1 — 2026-08-07

- Add grab-hand click-and-drag horizontal scrolling to the bottom filmstrip.
- Preserve ordinary thumbnail selection when the pointer was clicked without dragging.

## 0.6.0 — 2026-08-07

- Replace the verbose scrolling sidebar guidance with compact clickable info popovers.
- Constrain the search field and tighten the application header.
- Move the selected-photo action into the header as **Move to Trash**.
- Add a hamburger menu with functional Trash & restore, Quick guide, and About panels.

## 0.5.0 — 2026-08-07

- Add comma-separated event/folder tags directly from the selected photo.
- Apply new event/folder tags to every photo in that folder and refresh search immediately.
- Suppress duplicate per-photo chips when the same label is already inherited from the folder.

## 0.4.1 — 2026-08-07

- Show the saved primary subject as a removable chip above its editor.
- Clear the subject with its × button and leave the text box ready for replacement text.

## 0.4.0 — 2026-08-07

- Reworked the sidebar into a numbered, plain-language tag-editing workflow.
- Clarified the distinction between one primary-subject phrase, visible photo tags, and inherited event/folder tags.
- Added comma-, semicolon-, and line-separated multi-tag entry.
- Explained that edits apply only to the selected photo and remain external until explicitly published.
- Renamed and fully explained the reversible Review Bin action.
- Widened the editing sidebar for clearer instructions and controls.

## 0.3.2 — 2026-08-07

- Replaced fixed header sizing with a resilient full-viewport application grid.
- Prevented high-resolution portrait media from expanding beyond the preview stage.
- Forced selected images and videos to fit completely inside the available viewer while preserving aspect ratio.
- Added an explicit visual-QA requirement for all future visibility-affecting changes.

## 0.3.1 — 2026-08-07

- Corrected the reported palm-tree search set with three verified per-image annotations.
- Confirmed that seven unrelated water, bird, fish, and vegetation photographs matched only through their shared event-folder name.
- Preserved broad folder matches under the explicit Day/event context search scope while keeping them out of default visible-image search.

## 0.3.0 — 2026-08-07

- Replaced the card grid with a large preview, right-hand metadata sidebar, and horizontal filmstrip.
- Added exact-date selection, previous/next-day controls, keyboard photo navigation, and explicit search scopes.
- Made subject and per-photo tags editable from the sidebar.
- Added per-photo suppression and restoration of incorrect embedded or inherited tags.
- Separated visible-image searching from day/event-context searching.
- Added a reversible Review Bin with an immediate Undo action and preserved-path audit records.
- Added CSRF protection to all localhost metadata-changing actions.

## 0.2.1 — 2026-08-07

- Renamed the Windows launcher to `Start LensLedger.cmd`.
- Added a branded terminal startup banner, local URL, and explicit Ctrl+C stop instruction.
- Added consistent product tagline and identity styling to the HTML viewer.
- Added graceful terminal shutdown messaging.

## 0.2.0 — 2026-08-07

- Named the application LensLedger and added its product logo.
- Turned search into a browsable HTML photo and video library.
- Added date-range filtering, sorting, and pagination.
- Separated visible subject, image-specific tags, and day context.
- Added durable per-photo annotations and corrected the dermatology sequence.

## 0.1.0 — 2026-08-07

- Created the incremental SQLite photo index and full-text search.
- Added local OCR, folder rules, XMP keyword reading, and recovered face embeddings.
- Kept indexing and annotations external to the original media files.
