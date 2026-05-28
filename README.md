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

A `.canvas-manifest.toml` file is written to your course repo to track Canvas IDs. Commit it so collaborators share the same mapping.

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
Usage: github-to-canvas [OPTIONS]

  Sync a Markdown course repo to Canvas LMS.

Options:
  --repo    PATH  Path to the course content repo  [required]
  --config  PATH  Path to canvas.toml config file  [default: canvas.toml]
  --help          Show this message and exit.
```

### Basic sync

```bash
# config file is canvas.toml in the current directory
github-to-canvas --repo ./my-course

# explicit config path
github-to-canvas --repo ./my-course --config ~/secrets/canvas.toml
```

### Typical workflow

```bash
# 1. Pull latest content
cd my-course && git pull

# 2. Sync to Canvas
CANVAS_API_TOKEN=your-token-here \
  github-to-canvas --repo . --config canvas.toml

# 3. Commit the updated manifest
git add .canvas-manifest.toml
git commit -m "sync: update Canvas IDs"
git push
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

If the manifest is lost you can re-run the tool against a fresh Canvas course, or re-create it manually from Canvas IDs.

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (for install/run)
- [Pandoc](https://pandoc.org/) (system install)
- A Canvas LMS account with API access
