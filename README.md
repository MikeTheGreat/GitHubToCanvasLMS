# github-to-canvas

Sync a Markdown course repository to [Canvas LMS](https://www.instructure.com/canvas).

Write your course content as Markdown files in a Git repository. Run this tool to convert them to HTML and publish them to Canvas — pages, assignments, discussion topics, and modules.

---

## How it works

```
course-repo/
├── pages/
│   └── syllabus.md
├── assignments/
│   └── week1.md
├── discussions/
│   └── week1-intro.md
├── modules/
│   └── week-1.md
├── snippets/          ← reusable Markdown fragments
│   └── office-hours.md
└── assets/
    └── images/
        └── diagram.png
```

On each run the tool:

1. Uploads everything in `assets/` to Canvas Files
2. Converts each `.md` file to HTML via Pandoc and uploads it to Canvas
3. Rewrites cross-links between files to the correct Canvas URLs
4. Syncs `modules/` last (after all content has Canvas IDs)

Files are skipped if their local modification time is not newer than the `last_synced` timestamp in the manifest — so unchanged files cost nothing on repeat runs.

A `.canvas-manifest.toml` file is written to your course repo to track Canvas IDs and sync times. Commit it so collaborators share the same mapping.

---

## Installation

### Recommended: install as a `uv` tool

```bash
uv tool install git+https://github.com/your-org/github-to-canvas
```

Then run from anywhere:

```bash
github-to-canvas --repo ./my-course --config ./canvas.toml
```

### Run without installing (one-off)

```bash
uvx --from git+https://github.com/your-org/github-to-canvas github-to-canvas \
  --repo ./my-course \
  --config ./canvas.toml
```

### Install for development

```bash
git clone https://github.com/your-org/github-to-canvas
cd github-to-canvas
uv venv
uv pip install -e ".[dev]"
```

After that, run the CLI directly without activating the venv:

```bash
uv run github-to-canvas --repo ./my-course --config ./canvas.toml
```

Or activate the venv first and then call the command normally:

```bash
source .venv/bin/activate
github-to-canvas --repo ./my-course --config ./canvas.toml
```

Run the tests the same way:

```bash
uv run pytest
# or, with the venv active:
pytest
```

> **Pandoc required.** Install it from [pandoc.org](https://pandoc.org/installing.html) or via your package manager (`brew install pandoc`, `apt install pandoc`, etc.).

---

## Configuration

### `canvas.toml`

Place this file in your course repo (or pass `--config` to point elsewhere). Commit it — it contains no secrets.

```toml
base_url  = "https://yourschool.instructure.com"
course_id = 12345

[auth]
# Fallback token for local use only. Prefer the CANVAS_API_TOKEN env var.
# Never commit a real token to version control.
api_token = ""
```

### API token

Get your token from Canvas: **Account → Settings → New Access Token**.

Pass it as an environment variable (recommended):

```bash
export CANVAS_API_TOKEN="your-token-here"
github-to-canvas --repo ./my-course
```

Or put it in the `[auth]` block of `canvas.toml` for local-only use (add `canvas.toml` to `.gitignore` if you do this).

---

## Usage

```
Usage: github-to-canvas update [OPTIONS]

  Sync a Markdown course repo to Canvas LMS.

Options:
  --repo PATH                     Path to the course content repo  [required]
  --config PATH                   Path to canvas.toml  [default: <repo>/canvas.toml]
  --force-uploads                 Re-upload all files even if unchanged since last sync
  --force-overwrite               Skip Canvas timestamp check; always overwrite Canvas
  -t, --target-recursively FILE   Comma-separated files; each is synced plus all resources
                                  it transitively references (BFS). Skips the full sync.
  -s, --single-target FILE        Comma-separated files to sync without traversing references.
                                  Runs after -t. Skips the full sync.
  --help                          Show this message and exit.
```

### Full sync (default)

Syncs every file in the course repo. Files that haven't changed since their last `last_synced` manifest timestamp are skipped automatically.

```bash
# canvas.toml lives inside the repo
github-to-canvas update --repo ./my-course

# explicit config path
github-to-canvas update --repo ./my-course --config ~/secrets/canvas.toml

# force re-upload of everything regardless of timestamps
github-to-canvas update --repo ./my-course --force-uploads
```

### Typical full-sync workflow

```bash
# 1. Pull latest content
cd my-course && git pull

# 2. Sync to Canvas (only changed files are uploaded)
CANVAS_API_TOKEN=your-token-here \
  github-to-canvas update --repo . --config canvas.toml

# 3. Commit the updated manifest
git add .canvas-manifest.toml
git commit -m "sync: update Canvas IDs"
git push
```

---

## Selective sync

Use `-t` or `-s` when you only want to sync part of the course. Both flags skip the full course sync.

### `-t` — recursive (BFS)

Syncs the specified file(s) and every resource they transitively reference, following links depth-first until no new local files are found.

```bash
# Re-sync a module and everything it links to
github-to-canvas update --repo . -t modules/week-1.md

# Re-sync two modules and all their dependencies
github-to-canvas update --repo . -t modules/week-1.md,modules/week-2.md

# Force re-upload even for unchanged files
github-to-canvas update --repo . -t modules/week-1.md --force-uploads
```

**What counts as a reference:**

- Content files (pages, assignments, discussions): all local `<img src>` and `<a href>` targets in the converted HTML
- Module files: all items listed in the module body
- Asset files: no outgoing references (assets are leaf nodes)

**Module ordering:** modules discovered during BFS are synced last, after all the content they reference has been uploaded and has Canvas IDs — the same guarantee as a full sync.

### `-s` — single target (no traversal)

Syncs only the listed file(s), with no recursive traversal. Useful when you know exactly which files changed and don't need their dependencies re-synced.

```bash
# Re-sync one page
github-to-canvas update --repo . -s pages/syllabus.md

# Re-sync several specific files
github-to-canvas update --repo . -s assignments/week1.md,discussions/week1-intro.md
```

### Combining `-t` and `-s`

`-t` runs first (full BFS). `-s` runs after, independently. If `-t` already uploaded a file and updated its manifest timestamp, `-s` will skip it automatically via the timestamp check — no special coordination needed.

```bash
# Re-sync a module and all its content (via -t),
# then also sync an unrelated page (via -s)
github-to-canvas update --repo . \
  -t modules/week-3.md \
  -s pages/office-hours.md
```

---

## Content file format

All content files use YAML frontmatter followed by a Markdown body.

### Page (`pages/`)

```markdown
---
title: "Syllabus"
editing_roles: teachers
published: true
---

# Course Syllabus

Welcome to the course. See [Week 1 Assignment](../assignments/week1.md).
```

### Assignment (`assignments/`)

```markdown
---
title: "Week 1 Problem Set"
points_possible: 50
due_at: "2025-02-01T23:59:00-05:00"
submission_types: ["online_upload"]
published: true
---

Submit a PDF of your solutions by the deadline.
```

### Discussion (`discussions/`)

```markdown
---
title: "Introduce Yourself"
require_initial_post: true
published: true
---

Tell us your name, your background, and what you hope to get from this course.
```

### Module (`modules/`)

Module files don't have a body that becomes HTML. The body lists content items and optional sub-headers.

```markdown
---
title: "Week 1: Introduction"
published: true
unlock_at: "2025-01-20T00:00:00-05:00"
---

## Readings

- [Syllabus](../pages/syllabus.md)

## Work

- [Week 1 Assignment](../assignments/week1.md)
- [Week 1 Discussion](../discussions/week1-intro.md)
```

### Snippets

Any Markdown link whose target resolves inside `snippets/` is replaced with the snippet's content before conversion. Useful for office hours, shared policies, etc.

```markdown
<!-- in pages/syllabus.md -->
[My Office Hours](../snippets/office-hours.md)
```

```markdown
<!-- snippets/office-hours.md -->
Office hours are Tuesdays 2–4 pm in Building 7, Room 201.
```

---

## Manifest file

The tool creates `.canvas-manifest.toml` in your course repo. Commit this file.

```toml
# .canvas-manifest.toml — commit this so collaborators share Canvas IDs

["pages/syllabus.md"]
canvas_id   = 11111
canvas_type = "page"
canvas_url  = "syllabus"
last_synced = "2025-02-01T10:00:00+00:00"

["assignments/week1.md"]
canvas_id   = 98765
canvas_type = "assignment"
last_synced = "2025-02-01T10:01:00+00:00"

["modules/week-1.md"]
canvas_id        = 55555
canvas_type      = "module"
last_synced      = "2025-02-01T10:02:00+00:00"
canvas_item_ids  = {"pages/syllabus.md" = 201, "assignments/week1.md" = 202}
```

The `last_synced` field is used to skip files that haven't changed since they were last uploaded — a file is re-uploaded only when its local modification time is newer than `last_synced`. Use `--force-uploads` to bypass this check.

If the manifest is lost you can re-run the tool against a fresh Canvas course, or re-create it manually from Canvas IDs.

---

## Canvas overwrite protection

By default, before uploading any item that already exists in Canvas the tool fetches its `updated_at` timestamp from Canvas and compares it to the local file's modification time. If Canvas is newer — meaning someone edited the item directly in Canvas after the last sync — the upload is **skipped**.

At the end of the run, all skipped items are printed together as a single list:

```text
The following resources were NOT uploaded because Canvas has a newer version.
Review these files and re-upload manually if needed (use --force-overwrite to skip this check):
  pages/syllabus.md
  assignments/week2.md
```

This lets you review the diverged items before deciding what to do:

- **Keep the Canvas version** — update your local file to match Canvas, then sync again.
- **Keep the local version** — use `--force-overwrite` to overwrite Canvas regardless:

```bash
github-to-canvas update --repo . --force-overwrite
```

`--force-overwrite` skips the Canvas timestamp check entirely. This is also faster (no extra API calls) when you know the local repo is the authoritative source and don't need the protection.

The two flags are independent:

| | `--force-uploads` | `--force-overwrite` |
| --- | --- | --- |
| Bypasses local `mtime` check | Yes | No |
| Bypasses Canvas timestamp check | No | Yes |
| Extra Canvas API calls (per item) | Same | Fewer (check skipped) |

Use both flags together to re-upload and overwrite everything unconditionally.

---

## IMSCC import

Import an existing Canvas course export (`.imscc` file) into a local Markdown repo:

```bash
github-to-canvas import course-export.imscc ./my-course-repo
```

This converts pages, assignments, and discussions to Markdown files and writes a `canvas.toml` skeleton. Quizzes are skipped.

### Verifying the import

After importing, run the coverage checker to spot-check that content from the IMSCC made it into the Markdown repo.  This script is **not** part of the automated test suite — run it manually after an import:

```bash
python scripts/check_imscc_coverage.py course-export.imscc ./my-course-repo
```

Options:

| Flag | Default | Description |
| --- | --- | --- |
| `--min-words N` | 10 | Minimum fragment length; increase to reduce coincidental matches |
| `--seed N` | 42 | Random seed — change to sample different fragments |
| `--categories LIST` | `assignment,discussion,page,syllabus` | Comma-separated types to check |

Example output:

```text
Parsing IMSCC manifest...
Building Markdown corpus from: ./my-course-repo

Checking 47 resources...

  [ OK ] assignment: 'Week 1 Problem Set'
  [ OK ] page: 'Syllabus'
  [MISS] discussion: 'Introduce Yourself'
  [SKIP] syllabus: 'Syllabus'  (< 10 words)
  ...

============================================================
Results: 44 OK  |  1 MISSING  |  2 skipped (too short)  |  47 total checked
============================================================

1 MISSING fragment(s):

  Type:    discussion
  Title:   Introduce Yourself
  Source:  g_discussion_1.xml
  Output:  discussions/introduce-yourself.md
  Context: ...Tell us your >>>>name, your background, and what you hope<<<< to get from...
```

Exit code 0 means all sampled fragments were found. Exit code 1 means at least one was missing.

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (for install/run)
- [Pandoc](https://pandoc.org/) (system install)
- A Canvas LMS account with API access
