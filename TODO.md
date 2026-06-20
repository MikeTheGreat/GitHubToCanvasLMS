# Possible Future Features

## `read_only` flag not respected when Canvas restores a soft-deleted rubric

When a rubric is deleted from Canvas and re-created by the sync tool, Canvas
appears to restore the soft-deleted rubric (matched by title) with its original
`read_only` value. Neither the `create_rubric` POST nor a follow-up PUT
successfully overrides `read_only` in this scenario. The PUT works fine for
rubrics that were never deleted — only the delete-and-recreate path is affected.

Possible workaround: create the rubric with a slightly different title to avoid
the soft-delete match, then rename it via PUT. Untested.

## Group sets and group assignments
Currently we don't manage this, but it would be nice

## Only set front page when relevant files have changed

Currently `run_sync` reads `front_page` from `course_settings/course_settings.toml` at startup and
calls `set_front_page` on every run, regardless of whether `course_settings/course_settings.toml` or
the target page's `.md` file has actually changed. It should only call `set_front_page`
when one of those two files has been re-synced in the same run.

## all commands must use die() for user-facing errors — no tracebacks, no raw exceptions.

## Import coverage gaps (found via pool sampling)

Running `check_imscc_coverage.py` with pool sampling against the `it-cs142` course
revealed real content that doesn't survive the `import` command. Reproduce with:

```sh
python scripts/check_imscc_coverage.py \
    it-cs142-imscc-unzipped Test_Import \
    --pool-samples 100 --seed 7 --categories ""
```

### Quiz/rubric body text not surfaced as Markdown

Rubric criterion text and quiz question bodies live in QTI XML and quiz rubric XML
but are not currently imported as any `.md` file. Examples found missing:

- `'Is the code clearly written, consistently and readably formatted, and'`
- `'Good 1.0 _7898 One or more categories of style are'`
- `'The assignment specified that you should use methods to finish'`
- `'is reasonably formatted blank Emerging 15.0 _1517 A few results'`
- `'Processing File I/O: Processing Complex Files 2024_Winter_Question_1_Example_Solution public class Answer'`

These come from quiz question prompts and assignment rubrics embedded in the IMSCC.
The rubric importer is not yet implemented; the quiz importer converts questions but
question body HTML isn't always round-tripping cleanly through Pandoc for all types.

### Page/assignment prose that was silently dropped

Plain prose from pages and assignments that should have been converted but is absent
from `Test_Import`. These point to either pages that were skipped entirely or content
inside HTML that the importer's `_extract_html_body` or Pandoc step lost:

- `'for that one other class. How To Find Due Dates'`
- `'Exam II Study Guide Exam II will be in-class. Please'`
- `'at the top (which lists your name, class, assignment number,'`
- `"image below) Once you've found the repo you can confirm"`
- `'find the Privacy & Security tab . Click on it'`
- `"don't fill this out then then instructor will assume that"`
- `"I'd rather receive thoughtful constructive criticism than answers engineered to"`
- `'Reference vs. value semantics Exam IV Study Guide Exam IV'`
- `'a merge conflict . For example, if you change the'`

To investigate: grep for a fragment in `it-cs142-imscc-unzipped` to find the source
file, then check whether the corresponding output file exists in `Test_Import` and
whether the text is present there.

## Unimplemented upload paths from `course_settings/`

These files are produced by the importer but have no upload path yet:

- **`course_settings/events.md`** — Calendar events. Requires parsing `## Title` / `**Date:**` sections back into structured event data and calling `canvas.create_calendar_event()` per event. Complex (deduplication, update-vs-create). See UPLOADER_CHANGES.md §3.
- **`course_settings/files_meta.toml`** — File visibility (`locked`, `hidden`, `display_name`, `unlock_at`) and folder visibility. Requires Canvas file IDs from the manifest (needs matching by `local_path` or `display_name`). Import-side fix also needed: `_write_files_meta_toml()` should write `local_path` alongside each file entry. See UPLOADER_CHANGES.md §16.

## External-tool nav-tab label resolution via tool-aliases file

**Problem.** Canvas does not export the names of external tools used only in course
navigation, so `import` writes those `tab_configuration` entries with an empty
`label = ""` placeholder.  The tab id is a Canvas migration hash
(`context_external_tool_g<md5>`) computed from the tool's numeric installation id
on the source course.  A different user importing the cartridge typically cannot
query the source course's API (unauthorized), and even querying their own courses
fails because tools installed in a different sub-account have different numeric ids
and therefore different hashes.

**Proposed feature: `generate-tool-aliases` subcommand.**

The person who *exports* the IMSCC (who has access to the source course) runs a
prep step that resolves tool names using their own credentials and embeds the
result in the cartridge for downstream importers.

Workflow:

1. **Exporter** runs:

   ```bash
   github-to-canvas generate-tool-aliases <path-to-export.imscc>
   ```

   This subcommand:
   - Opens the IMSCC (zip) and parses `course_settings/course_settings.xml` to
     extract the `tab_configuration` entries with `context_external_tool_…` ids.
   - Uses `CANVAS_API_TOKEN` + the `canvas_domain` / `course_id` from
     `course_settings/context.xml` to call `course.get_tabs()` on the source
     course.
   - For each external-tool tab, computes the migration hash
     (`"g" + md5("context_external_tool_<numeric_id>")`) and maps it to the
     tab's label.
   - Also stores the plain numeric id as a fallback key (for per-course-installed
     tools that appear with a raw numeric id instead of a `g`-prefixed hash).
   - Writes the mapping as a TOML file (e.g. `tool_aliases.toml`) and adds it
     into the IMSCC zip at a well-known path (e.g. top-level `tool_aliases.toml`
     or under `course_settings/`).

   Example `tool_aliases.toml`:

   ```toml
   # Auto-generated by: github-to-canvas generate-tool-aliases
   "gd9568f5b0d2a343486654adb2ae69aac" = "Zoom"
   "g67e4019c6ea3ce88e6856319395ed4e4" = "Panopto Recordings"
   "758383" = "Library"
   ```

2. **Importer** runs `github-to-canvas import <path-to-export.imscc> <output>` as
   usual.  `_resolve_tool_titles()` checks for `tool_aliases.toml` inside the
   cartridge and loads it before falling back to BLTI XML.

Precedence for tool name resolution:

- BLTI `<blti:title>` from the cartridge (already implemented, rarely available)
- `tool_aliases.toml` in the cartridge (new)
- Empty `label = ""` placeholder with warning (existing fallback)

**Implementation notes.**

- The hash algorithm is Canvas's `CC::CCHelper.create_key`:
  `"g" + md5("context_external_tool_<numeric_id>")`.  See `canvas_api.py` for
  `TOOL_TAB_PREFIX`.
- The subcommand needs to handle both zip files and extracted directories.
- No new CLI flags on `import` — it just looks for the file in the cartridge.
- The exporter must have access to the source course (they exported it, so they
  do).  This avoids the authorization problem that sinks any approach based on the
  *importer's* credentials.

## Round-trip fidelity gaps (import → sync)

Fields written by the importer that are silently ignored on upload:

### `original_answer_ids` and `### Per-answer` feedback are never re-uploaded

`imscc_import._write_question_file` emits `original_answer_ids` in YAML frontmatter
and a `### Per-answer` block under `## Feedback`, but neither is consumed on sync:

- `canvas_api._build_question_params` does not pass `original_answer_ids` to Canvas.
- `quiz._parse_feedback_section` only reads `### General`, `### Correct`, and
  `### Incorrect`; the `### Per-answer` block is silently ignored.

These fields round-trip through the file but are never re-uploaded. Either consume
them on upload or document clearly that they are import-only metadata (the README
currently notes this).

### `pattern_match_question` only uploads the first pattern

`quiz.py:180` uses `patterns[0]` when building the answer list for
`pattern_match_question`, so only the first entry in `answers:` is ever sent to
Canvas. If multiple accepted patterns are listed, the rest are silently dropped.
Decide whether to fix this (iterate over all patterns) or document it as intentional.

### `fill_in_blank_question` and `pattern_match_question` both become `short_answer_question`

Both types are converted to Canvas `short_answer_question` in `quiz.py`. The
distinctions (`pattern_match_question` uses substring matching; Canvas also has
`fill_in_multiple_blanks_question`) are lost on upload. This is documented in the
README. Confirm this is the intended mapping or add separate handling.

## Re-sync is not idempotent for question banks and rubrics

Unlike pages/assignments/discussions/quizzes (which look up the existing Canvas item
by manifest `canvas_id` and update it in place), question banks and rubrics have no
update path:

### Question banks create a duplicate on every re-sync

`canvas_api.sync_question_bank` always calls `course.create_question_bank(...)`; it
never consults the manifest's existing `canvas_id` to update or replace the prior
bank, and the old bank is never deleted. So whenever a bank is re-synced (its
`.toml` is newer than `last_synced`, or `--force-uploads` is used) a **second bank
with the same name is created in Canvas**, leaving the old one behind.

Note also that `_sync_question_banks`'s `needs_sync` check only looks at the bank's
`.toml` mtime — editing a question `.md` file alone does **not** trigger a re-sync.

**Why this is hard to fix (investigated 2026-06):** unlike the other content types,
there is no supported API to update or delete a question bank, so the usual
"look up `canvas_id` and update in place" pattern cannot be applied.

- **canvasapi (3.6.0) has no question-bank support at all.** `course.create_question_bank()`
  and `bank.create_assessment_question()` are not methods on the library's `Course`
  object (verified: `hasattr(Course, "create_question_bank")` → `False`). The current
  upload path therefore **cannot run against a real Canvas** — it would raise
  `AttributeError`. The unit tests only pass because the test course is a `MagicMock`
  that auto-fabricates any attribute, so this is invisible in CI.
- **The documented Canvas REST API for Assessment Question Banks is read-only.**
  `/doc/api/assessment_question_banks.html` documents exactly three endpoints, all GET:
  `GET /api/v1/question_banks`, `GET /api/v1/question_banks/:id`, and
  `GET /api/v1/question_banks/:id/questions`. There is **no public POST/PUT/DELETE**
  for banks, nor for the assessment questions inside them. (Confirmed on two official
  mirrors.)
- **GraphQL does not cover them either** — Canvas's GraphQL API exposes no
  assessment-question-bank create/update/delete mutations.
- **The only write path is undocumented UI controller routes** —
  `POST/PUT/DELETE /courses/:id/question_banks...` (and `.../questions`), which the Canvas
  web UI uses. These are unstable across Canvas releases and frequently require
  session/CSRF auth rather than a bearer token, so they may simply reject the API token
  on a given instance.

**Fix options, in order of preference:**

1. Verify whether the undocumented UI routes accept bearer-token auth on the target
   Canvas instance (a small read-only probe first, e.g. `GET /courses/:id/question_banks`).
   If they do, implement delete-then-recreate via raw `course._requester.request(...)`
   calls (same pattern already used for `update_late_policy` / `update_post_policy`),
   looking up the prior bank via the manifest `canvas_id`.
2. If the write routes are unavailable, treat question-bank upload as create-only and
   document that re-syncing requires manually deleting the old bank in Canvas first
   (mirrors the rubric limitation below).

Either way, also extend the staleness check to cover the `questions/*.md` files
(cf. how `_quiz_needs_sync` already does this for quizzes), so question edits trigger
a re-sync.

See the matching `KNOWN LIMITATION` note in `canvas_api.sync_question_bank` for the
same findings at the call site.

### Rubrics are never updated once created

`canvas_api.sync_rubrics` matches rubrics **by title** and skips any whose title
already exists in Canvas. Editing a rubric's criteria/ratings locally and
re-syncing has no effect — the existing Canvas rubric is left untouched. To change
a rubric you must currently delete it in Canvas first (or rename it). Consider an
update-in-place path matched by manifest id rather than title.

## Snippet dependency tracking for staleness

Editing a snippet file does not trigger re-sync of content files that include it. The
sync engine checks each content file's own mtime against `last_synced` in the manifest;
since changing a snippet only updates the snippet file's mtime, the including files are
not considered stale. The user must currently use `--force-uploads` or manually `touch`
the including files.

A fix would track which snippets each content file includes (at sync time) and mark
those files as stale when any referenced snippet's mtime is newer than the content
file's `last_synced`. This is similar to how `_quiz_needs_sync` already checks question
file mtimes in addition to the quiz-level file.

## Quiz BFS traversal (`-t` with quizzes)

`_get_file_refs()` returns an empty set for `quizzes/` files, so `-t` on a module that includes a quiz will not follow links embedded in quiz description or question HTML. The quiz itself is synced (including link rewriting), but BFS won't pre-upload unreferenced assets or pages that only appear inside quiz content.

The fix would be to parse the quiz `.md` + question files for local `<a>`/`<img>` refs in `_get_file_refs()` and return them so BFS can follow them. This is lower priority since `rewrite_links()` already handles stub-creation for missing manifest entries at upload time.

## `--rebuild-manifest`: re-sync manifest from Canvas

If the manifest file is lost, corrupted, or drifts out of sync with Canvas, a `--rebuild-manifest` flag would walk the live Canvas course and reconstruct `.canvas-manifest.toml` from what actually exists there.

How it would work:

- Query Canvas for all pages, assignments, discussions, files, and modules in the course
- For each item, match it back to a local file by title or URL slug
- Write the Canvas IDs into a fresh manifest
- Report any Canvas items that could not be matched to a local file (orphans), and any local files that have no corresponding Canvas item

This is a recovery/diagnostic tool, not part of the normal sync flow.

## `download` subcommand: download Canvas course to local Markdown structure

A `download` subcommand would do the reverse of the main sync: pull content from an existing Canvas course and write it out as a local Markdown repo, suitable for then being managed by this tool.

How it would work:

- Fetch all pages, assignments, discussions, modules, and files from Canvas
- Convert HTML body content back to Markdown (e.g. via `pandoc --from html --to markdown`)
- Write each item as a `.md` file in the appropriate local directory (`pages/`, `assignments/`, etc.), with Canvas metadata written as YAML frontmatter
- Download files to `assets/`, preserving Canvas folder structure
- Write module definitions as module `.md` files with links to the downloaded content files
- Populate `.canvas-manifest.toml` with the Canvas IDs of all downloaded items

Useful for bootstrapping a repo from a course that was originally built directly in Canvas, or for creating a local backup.

## End-to-end tests against a live Canvas sandbox

An optional smoke-test suite that runs the full tool against a real Canvas sandbox course and then queries Canvas via the API to verify that content landed correctly (HTML body, published state, module item order, etc.).

This is intentionally not part of the main test suite — it requires Canvas credentials, a dedicated sandbox course, and state cleanup between runs. It is slow and inherently network-dependent.

When implemented, the suggested approach:

- Maintain a dedicated Canvas sandbox course used only for testing
- Before each run, delete all pages/assignments/discussions/modules in the sandbox to get a clean slate
- Run the tool against `tests/fixtures/` pointed at the sandbox
- Use `canvasapi` directly in the test assertions to fetch each uploaded item and verify its content, metadata, and published state
- Run this suite manually or in a separate CI job gated on `CANVAS_API_TOKEN` being present — not on every push

## ~~`publish` subcommand: generate a public MkDocs static site from the course repo~~ — DONE

Implemented in `src/github_to_canvas/publish.py`; see ARCHITECTURE.md → "`publish`
Subcommand" for the shipped behaviour. The original design notes are kept below
for reference.

A `publish` subcommand that converts the local course repo into a static website and deploys it to GitHub Pages (or any static host). The goal is a publicly shareable site that feels familiar to Canvas users — same left-sidebar navigation model, same content sections — without exposing any student data.

### Why MkDocs + Material

- **MkDocs** is pure Python and installs cleanly via `uv`/`pip` alongside the rest of this tool's stack. No Ruby (Jekyll), no Go (Hugo).
- **Material for MkDocs** (`mkdocs-material`) is a polished, actively maintained theme with left-sidebar navigation, search, and mobile support — all built in. Canvas is also Material Design-influenced, so the structural similarity is immediate.
- Deployment to GitHub Pages is a single command: `mkdocs gh-deploy`.

### New dependencies

Add to `pyproject.toml` as optional extras (e.g., `[project.optional-dependencies] publish = [...]`):

- `mkdocs`
- `mkdocs-material`

So the user installs them only if they want the `publish` feature:

```sh
uv tool install github-to-canvas[publish]
```

### What the `publish` subcommand does

1. Read the course repo's `modules/` directory to determine content ordering and grouping.
2. Read `course_settings/course_settings.toml` (if present) for the course name / nickname.
3. Copy (or symlink) content files into a temporary `docs/` staging directory, organized by module.
4. Generate a `mkdocs.yml` in the staging directory with:
   - `site_name` set to the course name
   - `nav:` list derived from module order
   - Material theme config (colors, features)
5. Copy `assets/` into `docs/assets/` so images and file links resolve.
6. Run `mkdocs build` (into a `site/` output directory) or `mkdocs gh-deploy` to push directly to GitHub Pages.

CLI sketch:

```sh
github-to-canvas publish [COURSE_DIR] [--output-dir site] [--deploy]
```

- `COURSE_DIR` defaults to `.` (same convention as the rest of the tool).
- `--output-dir` sets where `mkdocs build` writes the static HTML (default: `site/`).
- `--deploy` runs `mkdocs gh-deploy` instead of `mkdocs build`, pushing directly to the `gh-pages` branch of the course repo.

### Navigation structure mapping

Canvas's left nav lists: **Home → Modules → Assignments → Pages → Discussions → Files**. The generated `nav:` in `mkdocs.yml` should mirror this structure, using the modules as top-level sections:

```yaml
nav:
  - Home: index.md
  - Module 1 — Introduction:
      - Overview: modules/module-1-introduction.md
      - Assignment: Your First Assignment: assignments/your-first-assignment.md
      - Page: Syllabus: pages/syllabus.md
  - Module 2 — …:
      - …
  - All Assignments: assignments/index.md   # auto-generated index
  - All Pages: pages/index.md               # auto-generated index
```

The module `.md` files already contain an ordered list of items (`[[assignment:slug]]`, `[[page:slug]]`, etc.) — the subcommand reads these to build the `nav:` tree. Auto-generated index pages for each content type (assignments, pages, discussions) give users a flat fallback view if they prefer.

### Canvas-like styling

#### Color palette

Canvas's sidebar uses a dark charcoal background. Approximate it in `mkdocs.yml`:

```yaml
theme:
  name: material
  palette:
    primary: custom
  custom_dir: overrides
```

Then in `docs/stylesheets/extra.css`:

```css
:root {
  --md-primary-fg-color: #2D3B45;        /* Canvas sidebar charcoal */
  --md-primary-fg-color--light: #3d4f5c;
  --md-primary-fg-color--dark:  #1e2a31;
  --md-accent-fg-color: #E66000;         /* Canvas default institution orange */
}

/* Make the nav sidebar background match Canvas's sidebar */
.md-nav--primary .md-nav__title,
[data-md-color-primary="custom"] .md-header {
  background-color: #2D3B45;
  color: #ffffff;
}

/* Active nav item: colored left-border indicator (Canvas style) */
.md-nav__item--active > .md-nav__link {
  border-left: 3px solid #E66000;
  padding-left: calc(0.6rem - 3px);
  color: #E66000;
  font-weight: 600;
}
```

The orange accent (`#E66000`) is Canvas's own default — institutions override it, but it's what most users recognize. If the course repo's `course_settings/course_settings.toml` carries institution branding colors in the future, those could override it automatically.

#### Course name in sidebar header

Canvas puts the course name prominently at the top of the sidebar. Material puts it in the top header bar, which is close enough. No custom template needed for a first pass.

For a closer match, add an `overrides/partials/nav-logo.html` Jinja snippet that displays the course name in the drawer. This is a one-file override using Material's [customization hooks](https://squidfunk.github.io/mkdocs-material/customization/#extending-the-theme).

#### Material theme features to enable

```yaml
theme:
  features:
    - navigation.sections        # group nav items under section headings (like Canvas modules)
    - navigation.indexes         # module overview page is clickable, not just a label
    - navigation.top             # back-to-top button
    - toc.integrate              # in-page TOC merged into left sidebar (saves horizontal space)
    - search.highlight
    - search.suggest
```

### What content is included / excluded

- **Included**: pages, assignments, discussions, quiz question text, module structure, assets (images, PDFs, etc.).
- **Excluded**: anything that is student-facing only or privacy-sensitive. At minimum, strip frontmatter keys like `published:`, `unlock_at:`, `due_at:` from the rendered output (or render them as informational metadata, not live dates). No student submission data is present in the course repo at all, so this is mainly about avoiding confusion rather than privacy.
- **Quizzes**: render the question text and answer choices as a readable study guide; omit `points_possible` per question if desired. The existing `quiz.py` parser already has all the data needed.

### Staging directory layout

The subcommand writes to a temp dir (or `--output-dir` staging area) that looks like:

```text
<staging>/
├── mkdocs.yml
├── docs/
│   ├── index.md                  # generated from course_settings or a README
│   ├── stylesheets/
│   │   └── extra.css
│   ├── assets/                   # copied from course repo assets/
│   ├── pages/
│   ├── assignments/
│   ├── discussions/
│   ├── quizzes/
│   └── modules/
└── overrides/                    # optional Jinja template overrides
```

### GitHub Pages deployment notes

- `mkdocs gh-deploy` pushes the built `site/` to the `gh-pages` branch of whatever git remote is configured in the course repo.
- The course repo and the tool repo are separate; the user runs `github-to-canvas publish` from inside their course repo.
- For GitHub Pages to serve the site, the course repo must be public (or GitHub Pages Pro for private repos).
- A GitHub Actions workflow could automate this: on push to `main`, run `github-to-canvas publish --deploy`. The subcommand could optionally emit a starter `.github/workflows/publish.yml` via a `--emit-workflow` flag.

### Implementation order

1. Build the `mkdocs.yml` + `docs/` staging writer (pure Python, no MkDocs dependency needed to generate the files).
2. Add `mkdocs` + `mkdocs-material` as optional deps and shell out to `mkdocs build`.
3. Add `--deploy` flag that shells out to `mkdocs gh-deploy`.
4. Add the `extra.css` Canvas-like styling.
5. (Optional, later) Add the `overrides/` Jinja course-name snippet.
6. (Optional, later) Add `--emit-workflow` for GitHub Actions automation.

### Related

- `imscc_import.py` already produces the local repo structure this subcommand reads from.
- `manifest.py` is not needed here (no Canvas IDs involved — this is a read-only export).
- Module ordering logic is the same as what `sync.py` uses; extract it into a shared helper if it isn't already.

## How to move/rename files locally without creating orphaned Canvas items

- Would be nice to be able to move things around in the local file system
  - Maybe if we store the canvas ID in the file?
- Alternately: what about having "move" / rename commands in the tool to handle this?
  - It'll need to update the manifest file

## ~~Preserve `<iframe>` embeds during IMSCC import~~ — DONE

Implemented in `_extract_iframes` / `_restore_iframes` in `imscc_import.py`.
`<iframe>` elements are extracted before Pandoc conversion and re-inserted as
raw HTML blocks in the Markdown output.  The sync-direction `markdown_to_html`
preserves raw HTML via Pandoc's default `raw_html` extension, so iframes
round-trip intact to Canvas.
