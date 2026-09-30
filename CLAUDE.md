# LensLedger — AI assistant instructions

These instructions apply to every AI coding assistant working in this repo
(Claude Code, Aider, Cursor, etc.). Follow them exactly.

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

### Step 8 — Leave the primary checkout alone

Do not pull, reset or otherwise touch
`C:\Dropbox\Websites\02 - Tools and Apps\GitHub Projects\LensLedger`.
It is David's own working copy and he updates it himself. See "Session
slots" below.

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

## Session slots

**A session works in one of six permanent worktrees, never in the primary
checkout.**

    C:\ll-worktrees\slot-1   branch claude/slot-1
    ...
    C:\ll-worktrees\slot-6   branch claude/slot-6

The Claude desktop app allows one active session per directory, and a
finished session holds its folder until it is archived. Six fixed slots give
somewhere to start work without David archiving sessions first, and because
they are reused rather than created per task, nothing accumulates.

**The primary checkout is David's.**
`C:\Dropbox\Websites\02 - Tools and Apps\GitHub Projects\LensLedger` is where
he opens sessions himself, and it has to stay available to him. No session
edits, stages, commits, pulls, stashes or checks out anything there. If a
session is started there anyway, it does its work in a free slot by absolute
path and leaves the primary checkout exactly as it found it.

### Starting: confirm the slot is clean, and say so

The previous occupant may have left something. From inside the slot:

```bash
git fetch origin
git status --porcelain            # must print nothing
git cherry origin/main HEAD       # any line starting "+" is unshipped
git rev-parse --abbrev-ref HEAD   # must be claude/slot-N
```

A line starting `-` from `git cherry` is already on `main` under a different
id (a rebase does that) and is shipped.

- **Clean:** `git reset --hard origin/main`, then start, and say in the first
  report that the slot was clean and at which `origin/main` commit.
- **Not clean:** never discard it. Keep it on a local branch, then reset:

  ```bash
  git add -A && git commit -m "WIP left in slot-N"   # only if status was dirty
  git branch rescue/slot-N-<yyyymmdd-hhmm>
  git reset --hard origin/main
  git clean -fd
  ```

  Report what was found and the branch name. Rescue branches are never
  pushed - check what is in one before anything leaves the machine, per Rule
  zero.

### Working and pushing

The slot branch is never pushed; `main` is. Rebase before pushing, and run the
suite again after the rebase, because a rebase replays your commits onto code
you have not tested against:

```bash
PYTHONPATH=src python -m unittest discover -s tests
git fetch origin
git rebase origin/main
PYTHONPATH=src python -m unittest discover -s tests
git push origin HEAD:main
```

Tag the commit that was pushed, after the rebase, never before it - a rebase
gives the commit a new id. If the push is rejected because `main` moved,
fetch, rebase and test again. Never force-push `main`.

Take the version number from `git show origin/main:src/product.py` just
before bumping, not from what the slot had when the session started. Two
sessions can bump to the same number and git will not report a conflict,
because both wrote the same bytes.

The stash stack is shared by every slot and the primary checkout, so a bare
`git stash pop` can take someone else's entry. Prefer a WIP commit.

### Finishing: reset the slot and leave it

**Slots are never deleted.** Once the work is pushed:

```bash
git fetch origin
git cherry origin/main HEAD       # nothing starting "+"
git reset --hard origin/main
git clean -fd
git status --porcelain            # prints nothing
```

Leave the folder in place for the next session. Do not run
`git worktree remove`, delete the `claude/slot-N` branch, or delete the
folder. Stop any process the session started from inside the slot first; a
background command that never returned keeps a handle on the folder.

If a slot is missing or broken, recreate it under the same name:
`git worktree prune -v`, then
`git worktree add -B claude/slot-N C:\ll-worktrees\slot-N origin/main`
after checking `git cherry origin/main claude/slot-N` shows nothing unshipped.

### Rule zero in a slot

`git clean -fd` removes untracked files but leaves ignored ones, so anything a
session put in an ignored path survives into the next occupant's slot. That
includes `.test-library/`, which holds an index of David's real photos and
real folder names: it is ignored (`.gitignore`) and must never be committed,
copied into a slot, or used as a fixture. `.test-library-synthetic/`, built by
`tools/make_test_library.py`, is the one to test against. At the end of a
session, `git status --porcelain --ignored` should show nothing real.

`C:\ll-worktrees` holds the six slots and nothing else. Session scratch goes
in the session's scratchpad. Older per-task worktrees under
`.claude/worktrees/` belong to the session that made them; if one is idle and
its branch is merged (`git branch -d` succeeds, not `-D`), removing it is
fine. Report what was found and removed rather than cleaning quietly.
