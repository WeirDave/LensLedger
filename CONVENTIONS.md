# LensLedger — conventions for AI coding assistants

This file is read by Aider and other tools that look for `CONVENTIONS.md`.
The canonical source is `CLAUDE.md` in this same repo — both files describe
the same rules. If they ever conflict, `CLAUDE.md` wins.

## Rule zero — never commit real personal photos or identifiable data

**Never put real photos, real people, or real location data into this
project, in any form, without asking him first.** Not as a screenshot, not
as a test fixture, not in a doc, not in a commit message, not "just to
reproduce the bug." Ask, and wait for an answer.

**What counts.** Real photos or video frames from David's library. Real GPS
coordinates or place names tied to a real photo — home, family addresses,
travel itineraries. Real names of family members, friends, or anyone
identifiable from face-review data. Real file or folder paths that reveal a
library location, an event name, or a person's name. Screenshots of the
running app taken against his actual library.

**Where it applies: everything that persists.** Source, comments, test
fixtures, sample data, `docs/`, release notes, screenshots, this file,
commit messages, and issue/PR comments.

**When he shares a real scan result or photo to settle a bug** — read it for
structure and metadata shape only: field names, counts, error strings. Do
not copy the content, do not turn it into a fixture, and do not let a name,
a GPS coordinate, or a face crop reach a commit. Build test fixtures
synthetically — invented names, invented or omitted coordinates, synthetic
or licensed-stock images.

**Assume this repository is public, because it is.** A public repo cannot be
quietly un-published — release notes can be edited, but ZIP assets, forks,
clones and commit history cannot be taken back the same way.

**If you're unsure whether something is traceable to a real person or
place, leave it out and ask.** Unsure is a hit.

This mirrors the confidentiality rule already standing in WaxFrame
Professional (`CLAUDE.md` §0) and WD-Wireless-Tools (`CLAUDE.md` "Rule
zero"), extended here to the kind of data this app actually handles: real
photos, faces, and locations rather than work data.


## Product identity

- Product name: **LensLedger**
- Version source of truth: `src/product.py` (`APP_VERSION`, `APP_RELEASE_DATE`)

## Versioning

Semantic versioning: `MAJOR.MINOR.PATCH`

- **MAJOR** — incompatible database, metadata, or workflow changes
- **MINOR** — new backward-compatible features
- **PATCH** — corrections and small backward-compatible improvements

## Commit format

Every commit subject line must read:

```
LensLedger vX.Y.Z — short summary of what changed
```

Example: `LensLedger v0.33.0 — Show per-photo scan errors, decluttered Scan photos page`

Do not use bare summaries. The product name and version are required.

## Testing

Run the full test suite before every commit:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

All tests must pass. Do not commit with failing tests.

For UI/CSS changes: visually verify in a browser, not just DOM-state checks.

## Release ceremony

After every fix or feature, perform the full release ceremony as part of
the same task. Every fix ships. Do not stop after editing the code.

Auto-commits are disabled in this repo. You are responsible for editing
ALL of the release files below before telling the user to commit.

1. **Make the code change** the user asked for
2. **Bump the version** — edit `src/product.py`: increment `APP_VERSION`,
   set `APP_RELEASE_DATE` to today (`YYYY-MM-DD`)
3. **Update CHANGELOG.md** — add a new `## X.Y.Z — YYYY-MM-DD` entry at the
   top (under the header). Concise past-tense bullets describing user-visible
   changes. See existing entries for tone.
4. **Write release notes** — create `docs/releases/vX.Y.Z.md`:
   - Motivation/context (what was wrong before)
   - What changed and how it works now
   - `## Fixed during testing` section if you found and fixed a bug
   - `## Verified` section listing test suite results and any manual checks
   - See `docs/releases/v0.33.0.md` for a good example
5. **Print the finish commands** — after editing all files, print this
   exact block so the user can copy-paste it into PowerShell:

   ```
   To finalize the release, run these commands in order:

   git add -A
   git commit -m "LensLedger vX.Y.Z — short summary"
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```

   Replace `X.Y.Z` with the actual version and write a real summary.

   If you can run shell commands directly (e.g. Claude Code), execute
   these yourself instead of printing them. Otherwise always print them.

## Project structure

```
src/            Python backend (photo_search.py is the main server)
web/            Frontend (JS in web/js/, CSS in web/css/)
tests/          Python unittest suite
docs/releases/  Per-version release notes (vX.Y.Z.md)
.github/        CI workflows (tests.yml, release.yml)
```

## Database

SQLite. Schema version tracked in `src/photo_index.py` (`SCHEMA_VERSION`).
Migrations are idempotent ALTER TABLE ADD COLUMN with column-existence guards.

## Don'ts

- Don't add features beyond what was asked for
- Don't refactor surrounding code while fixing a bug
- Don't add comments explaining what code does (only why, if non-obvious)
- Don't skip the release ceremony or any step within it
- Don't ask for permission to push

## Session hygiene

Prune stale worktrees at the start of every session: `git worktree prune -v`,
then check whether the `.claude/worktrees/<name>` directory it names is still
sitting on disk — prune clears git's own registration, but the folder itself
sometimes survives that and needs removing by hand. Delete the matching
`claude/<name>` local branch too if it has no commits `main` doesn't already
have.

A session that dies mid-task — crash, timeout, closed window — leaves both
behind. This has happened across David's repos before, and a dead worktree
is exactly the kind of forgotten corner that ends up holding content nobody
meant to leave sitting around (real photos or personal data, per Rule zero
above). Report what you found and removed rather than cleaning quietly.
