# github-to-canvas

Sync a Markdown course repository to [Canvas LMS](https://www.instructure.com/canvas).

Write your course content as Markdown files in a Git repository. Run this tool to convert them to HTML and publish them to Canvas — pages, assignments, discussion topics, and modules.


## Important Gotch'yas:
- If you change a Module Markdown file then all the links in that module will be invalidated.  
- If you want to move or rename a file please use the `mv` subcommand - it'l adjust links, the manifest cache, etc, for you.



## Contents

- [github-to-canvas](#github-to-canvas)
  - [Important Gotch'yas:](#important-gotchyas)
  - [Contents](#contents)
  - [How it works](#how-it-works)
  - [Installation](#installation)
    - [Requirements](#requirements)
    - [Recommended: install as a `uv` tool](#recommended-install-as-a-uv-tool)
    - [Run without installing (one-off)](#run-without-installing-one-off)
    - [Install for development](#install-for-development)
    - [Installing Pandoc](#installing-pandoc)
  - [Configuration](#configuration)
    - [`canvas.toml`](#canvastoml)
    - [API token](#api-token)
  - [Usage](#usage)
  - [Canvas overwrite protection](#canvas-overwrite-protection)
    - [Full sync (default)](#full-sync-default)
    - [Typical full-sync workflow](#typical-full-sync-workflow)
  - [Selective sync](#selective-sync)
    - [`-t` — recursive (BFS)](#-t--recursive-bfs)
    - [`-s` — single target (no traversal)](#-s--single-target-no-traversal)
    - [Combining `-t` and `-s`](#combining--t-and--s)
  - [Removing content (`prune`)](#removing-content-prune)
  - [Moving and renaming files (`mv`)](#moving-and-renaming-files-mv)
  - [Content file format](#content-file-format)
    - [`course_settings.toml`](#course_settingstoml)
      - [Centralized due dates](#centralized-due-dates)
    - [Syllabus (`course_settings/syllabus.md`)](#syllabus-course_settingssyllabusmd)
    - [Rubrics (`course_settings/rubrics.toml`)](#rubrics-course_settingsrubricstoml)
    - [Other `course_settings/` files (import-only)](#other-course_settings-files-import-only)
    - [Page (`pages/`)](#page-pages)
    - [Assignment (`assignments/`)](#assignment-assignments)
    - [Discussion (`discussions/`)](#discussion-discussions)
    - [Announcement (`announcements/`)](#announcement-announcements)
    - [Module (`modules/`)](#module-modules)
    - [Quiz (`quizzes/`)](#quiz-quizzes)
    - [Question banks (`question_banks/`)](#question-banks-question_banks)
    - [Snippets](#snippets)
      - [Inline snippets and the `CANVAS_COURSE_REFERENCE` snippet](#inline-snippets-and-the-canvas_course_reference-snippet)
      - [Shared frontmatter via `PASTE_SNIPPET_INTO_FRONTMATTER`](#shared-frontmatter-via-paste_snippet_into_frontmatter)
  - [Manifest file](#manifest-file)
    - [Deleting a file in Canvas](#deleting-a-file-in-canvas)
  - [IMSCC import](#imscc-import)
    - [Verifying the import](#verifying-the-import)
  - [Listing content titles (`list-titles`)](#listing-content-titles-list-titles)
  - [Resolving external-tool labels (`create-tool-aliases`)](#resolving-external-tool-labels-create-tool-aliases)
    - [Workflow](#workflow)

---

## How it works

```
course-repo/
├── pages/                  ← subfolders OK (flattened to Canvas)
│   ├── syllabus.md
│   └── week1/
│       └── notes.md
├── assignments/            ← subfolders OK
│   └── week1.md
├── discussions/            ← subfolders OK
│   └── week1-intro.md
├── announcements/           ← posted only when published: true
│   └── midterm-reminder.md
├── modules/
│   └── week-1.md
├── snippets/               ← reusable Markdown fragments
│   └── office-hours.md
└── assets/
    └── images/
        └── diagram.png
```

On each run the tool:

1. Applies `course_settings.toml` (name, dates, grading standards, assignment groups, policies)
2. Uploads `course_settings/syllabus.md` as the course syllabus body
3. Uploads everything in `assets/` to Canvas Files
4. Converts each `.md` in `pages/`, `assignments/`, `discussions/`, `announcements/` (including subfolders) to HTML via Pandoc and uploads
5. Syncs `quizzes/` (Classic Quizzes API) and `question_banks/`
6. Rewrites cross-links between files to correct Canvas URLs
7. Syncs `modules/` last (after all content has Canvas IDs)

Files are skipped if their local modification time is older than the `last_synced` timestamp in the manifest — so unchanged files cost nothing on repeat runs.

Files matched by a `.gitignore` (or an optional `.canvasignore`, same syntax) at the repo root are never uploaded — handy for excluding editor backups and temp files such as Word's `~$*.docx`.

A `.canvas-manifest.toml` file is written to your course repo to track Canvas IDs and sync times. Commit it so collaborators share the same mapping.

---

## Installation

### Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (for install/run)
- Pandoc — either install it system-wide **or** run `github-to-canvas setup` after installing the tool (see below)
- A Canvas LMS account with API access

### Recommended: install as a `uv` tool

```bash
uv tool install git+https://github.com/MikeTheGreat/GitHubToCanvasLMS
```

Then run from anywhere:

```bash
github-to-canvas ./my-course
```

### Run without installing (one-off)

```bash
uvx --from git+https://github.com/MikeTheGreat/GitHubToCanvasLMS github-to-canvas \
  ./my-course
```

One-off `uvx` runs require a system-wide Pandoc install — `github-to-canvas setup` cannot help here because `uvx` uses a temporary environment that is discarded after the run.

### Install for development

```bash
git clone https://github.com/MikeTheGreat/GitHubToCanvasLMS
cd GitHubToCanvasLMS
uv venv
uv pip install -e ".[dev]"
```

After that, run the CLI directly without activating the venv:

```bash
uv run github-to-canvas ./my-course
```

Or activate the venv first and then call the command normally:

```bash
source .venv/bin/activate
github-to-canvas ./my-course
```

Run the tests the same way:

```bash
uv run pytest
# or, with the venv active:
pytest
```

### Installing Pandoc

You have two options:

- **System-wide install** (required for `uvx` one-off runs): install from [pandoc.org](https://pandoc.org/installing.html) or via your package manager (`brew install pandoc`, `apt install pandoc`, etc.).
- **Tool-local install** (after `uv tool install` only): run the `setup` subcommand to download Pandoc into the tool's own environment:

  ```bash
  github-to-canvas setup
  ```

  This places the Pandoc binary alongside the tool so no separate system installation is needed. It is a no-op if Pandoc is already found.

---

## Configuration

### `canvas.toml`

Place this file in your course repo's `course_settings/` folder (i.e. `course_settings/canvas.toml`), or pass `--config` to point elsewhere. Commit it — it contains no secrets.

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
github-to-canvas ./my-course
```

Or put it in a `.env` file in your working directory (loaded automatically on startup):

```bash
# .env
CANVAS_API_TOKEN=your-token-here
```

Or put it in the `[auth]` block of `course_settings/canvas.toml` for local-only use (add `course_settings/canvas.toml` to `.gitignore` if you do this).

---

## Usage

```
Usage: github-to-canvas update [OPTIONS] REPO

  Sync a Markdown course repo to Canvas LMS.

Arguments:
  REPO                            Path to the course content repo  [required]

Options:
  --config PATH                   Path to canvas.toml  [default: <repo>/course_settings/canvas.toml]
  --force-uploads                 Re-upload all files even if unchanged since last sync
  --force-overwrite               Skip Canvas timestamp check; always overwrite Canvas
  -t, --target-recursively FILE   Comma-separated files; each is synced plus all resources
                                  it transitively references (BFS). Skips the full sync.
  -s, --single-target FILE        Comma-separated files to sync without traversing references.
                                  Runs after -t. Skips the full sync.
  --help                          Show this message and exit.
```
**Warning**: In order to get changes to the left-hand course navigation column you may need to go
to Settings ➡ Navigation and then click the 'Save' button.

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
github-to-canvas update . --force-overwrite
```

`--force-overwrite` skips the Canvas timestamp check entirely. This is also faster (no extra API calls) when you know the local repo is the authoritative source and don't need the protection.

The two flags are independent:


|                                   | `--force-uploads` | `--force-overwrite`   |
| --------------------------------- | ----------------- | --------------------- |
| Bypasses local`mtime` check       | Yes               | No                    |
| Bypasses Canvas timestamp check   | No                | Yes                   |
| Extra Canvas API calls (per item) | Same              | Fewer (check skipped) |

Use both flags together to re-upload and overwrite everything unconditionally.

---

### Full sync (default)

Syncs every file in the course repo. Files that haven't changed since their last `last_synced` manifest timestamp are skipped automatically.

```bash
# canvas.toml lives in the repo's course_settings/ folder (default)
github-to-canvas update ./my-course

# explicit config path
github-to-canvas update ./my-course --config ~/secrets/canvas.toml

# force re-upload of everything regardless of timestamps
github-to-canvas update ./my-course --force-uploads
```

### Typical full-sync workflow

```bash
# 1. Pull latest content
cd my-course && git pull

# 2. Sync to Canvas (only changed files are uploaded)
CANVAS_API_TOKEN=your-token-here \
  github-to-canvas update .

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
github-to-canvas update . -t modules/week-1.md

# Re-sync two modules and all their dependencies
github-to-canvas update . -t modules/week-1.md,modules/week-2.md

# Force re-upload even for unchanged files
github-to-canvas update . -t modules/week-1.md --force-uploads
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
github-to-canvas update . -s pages/syllabus.md

# Re-sync several specific files
github-to-canvas update . -s assignments/week1.md,discussions/week1-intro.md
```

### Combining `-t` and `-s`

`-t` runs first (full BFS). `-s` runs after, independently. If `-t` already uploaded a file and updated its manifest timestamp, `-s` will skip it automatically via the timestamp check — no special coordination needed.

```bash
# Re-sync a module and all its content (via -t),
# then also sync an unrelated page (via -s)
github-to-canvas update . \
  -t modules/week-3.md \
  -s pages/office-hours.md
```

---

## Removing content (`prune`)

`update` never deletes anything from Canvas — if you delete or rename a Markdown
file locally, the item it created stays in Canvas and a stale entry remains in the
manifest. Use `prune` to clean those up.

```text
Usage: github-to-canvas prune [OPTIONS] REPO

  Delete or unpublish Canvas items whose local source file no longer exists.

Options:
  --config PATH    Path to canvas.toml  [default: <repo>/course_settings/canvas.toml]
  --delete         Delete the orphaned items from Canvas
  --unpublish      Unpublish (set published=False) the orphaned items on Canvas
  --manifest-only  Remove orphaned entries from the local manifest only; never
                   touch Canvas
  --help           Show this message and exit.
```

`prune` looks for **orphans** — manifest entries whose local file is gone — which
covers both deleting a file and renaming one (the old path is orphaned; the new
name syncs as a fresh item on the next `update`).

You must pass exactly one of `--delete`, `--unpublish`, or `--manifest-only`;
there is no default, so the intent is always explicit. Changes are applied
**immediately** — there is no preview or confirmation prompt — so commit your
manifest first if you want an easy way to undo.

```bash
# Delete every orphaned item from Canvas
github-to-canvas prune ./my-course --delete

# ...or hide them instead of deleting (where the type supports it)
github-to-canvas prune ./my-course --unpublish

# ...or just clear stale manifest entries without touching Canvas
github-to-canvas prune ./my-course --manifest-only
```

Pages, assignments, discussions, quizzes, and modules can be either deleted or
unpublished. Files can only be deleted (Canvas files have no published flag).
Question banks and course-level bookkeeping entries (syllabus, course settings,
module order) are skipped with a warning and keep their manifest entry. A failure
on one item is reported but does not stop the rest of the run.

If a `--delete`/`--unpublish` orphan is **already gone** on Canvas (you deleted it
by hand, or a previous run did), that's treated as success and its stale manifest
entry is dropped — so a half-finished cleanup won't keep failing on the same item.

`--manifest-only` is a local-only escape hatch: it drops every orphaned manifest
entry **without contacting Canvas at all** (no API token or course connection
needed). Use it to clear entries the other modes leave behind — items you already
removed from Canvas manually, unsupported types, or in-use protected resources. It
ignores the in-use protection and type rules above because it never changes
anything on Canvas; it only forgets the local bookkeeping.

---

## Moving and renaming files (`mv`)

Use `mv` to move or rename files and directories within your course repo.
It handles all the bookkeeping so nothing breaks on the next `update`:

```text
Usage: github-to-canvas mv [OPTIONS] SRC DEST

  Move or rename a file/directory, updating the manifest and all references.

Options:
  -n, --noop     Show what would change without making any modifications.
  -v, --verbose  Print each individual change (moved file, updated link, etc.).
  --help         Show this message and exit.
```

`mv` updates:

- The file/directory on disk (via `git mv` when inside a git repo and the
  source is git-tracked; falls back to a plain filesystem move otherwise,
  e.g. for files not yet `git add`ed)
- `.canvas-manifest.toml` — manifest keys and `canvas_item_ids` in module entries
- All Markdown files — relative links and snippet references
- `module_order.toml` — if a module file is renamed
- `course_settings.toml` — `dashboard_image` and `front_page`, if the file they point to is renamed

**Examples:**

```bash
# Rename a page
github-to-canvas mv pages/old-name.md pages/new-name.md

# Rename an asset directory (updates all references across the repo)
github-to-canvas mv assets/Lecture-Related/Unit-01 assets/lecture-related/unit-01

# Rename a quiz folder (also renames the inner .md to match)
github-to-canvas mv quizzes/old-quiz quizzes/new-quiz

# Preview what would change without doing anything
github-to-canvas mv --noop pages/old.md pages/new.md
```

**Restrictions:**

- Both source and destination must be within the same course repo
- Cannot move across content-type directories (e.g. `pages/` to `assignments/`)
- The destination's parent directory must already exist — this prevents accidental
  renames of intermediate path components due to typos. To rename multiple levels,
  rename them one at a time.
- The repo root is auto-detected by looking for `course_settings/course_settings.toml`

This command is purely local — it never contacts Canvas. Run `update` afterward
to push the changes.

---

## Content file format

Content files (`pages/`, `assignments/`, `discussions/`, `modules/`, quizzes, and
questions) use **YAML frontmatter** followed by a **Markdown body**. Course-level
settings live in `course_settings.toml` and use **TOML**.

> **Subfolder support:** `pages/`, `assignments/`, and `discussions/` can contain
> arbitrarily nested subfolders for local organisation. Canvas uses a flat namespace,
> so the tool flattens everything on upload. If two files within the same content
> type share the same title (from frontmatter, or filename if no title is set), the
> tool will print an error and abort — rename one of them or give them different
> `title:` values. Module and quiz directories remain flat.

Every example below lists **all available options** with an inline comment for each.
To create a new file, copy the whole block and then delete, edit, or replace the
options you don't want. **Every field is optional** unless a comment says otherwise —
omitted fields are simply left unchanged in Canvas (and `title` falls back to the
filename). All dates are ISO 8601 strings; include a timezone offset (e.g. `-08:00`)
to avoid surprises.

> **Tip — adding comments to Markdown files:** Pandoc's raw attribute block syntax
> lets you embed text that is completely ignored during conversion and will never
> appear in Canvas output:
>
> ````markdown
> ```{=comment}
> This text is completely ignored by Pandoc.
> It will never appear in any output format.
> ```
> ````
>
> This works everywhere, including the structured lists the tool parses itself:
> quiz question links, module items, and question `## Answers` entries inside a
> `{=comment}` block are commented out (not uploaded, not published). Any word
> works after the `=` (e.g. `{=comment-until-fall}`) — everything except
> `{=html}` is treated as a comment.
>
> Relatedly, regular fenced code blocks are always literal: links, headings,
> and snippet references (`$path.md$`) inside ```` ``` ```` fences are shown
> as-is, never expanded or treated as quiz questions / module items.

### `course_settings.toml`

Placed inside the `course_settings/` folder (i.e. `course_settings/course_settings.toml`).
Drives the course's own settings: identity, dates, visibility, grading scheme,
assignment groups, and policies. Applied before any content is uploaded.

```toml
# course_settings/course_settings.toml — TOML syntax. Every key is optional.

# ── Course identity & display ────────────────────────────────────────────
title        = "Intro to Programming"          # Canvas course name
course_code  = "CS 101"                        # short code shown in the UI
start_at     = "2025-01-06T00:00:00-08:00"     # course start date
conclude_at  = "2025-03-20T23:59:00-07:00"     # course end date
default_view = "wiki"   # landing page: feed | wiki | modules | syllabus | assignments
front_page   = "pages/welcome.md"  # wiki home page (only used when default_view = "wiki")
license      = "private"  # private | public_domain | cc_by | cc_by_sa | cc_by_nc
                          #   | cc_by_nc_sa | cc_by_nd | cc_by_nc_nd
dashboard_image = "assets/course-banner.png"    # image shown on the Canvas Dashboard card

# ── Visibility & enrollment ──────────────────────────────────────────────
is_public               = false   # course visible to the public
is_public_to_auth_users = false   # visible to any logged-in user
public_syllabus         = false   # syllabus visible to the public
public_syllabus_to_auth = false   # syllabus visible to any logged-in user
open_enrollment         = false
self_enrollment         = false

# ── Grades ───────────────────────────────────────────────────────────────
grading_standard_enabled = true   # use a letter-grade scheme
# grading_standard_id is set automatically from [[grading_standards]] below.
# Only set it by hand to point at a scheme that already exists in Canvas:
# grading_standard_id    = 12345
hide_final_grade         = false  # hide running total from students
hide_distribution_graphs = false  # hide grade-distribution graphs
# Weight the final grade by assignment group ("percent") or grade on raw
# points ("equal"). Usually you can omit this: if any [[assignment_groups]]
# entry below has a group_weight, weighting is turned on automatically.
# Set it explicitly only to force weighting OFF while keeping the weights:
# group_weighting_scheme = "equal"

# ── Discussions / forums / wiki ──────────────────────────────────────────
allow_student_discussion_topics  = true
allow_student_discussion_editing = true
allow_student_forum_attachments  = true
allow_student_wiki_edits         = false
lock_all_announcements           = false

# ── Announcements on the home page ───────────────────────────────────────
show_announcements_on_home_page = false
home_page_announcement_limit    = 3

# ── Access windows ───────────────────────────────────────────────────────
restrict_student_future_view         = false  # hide course before start_at
restrict_student_past_view           = false  # hide course after conclude_at
restrict_enrollments_to_course_dates = false

# ── Miscellaneous ────────────────────────────────────────────────────────
syllabus_course_summary = true    # show the auto course summary on the syllabus
usage_rights_required   = false   # require usage rights on uploaded files
enable_course_paces     = false

# ── Course navigation (the left-hand sidebar) ────────────────────────────
# Inline array of objects, one per tab, in the order they should appear.
# Name each tab with `id` or `label` (they're interchangeable) — just type the
# tab's name as you see it in Canvas, whether it's a built-in tab or an external
# tool; matching is case-insensitive. Omit `hidden` to leave a tab visible.
#
# IMPORTANT: this is a top-level key, so it MUST appear BEFORE any [section]
# or [[section]] header below (e.g. [late_policy], [[grading_standards]]).
# In TOML, every key after a section header belongs to that section — put
# tab_configuration here, above them, or it will be silently ignored.
tab_configuration = [
    { id = "Home" },
    { id = "Modules" },
    { id = "Assignments" },
    { id = "Grades" },
    { id = "Zoom" },                    # an external (LTI) tool — same syntax
    { id = "Files", hidden = true },    # hide a tab from students
    { id = "Discussions", hidden = true },
]

# ── Centralized due dates ───────────────────────────────────────────────
# Manage unlock/due/lock dates for assignments, discussions, and quizzes in
# one place instead of editing each file's frontmatter individually.
# Each entry is an inline table with the item's title and up to three dates.
# An empty string means "leave alone" — any frontmatter value or existing
# Canvas value is preserved. These override any dates in frontmatter.
# The optional `type` field disambiguates if two items share a title
# (valid values: assignment, discussion, quiz).
#
# Use `github-to-canvas list-titles <repo>` to see all available titles.
#
# IMPORTANT: like tab_configuration, this is a top-level key and must appear
# BEFORE any [section] or [[section]] header.
due_dates = [
    { name = "Week 1 Problem Set", unlock_at = "2025-01-27T00:00:00-05:00", due_at = "2025-02-01T23:59:00-05:00", lock_at = "2025-02-08T23:59:00-05:00" },
    { name = "Week 1 Discussion", type = "discussion", unlock_at = "NONE", due_at = "2025-02-03T23:59:00-05:00", lock_at = "CREATE_NONE_THEN_KEEP" },
    { name = "Midterm Quiz", type = "quiz", unlock_at = "NONE", due_at = "2025-03-01T23:59:00-05:00", lock_at = "CREATE_NONE_THEN_KEEP" },
]

# ── Default post policy (when grades become visible to students) ──────────
[default_post_policy]
post_manually = true   # true = grades hidden until you post them; false = automatic

# ── Late / missing submission policy ─────────────────────────────────────
[late_policy]
missing_submission_deduction_enabled    = false
missing_submission_deduction            = 0.0    # percent deducted for missing work
late_submission_deduction_enabled       = false
late_submission_deduction               = 0.0    # percent deducted per interval
late_submission_interval                = "day"  # "day" or "hour"
late_submission_minimum_percent_enabled = false
late_submission_minimum_percent         = 0.0    # floor: never deduct below this %

# ── Grading standards (letter-grade schemes) ─────────────────────────────
# Array-of-tables. `data` is a list of [label, minimum-fraction] rows, highest
# first. The first standard created here becomes the course grading_standard_id.
[[grading_standards]]
title = "Standard Scale"
data = [
  ["A", 0.90],
  ["B", 0.80],
  ["C", 0.70],
  ["D", 0.60],
  ["F", 0.0],
]
points_based   = false   # optional: scheme is points-based rather than percent
scaling_factor = 1.0     # optional: used together with points_based

# ── Assignment groups (grade categories & weighting) ─────────────────────
# Giving any group a group_weight automatically enables weighted grading for
# the course (the percentages then show on the Assignments page); see
# group_weighting_scheme in the Grades section above to override that.
[[assignment_groups]]
title        = "Homework"
position     = 1         # display order (lowest first)
group_weight = 40.0      # percent of the final grade
# Optional drop rules — drop_type is "drop_lowest" or "drop_highest":
[[assignment_groups.rules]]
drop_type  = "drop_lowest"
drop_count = 1

[[assignment_groups]]
title        = "Exams"
position     = 2
group_weight = 60.0
```

`id` and `label` are interchangeable, and matching is **case-insensitive** — just
type the name shown in the sidebar. That name resolves to a built-in tab id first
(`announcements`, `assignments`, `chat`, `collaborations`, `conferences`,
`discussions`, `files`, `grades`, `groups`, `modules`, `outcomes`, `pages`,
`people`, `quizzes`, `syllabus`, `home`, `settings`), and otherwise to any tab's
display label — which is how external tools (Zoom, Panopto, etc.) and renamed
built-ins (e.g. Conferences shown as "BigBlueButton") are matched. `home` and
`settings` are always shown and can't be moved or hidden. Only reordering and
hiding are supported — tabs can't be created here; any entry that doesn't match a
tab already in the course is skipped with a warning.

> **Edge case:** because a built-in id is matched before a tool label, if a course
> happened to have an external tool named exactly like a built-in tab id (e.g. a
> tool literally named "assignments"), the built-in wins and there's currently no
> way to target the tool in that clash. This is extremely unlikely; mentioned only
> for completeness.
>
> **Imported external tools may need a label filled in.** Canvas does not export
> the names of external tools that are used only in course navigation, so on
> `import` such tabs are written with an empty `label = ""` (their original id is
> kept for reference) and a warning is printed. Fill in each label with the tool's
> name as it appears in the destination course — e.g. `{ label = "Panopto", id =
> "context_external_tool_g…" }` — to position or hide it; until then sync leaves
> that tab untouched. The [`create-tool-aliases`](#resolving-external-tool-labels-create-tool-aliases)
> subcommand can generate a complete `tab_configuration` block with labels
> filled in from a Canvas course where you have already imported the IMSCC.

#### Centralized due dates

The `due_dates` array lets you manage `unlock_at`, `due_at`, and `lock_at` for
all assignments, discussions, and quizzes in one place. Each entry names a
content item by its `title` (from frontmatter); an optional `type` field
(`assignment`, `discussion`, or `quiz`) disambiguates if two items share a
title. Centralized dates override any dates set in frontmatter.

Each date field (`unlock_at`, `due_at`, `lock_at`) accepts either a date string
or one of these sentinel values (case-insensitive):

| Value | Behaviour |
| --- | --- |
| `"2025-02-01T23:59:00"` | Set this date on Canvas |
| `"NONE"` | Actively **clear** this date on Canvas |
| `"KEEP"` | Leave whatever Canvas currently has (don't send this field) |
| `""` (empty string) | Same as `KEEP`, but prints a warning suggesting you use an explicit value |
| `"CREATE_NONE_THEN_KEEP"` | Clear the date when creating a new item; on subsequent updates, act as `KEEP` |

`CREATE_NONE_THEN_KEEP` is useful for `lock_at` — it clears any lock date
imported from a previous term when the assignment is first created, but leaves
it alone if you later set one by hand in Canvas.

If Canvas rejects the due dates (e.g. `due_at` falls outside existing
`unlock_at`/`lock_at` availability dates), the tool retries the upload without
date fields and prints a warning. The content is still synced; only the dates
are skipped.

During `update`, the tool prints warnings for:

- A `due_dates` entry whose `name` doesn't match any content file (typo or
  stale entry).
- An assignment, discussion, or quiz that has **no** corresponding `due_dates`
  entry (so you know what's not yet tracked centrally).

Use [`list-titles`](#listing-content-titles-list-titles) to see all available
titles and their current due dates.

### Syllabus (`course_settings/syllabus.md`)

The Markdown body of this file is uploaded as the course's **syllabus** (the
Canvas "Syllabus" page). Frontmatter is optional and ignored — only the body is
used. Cross-links to other local files are rewritten to Canvas URLs, just like in
pages.

```markdown
---
title: "Syllabus"   # optional and ignored — only the Markdown body below is uploaded
---

# Welcome to CS 101

Class meets MWF 10–11 in Room 200.

See the [Week 1 Assignment](../assignments/week1.md) and the
[grading policy](../snippets/grading-policy.md).
```

### Rubrics (`course_settings/rubrics.toml`)

Optional. Defines reusable grading rubrics for the course. Read during the same
course-settings sync as `course_settings.toml`. Rubrics are matched **by title** —
a rubric whose title already exists in Canvas is **updated in place**; missing
rubrics are created.

```toml
# course_settings/rubrics.toml — array-of-tables, one [[rubrics]] block per rubric.

[[rubrics]]
title = "Essay Rubric"   # matched by title; updated in place if it already exists
reusable = true          # share one rubric across assignments (default: not sent)
read_only = false        # allow instructors to edit the rubric (default: not sent)

# One [[rubrics.criteria]] block per row of the rubric:
[[rubrics.criteria]]
description = "Thesis"
long_description = "Evaluates the clarity and strength of the thesis statement."
points = 5
# One [[rubrics.criteria.ratings]] block per rating level (highest first):
[[rubrics.criteria.ratings]]
description = "Clear and arguable"
long_description = "Thesis is specific, debatable, and well-positioned."
points = 5
[[rubrics.criteria.ratings]]
description = "Present but weak"
points = 3
[[rubrics.criteria.ratings]]
description = "Missing"
points = 0

[[rubrics.criteria]]
description = "Evidence"
points = 5
[[rubrics.criteria.ratings]]
description = "Well supported"
points = 5
[[rubrics.criteria.ratings]]
description = "Unsupported"
points = 0
```

`long_description` is optional at both criterion and rating levels. When present,
it is sent to Canvas as the extended description (visible when expanding a rubric
row). When absent or empty, only `description` and `points` are sent.

`reusable` and `read_only` are optional rubric-level flags sent to Canvas when
present. `reusable = true` means Canvas shares one rubric instance across all
assignments that reference it (instead of copying per assignment). `read_only =
false` (the default the `import` command writes) lets instructors edit the rubric
in the Canvas UI.

When writing a rubric from scratch, only `title`, `description`, `points`, and
`ratings` are required. A minimal rubric looks like:

```toml
[[rubrics]]
title = "My Rubric"

[[rubrics.criteria]]
description = "Quality"
points = 5

[[rubrics.criteria.ratings]]
description = "Excellent"
points = 5

[[rubrics.criteria.ratings]]
description = "Poor"
points = 0
```

> The `import` command also writes metadata to each rubric and criterion
> (`identifier`, `points_possible`, `criterion_id`, rating `id`, etc.). Those
> extra fields are preserved in the file but **ignored on upload** — you do not
> need them when creating rubrics by hand. The `import` command always sets
> `read_only = false` and `reusable = true`, regardless of the values in the
> IMSCC export, so that imported rubrics are editable and shared.

### Other `course_settings/` files (import-only)

The `import` command also produces the two files below. They are **not yet
uploaded** by the sync (no Canvas upload path exists for them yet — see `TODO.md`);
they are written so the data survives an import and is available for a future
release. Documented here for completeness.

**`course_settings/events.md`** — course calendar events, one per `##` heading:

```markdown
---
title: "Course Events"
---

## Midterm Exam

**Date:** 2025-02-14T10:00:00-08:00

Bring a pencil and your student ID.

## Spring Break (no class)

**Date:** 2025-03-24 (all day)
```

**`course_settings/files_meta.toml`** — per-file and per-folder visibility metadata
(locking, hiding, display names):

```toml
# course_settings/files_meta.toml — import-only; not yet uploaded.

[[folders]]
path = "course files/handouts"
hidden = false                       # true = hidden from students

[[files]]
identifier  = "gabc123"              # IMSCC resource id (from the export)
display_name = "Worksheet 1.docx"
locked      = false                  # true = locked
hidden      = false                  # true = hidden from students
unlock_at   = "2025-02-01T00:00:00-08:00"   # available to students from this time
```

### Page (`pages/`)

```markdown
---
title: "Syllabus"        # defaults to the filename if omitted
editing_roles: teachers  # who may edit in Canvas: teachers | students
                         #   | "teachers,students" | members | public
published: true          # true = visible to students; false = draft (the default)
---

## Course Syllabus

Welcome to the course. See [Week 1 Assignment](../assignments/week1.md).
```

Pages are updated in place on Canvas when re-synced.

**Images in Markdown:**  Pandoc turns a standalone image paragraph into a `<figure>` with a visible caption. Use the trailing-backslash trick to suppress the caption, or leave the alt text empty for decorative images:

| Goal | Markdown |
| --- | --- |
| Image with alt text, no visible caption | `![Alt text](image.svg)\` (trailing `\`) |
| Decorative image (no alt, no caption) | `![](image.svg)` |
| Image with visible caption | `![Caption text](image.svg)` (standalone paragraph) |

**Accessibility (decorative images):** an image whose alt text is empty (or only spaces) is automatically marked as decorative in the uploaded HTML — it gets `alt=""` and `role="presentation"`, the same markup the Canvas editor produces when you tick "Decorative image". This keeps the Canvas accessibility checker happy and tells screen readers to skip the image. Images with real alt text are left alone, so every image is accessible either way: give meaningful images alt text, and leave the alt text empty for purely decorative ones.

**WARNING:**  Make sure that you start your headers at H2.  **DO NOT USE H1 HEADERS!!!**  
Canvas will translate the H1 headers into styled normal paragraphs so it looks right but
will not work correctly with screen readers!!

### Assignment (`assignments/`)

Assignments are updated in place on Canvas when re-synced.

```markdown
---
title: "Week 1 Problem Set"   # defaults to the filename if omitted
published: true               # true = visible to students; false = draft (the default)
points_possible: 50
grading_type: "points"        # points | percent | letter_grade | gpa_scale
                              #   | pass_fail | not_graded
submission_types: ["online_upload"]  # one or more of: online_upload,
                              #   online_text_entry, online_url, online_quiz,
                              #   media_recording, student_annotation,
                              #   on_paper, external_tool, none
allowed_extensions: ["pdf", "docx"]  # file types students may upload; only
                              #   meaningful when submission_types includes
                              #   online_upload; omit to allow any file type
annotatable_attachment: "assets/rubric.pdf"  # asset to annotate; required when
                              #   submission_types includes student_annotation;
                              #   path relative to repo root — asset must be
                              #   synced before (or in the same run as) this
                              #   assignment
allowed_attempts: -1          # -1 = unlimited (default); positive integer
                              #   limits the number of submission attempts
due_at:    "2025-02-01T23:59:00-05:00"   # graded as late after this
unlock_at: "2025-01-27T00:00:00-05:00"   # becomes available at this time
lock_at:   "2025-02-08T23:59:00-05:00"   # no submissions accepted after this

# ── Assignment group (grading category) ───────────────────────────────────
assignment_group_id: "Labs"              # name of an assignment group defined in
                                         #   course_settings.toml, or a numeric
                                         #   Canvas ID; controls which grade bucket
                                         #   this assignment falls under

# ── Rubric ────────────────────────────────────────────────────────────────
rubric: "Essay Rubric"                   # title of a rubric defined in
                                         #   course_settings/rubrics.toml, or a
                                         #   numeric Canvas rubric ID; creates a
                                         #   rubric association on the assignment
use_for_grading: true                    # use the rubric score as the assignment
                                         #   grade (default: true); set to false
                                         #   for feedback-only rubrics

# ── Group assignment ──────────────────────────────────────────────────────
group_category_id: 12345                 # numeric ID of an existing group set
                                         #   (see note below); makes this a
                                         #   group assignment
grade_group_students_individually: false # true = "assign grades to each
                                         #   student individually"

# ── Anonymous grading ─────────────────────────────────────────────────────
anonymous_grading: false                 # hide student identities while grading

# ── Moderated grading ─────────────────────────────────────────────────────
moderated_grading: false                 # allow multiple provisional graders
grader_count: 2                          # required when moderated_grading is on
final_grader_id: 567                      # user ID who picks the final grade
grader_comments_visible_to_graders: true
graders_anonymous_to_graders: false
grader_names_visible_to_final_grader: true

# ── Peer reviews ──────────────────────────────────────────────────────────
peer_reviews: false                      # enable peer reviews
automatic_peer_reviews: false            # Canvas assigns reviewers automatically
peer_review_count: 1                     # reviews each student must complete
peer_reviews_assign_at: "2025-02-03T00:00:00-05:00"  # when auto-assignment runs
anonymous_peer_reviews: false
intra_group_peer_reviews: false          # allow reviews within the same group
---
# Week 1 Problem Set

Description goes here
Submit a PDF of your solutions by the deadline.
```

**Assignment group, rubric, group assignments, anonymous/moderated grading, and
peer reviews** are all settable from the frontmatter above. A few caveats:

- **Assignment group:** `assignment_group_id` accepts either the group name as a
  string (e.g. `"Labs"`) or a numeric Canvas ID. When a name is given the tool
  resolves it to the Canvas ID using the groups defined in `course_settings.toml`.
  If the name is not found a warning is printed and the field is skipped. This
  field works the same way on every graded content type — assignments, quizzes
  (see [Quiz](#quiz-quizzes)), and graded discussions (see
  [Discussion](#discussion-discussions)).
- **Rubric:** `rubric` accepts either a rubric title (string) or numeric Canvas
  rubric ID. When a title is given the tool resolves it to the Canvas ID using
  rubrics defined in `course_settings/rubrics.toml`. A rubric association is
  created (or updated) on each assignment sync. `use_for_grading` defaults to
  `true`; set it to `false` for advisory-only (feedback without grade impact)
  rubrics.
- **Group set:** Canvas identifies a group set (group *category*) by numeric ID,
  not by name. This tool does **not** create or manage group sets — create the
  group set in the Canvas UI (or via the API) first, then put its numeric
  `group_category_id` here. (The ID appears in the URL when you view the group
  set in Canvas: `.../groups#tab-<id>`.)
- **Moderated grading:** Canvas requires `grader_count` (and usually
  `final_grader_id`) when `moderated_grading` is `true`. `final_grader_id` is a
  Canvas **user** ID.

### Discussion (`discussions/`)

Discussions are updated in place on Canvas when re-synced.

A discussion is **ungraded** unless you add the grading fields (`points_possible`,
`due_at`, `lock_at`, `unlock_at`, `assignment_group_id`). Including any of them
attaches a Canvas assignment and makes the discussion graded; omit them all for
an ungraded discussion.

```markdown
---
title: "Week 1 Discussion"   # defaults to the filename if omitted
published: true              # true = visible to students; false = draft (the default)
require_initial_post: true   # students must post before they can see classmates' replies

# Grading fields — include these to make the discussion graded; delete them
# all for a plain, ungraded discussion:
points_possible: 10
due_at:    "2025-02-01T23:59:00-05:00"
unlock_at: "2025-01-27T00:00:00-05:00"
lock_at:   "2025-02-08T23:59:00-05:00"
assignment_group_id: "Labs"  # name of an assignment group defined in
                             #   course_settings.toml, or a numeric Canvas ID
---

Post your initial response by Wednesday, then reply to two classmates.
```

### Announcement (`announcements/`)

Files in `announcements/` become Canvas **announcements** (internally a discussion
topic with `is_announcement=true`). They are updated in place on Canvas when
re-synced.

The key difference from other content: **Canvas has no "draft" state for
announcements** — creating one posts it immediately. So `published` controls
whether the announcement is sent to Canvas *at all*:

- `published: false` → **not posted.** `update` skips the file (with a warning)
  and it stays staged in your repo. This lets you keep a set of announcements
  ready and release each one when the time is right (e.g. the midterm reminder).
- `published: true` → **posted now** (or scheduled, if you set `delayed_post_at`).

To release a staged announcement, change its `published` to `true` and run
`update`. Announcements cannot be graded, so grading/due-date fields do not apply.

```markdown
---
title: "Midterm Reminder"    # defaults to the filename if omitted
published: false             # false = staged, not posted (default); true = post it now

# Optional Canvas announcement settings (all omittable):
delayed_post_at: "2025-10-13T08:00:00-07:00"  # schedule automatic posting at this time
lock_at: "2025-10-20T23:59:00-07:00"          # stop accepting comments at this time
locked: true                 # lock the announcement (no comments)
discussion_type: threaded    # "threaded" or "side_comment"
require_initial_post: true   # readers must comment before seeing others' comments
allow_rating: true           # let users "like" comments
---

The midterm is **next week** — review the study guide and come prepared.
```

The optional settings above are forwarded to Canvas only when present. (There is
no ordering field: Canvas always lists announcements newest-first by post date,
so `position` has no effect and is not used.)

Any *other* frontmatter field — a typo, or a Canvas setting the tool doesn't
support — is **not** sent silently: `update` prints a `WARNING: … ignoring
frontmatter field '…'` when it processes the file and repeats the full list in a
summary at the end of the run, so you always know exactly what was skipped.

When you import a Canvas export, announcements are written here automatically with
`published: false`, and the original export metadata is preserved as commented-out
frontmatter — uncomment any of the supported settings above to apply it on the
next `update` (see [IMSCC import](#imscc-import)).

### Module (`modules/`)

Module files don't have a body that becomes HTML. The body lists content items and text headers (SubHeaders). Links to local `.md` files become Canvas content items; absolute URLs become ExternalUrl items.

**Sync behavior:** When a module is synced, the module itself is updated in place on Canvas, but all of its items (content links, SubHeaders, ExternalUrls) are deleted and re-created from the module file.

```markdown
---
title: "Week 1: Introduction"          # defaults to the filename if omitted
published: true                        # true = visible to students; false = draft (the default)
unlock_at: "2025-01-20T00:00:00-05:00" # module stays locked until this time
require_sequential_progress: false     # true = students must complete items in order
---

## Readings                            <!-- a "## heading" becomes a SubHeader at indent 0 -->

- [Syllabus](../pages/syllabus.md)                  <!-- link to a local .md → content item -->
- [Course Website](https://example.com)              <!-- absolute URL → ExternalUrl item -->

## Work

- [Week 1 Assignment](../assignments/week1.md)
- [Week 1 Discussion](../discussions/week1-intro.md)
- [Week 1 Quiz](../quizzes/week-1-quiz/week-1-quiz.md)
- Please read the instructions carefully    <!-- plain-text list item → SubHeader at indent 1 -->
```

The module body uses four kinds of lines:

- A `## heading` becomes a **SubHeader** item at indent level 0.
- A **plain-text list item** (no link) becomes a **SubHeader** item starting at indent level 1. Indenting with leading spaces increases the level (2 spaces per level).
- A bullet linking to a local `.md` file becomes a **content item** (Page,
  Assignment, Discussion, or Quiz — inferred from the target's folder).
- A bullet linking to an absolute `http(s)://` URL becomes an **ExternalUrl** item.

ExternalUrl items default to `new_tab: true` (Canvas opens the link in a new window). Add `<!-- target="_self" -->` after the link to embed in an iframe instead.

**Per-item published state:** By default every item in a module is published (visible to students). To mark an individual item as unpublished, add `<!-- published="false" -->` after the link:

```markdown
- [Visible Page](../pages/intro.md)
- [Hidden Draft](../pages/draft.md) <!-- published="false" -->
- [Hidden Link](https://example.com) <!-- published="false" -->
```

This sets the Canvas module item's published state — the item still appears in Canvas for instructors but is hidden from students. Multiple attributes can be combined in a single comment: `<!-- target="_self" published="false" -->`.

> **Known Canvas limitation:** The Canvas API returns a server error (500) when
> trying to set `published=false` on **File-type** module items (e.g. `.docx`,
> `.pdf`, or other non-Markdown assets linked directly in a module). Pages,
> assignments, discussions, quizzes, and external URLs all work correctly. For
> File items the tool prints a summary at the end listing which items you need
> to unpublish manually in the Canvas web UI.

Unpublished items are also excluded from the `publish` subcommand's static website (including any assets reachable only through unpublished links).

The `import` subcommand preserves per-item published state from the Canvas export — unpublished items in the original course get the `<!-- published="false" -->` comment automatically.

**Item indentation:** Indent list items with leading spaces to set their Canvas indentation level. Every 2 spaces of indentation adds one indent level. Canvas supports indent levels 0-5; deeper indentation is clamped to 5 with a warning.

```markdown
## Welcome                                          <!-- SubHeader indent 0 -->

- [Course Overview](../pages/overview.md)           <!-- indent 0 (flush left) -->

## Useful Links                                     <!-- SubHeader indent 0 -->

  - [Grading Guide](../pages/grading.md)            <!-- indent 1 (2 spaces) -->
  - [Zoom Links](../pages/zoom.md)                  <!-- indent 1 -->
    - [Zoom Etiquette](../pages/zoom-etiquette.md)  <!-- indent 2 (4 spaces) -->
  - Important reminder about Zoom                   <!-- SubHeader indent 2 -->
    - Another note                                  <!-- SubHeader indent 3 -->
```

Top-level `## headings` always appear at indent level 0. Plain-text list items start at indent 1 (flush `- text`) and increase with nesting. The same indentation is preserved in the published website.

**Module display order** is controlled by `course_settings/module_order.toml`. Without this file, modules are synced in alphabetical filename order. Create the file to assign explicit Canvas positions:

```toml
# course_settings/module_order.toml
# Lists module filenames in the order they should appear in Canvas (position 1 = top).
# Filenames are relative to the modules/ directory.
# Modules not listed here are placed after all listed ones by Canvas.
order = [
    "week-1.md",
    "week-2.md",
    "final-exam.md",
]
```

When this file is modified, the tool repositions the listed modules on Canvas without re-syncing their content. If a listed module isn't found locally or hasn't been synced to Canvas yet, a warning is printed. This file is also generated automatically by the `import` subcommand, preserving the module order from the original Canvas export.

### Quiz (`quizzes/`)

Each quiz lives in its own sub-folder. The folder name becomes the quiz slug.

**Sync behavior:** When a quiz is synced, the quiz itself is updated in place on Canvas, but all of its questions are deleted and re-created from the question files. The publish state is applied after the questions, so a quiz that becomes published during the sync goes live with its new questions — no manual step needed.

> **Updating an already-published quiz:** Canvas holds question changes to a
> published quiz as a pending draft — students keep seeing the old questions,
> and the quiz page shows an "unsaved changes" banner. The Canvas API offers no
> way to accept those changes programmatically, so the tool prints a warning
> with a link to the quiz: open it and click **Save It Now**. (Unpublishing and
> re-publishing would work around it, but Canvas forbids that once students have
> submissions and it re-sends notifications, so the tool doesn't do it.)

```text
quizzes/
└── week-1-quiz/
    ├── week-1-quiz.md          ← quiz settings + ordered question list
    └── questions/
        ├── what-is-2-plus-2.md
        └── explain-gravity.md
```

**Quiz-level file** — frontmatter holds settings; the body is an optional description followed by a numbered list of links to question files (order = Canvas question order):

```markdown
---
title: "Week 1 Quiz"      # defaults to the folder name if omitted
published: true           # true = visible to students; false = draft (the default)
quiz_type: assignment     # assignment | practice_quiz | graded_survey | survey
points_possible: 10
time_limit: 30            # minutes; omit for no time limit
allowed_attempts: 1       # -1 = unlimited attempts
shuffle_answers: false    # randomize answer order per student
show_correct_answers: true  # reveal correct answers after submitting
assignment_group_id: "Labs"  # name of an assignment group defined in
                             #   course_settings.toml, or a numeric Canvas ID;
                             #   only affects grading for graded quiz_type values
                             #   (assignment, graded_survey) — see note below
---

Read each question carefully.   <!-- optional description; everything that isn't a
                                     numbered question link becomes the quiz body -->

1. [What is 2+2?](questions/what-is-2-plus-2.md)
2. [Explain gravity](questions/explain-gravity.md)
```

**Commenting out questions:** wrap question links in a raw-attribute block (the
same [comment trick](#course_settingstoml) that works everywhere else) and they
are skipped — not uploaded, not shown in the description. The numbers on the
remaining links don't need to be renumbered; question order comes from list
order, not the numbers.

````markdown
1. [What is 2+2?](questions/what-is-2-plus-2.md)
```{=comment}
2. [Skipped this quarter](questions/skipped.md)
```
3. [Explain gravity](questions/explain-gravity.md)
````

**Assignment group:** `assignment_group_id` works the same way as it does for
[assignments](#assignment-assignments) — a group name resolved via
`course_settings.toml`, or a numeric Canvas ID. Canvas only uses it for grading
when `quiz_type` is `assignment` or `graded_survey`; it has no effect for
`practice_quiz` or `survey` since those aren't graded.

**Question files** — each question is a separate `.md` file. Every question type shares these fields:


| Field             | Default if omitted | Notes                                |
| ----------------- | ------------------ | ------------------------------------ |
| `title`           | filename stem      | Shown as the question name in Canvas |
| `question_type`   | `essay_question`   | See types below                      |
| `points_possible` | `0`                |                                      |

**Supported question types and their required fields:**

> **Note:** None of these fields are required for the *sync to succeed* — a question with missing fields will upload without errors. However, the question will be ungradable in Canvas until the fields are provided.


| Question type                | Fields needed to be gradable                                                         |
| ---------------------------- | ------------------------------------------------------------------------------------ |
| `multiple_choice_question`   | `correct` (1-based index of the right answer) + `## Answers` section listing choices |
| `true_false_question`        | `correct: true` or `correct: false`                                                  |
| `multiple_response_question` | `correct` (list of 1-based indices, e.g. `[1, 3]`) + `## Answers` section            |
| `fill_in_blank_question`     | `answers` list in frontmatter (accepted strings)                                     |
| `pattern_match_question`     | `answers` list in frontmatter (accepted patterns)                                    |
| `essay_question`             | — (manually graded; no`correct` needed)                                             |

> `fill_in_blank_question` and `pattern_match_question` are both uploaded to Canvas
> as a *Short Answer* question. For `pattern_match_question`, only the **first**
> entry in `answers` is used.

Example MCQ question file (the `## Answers` list numbering is ignored — choices are
read in order, and `correct` is the 1-based position of the right one):

```markdown
---
title: "What is 2+2?"              # defaults to the filename if omitted
question_type: multiple_choice_question
points_possible: 1
correct: 2                        # 1-based index into the ## Answers list below
---

What is the result of adding 2 and 2?

## Answers

1. 3
2. 4
3. 5
```

Example multiple-response question file (`correct` is a list of 1-based indices):

```markdown
---
title: "Which are prime numbers?"
question_type: multiple_response_question
points_possible: 2
correct: [1, 3]                   # answers 2 and 5 are wrong
---

Select all of the prime numbers.

## Answers

1. 2
2. 4
3. 5
4. 6
```

Example true/false question file:

```markdown
---
title: "The sky is blue"
question_type: true_false_question
points_possible: 1
correct: true                     # true or false
---

The sky appears blue during the day due to Rayleigh scattering.
```

Example fill-in-the-blank question file (any listed string counts as correct):

```markdown
---
title: "Capital of France"
question_type: fill_in_blank_question
points_possible: 1
answers: ["Paris", "paris"]       # accepted answers (case matters in Canvas)
---

The capital of France is ____.
```

Example pattern-match question file (only the first pattern is used):

```markdown
---
title: "Name a primary color"
question_type: pattern_match_question
points_possible: 1
answers: ["red"]                  # accepted as a substring match
match_type: substring             # optional; written by `import`
---

Type one primary color.
```

Example essay question file (manually graded — no `correct`/`answers` needed):

```markdown
---
title: "Explain gravity"
question_type: essay_question
points_possible: 5
---

In 3–5 paragraphs, explain the concept of gravity.
```

**Optional feedback and sample solutions** — any question type may add a
`## Feedback` section with up to three subsections, and essay questions may add a
`## Sample Solution`:

```markdown
---
title: "What is 2+2?"
question_type: multiple_choice_question
points_possible: 1
correct: 2
---

What is the result of adding 2 and 2?

## Answers

1. 3
2. 4
3. 5

## Feedback

### General

Shown to every student regardless of their answer.

### Correct

Shown when the student answers correctly.

### Incorrect

Shown when the student answers incorrectly.

## Sample Solution

For essay-type questions, this text is uploaded as the question's neutral
(general) comment. Ignored if a `## Feedback` → `### General` block is also present.
```

> The `import` command may also emit `original_answer_ids` in a question's
> frontmatter and a `### Per-answer` block under `## Feedback`. These are preserved
> for round-tripping but are **not** sent to Canvas on upload.

### Question banks (`question_banks/`)

A question bank is a reusable pool of questions (not attached to a single quiz).
Each bank lives in its own sub-folder under `question_banks/`; the folder holds a
`.toml` settings file (named after the folder) and a `questions/` directory.

```text
question_banks/
└── midterm-pool/
    ├── midterm-pool.toml       ← bank settings
    └── questions/
        ├── what-is-2-plus-2.md
        └── explain-gravity.md
```

The bank's `.toml` file holds just the bank title:

```toml
# question_banks/midterm-pool/midterm-pool.toml
bank_title = "Midterm Pool"   # defaults to the folder name if omitted
```

The question files use the **exact same format** as quiz question files (see
[Quiz (`quizzes/`)](#quiz-quizzes) above) — all question types, the `## Answers`
section, and the `## Feedback` / `## Sample Solution` sections all work the same way.

> Each sync **creates a new question bank** in Canvas; banks are not matched or
> updated in place. Re-syncing a changed bank will create a duplicate in Canvas —
> delete the old one manually if needed.

### Snippets

Any Markdown link whose target resolves inside `snippets/` is replaced with the snippet's content before conversion. Snippets are expanded in all Markdown file types — pages, assignments, discussions, modules, quizzes, question files, and question banks. Useful for office hours, shared policies, etc.

> **Editing a snippet propagates automatically on the next full `update`/`publish` run.** The tool tracks which snippets each file references and treats that file as stale if any referenced snippet changed since it was last synced — you don't need `--force-uploads` or to `touch` anything. The one exception: a narrow `-s`/`-t` run stays strictly scoped to the files you named. If you run `-s assignments/week1.md` and a snippet shared with `assignments/week2.md` changed, only `week1.md` re-syncs — `week2.md` picks up the change on the next full `update`/`publish`.

```markdown
<!-- in pages/syllabus.md -->
[My Office Hours](../snippets/office-hours.md)
```

```markdown
<!-- snippets/office-hours.md -->
Office hours are Tuesdays 2–4 pm in Building 7, Room 201.
```

#### Inline snippets and the `CANVAS_COURSE_REFERENCE` snippet

Snippets can also be embedded *inline* using a dollar-sign-fenced path:

```text
$path/to/snippet.md$
```

The path is relative to the file that contains the reference. Before conversion the tool replaces the whole `$…$` token with the snippet's content (whitespace-stripped), making it safe to embed inside a Markdown link URL or anywhere else in text.

The most common use is `snippets/inline/CANVAS_COURSE_REFERENCE.md`, which holds the full Canvas course base URL (protocol, institution hostname, and course ID). Links that point into your own course can reference it, keeping the institution name and course ID in one place:

```markdown
<!-- in pages/overview.md -->
Go through the list of your [Grades]($../snippets/inline/CANVAS_COURSE_REFERENCE.md$/grades "Grades") here in Canvas.
```

```markdown
<!-- snippets/inline/CANVAS_COURSE_REFERENCE.md -->
https://cascadia.instructure.com/courses/2735320
```

Before conversion, `$../snippets/inline/CANVAS_COURSE_REFERENCE.md$` is replaced with the snippet content, producing:

```markdown
Go through the list of your [Grades](https://cascadia.instructure.com/courses/2735320/grades "Grades") here in Canvas.
```

The snippet file name is written in ALL CAPS to make it visually distinct from regular (lowercase) snippet files.

> **Note on `$` in links** — Inside a Markdown link URL, `$` has no special meaning and is valid in HTML `href` attributes (RFC 3986 sub-delimiter). The preprocessing step runs before Pandoc, so there is no conflict with Pandoc's `$…$` math syntax. The link will appear broken in a Markdown editor preview, but the Markdown structure itself is unaffected.

**The `import` subcommand creates this snippet automatically.** When you import a `.imscc` file, the tool reads the institution hostname and course ID from the export metadata and writes `snippets/inline/CANVAS_COURSE_REFERENCE.md` with the full base URL. Every Markdown link whose URL starts with that base URL is rewritten to use the `$path$` snippet reference, with the link text added as a hover title.

#### Shared frontmatter via `PASTE_SNIPPET_INTO_FRONTMATTER`

The two snippet forms above only reuse **body** content. To reuse **frontmatter** values — e.g. every "worksheet" assignment sharing the same `points_possible` and `rubric` — lead the body with one or more links of the form:

```markdown
---
title: "Worksheet 1"
canvas_type: assignment
published: true
---
[PASTE_SNIPPET_INTO_FRONTMATTER](../snippets/worksheet-defaults.md)
[PASTE_SNIPPET_INTO_FRONTMATTER](../snippets/another-snippet.md)

Do the worksheet...
```

```yaml
<!-- snippets/worksheet-defaults.md -->
points_possible: 50
rubric: "Worksheet Rubric"
submission_types: [online_upload]
```

Each referenced file must be a plain YAML mapping (not Markdown prose) — its keys are merged into the file's own frontmatter before the rest of processing. Rules:

- The link text must be exactly `PASTE_SNIPPET_INTO_FRONTMATTER` (case-sensitive) — this is what makes the reference visually distinct and lets you Ctrl+click it in VS Code to jump straight to the shared defaults file.
- These lines must be the **first thing in the body** — only blank/whitespace-only lines may precede or separate them. The scan stops at the first line that isn't blank and isn't a `PASTE_SNIPPET_INTO_FRONTMATTER` link; everything from there on is treated as ordinary body content.
- Multiple references are merged in order, later snippets overriding earlier ones for any keys they share.
- The file's own frontmatter always wins over snippet values, so a single file can still override one or two fields from a shared default.
- Nested includes (a frontmatter snippet referencing another snippet) are not supported.

This is a different tool than the centralized `due_dates` table in `course_settings.toml` (see [Centralized due dates](#course_settingstoml)): `due_dates` is for fields that should mostly be *unique per item* but reviewed in one place; `PASTE_SNIPPET_INTO_FRONTMATTER` is for fields that should be *identical* across many files, edited once and reflected everywhere that includes the snippet (after a re-sync — see the staleness caveat above).

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

### Deleting a file in Canvas

The tool does **not** detect Canvas-side deletions. If you delete a file in Canvas (e.g. an image in Canvas Files) but the local file is unchanged, the next `update` run will silently skip it — the local mtime is still older than `last_synced`, so `needs_sync` returns false and no upload occurs. The manifest entry remains, pointing at a now-dead Canvas ID. Any pages that embed the deleted file will show broken links.

To fix this, re-upload the affected file using one of:

```bash
# Re-upload everything
github-to-canvas update . --force-uploads

# Re-upload just the one file (touch updates mtime so the tool treats it as changed)
touch assets/Images/path/to/file.png
github-to-canvas update .
```

After a forced re-upload the file receives a **new** Canvas ID. Pages that reference the image must then be re-synced so their embedded URLs are rewritten to the new ID.

## IMSCC import

Import an existing Canvas course export (`.imscc` file) into a local Markdown repo:

```bash
github-to-canvas import course-export.imscc ./my-course-repo
```

This converts pages, assignments, discussions, announcements, quizzes, question banks, modules, and course settings to local files ready for use with this tool. A `canvas.toml` skeleton is written with the Canvas domain and course ID pre-filled from the export metadata.

**Announcements** are imported into an `announcements/` folder, one Markdown file per announcement. Only the announcement itself is imported — any student replies, likes, or comments are not part of a Canvas export, so there is nothing to import. Each file gets `published: false`, which means `update` leaves it **staged (not posted)** until you set `published: true` — handy for re-posting announcements when the time is right (e.g. a midterm reminder the week before the midterm). The frontmatter keeps `title` and `published` as active fields; the original export's other metadata (post date, workflow state, etc.) is preserved as commented-out lines you can uncomment to apply (see [Announcement](#announcement-announcements)).

> **Heading levels:** Canvas already strips H1s from the content it exports, so imported headings normally keep their original levels — an H2 stays an H2. As a safety net, if a converted file *does* still contain an H1 (which Canvas would silently turn into an inaccessible styled paragraph; see the H1 warning above), the import shifts every heading in that file down one level so the H1 becomes an H2. Files without an H1 are left untouched.
>
> **Attribute cleanup:** Pandoc attaches curly-brace attribute blocks (e.g. `## Heading {#id .class style="..."}`) to headings, links, images, spans, and fenced divs during HTML→Markdown conversion. Import strips these down to just `id` (kept in case a table of contents links to it) and `style` (kept as user-authored formatting) — classes and other Canvas-internal attributes are dropped. If nothing is left, the `{...}` is removed entirely, and an emptied fenced div (`:::`) is unwrapped rather than left as an empty wrapper.

### Verifying the import

After importing, run the coverage checker to spot-check that content from the IMSCC made it into the Markdown repo.  This script is **not** part of the automated test suite — run it manually after an import:

```bash
python scripts/check_imscc_coverage.py course-export.imscc ./my-course-repo
```

Options:


| Flag                | Default                                            | Description                                                      |
| ------------------- | -------------------------------------------------- | ---------------------------------------------------------------- |
| `--min-words N`     | 10                                                 | Minimum fragment length; increase to reduce coincidental matches |
| `--seed N`          | 42                                                 | Random seed — change to sample different fragments               |
| `--categories LIST` | `announcement,assignment,discussion,page,syllabus` | Comma-separated types to check                                   |

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

## Listing content titles (`list-titles`)

The `list-titles` subcommand prints every assignment, discussion, and quiz in
the repo along with its due date (if any) and file path. Items are sorted by
due date (earliest first); items without a due date are listed last,
alphabetically by title.

```bash
github-to-canvas list-titles path/to/course-repo
```

Example output:

```text
Week 1 Problem Set  2025-02-01 23:59  assignments/week1.md
Introduce Yourself  2025-02-01 23:59  discussions/week1-intro.md
Midterm Quiz        2025-03-01 23:59  quizzes/midterm/midterm.md
A Quiz                                quizzes/a-quiz/a-quiz.md
```

This is useful when setting up the centralized `due_dates` table in
`course_settings.toml` — you can see all available titles at a glance.
Due dates shown reflect centralized overrides when present.

---

## Resolving external-tool labels (`create-tool-aliases`)

Canvas does not export the names of external tools that are used only in course
navigation, so after `import` the `tab_configuration` in
`course_settings/course_settings.toml` has empty `label = ""` placeholders for
those tools. The `create-tool-aliases` subcommand fills in these labels by
reading the navigation tabs from a live Canvas course.

### Workflow

1. Import the `.imscc` file into an empty Canvas course (via the Canvas UI:
   Settings → Import Course Content).
2. Run `create-tool-aliases`, passing any URL from that course:

   ```bash
   github-to-canvas create-tool-aliases https://school.instructure.com/courses/12345
   ```

   Any URL containing `/courses/<id>` works — you can paste whatever page you
   happen to have open (e.g. `.../courses/12345/rubrics`).

3. The subcommand prints a complete `tab_configuration` block to stdout with
   tool labels filled in:

   ```toml
   tab_configuration = [
       { id = "home" },
       { id = "modules" },
       { id = "assignments" },
       { label = "Zoom", id = "context_external_tool_gd9568f5b0d2a343486654adb2ae69aac" },
       { label = "Panopto Recordings", id = "context_external_tool_g67e4019c6ea3ce88e6856319395ed4e4" },
       { id = "grades" },
       { id = "people" },
   ]
   ```

4. Compare this output with the `tab_configuration` in your working course's
   `course_settings/course_settings.toml` and fill in (or replace) the labels.

The API token is read from the `CANVAS_API_TOKEN` environment variable. The
base URL is extracted from the course URL, so no `canvas.toml` is needed for
this subcommand.

> **Note:** The tool order in the output should match the order in the imported
> course's navigation sidebar. If the order differs from your working course's
> `tab_configuration`, rearrange the entries manually — sync matches tabs by
> label, not by position or id.
