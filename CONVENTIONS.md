# LensLedger — conventions for AI coding assistants

This file is read by Aider and other tools that look for `CONVENTIONS.md`.
The canonical source is `CLAUDE.md` in this same repo — both files describe
the same rules. If they ever conflict, `CLAUDE.md` wins.

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

After every fix or feature lands on `main` with tests green, perform the
full release ceremony. Every fix ships. Do not ask for permission to push.

1. **Bump the version** — edit `src/product.py`: increment `APP_VERSION`,
   set `APP_RELEASE_DATE` to today (`YYYY-MM-DD`)
2. **Update CHANGELOG.md** — add a new `## X.Y.Z — YYYY-MM-DD` entry at the
   top (under the header). Concise past-tense bullets describing user-visible
   changes. See existing entries for tone.
3. **Write release notes** — create `docs/releases/vX.Y.Z.md`:
   - Motivation/context (what was wrong before)
   - What changed and how it works now
   - `## Fixed during testing` section if you found and fixed a bug
   - `## Verified` section listing test suite results and any manual checks
   - See `docs/releases/v0.33.0.md` for a good example
4. **Commit** — stage all changed files, commit as
   `LensLedger vX.Y.Z — short summary`
5. **Print the finish commands** — after committing, print the following
   block so the user can copy-paste it into their terminal to finalize
   the release:

   ```
   To finalize the release, run these commands:

   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```

   Replace `X.Y.Z` with the actual version number you just committed.

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
