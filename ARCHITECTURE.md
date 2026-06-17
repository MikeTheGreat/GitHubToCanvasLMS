# GitHubToCanvasLMS — Internal Documentation

## Project Intent

A tool for managing Canvas LMS course content through Markdown files stored in a GitHub repository. The workflow converts Markdown (and supporting assets) into HTML fragments and uploads them to a Canvas LMS instance via the Canvas API.

## Workflow

```text
// Setup:
GitHub repo (Markdown + assets)
git clone (local)

// This tool:
1. git pull                             (ensure local copy is up-to-date)
2. load .canvas-manifest.toml           (into in-memory dict; single source of truth during the run)
2.5. if course_settings.toml exists: apply course metadata to Canvas (name, dates, flags, grading
     standards, assignment groups, late policy, post policy, rubrics)
2.6. if course_settings/syllabus.md exists: convert body to HTML and set as course syllabus body
3. upload assets/                       (see processing order below)
     → skip any file whose mtime ≤ manifest last_synced (unless --force-uploads)
4. for each content folder in alphabetical order (excludes assets/, course_settings/, modules/,
   question_banks/, quizzes/, snippets/, hidden dirs):
     for each .md file in that folder, alphabetically:
       a. skip if mtime ≤ manifest last_synced (unless --force-uploads); print "Skipping (up-to-date)"
       b. snippet preprocessing: replace any [text](snippets/...) links with snippet file contents
       c. convert Markdown → HTML via Pandoc
       d. for each <img> and <a href> that points to a local file:
            - if local file does not exist → print error, remove the tag, skip (do NOT stub)
            - if in manifest → rewrite tag to Canvas URL
            - if NOT in manifest → create empty stub in Canvas (unpublished, title only)
                                   → add to manifest dict AND flush to disk
                                   → rewrite tag to Canvas URL
       e. upload the fully-resolved HTML to Canvas (create or update via manifest)
       f. update manifest dict and flush to disk
4b. for each quiz folder in quizzes/ alphabetically:
     → skip if quiz .md AND all question files have mtime ≤ manifest last_synced
     → parse quiz-level .md (frontmatter + ordered question list)
     → parse each question .md file
     → run rewrite_links() on quiz description HTML and each question_text HTML (same as step 4d)
     → create or update quiz in Canvas (Classic Quizzes API)
     → delete all existing quiz questions, re-add in order
     → update manifest dict and flush to disk
4c. for each question bank in question_banks/ alphabetically:
     → skip if bank .toml mtime ≤ manifest last_synced (unless --force-uploads)
     → parse bank metadata .toml; parse each question .md in questions/
     → create Canvas question bank and populate with questions
     → update manifest dict and flush to disk
5. sync modules/ alphabetically         (all content IDs now guaranteed in manifest)
     → skip any module whose mtime ≤ manifest last_synced (unless --force-uploads)
```

**Processing order:**

Course settings and syllabus are applied first. Then `assets/`. Then regular content folders alphabetically. Then `quizzes/`. Then `question_banks/`. Finally `modules/`. All other content folders (`assignments/`, `discussions/`, `pages/`, etc.) are processed in alphabetical order, with files within each folder also sorted alphabetically. `course_settings/`, `question_banks/`, `quizzes/`, `snippets/`, and `assets/` are excluded from the regular content pass — each has its own dedicated phase.

Asset traversal is depth-first with files before subdirectories, both sorted alphabetically:

```text
assets/fig.png          ← files at this level first, alphabetically
assets/logo.png
assets/images/          ← then subdirectories, alphabetically
assets/images/chart.png
assets/images/diagram.png
assets/slides/
assets/slides/week1.pdf
```

**Asset upload detail:**

The `assets/` folder hierarchy is mirrored into Canvas Files. `assets/images/fig.png` is uploaded into a Canvas folder named `images` (not into the course root). This keeps the Canvas Files area organised and avoids name collisions.

**Post-Pandoc link-rewriting detail:**

- `<img src="../assets/images/x.png">` → look up Canvas file URL from manifest → rewrite src
- `<a href="../pages/foo.md">` → look up or stub-create → rewrite to `/courses/:id/pages/slug`
- `<a href="../assignments/bar.md">` → look up or stub-create → rewrite to `/courses/:id/assignments/:id`
- `<a href="../discussions/baz.md">` → look up or stub-create → rewrite to `/courses/:id/discussion_topics/:id`
- `<a href="../quizzes/foo/foo.md">` → look up → rewrite to `/courses/:id/quizzes/:id`
- `<a href="https://...">` → leave unchanged
- `<a href="#anchor">` → leave unchanged

**Stub creation:**

When a linked file has no Canvas ID yet, the tool creates a minimal placeholder in Canvas (title only, empty body, unpublished) purely to obtain the Canvas ID. The stub is overwritten with real content when that file is processed in the main loop. Content type for the stub is derived from the linked file's directory convention or its frontmatter `canvas_type` field.

**Manifest flushing:**

The manifest is flushed to disk immediately after every write (stub creation, asset upload, content upload) — not batched. This means an interrupted sync can be resumed without re-uploading already-completed items or losing Canvas IDs.

**Console output:**

```text
Uploading asset: assets/images/fig.png
Skipping (up-to-date): assets/slides/week1.pdf
Processing: assignments/week1.md
  Stub-creating: pages/syllabus.md (referenced but not yet synced)
  Uploading: assignments/week1.md
Skipping (up-to-date): discussions/week1-intro.md
Processing: pages/syllabus.md
  Uploading: pages/syllabus.md
Syncing module: modules/week-1.md
```

## `update` Subcommand

```text
Usage: github-to-canvas update [OPTIONS] REPO
```

`REPO` is the only required argument — a positional path to the course content repo. There is no `--repo` flag.

## CLI Options

### `--force-uploads`

Re-uploads every file regardless of its mtime vs `last_synced`. Bypasses the timestamp check for all file types (assets, content, modules).

### `--force-overwrite`

Skips the Canvas-side timestamp check entirely and always overwrites whatever is in Canvas. Use this when you know the local files are authoritative and want to avoid the extra API calls that the overwrite-protection check requires.

Without this flag, after a file passes the local mtime check, the tool fetches `updated_at` from Canvas for every item that already exists in the manifest. If Canvas has a newer version (i.e. someone edited the item directly in Canvas after the last sync) the upload is skipped and the file is added to a summary list printed at the end of the run — making it easy to review which items diverged before deciding whether to overwrite them.

### `--target-recursively / -t` (BFS selective sync)

Accepts a comma-separated list of local file paths. For each target, the tool:

1. Converts and uploads the file (subject to timestamp check, overridden by `--force-uploads`)
2. Extracts all locally-referenced files from its content:
   - Content files: local `<img src>` and `<a href>` targets found in the Pandoc-converted HTML
   - Module files: all items listed in the module body
3. Adds any unvisited referenced files to a BFS queue
4. Repeats until the queue is empty

Modules encountered during BFS are deferred until all other content in the BFS wave has been processed and has manifest entries, because `add_module_item` requires canvas IDs for all referenced content. This matches the ordering guarantee of the full sync.

The full course sync is skipped when `-t` or `-s` is present.

### `--single-target / -s` (non-recursive selective sync)

Accepts a comma-separated list of local file paths. Each file is converted and uploaded (subject to timestamp check) with no BFS traversal of its references. The full course sync is skipped.

### Combining `-t` and `-s`

`-t` runs first (full BFS). `-s` runs afterwards, independently — it does not consult `-t`'s visited set. Instead, the manifest `last_synced` timestamps written by `-t` prevent `-s` from re-uploading anything that `-t` just processed, because `needs_sync` compares file mtime against the freshly written `last_synced`.

This ordering means: run `-t` on a module to recursively re-sync everything it references, then run `-s` on a few additional targeted files without worrying about overlap.

### Target path resolution

Paths passed to `-t` and `-s` are resolved as follows:

- Absolute paths: used directly, then made relative to `--repo`
- Relative paths: resolved relative to the current working directory, then made relative to `REPO`

Paths that resolve outside the repo root print a warning and are skipped.

## `prune` Subcommand

```text
Usage: github-to-canvas prune [OPTIONS] REPO

  --delete       Delete the orphaned items from Canvas.
  --unpublish    Unpublish (set published=False) the orphaned items on Canvas.
  --config PATH  Path to canvas.toml (default: <repo>/canvas.toml)
```

Removes Canvas items whose local source file no longer exists. Because the
manifest is the only record of what the tool created, an entry is treated as an
**orphan** when `REPO / <local_key>` is gone from disk — which covers both
deleting a file and renaming one (a rename leaves the old path orphaned while the
new path syncs as a fresh item, per the path-keyed manifest).

Exactly one of `--delete` or `--unpublish` is required; there is no default, so
the destructive intent is always explicit. Changes are applied immediately (no
preview or confirmation prompt). Pandoc is **not** required for this subcommand.

### How it works

1. Load the manifest and compute the orphan set: every entry whose local file is missing.
2. For each orphan, dispatch by `canvas_type`:
   - `--delete`: fetch the object and call `.delete()`, then drop the manifest key.
   - `--unpublish`: set `published=False` on the object, then drop the manifest key.
   - The Canvas object is addressed by URL slug (`canvas_url`) for pages and by `canvas_id` for everything else — the same identifier rule used by the timestamp check.
3. Flush the manifest after each successful prune, so an interrupted run leaves a consistent file.
4. Print a `N deleted/unpublished, M skipped, K errors` summary.

A failure on one item is caught, reported as a warning, and does **not** abort the
run — that entry keeps its manifest key so it can be retried.

### Type support

| `canvas_type` | `--delete` | `--unpublish` |
| --- | --- | --- |
| `page`, `assignment`, `discussion`, `quiz`, `module` | ✅ deleted | ✅ unpublished |
| `file` | ✅ deleted | ⏭️ skipped (Canvas files use a hidden/locked state, not a `published` boolean) |
| `question_bank` | ⏭️ skipped (no reliable delete via `canvasapi`) | ⏭️ skipped (no unpublish concept) |
| `syllabus`, `course_settings`, `module_order` | ⏭️ skipped (bookkeeping / course fields, no standalone object) | ⏭️ skipped |

Skipped orphans print a warning and **retain** their manifest entry (nothing is
changed on Canvas), so they can be cleaned up manually if needed.

## `import` Subcommand

Converts an exported Canvas course (`.imscc` file) into a local Markdown repo ready for use with this tool.

```text
github-to-canvas import <imscc_path> <output_dir>
```

- `<imscc_path>`: `.imscc` zip file **or** a pre-extracted directory (auto-detected)
- `<output_dir>`: where the course repo is written (fails if non-empty)

**Implementation:** `src/github_to_canvas/imscc_import.py`

### IMSCC resource classification

| IMSCC type | href / file location | → category |
| --- | --- | --- |
| `webcontent` | `wiki_content/` | `page` |
| `webcontent` | `web_resources/` | `asset` |
| `imsdt_xmlv1p1` | `gXXX.xml` | `discussion` |
| `associatedcontent/...` | `gXXX/*.html` | `assignment` |
| `imswl_xmlv1p1` | `gXXX.xml` | `external_url` |
| `imsqti_xmlv1p2/...` | `gXXX/assessment_meta.xml` (standard) or via `<dependency>` (Canvas export) | `quiz` |
| `imsbasiclti_xmlv1p0/v1p3` | `<file href>` in `lti_resource_links/` or root | `lti` (no local output) |
| `associatedcontent/...` | `non_cc_assessments/*.xml.qti` (objectbank root child) | `question_bank` |
| `associatedcontent/...` | `non_cc_assessments/*.xml.qti` (assessment root child) | skipped (quiz-linked QTI, handled via quiz dependency) |

**Quiz detection — two manifest formats:**

Canvas uses two different manifest layouts for quizzes depending on export version:

- **Standard format**: `imsqti_xmlv1p2/` resource with `href="gXXX/assessment_meta.xml"` and a `<file>` child pointing to the QTI questions file. Both `meta_path` and `qti_path` come directly from this one resource.

- **Canvas export format**: `imsqti_xmlv1p2/` resource with `href=""` (empty) and a `<dependency>` child referencing a companion `associatedcontent/` resource. The companion resource's `href` points to `assessment_meta.xml` and its `<file>` children list both the meta file and a `non_cc_assessments/*.xml.qti` QTI file. The `non_cc_assessments/*.xml.qti` is preferred for question parsing because it uses `question_type` and `points_possible` metadata labels (vs. `cc_profile` labels in the CC format QTI in `gXXX/assessment_qti.xml`).

**LTI resources:** Canvas sets `href=""` on `imsbasiclti_` resource elements; the actual file path is in a `<file href="...">` child. The `imscc_path` is read from this child. LTI 1.3 resources cannot be round-tripped into a usable local format (tool installation is platform-specific), so no local output is written. LTI items in modules are handled separately via the module item's own `url` field.

**Question banks:** `non_cc_assessments/*.xml.qti` files whose root child element is `<objectbank>` (Canvas question pools) are classified as `question_bank` and converted to `question_banks/{slug}/`. Files whose root child is `<assessment>` (quiz-linked QTI) are skipped since the quiz's own dependency chain already handles them.

The `_syllabus` resource (`course_settings/syllabus.html`) → `course_settings/syllabus.md`.

### Processing phases

1. Extract zip to a temp dir if needed; pass directory through unchanged
2. Parse `imsmanifest.xml` → build in-memory resource map (`gXXX → {category, local_path, title, ...}`)
3. Copy `web_resources/` → `assets/`, preserving subdirectory structure
4. **Pages:** strip `<html>/<head>/<body>` wrapper, rewrite internal links, Pandoc HTML→Markdown, write `pages/{stem}.md` with `title` and `published: true` frontmatter
5. **Assignments:** read `gXXX/assignment_settings.xml` for title, points_possible, due_at, lock_at, unlock_at, submission_types, grading_type, workflow_state; convert HTML body; write `assignments/{stem}.md`
6. **Discussions:** parse topic XML body and paired topicMeta for title, published, require_initial_post; skip announcements with warning; if `<attachments>` block present, append a `## Attachments` section with `../assets/{href}` links; write `discussions/{slugify(title)}.md`
6b. **Quizzes:** read `gXXX/assessment_meta.xml` for quiz settings; parse QTI 1.2 XML (`gXXX/gXXX.xml`) for questions; write `quizzes/{slug}/{slug}.md` and one file per question under `quizzes/{slug}/questions/`; unsupported question types emit a warning and are skipped
6c. **Question banks:** parse `non_cc_assessments/*.xml.qti` objectbank files; read bank metadata (bank_title, bank_context_uuid, bank_state) from `<qtimetadata>`; parse all `<item>` children as questions (same QTI format as quizzes, plus `original_answer_ids` metadata); write `question_banks/{slug}/{slug}.toml` and one question file per item under `question_banks/{slug}/questions/`
7. **Modules:** read `course_settings/module_meta.xml`; emit items in position order (see below); write `modules/{slugify(title)}.md`
8. **Course settings:** collect data from all `course_settings/*.xml` files and `imsmanifest.xml` metadata; write:
   - `course_settings.toml` (root) — all course-level settings (see below)
   - `course_settings/syllabus.md` — syllabus HTML converted to Markdown
   - `course_settings/events.md` — calendar events (if any exist in `events.xml`)
   - `course_settings/rubrics.toml` — rubric definitions with criteria and ratings (if any exist in `rubrics.xml`)
   - `course_settings/files_meta.toml` — file visibility/lock metadata (from `files_meta.xml`)
   - `canvas.toml` (root) — connection config skeleton pre-populated from `context.xml`
   - `media_tracks.xml` and `canvas_export.txt` are skipped (no useful round-trip content)

### Internal link rewriting

Applied to raw HTML before calling Pandoc (phases 4–6):

| Source link | Rewritten to |
| --- | --- |
| `$CANVAS_OBJECT_REFERENCE$/assignments/gXXX` | `../assignments/foo.md` |
| `$CANVAS_OBJECT_REFERENCE$/pages/gXXX` | `../pages/foo.md` |
| `$CANVAS_OBJECT_REFERENCE$/discussion_topics/gXXX` | `../discussions/foo.md` |
| `$CANVAS_OBJECT_REFERENCE$/modules/gXXX` | warn + leave as plain text (no href) |
| `$IMS-CC-FILEBASE$/path/to/file` | `../assets/path/to/file` |
| `https://...` | unchanged |

Unknown `gXXX` not in resource map → warn and remove href, keep link text.

### Module file generation

- `ContextModuleSubHeader` → `## Title` heading
- `WikiPage` / `Assignment` / `Discussion` / `DiscussionTopic` / `Quizzes::Quiz` → `- [display_title](../type/file.md)` (`DiscussionTopic` is the name used in real Canvas IMSCC exports; `Discussion` is the IMS CC name — both are handled)
- `ExternalUrl` → `- [display_title](https://url)` — if the linked webLink resource has `target` or `windowFeatures` attributes on its `<url>` element, they are appended as an HTML comment: `<!-- target="_blank" windowFeatures="width=800" -->`
- `ContextExternalTool` (LTI embedded tool) → `- [display_title](url)` (URL comes from the module item's own `url` field, not the LTI resource XML)
- `Attachment` (Canvas File) → `# SKIPPED: Attachment - "title"` comment line + printed warning (no local file equivalent)

### Course settings output (`course_settings.toml`)

A TOML file written at the repo root capturing all course-level metadata. Sources and content:

| Source file | Fields extracted |
| --- | --- |
| `imsmanifest.xml` (lom metadata) | `last_modified`, `copyright_restrictions`, `copyright_description` |
| `course_settings.xml` | All elements: title, course_code, dates, visibility flags, grading settings, tab configuration, post policy, etc. |
| `grading_standards.xml` | `[[grading_standards]]` — title, data (threshold array), points_based, scaling_factor |
| `assignment_groups.xml` | `[[assignment_groups]]` — title, position, group_weight, rules (drop_lowest etc.) |
| `late_policy.xml` | `[late_policy]` — deduction enablement, deduction amounts, interval |
| `context.xml` | `canvas_domain` → pre-fills `canvas.toml` base_url; `course_id` → pre-fills `canvas.toml` course_id |

### Additional course settings outputs

| Output file | Source | Content |
| --- | --- | --- |
| `course_settings/rubrics.toml` | `rubrics.xml` | `[[rubrics]]` — identifier, title, boolean flags, points_possible, rating_order; nested `[[rubrics.criteria]]` with description, long_description, points; nested `[[rubrics.criteria.ratings]]` with id, description, long_description, points |
| `course_settings/files_meta.toml` | `files_meta.xml` | `[[folders]]` — path, hidden; `[[files]]` — identifier, locked, hidden, display_name, unlock_at |

Boolean fields (`true`/`false`) are stored as TOML booleans. Numeric fields are stored as int or float. Empty elements are omitted. The nested `default_post_policy` element is stored as a TOML inline table.

`canvas.toml` is written with `base_url` pre-filled from `context.xml`'s `canvas_domain` (instead of a placeholder). If `context.xml` is absent, the placeholder is used.

### Key behaviours

- **No `.canvas-manifest.toml` written** — IMSCC `gXXX` identifiers are not real Canvas numeric IDs; the first `sync` run creates all items and populates the manifest with real IDs.
- **`course_settings/` directory** — syllabus, events, and any other converted HTML content land here.
- **`course_settings.toml` at root** — all course-wide settings (not in `course_settings/` subdir) so they are immediately visible.
- **Graded discussion metadata captured** — `points_possible`, `due_at`, etc. written to frontmatter even if not currently used by sync.
- **Quiz question slugification** — question filenames are derived from the QTI `title` attribute via `_slugify()`. Special characters (e.g. `+`) are stripped, so "What is 2+2?" becomes `what-is-22.md`.

### Console output style

```text
Extracting: course.imscc → /tmp/...
Copying asset: assets/Images/logo.png
Converting page: pages/syllabus.md
Converting assignment: assignments/week-1-problem-set.md
Converting discussion: discussions/week-01-forum.md
  WARNING: Skipping announcement: "Coding Exercises 07 has been graded"
  Converting question: quizzes/week-1-quiz/questions/what-is-2-plus-2.md
  Converting question: quizzes/week-1-quiz/questions/explain-gravity.md
Converting quiz: quizzes/week-1-quiz/week-1-quiz.md
Generating module: modules/getting-started.md
Done. Wrote course repo to: ./my-course/
```

## `publish` Subcommand

Generates a public [MkDocs](https://www.mkdocs.org/) + [Material](https://squidfunk.github.io/mkdocs-material/)
static website from the local course repo and optionally deploys it to GitHub
Pages. The site mirrors Canvas's left-sidebar navigation model so it feels
familiar, without exposing any student data. This is a read-only export — it
never touches Canvas, the API, or `.canvas-manifest.toml`.

```text
github-to-canvas publish [COURSE_DIR] [--output-dir site] [--deploy] [--emit-workflow]
```

- `COURSE_DIR`: the course content repo (defaults to `.`)
- `--output-dir`: where `mkdocs build` writes the static HTML (default: `site/`)
- `--deploy`: run `mkdocs gh-deploy` (push to the repo's `gh-pages` branch) instead of a local build
- `--emit-workflow`: also write a starter `.github/workflows/publish.yml` into the course repo

**Implementation:** `src/github_to_canvas/publish.py`

**Optional dependency:** MkDocs and Material are an opt-in extra so the rest of
the tool installs without them. Install with `uv tool install github-to-canvas[publish]`
(or `pip install mkdocs mkdocs-material`). If `mkdocs` is not on `PATH`, the
subcommand exits via `die()` with an install hint. Pandoc is **not** needed for
the publish flow — page/assignment/discussion bodies are staged as Markdown and
MkDocs renders them.

### What it does

1. Determine the site name from `course_settings.toml` (`title` / `name` /
   `course_code`), falling back to the course directory name.
2. Build the `nav:` tree from `modules/` (alphabetical, matching the sync
   order). Each module becomes a top-level nav section; the module's own `.md`
   becomes a clickable overview/index page (Material `navigation.indexes`).
   SubHeaders (`## Heading` lines) become nested nav groups; `ExternalUrl`
   items become absolute-URL nav links.
3. Stage a temporary MkDocs tree (`mkdocs.yml` + `docs/` + `overrides/`) and run
   `mkdocs build --site-dir <output-dir>` (or `mkdocs gh-deploy --force` with
   `--deploy`), with the working directory set to `COURSE_DIR` so `gh-deploy`
   finds the course repo's git remote.

### Content selection

**Only content referenced by a module is published** — orphaned pages,
assignments, discussions, and quizzes are excluded. Per-content handling:

| Content | Published as |
| --- | --- |
| Pages / assignments / discussions | Markdown body, frontmatter stripped, snippet includes expanded inline, an H1 prepended if the body has none. Cross-links and asset links are left as-is (the staged `docs/` mirrors the repo layout, so relative links resolve). |
| Quizzes | A single readable study-guide page (`docs/quizzes/<slug>.md`) with the description and each question's prompt and answer choices; correct choices are marked `**(correct)**`. The `quizzes/<slug>/<slug>.md` folder structure is flattened, and links pointing at it are rewritten. |
| Assets | `assets/` is copied wholesale into `docs/assets/` so every referenced image/file resolves. |

### Canvas-like styling

`docs/stylesheets/extra.css` sets Material's CSS variables to Canvas's charcoal
sidebar (`#2D3B45`) and orange accent (`#E66000`), plus an active-item left
border. `overrides/main.html` extends Material's `base.html` and prepends the
course name to the navigation drawer via the `site_nav` block (a no-op on theme
versions lacking that block, so it never breaks the build).

### Publish console output

```text
Staging site in: /tmp/g2c-publish-xxxx
  Staging module: modules/week-1.md
  Staging content: assignments/week1.md
  Staging content: pages/syllabus.md
Site: Intro to CS  (1 module(s), 3 content file(s))
Running: mkdocs build --site-dir /abs/site -f /tmp/g2c-publish-xxxx/mkdocs.yml
Built static site: /abs/site
```

## Configuration

### Tool config file (`canvas.toml`)

Provided once per course repo, checked into git:

```toml
base_url = "https://yourschool.instructure.com"
course_id = 12345

[auth]
# Prefer env var CANVAS_API_TOKEN; this is a fallback for local use only
api_token = ""
```

The API token should be passed via the `CANVAS_API_TOKEN` environment variable rather than committed to the repo. On startup the tool searches for a `.env` file starting from the current working directory (walking up the directory tree) and loads it automatically, so a `.env` in your course repo or any parent directory is picked up without any extra steps.

### Content mapping

Content type is determined by **directory convention** — no explicit config needed for the common case:

```text
pages/          → Canvas Pages
assignments/    → Canvas Assignments
discussions/    → Canvas Discussion Topics
quizzes/        → Canvas Quizzes (Classic) — nested structure, see below
modules/        → Canvas Modules (special — see below)
```

The directory name maps directly to the Canvas content type. A frontmatter `canvas_type` field can override the default if a file lives outside these directories.

### Per-file metadata (YAML frontmatter)

Each Markdown file carries its Canvas-specific metadata in YAML frontmatter. The body of the file (everything after the frontmatter block) is the content that gets converted to HTML.

**Common fields (all types):**

```yaml
---
title: "Week 1 Introduction"
published: true
---
```

**Assignment-specific fields:**

```yaml
---
title: "Week 1 Problem Set"
canvas_type: assignment      # optional if file is in assignments/
points_possible: 50
due_at: "2025-02-01T23:59:00-05:00"
lock_at: "2025-02-08T23:59:00-05:00"
unlock_at: "2025-01-27T00:00:00-05:00"
grading_type: "points"       # points | percent | letter_grade | gpa_scale | pass_fail
submission_types: [online_upload]
published: true
---
```

**Discussion-specific fields:**

```yaml
---
title: "Introduce Yourself"
require_initial_post: true
published: true
---
```

Graded discussions additionally accept these fields, which are passed to Canvas as nested assignment params:

```yaml
---
title: "Week 1 Discussion"
require_initial_post: true
points_possible: 10
due_at: "2025-02-01T23:59:00-05:00"
lock_at: "2025-02-08T23:59:00-05:00"
unlock_at: "2025-01-27T00:00:00-05:00"
published: true
---
```

**Page-specific fields:**

```yaml
---
title: "Syllabus"
editing_roles: teachers
published: true
---
```

### Quiz file format

Quizzes use a **nested folder structure** — each quiz lives in its own sub-folder under `quizzes/`, named with the slugified quiz title. Questions are stored as individual files in a `questions/` sub-folder.

```text
quizzes/
└── my-quiz/
    ├── my-quiz.md          # quiz-level file (same name as folder)
    └── questions/
        ├── question-one.md # individual question files (human-readable names)
        └── question-two.md
```

**Quiz-level file** (`quizzes/{slug}/{slug}.md`):

The frontmatter holds quiz settings. The body is an optional description shown to students before they begin, followed by a numbered list of links to question files. The link order defines the question order in Canvas.

```yaml
---
title: "Week 1 Quiz"
quiz_type: assignment        # assignment | practice_quiz | graded_survey | survey
points_possible: 6.0
time_limit: 30               # minutes; omit if no time limit
allowed_attempts: 1
shuffle_answers: false
show_correct_answers: true
published: true
---

Read each question carefully before answering.

1. [What is 2+2?](questions/what-is-2-plus-2.md)
2. [Explain gravity](questions/explain-gravity.md)
```

**Multiple choice question** (`question_type: multiple_choice_question`):

`correct` is the 1-based index of the correct answer in the `## Answers` list.

```yaml
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
```

**True/false question** (`question_type: true_false_question`):

No `## Answers` section — Canvas always provides "True" and "False". `correct` is the boolean value.

```yaml
---
title: "The sky is blue"
question_type: true_false_question
points_possible: 1
correct: true
---

The sky appears blue during the day due to Rayleigh scattering.
```

**Essay question** (`question_type: essay_question`):

No `correct` field or `## Answers` section — manually graded.

```yaml
---
title: "Explain gravity"
question_type: essay_question
points_possible: 5
---

In 3–5 paragraphs, explain the concept of gravity and how it affects objects with different masses.
```

**Multiple-response question** (`question_type: multiple_response_question`):

`correct` is a list of 1-based indices of all correct answers. Answers section is the same format as MCQ.

```yaml
---
title: "Select all prime numbers"
question_type: multiple_response_question
points_possible: 2
correct: [1, 2, 4]
---

Which of the following are prime numbers? Select all that apply.

## Answers

1. 2
1. 3
1. 4
1. 5
```

**Fill-in-blank question** (`question_type: fill_in_blank_question`):

`answers` is a list of all accepted correct strings (case-insensitive exact match). No `## Answers` section.

```yaml
---
title: "Speed of light"
question_type: fill_in_blank_question
points_possible: 1
answers: [300000, 300 000]
---

The speed of light is approximately _____ km/s.
```

**Pattern-match question** (`question_type: pattern_match_question`):

`answers` lists accepted patterns (case-insensitive substring match). `match_type: substring` signals the matching mode.

```yaml
---
title: "Name a language"
question_type: pattern_match_question
points_possible: 1
answers: [python, r language]
match_type: substring
---

Name a programming language used in data science.
```

**Question feedback** (all types): If the QTI item has `<itemfeedback>` elements, a `## Feedback` section is appended after the question text / answers with subsections `### General`, `### Correct`, `### Incorrect`, and `### Per-answer` (only those present in the source). Per-answer feedback lists each answer by 1-based index.

**Essay sample solution**: If the QTI item has `<itemfeedback ident="solution">`, a `## Sample Solution` section is appended after the question text.

**Quiz manifest entry:**

The manifest key is the quiz-level `.md` path. `canvas_question_ids` maps each question file's path (relative to the quiz folder) to its Canvas question ID.

```toml
["quizzes/my-quiz/my-quiz.md"]
canvas_type = "quiz"
canvas_id = 12345
last_synced = "2025-02-01T10:00:00"
canvas_question_ids = {"questions/what-is-2-plus-2.md" = 111, "questions/explain-gravity.md" = 222}
```

**Quiz sync behaviour:**

- The quiz is re-synced if the quiz `.md` file **or any question file** has mtime newer than `last_synced`. A change to a single question triggers a full quiz re-sync.
- On each sync, all existing Canvas questions are deleted and re-created in the order listed in the quiz `.md`. This keeps question order correct and avoids stale questions after edits.
- Canvas module items reference the quiz by `canvas_id` and use module item type `"Quiz"`.
- Quiz description HTML and question text HTML are passed through `rewrite_links()` before upload, so cross-links to other course content resolve to correct Canvas URLs.

**Supported question types:** `multiple_choice_question`, `true_false_question`, `essay_question`, `multiple_response_question`, `fill_in_blank_question`, `pattern_match_question`. Other types emit a warning and are skipped.

**Effectively required fields per question type** (missing them won't crash the upload, but the question will be ungradable in Canvas):

| Question type | Field | Effect if missing |
| --- | --- | --- |
| `multiple_choice_question` | `correct` (1-based int) | No answer marked correct |
| `multiple_choice_question` | `## Answers` section | No answer choices at all |
| `true_false_question` | `correct` (bool) | Neither True nor False marked correct |
| `multiple_response_question` | `correct` (list of 1-based ints) | No answers marked correct |
| `multiple_response_question` | `## Answers` section | No answer choices at all |
| `fill_in_blank_question` | `answers` (list of strings) | No accepted answers |
| `pattern_match_question` | `answers` (list of patterns) | No accepted patterns |

All other fields across all resource types (`title`, `published`, dates, points, etc.) have safe defaults — nothing will crash or be skipped if they are absent.

### Question bank file format

Canvas question banks (pools) are written to `question_banks/{slug}/`:

```text
question_banks/
└── unfiled-questions/
    ├── unfiled-questions.toml   # bank metadata
    └── questions/
        ├── how-many-exams.md    # one file per question (same format as quiz questions)
        └── ...
```

**Bank metadata TOML** (`question_banks/{slug}/{slug}.toml`):

```toml
bank_title = "Unfiled Questions"
bank_context_uuid = "SRI51UyJjHbdsdzYFn1LYxMYjMYh4GITEORKR38K"
bank_state = "active"
```

**Bank question files** use the same format as quiz question files, with one additional frontmatter field:

`original_answer_ids` — Canvas-internal answer IDs needed to maintain stable answer identity on re-import. Present only on choice-based questions (MCQ, multiple-response) where Canvas assigned them.

```yaml
---
title: "How many exams?"
question_type: multiple_choice_question
points_possible: 1.0
correct: 3
original_answer_ids: [8230, 5348, 7678, 5601]
---
```

Note: quizzes that draw from question banks export their questions **inline** in the quiz's own QTI file. There is no "draw N from bank X" reference in the IMSCC output. Question banks and quizzes are independent exports. Deleted banks (`bank_state = "deleted"`) are still imported in deleted state to preserve round-trip fidelity.

**Question bank manifest entry:** The manifest key is the bank `.toml` path. `canvas_type = "question_bank"`.

### Course settings files

These files are never uploaded as Canvas Pages. Each has a dedicated upload path:

| File | Upload behaviour |
| --- | --- |
| `course_settings.toml` (repo root) | Applied via `course.update()` for flat metadata; dedicated API calls for grading standards, assignment groups, late policy, post policy, and rubrics |
| `course_settings/syllabus.md` | Body converted to HTML and set as `course.syllabus_body` via `course.update()` |
| `course_settings/events.md` | Not yet uploaded (future feature) |
| `course_settings/rubrics.toml` | Each rubric created via `course.create_rubric()` if title not already present |
| `course_settings/files_meta.toml` | Not yet uploaded (requires matching Canvas file IDs after asset upload) |

**`course_settings.toml` upload detail:**

The following sections are handled separately from the flat `course.update()` call:

- `[[grading_standards]]` — each entry created via `course.create_grading_standard()` if title not already present; first standard's ID passed as `grading_standard_id` in the course update
- `[[assignment_groups]]` — each entry created or updated via `course.create_assignment_group()` / `ag.edit()`, matched by name; processed in `position` order
- `[late_policy]` — applied via `PATCH /api/v1/courses/:id/late_policy` (raw requester call, not wrapped in `canvasapi`)
- `[default_post_policy]` — applied via `PUT /api/v1/courses/:id/post_policies` (raw requester call)

Read-only or infrastructure fields (`storage_quota`, `root_account_uuid`, `image_identifier_ref`, `last_modified`, `copyright_restrictions`, `copyright_description`, and others) are present in the TOML for round-trip fidelity but are silently ignored by the uploader.

### Module file format

Module files differ from content files: they don't have a body that becomes HTML. Instead, the frontmatter holds module attributes and the body is a Markdown list of links to local content files. The order of links defines the order of items in the Canvas module.

```markdown
---
title: "Week 1: Introduction"
published: true
unlock_at: 2025-01-20T00:00:00-05:00
require_sequential_progress: false
---

- [Syllabus](../pages/syllabus.md)
- [Week 1 Lecture Notes](../pages/week1-lecture.md)
- [Week 1 Assignment](../assignments/week1.md)
- [Week 1 Discussion](../discussions/week1-intro.md)
```

The link text becomes the display title of the item within the module. The link target is either:

- A relative path to a local content file (page, assignment, discussion, or quiz)
- An absolute URL (`https://...`) — rendered as a Canvas ExternalUrl module item

**External URL items** with a `target="_blank"` attribute (written as an HTML comment on the same line by the importer) open in a new tab in Canvas:

```markdown
- [Course Website](https://example.com) <!-- target="_blank" windowFeatures="width=800" -->
```

The `target` attribute is mapped to Canvas's `new_tab` boolean; `windowFeatures` is discarded (no Canvas equivalent). External URL items without a comment default to `new_tab: false`.

**Section sub-headers** within a module (Canvas calls these `SubHeader` items) are represented as Markdown headings in the body:

```markdown
---
title: "Week 1: Introduction"
published: true
---

## Readings

- [Week 1 Lecture Notes](../pages/week1-lecture.md)

## Work

- [Week 1 Assignment](../assignments/week1.md)
- [Week 1 Discussion](../discussions/week1-intro.md)
```

**Synchronisation notes:**

- All content files linked from a module must already exist in Canvas (i.e., have entries in the manifest) before the module can be synced. The tool should sync content first, modules second.
- Canvas modules hold a flat ordered list of items. The tool syncs this by comparing the desired item list (derived from the Markdown) against the current Canvas module items and adding, removing, or reordering as needed.
- The manifest tracks both the module's Canvas ID and the Canvas item IDs within it (needed to reorder or delete individual items).

### Manifest file (`.canvas-manifest.toml`)

The tool maintains a `.canvas-manifest.toml` in the course repo that maps local file paths to their Canvas IDs. The tool reads this file to decide whether to create or update, and writes to it after each successful publish.

```toml
# .canvas-manifest.toml — commit this file so collaborators share the same Canvas ID mapping

["pages/syllabus.md"]
canvas_id = 11111
canvas_type = "page"
last_synced = "2025-02-01T10:00:00"

["assignments/week1.md"]
canvas_id = 98765
canvas_type = "assignment"
last_synced = "2025-02-01T10:01:00"

["modules/week-1.md"]
canvas_id = 55555
canvas_type = "module"
last_synced = "2025-02-01T10:02:00"
# canvas_item_ids maps each linked file path to its Canvas module item ID
# (needed to reorder or delete individual items within the module)
canvas_item_ids = {"pages/syllabus.md" = 201, "assignments/week1.md" = 202}
```

Source Markdown files stay clean — no tool-written fields mixed in with author-written frontmatter. On first publish the tool creates the item, records the Canvas ID in the manifest, and writes the updated manifest back to disk. On subsequent runs it looks up the Canvas ID from the manifest and updates the existing item.

## Snippets (`snippets/` directory)

A `snippets/` directory at the repo root holds reusable Markdown fragments. Content files include a snippet using a **normal Markdown link** whose target path resolves into `snippets/`. The preprocessor detects these links and replaces the entire `[text](path)` token with the raw Markdown content of the snippet file, before Pandoc ever runs. Students see the rendered content inline — not a hyperlink.

Example use case: `snippets/office-hours.md` contains your current office hours. Reference it from a dozen pages; update once, re-sync, and the change propagates everywhere.

**Include syntax — standard Markdown links:**

```markdown
[My Office Hours](../snippets/office-hours.md)
```

Any link whose resolved path falls inside the `snippets/` directory triggers inclusion. The link text is discarded and replaced by the snippet's Markdown content.

A snippet containing a single sentence or phrase pastes in cleanly mid-sentence — Markdown treats a single embedded newline as a space. A snippet containing block-level elements (headings, blank lines between paragraphs, lists, code blocks) will break out of any surrounding sentence, so those should be placed as standalone blocks in the including file. The snippet's content determines where it can sensibly be used.

Markdown editors render the include as a normal clickable link, making it easy to navigate to the snippet source to see what will be inlined.

**Behaviour:**

- Substitution happens before Pandoc, so snippet Markdown (headings, lists, etc.) renders naturally as HTML
- Snippet files contain only Markdown body content — no frontmatter
- Snippets are never uploaded to Canvas and have no manifest entries
- **Nested includes are not supported.** If a snippet contains a link to another snippet, the tool prints an error message and leaves the inner link as a plain hyperlink (it is not expanded). This also prevents circular includes.
- Links inside a snippet that point to content files (pages, assignments, etc.) are treated normally by the post-Pandoc link-rewriting step — they will be rewritten to Canvas URLs just like any other link
