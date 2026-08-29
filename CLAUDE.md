# LensLedger — AI assistant instructions

These instructions apply to every AI coding assistant working in this repo
(Claude Code, Aider, Cursor, etc.). Follow them exactly.

## Product identity

- Product name: **LensLedger**
- Version source of truth: `src/product.py` (`APP_VERSION`, `APP_RELEASE_DATE`)
- Tagline: "Your photos, understood."

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

This is non-negotiable. Do not use bare summaries like "Fix bug in scan page."

## Testing

Run the full test suite before every commit:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

All tests must pass. Do not commit with failing tests.

For UI/CSS changes: visually verify in a browser, not just DOM-state checks.

## Release ceremony

After every fix or feature lands on `main` with tests green, perform the
**full release ceremony** without asking for permission. Every fix ships.
The steps below must all happen, in order, as a single uninterrupted flow.

### Step 1 — Bump the version

Edit `src/product.py`:
- Increment `APP_VERSION` (follow semver rules above)
- Set `APP_RELEASE_DATE` to today's date (`YYYY-MM-DD`)

### Step 2 — Update CHANGELOG.md

Add a new entry at the top of the changelog, under the header line.
Format:

```markdown
## X.Y.Z — YYYY-MM-DD

- Bullet point describing each user-visible change.
- Use past tense, describe what changed, not implementation details.
```

Keep bullets concise. See existing entries for tone and detail level.

### Step 3 — Write release notes

Create `docs/releases/vX.Y.Z.md` with detailed release notes:

- Do NOT start with an H1 title like `# LensLedger vX.Y.Z — ...` — GitHub
  already shows the release title (`LensLedger vX.Y.Z`), so a leading H1
  just duplicates it. Start directly with the first content section.
- Start with the motivation/context (what problem, what was wrong before)
- Describe what changed and how it works now
- If you found and fixed a bug during testing, add a `## Fixed during testing`
  section explaining what broke and how you fixed it
- End with a `## Verified` section listing what you tested:
  - Test suite result (count and status)
  - Any manual/browser verification you performed

See `docs/releases/v0.80.0.md` for a good example of all sections.

### Step 4 — Commit

Stage all changed files and commit with the standard subject format:

```
LensLedger vX.Y.Z — short summary
```

### Step 5 — Tag

```bash
git tag vX.Y.Z
```

### Step 6 — Push

```bash
git push origin main
git push origin vX.Y.Z
```

Do not ask for permission to push. Just push.

### Step 7 — Watch CI

- The `Tests` workflow runs on push to main (Python 3.11 + 3.14)
- The `Release` workflow triggers on the version tag push — it re-runs
  tests, verifies `APP_VERSION` matches the tag, verifies
  `docs/releases/vX.Y.Z.md` exists, builds a ZIP, and creates a GitHub
  Release with the release notes as the body
- Wait for both workflows to complete and confirm they are green
- Confirm the GitHub Release was published at
  `https://github.com/WeirDave/LensLedger/releases/tag/vX.Y.Z`

### Step 8 — Sync primary checkout (worktree sessions only)

If working from a git worktree (not the primary folder), run:

```bash
cd "C:\Dropbox\Websites\02 - Tools and Apps\GitHub Projects\LensLedger"
git pull --ff-only
```

If the primary checkout is dirty or diverged, stop and flag it — do not force.

### Step 9 — Report

After everything is done, give a brief summary:
- What shipped (version, one-line summary)
- Link to the GitHub Release
- CI status

## Project structure

```
src/            Python backend (photo_search.py is the main server)
web/            Frontend (HTML templates inline in photo_search.py,
                JS in web/js/, CSS in web/css/)
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
- Don't ask for permission to push — the user wants autonomous releases
