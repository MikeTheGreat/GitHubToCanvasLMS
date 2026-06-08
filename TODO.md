# Possible Future Features

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
- **Tab configuration** in `course_settings.toml` — `tab_configuration` JSON string. Call `tab.update(hidden=...)` per tab via `course.list_tabs()`. See UPLOADER_CHANGES.md §1f.

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

## How to move/rename files locally without creating orphaned Canvas items

- Would be nice to be able to move things around in the local file system
  - Maybe if we store the canvas ID in the file?
- Alternately: what about having "move" / rename commands in the tool to handle this?
  - It'll need to update the manifest file
