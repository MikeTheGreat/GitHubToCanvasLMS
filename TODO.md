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

- **`course_settings/events.md`** — Calendar events. Requires parsing `## Title` / `**Date:**` sections back into structured event data and calling `canvas.create_calendar_event()` per event. Complex (deduplication, update-vs-create). See UPLOADER_CHANGES.md §3.
- **`course_settings/files_meta.toml`** — File visibility (`locked`, `hidden`, `display_name`, `unlock_at`) and folder visibility. Requires Canvas file IDs from the manifest (needs matching by `local_path` or `display_name`). Import-side fix also needed: `_write_files_meta_toml()` should write `local_path` alongside each file entry. See UPLOADER_CHANGES.md §16.

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

# Bugs to Fix:
- Changing module_order.toml doesn't re-arrange the modules in Canvas
