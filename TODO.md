# Possible Future Features

## Show due dates in the published MkDocs site

The `publish` subcommand generates a static MkDocs site but does not currently
display due dates for assignments, discussions, or quizzes. If it did, it would
need to read the centralized `due_dates` table from
`course_settings/course_settings.toml` and apply the same override logic that
`update` uses (centralized dates take precedence over per-file frontmatter).

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

- **`course_settings/events.md`** — Calendar events. Requires parsing `## Title` / `**Date:**` sections back into structured event data and calling `canvas.create_calendar_event()` per event. Complex (deduplication, update-vs-create). See the course-settings upload table in ARCHITECTURE.md.
- **`course_settings/files_meta.toml`** — File visibility (`locked`, `hidden`, `display_name`, `unlock_at`) and folder visibility. Requires Canvas file IDs from the manifest (needs matching by `local_path` or `display_name`). Import-side fix also needed: `_write_files_meta_toml()` should write `local_path` alongside each file entry. See the course-settings upload table in ARCHITECTURE.md.

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

## Question bank staleness doesn't check individual question file mtimes

`_sync_question_banks` only compares the bank's `.toml` file's own mtime against
`last_synced` — editing a question file's content (without touching the `.toml`)
does not mark the bank as stale, unlike quizzes (`_quiz_needs_sync` already folds
question file mtimes into its comparison). Snippet changes referenced by question
files *are* caught (see ARCHITECTURE.md's "Snippet dependency staleness"); this gap
is specifically about edits to the question file's own content.

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

## Angle-bracket URL syntax not handled in some Markdown parsers

Markdown allows `[text](<url>)` to wrap a URL in angle brackets (typically used
for filenames that contain spaces). This is handled in `mv.py` (link rewriting)
and `publish.py:extract_local_refs` (reachability traversal), but three other
places that parse raw Markdown link syntax do not strip the brackets and would
silently fail if a link used this syntax:

- **`convert.py:_SNIPPET_LINK_RE`** — block snippet expansion: `[text](<snippets/foo.md>)` would not be recognized as a snippet ref
- **`sync.py:_MODULE_LINK_RE`** — module item parsing: `- [Title](<../pages/foo.md>)` would fail to resolve the content file
- **`quiz.py:_QUIZ_LINK_RE`** — quiz question list: `1. [Q1](<questions/q1.md>)` would fail to find the question file

These are considered negligible risk in practice because those link syntaxes are
controlled/structured formats that users don't write by hand with angle brackets,
but they could theoretically be triggered by an editor that auto-inserts `<>` for
paths with spaces.

Fix pattern (same as `mv.py` and `publish.py`): after capturing the URL group,
check `if href.startswith("<") and href.endswith(">"):` and strip the brackets.

## Filesystem watcher for automatic move/rename tracking

A background daemon (using Python's `watchdog` library) that monitors the course
repo for file moves/renames and automatically runs the same logic as the `mv`
subcommand — updating the manifest, rewriting cross-references, and updating
`module_order.toml`.

The `mv` subcommand's core logic (in `mv.py`) is designed to be called
programmatically, so the watcher would be a thin event-detection layer on top.

Caveats to address:
- Dropbox sync generates spurious file events (creates/deletes/moves) that must
  be distinguished from user-initiated operations
- Some editors implement rename as create-new + delete-old rather than atomic
  rename, so the watcher would need heuristic correlation
- Must be running at the time of the move — missed events are silent failures
- Cross-filesystem moves decompose into copy+delete with no way to correlate

## In course_settings.toml, within due_dates, KEEP and CREATE_NONE_THEN_KEEP do the same thing
Maybe remove KEEP?

## Would be nice to import Announcements
- By default make them unpublished, so they could be posted later?

## Publish command: include a schedule
- it would be nice to include a schedule, sorted chronologically
- maybe on the syllabus page, like in Canvas?
  - Rename to Syllabus + Schedule

## Optional: block *all* outbound HTTP in tests, not just `requests.put`

`tests/conftest.py` patches only `requests.put` (the one raw-requests call in
`canvas_api._set_module_item_published`). All other network calls go through the
mocked `canvasapi`. A socket-level guard (e.g., an autouse fixture that raises on
`socket.connect` to non-local addresses) would make "a test forgot to mock" fail
loudly instead of hitting a real Canvas.

Effort: ~30 min. Risk: none in principle; skip if it fights with pypandoc.

## Centralized alt-text (`alt_text.toml`) — DECIDED AGAINST (see note)

> **Decision (2026-07-02):** The user chose **not** to build this. Instead they
> will use **Ally's built-in AI Alt Text Assistant** (admin-enabled) to populate
> file-level image descriptions. Kept here only so the design work and the Ally
> research aren't lost. **Do not start implementing without re-confirming with the
> user first.**

### The original goal

Canvas/Ally nags for alt text in two independent places, backed by two different
stores:

1. **Alt on the `<img>` embed** (inside a page/assignment/discussion RCE) — written
   into that item's HTML and stored in Canvas. **The tool already handles this**
   via `convert.mark_decorative_images` and the `<img alt="...">` it emits.
2. **Alt on the standalone image *file*** (Canvas Files tool / Ally checker) — this
   is what the user was being nagged about and wanted to automate.

### Why file-level alt CANNOT be set programmatically (the blocker)

- **The Canvas Files API has no alt/description field.** `PUT /api/v1/files/:id`
  (what `canvasapi`'s `File.update()` calls) accepts only `name`,
  `parent_folder_id`, `lock_at`, `unlock_at`, `locked`, `hidden`,
  `visibility_level`. There is nothing on the Canvas file object to write alt text to.
- **Ally stores Files-tool descriptions in Ally's own system, not Canvas.** Per
  Anthology's docs: *"Ally will store the description so that your students can
  access it when they encounter the image in your course. However, when a
  description is added from the Files tool, it will not be stored by Canvas."*
- **No public instructor-facing Ally write API.** The only Ally API is the
  admin-level, read-oriented reporting/REST integration — it cannot push per-file
  descriptions. So there is no back door either.

Citations:

- [Anthology Ally — Add Image Descriptions](https://help.anthology.com/ally-lms/en/instructors/improve-content-accessibility/add-image-descriptions.html)
  (storage-location quote above)
- [Anthology Ally — AI Alt Text Assistant](https://help.anthology.com/ally-lms/en/administrators/ai-alt-text-assistant.html)
  (the tool the user is going with)
- [Canvas LMS REST API — Files](https://canvas.instructure.com/doc/api/files.html)
  (update params)

### The design that was worked out (if ever revisited)

A separate `course_settings/alt_text.toml` (kept as its own file, not a table in
`course_settings.toml`, because the list can get verbose) holding an array of
`[[files]]` entries, each with a repo-root-relative `path` and an `alt_text`
string:

```toml
[[files]]
path = "assets/homeworks/a1-banner.png"
alt_text = 'Banner for Assignment 1, with the text "Ask your instructor if you''d like help!"'
```

Three ways a Markdown image reference would resolve:

1. **Decorative** — `![](path.png)` → `alt=""` + `role="presentation"` (already the
   current behavior via `mark_decorative_images`).
2. **Centralized default** — `![USE_ALT_TEXT_TOML](path.png)` → look the image up in
   `alt_text.toml` and substitute its `alt_text`.
3. **Inline override** — `![Some specific description](path.png)` → use the inline
   text verbatim (per-use text wins; correct because the same image often needs
   different alt in different contexts).

Key implementation decisions that were settled:

- **Key on repo-root-relative paths, not `../`-relative.** A Markdown image path is
  relative to *its* `.md` file, so the same shared image referenced from two files
  has two different raw strings. Canonicalize both sides the way
  `link_rewrite._to_local_key` already does
  (`(source_file.parent / href).resolve().relative_to(course_root)`) before matching.
- **Resolve the sentinel as Markdown preprocessing, before Pandoc** — i.e. rewrite
  `![USE_ALT_TEXT_TOML](path)` into `![<looked-up text>](path)` in the same spirit
  as `convert.preprocess_snippets`, so link rewriting and decorative marking
  downstream work unchanged.
- **A missing lookup is a HARD ERROR** (user confirmed). If a file uses
  `USE_ALT_TEXT_TOML` but there is no matching `alt_text.toml` entry — or the
  `alt_text.toml` file itself is absent — fail loudly (like the snippet loader on a
  missing snippet). A silent fallback to `alt=""` would ship inaccessible content
  images while hiding the mistake.
- **Companion `list`/`print` subcommand** — print all alt strings sorted by file
  path, as a paste-ready crib sheet for the manual Ally Files-tool entry (the only
  part that can't be automated). This was the realistic ceiling on automation
  before the user opted for Ally's AI assistant instead.

# Bugs to Fix:
- Changing module_order.toml doesn't re-arrange the modules in Canvas

- Within a module Markdown file: 'published' is connected to whether the underlying item is published (for example, an assignment) - maybe remove the <!-- published:false --> mechanism from modules & rely on the underlying content instead?

- Even though I'm not changing anything in Canvas I sometimes still see an error about 'newer item on Canvas', like so:
The following resources were NOT uploaded because Canvas has a newer version.
Review these files and re-upload manually if needed (use --force-overwrite to skip this check):
  assignments/worksheets/01-a-unit-worksheets.md
  assignments/worksheets/01-b-unit-worksheets.md

