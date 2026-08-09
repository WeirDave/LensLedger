# LensLedger changelog

LensLedger uses semantic versioning: `MAJOR.MINOR.PATCH`.

- **MAJOR** — incompatible database, metadata, or workflow changes
- **MINOR** — new backward-compatible features
- **PATCH** — corrections and small backward-compatible improvements

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
