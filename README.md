# github-to-canvas

Sync a Markdown course repository to [Canvas LMS](https://www.instructure.com/canvas).

Write your course content as Markdown files in a Git repository. Run this tool to convert them to HTML and publish them to Canvas — pages, assignments, discussion topics, and modules.

---

## Contents

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
- [Content file format](#content-file-format)
  - [`course_settings.toml`](#course_settingstoml)
  - [Syllabus (`course_settings/syllabus.md`)](#syllabus-course_settingssyllabusmd)
  - [Rubrics (`course_settings/rubrics.toml`)](#rubrics-course_settingsrubricstoml)
  - [Other `course_settings/` files (import-only)](#other-course_settings-files-import-only)
  - [Page (`pages/`)](#page-pages)
  - [Assignment (`assignments/`)](#assignment-assignments)
  - [Discussion (`discussions/`)](#discussion-discussions)
  - [Module (`modules/`)](#module-modules)
  - [Quiz (`quizzes/`)](#quiz-quizzes)
  - [Question banks (`question_banks/`)](#question-banks-question_banks)
  - [Snippets](#snippets)
- [Manifest file](#manifest-file)
- [IMSCC import](#imscc-import)
  - [Verifying the import](#verifying-the-import)

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

1. Applies `course_settings.toml` (name, dates, grading standards, assignment groups, policies)
2. Uploads `course_settings/syllabus.md` as the course syllabus body
3. Uploads everything in `assets/` to Canvas Files
4. Converts each `.md` in `pages/`, `assignments/`, `discussions/` to HTML via Pandoc and uploads
5. Syncs `quizzes/` (Classic Quizzes API) and `question_banks/`
6. Rewrites cross-links between files to correct Canvas URLs
7. Syncs `modules/` last (after all content has Canvas IDs)

Files are skipped if their local modification time is older than the `last_synced` timestamp in the manifest — so unchanged files cost nothing on repeat runs.

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
github-to-canvas ./my-course
```

Or put it in a `.env` file in your working directory (loaded automatically on startup):

```bash
# .env
CANVAS_API_TOKEN=your-token-here
```

Or put it in the `[auth]` block of `canvas.toml` for local-only use (add `canvas.toml` to `.gitignore` if you do this).

---

## Usage

```
Usage: github-to-canvas update [OPTIONS] REPO

  Sync a Markdown course repo to Canvas LMS.

Arguments:
  REPO                            Path to the course content repo  [required]

Options:
  --config PATH                   Path to canvas.toml  [default: <repo>/canvas.toml]
  --force-uploads                 Re-upload all files even if unchanged since last sync
  --force-overwrite               Skip Canvas timestamp check; always overwrite Canvas
  -t, --target-recursively FILE   Comma-separated files; each is synced plus all resources
                                  it transitively references (BFS). Skips the full sync.
  -s, --single-target FILE        Comma-separated files to sync without traversing references.
                                  Runs after -t. Skips the full sync.
  --help                          Show this message and exit.
```

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
# canvas.toml lives inside the repo (default)
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

## Content file format

Content files (`pages/`, `assignments/`, `discussions/`, `modules/`, quizzes, and
questions) use **YAML frontmatter** followed by a **Markdown body**. Course-level
settings live in `course_settings.toml` and use **TOML**.

Every example below lists **all available options** with an inline comment for each.
To create a new file, copy the whole block and then delete, edit, or replace the
options you don't want. **Every field is optional** unless a comment says otherwise —
omitted fields are simply left unchanged in Canvas (and `title` falls back to the
filename). All dates are ISO 8601 strings; include a timezone offset (e.g. `-08:00`)
to avoid surprises.

### `course_settings.toml`

Placed at the **repo root** (not inside `course_settings/`). Drives the course's
own settings: identity, dates, visibility, grading scheme, assignment groups, and
policies. Applied before any content is uploaded.

```toml
# course_settings.toml — repo root, TOML syntax. Every key is optional.

# ── Course identity & display ────────────────────────────────────────────
title        = "Intro to Programming"          # Canvas course name
course_code  = "CS 101"                        # short code shown in the UI
start_at     = "2025-01-06T00:00:00-08:00"     # course start date
conclude_at  = "2025-03-20T23:59:00-07:00"     # course end date
default_view = "wiki"   # landing page: feed | wiki | modules | syllabus | assignments
license      = "private"  # private | public_domain | cc_by | cc_by_sa | cc_by_nc
                          #   | cc_by_nc_sa | cc_by_nd | cc_by_nc_nd

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
[[assignment_groups]]
title        = "Homework"
position     = 1         # display order (lowest first)
group_weight = 40.0      # percent of the final grade (when weighting is on)
# Optional drop rules — drop_type is "drop_lowest" or "drop_highest":
[[assignment_groups.rules]]
drop_type  = "drop_lowest"
drop_count = 1

[[assignment_groups]]
title        = "Exams"
position     = 2
group_weight = 60.0
```

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
a rubric whose title already exists in Canvas is left untouched (existing rubrics
are never modified, only missing ones are created).

```toml
# course_settings/rubrics.toml — array-of-tables, one [[rubrics]] block per rubric.

[[rubrics]]
title = "Essay Rubric"   # matched by title; skipped if it already exists in Canvas

# One [[rubrics.criteria]] block per row of the rubric:
[[rubrics.criteria]]
description = "Thesis"
points = 5
# One [[rubrics.criteria.ratings]] block per rating level (highest first):
[[rubrics.criteria.ratings]]
description = "Clear and arguable"
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

> The `import` command also writes read-only metadata to each rubric and criterion
> (`identifier`, `points_possible`, `criterion_id`, `long_description`, rating
> `id`, etc.). Those extra fields are preserved in the file but **ignored on
> upload** — only `title` and each criterion's / rating's `description` and
> `points` are sent to Canvas.

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

# Course Syllabus

Welcome to the course. See [Week 1 Assignment](../assignments/week1.md).
```

### Assignment (`assignments/`)

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
due_at:    "2025-02-01T23:59:00-05:00"   # graded as late after this
unlock_at: "2025-01-27T00:00:00-05:00"   # becomes available at this time
lock_at:   "2025-02-08T23:59:00-05:00"   # no submissions accepted after this

# ── Assignment group (grading category) ───────────────────────────────────
assignment_group_id: "Labs"              # name of an assignment group defined in
                                         #   course_settings.toml, or a numeric
                                         #   Canvas ID; controls which grade bucket
                                         #   this assignment falls under

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

**Assignment group, group assignments, anonymous/moderated grading, and peer
reviews** are all settable from the frontmatter above. A few caveats:

- **Assignment group:** `assignment_group_id` accepts either the group name as a
  string (e.g. `"Labs"`) or a numeric Canvas ID. When a name is given the tool
  resolves it to the Canvas ID using the groups defined in `course_settings.toml`.
  If the name is not found a warning is printed and the field is skipped.
- **Group set:** Canvas identifies a group set (group *category*) by numeric ID,
  not by name. This tool does **not** create or manage group sets — create the
  group set in the Canvas UI (or via the API) first, then put its numeric
  `group_category_id` here. (The ID appears in the URL when you view the group
  set in Canvas: `.../groups#tab-<id>`.)
- **Moderated grading:** Canvas requires `grader_count` (and usually
  `final_grader_id`) when `moderated_grading` is `true`. `final_grader_id` is a
  Canvas **user** ID.

### Discussion (`discussions/`)

A discussion is **ungraded** unless you add the grading fields (`points_possible`,
`due_at`, `lock_at`, `unlock_at`). Including any of them attaches a Canvas
assignment and makes the discussion graded; omit them all for an ungraded
discussion.

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
---

Post your initial response by Wednesday, then reply to two classmates.
```

### Module (`modules/`)

Module files don't have a body that becomes HTML. The body lists content items and optional sub-headers. Links to local `.md` files become Canvas content items; absolute URLs become ExternalUrl items.

```markdown
---
title: "Week 1: Introduction"          # defaults to the filename if omitted
published: true                        # true = visible to students; false = draft (the default)
unlock_at: "2025-01-20T00:00:00-05:00" # module stays locked until this time
require_sequential_progress: false     # true = students must complete items in order
---

## Readings                            <!-- a "## heading" becomes a SubHeader item -->

- [Syllabus](../pages/syllabus.md)                  <!-- link to a local .md → content item -->
- [Course Website](https://example.com) <!-- target="_blank" -->  <!-- absolute URL → ExternalUrl item -->

## Work

- [Week 1 Assignment](../assignments/week1.md)
- [Week 1 Discussion](../discussions/week1-intro.md)
- [Week 1 Quiz](../quizzes/week-1-quiz/week-1-quiz.md)
```

The module body uses three kinds of lines:

- A `## heading` becomes a **SubHeader** item.
- A bullet linking to a local `.md` file becomes a **content item** (Page,
  Assignment, Discussion, or Quiz — inferred from the target's folder).
- A bullet linking to an absolute `http(s)://` URL becomes an **ExternalUrl** item.

The `<!-- target="_blank" -->` comment sets `new_tab: true` on the Canvas ExternalUrl item (opens in a new tab). Lines without a comment default to `new_tab: false`.

### Quiz (`quizzes/`)

Each quiz lives in its own sub-folder. The folder name becomes the quiz slug.

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
---

Read each question carefully.   <!-- optional description; everything that isn't a
                                     numbered question link becomes the quiz body -->

1. [What is 2+2?](questions/what-is-2-plus-2.md)
2. [Explain gravity](questions/explain-gravity.md)
```

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

## IMSCC import

Import an existing Canvas course export (`.imscc` file) into a local Markdown repo:

```bash
github-to-canvas import course-export.imscc ./my-course-repo
```

This converts pages, assignments, discussions, quizzes, question banks, modules, and course settings to local files ready for use with this tool. A `canvas.toml` skeleton is written with the Canvas domain and course ID pre-filled from the export metadata.

### Verifying the import

After importing, run the coverage checker to spot-check that content from the IMSCC made it into the Markdown repo.  This script is **not** part of the automated test suite — run it manually after an import:

```bash
python scripts/check_imscc_coverage.py course-export.imscc ./my-course-repo
```

Options:


| Flag                | Default                               | Description                                                      |
| ------------------- | ------------------------------------- | ---------------------------------------------------------------- |
| `--min-words N`     | 10                                    | Minimum fragment length; increase to reduce coincidental matches |
| `--seed N`          | 42                                    | Random seed — change to sample different fragments              |
| `--categories LIST` | `assignment,discussion,page,syllabus` | Comma-separated types to check                                   |

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
