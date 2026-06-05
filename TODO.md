# Possible Future Features

## Quiz: link-rewriting in question/description HTML

Currently `_get_file_refs()` returns an empty set for `quizzes/` files, and `_sync_quiz()` uploads quiz description and question text HTML without running it through `rewrite_links()`. This means:

- BFS (`-t`) never follows links embedded in quiz content.
- Links like `<a href="../pages/intro.md">` inside a quiz description or question prompt are uploaded as-is and will be dead links in Canvas.

The fix would be to call `rewrite_links()` on the converted description HTML and on each question's `question_text` HTML before upload, and to add quiz file ref extraction to `_get_file_refs()`. This is the same pattern used for pages/assignments/discussions. See the TODO comment in `sync.py:_get_file_refs`.

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
