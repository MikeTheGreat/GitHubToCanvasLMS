# GitHubToCanvasLMS

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
3. upload assets/                       (see processing order below)
4. for each content folder in alphabetical order (excludes assets/ and modules/):
     for each .md file in that folder, alphabetically:
       a. snippet preprocessing: replace any [text](snippets/...) links with snippet file contents
       b. convert Markdown → HTML via Pandoc
       c. for each <img> and <a href> that points to a local file:
            - if local file does not exist → print error, remove the tag, skip (do NOT stub)
            - if in manifest → rewrite tag to Canvas URL
            - if NOT in manifest → create empty stub in Canvas (unpublished, title only)
                                   → add to manifest dict AND flush to disk
                                   → rewrite tag to Canvas URL
       d. upload the fully-resolved HTML to Canvas (create or update via manifest)
       e. update manifest dict and flush to disk
5. sync modules/ alphabetically         (all content IDs now guaranteed in manifest)
```

**Processing order:**

`assets/` is always processed first, `modules/` always last. All other content folders (`assignments/`, `discussions/`, `pages/`, etc.) are processed in alphabetical order between them, with files within each folder also sorted alphabetically. This makes console output predictable and easy to follow.

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
- `<a href="https://...">` → leave unchanged
- `<a href="#anchor">` → leave unchanged

**Stub creation:**

When a linked file has no Canvas ID yet, the tool creates a minimal placeholder in Canvas (title only, empty body, unpublished) purely to obtain the Canvas ID. The stub is overwritten with real content when that file is processed in the main loop. Content type for the stub is derived from the linked file's directory convention or its frontmatter `canvas_type` field.

**Manifest flushing:**

The manifest is flushed to disk immediately after every write (stub creation, asset upload, content upload) — not batched. This means an interrupted sync can be resumed without re-uploading already-completed items or losing Canvas IDs.

**Console output:**

The tool prints a line for each action, for example:

```text
Uploading asset: assets/images/fig.png
Uploading asset: assets/slides/week1.pdf
Processing: assignments/week1.md
  Stub-creating: pages/syllabus.md (referenced but not yet synced)
  Uploading: assignments/week1.md
Processing: pages/syllabus.md
  Uploading: pages/syllabus.md
Syncing module: modules/week-1.md
```

## Key Design Decisions

- **Source of truth**: GitHub repo containing `.md` files and supporting assets (images, etc.)
- **Conversion**: Pandoc for Markdown → HTML conversion (produces clean HTML fragments suitable for Canvas)
- **Delivery**: Command-line tool, packaged as a `uv` tool for easy installation and running via `uvx`
- **Canvas content types**: Pages, Assignments, Discussion Forums, Modules

## Repository Structure (Proposed)

```text
course-repo/           ← the user's course content repo (separate from this tool)
├── pages/
│   └── syllabus.md
├── assignments/
│   └── assignment-1.md
├── discussions/
│   └── week-1-intro.md
├── modules/
│   └── week-1.md
├── snippets/
│   └── office-hours.md
└── assets/
    └── images/

github-to-canvas/      ← this tool repo
├── CLAUDE.md
├── pyproject.toml
├── src/
│   └── github_to_canvas/
│       ├── __init__.py
│       ├── cli.py           # entry point / argument parsing
│       ├── convert.py       # Markdown → HTML via Pandoc
│       ├── canvas_api.py    # Canvas upload logic via canvasapi library
│       └── config.py        # config file handling (API token, base URL, course ID)
└── tests/
    ├── fixtures/            ← mini course repo covering all test cases
    │   ├── pages/
    │   ├── assignments/
    │   ├── discussions/
    │   ├── modules/
    │   ├── snippets/
    │   └── assets/
    ├── test_convert.py      # unit tests: snippet preprocessing, Pandoc output
    ├── test_link_rewrite.py # unit tests: HTML link/img rewriting logic
    ├── test_manifest.py     # unit tests: manifest read/write/flush
    └── test_sync.py         # integration tests: full pipeline with mocked canvasapi
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

The API token should be passed via the `CANVAS_API_TOKEN` environment variable rather than committed to the repo.

### Content mapping

Content type is determined by **directory convention** — no explicit config needed for the common case:

```text
pages/          → Canvas Pages
assignments/    → Canvas Assignments
discussions/    → Canvas Discussion Topics
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
due_at: 2025-02-01T23:59:00-05:00
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

**Page-specific fields:**

```yaml
---
title: "Syllabus"
editing_roles: teachers
published: true
---
```

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

The link text becomes the display title of the item within the module. The link target is a path relative to the module file, pointing to a local content file.

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

### Create vs. Update: central manifest file

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

## Snippets (`snippets/` directory — reusable Markdown fragments)

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

## Canvas API Notes

- Use the [`canvasapi`](https://github.com/ucfopen/canvasapi) Python library rather than raw HTTP calls
- `canvasapi` wraps the Canvas REST API with Python objects (`course.get_pages()`, `course.create_assignment()`, etc.)
- Canvas REST API docs (for reference): `<base_url>/doc/api/live`
- Content types and their `canvasapi` entry points:
  - Pages: `course.get_page()` / `course.create_page()`
  - Assignments: `course.get_assignment()` / `course.create_assignment()`
  - Discussion Topics: `course.get_discussion_topic()` / `course.create_discussion_topic()`
  - Modules: `course.get_module()` / `course.create_module()`
  - Module items: `module.get_module_items()` / `module.create_module_item()` / `module_item.delete()`
- HTML body field name varies by content type (`body`, `description`, `message`) — check `canvasapi` docs per object type
- Module item `type` values: `Page`, `Assignment`, `Discussion`, `File`, `ExternalUrl`, `SubHeader`
- Sync content (pages/assignments/discussions) before syncing modules — modules reference content by Canvas ID

## Pandoc Notes

- Invoke as a subprocess or via `pypandoc`
- Use `--from markdown+smart` for smart punctuation
- Output `--to html5` for clean fragments
- Avoid `--standalone` so Canvas gets only the body fragment, not a full HTML document
- Math support: `pandoc --mathml`  if course content includes equations
  - CanvasLMS will remove any JS so we must use static content that is screen-reader accessible

## Tech Stack

- **Language**: Python 3.11+
- **Package manager**: `uv` (tool distribution via `uvx` / `uv tool install`)
- **Markdown conversion**: Pandoc (system install) via `pypandoc`
- **Canvas API client**: `canvasapi` (ucfopen/canvasapi) — Python wrapper around the Canvas REST API
- **CLI**: `click` or `argparse`
- **Config**: `tomllib` (stdlib) for `.toml` config files

## Testing Strategy

Tests are organised in three layers. All tests live in `tests/` in this repo; no external test repos or live Canvas instances are required for the normal test suite.

### Layer 1 — Pure unit tests

Test individual functions in isolation, with no network or Canvas dependency. Each test passes in a string or small data structure and asserts on the output:

- **Snippet preprocessor** (`test_convert.py`): given Markdown text with `[text](../snippets/...)` links, assert the correct substitution; test nested-include error behaviour.
- **Link rewriter** (`test_link_rewrite.py`): given an HTML fragment and a manifest dict, assert that `<img src>` and `<a href>` are rewritten to the correct Canvas URLs; test each link type (page, assignment, discussion, asset, external, anchor).
- **Manifest** (`test_manifest.py`): TOML round-trips, flush-on-every-write behaviour, create-vs-update lookup logic.
- **Processing order**: asset-first, module-last, alphabetical sorting of folders and files within folders.
- **Frontmatter parsing**: all content types and their specific fields.

### Layer 2 — Integration tests with mocked Canvas (`test_sync.py`)

Mock the `canvasapi` library with `pytest-mock`. Run the full sync pipeline against the fixture course and assert on which `canvasapi` methods were called, with what arguments, and in what order. Key scenarios to cover:

- First sync (no manifest): all items created, manifest written after each item
- Second sync (manifest present): all items updated, no creates called
- Stub creation: an assignment that links to a page not yet synced triggers a stub create before the assignment upload, followed by a real page upload that overwrites the stub
- Interrupted sync: pre-populated partial manifest causes only the remaining items to be synced
- Missing local file: tool prints an error, removes the tag, and continues (no stub, no crash)
- Module sync: module items created in the correct order; SubHeader items interleaved correctly

### Layer 3 — Test fixtures (`tests/fixtures/`)

A minimal but complete course repo committed directly into this tool repo. It covers every case the tests need — cross-links between content types, snippet includes, nested assets, modules with SubHeaders, files with every frontmatter variant. Fixtures are plain files; no special tooling needed to use them.

**What to assert on, not what to skip:** The integration tests assert on the `canvasapi` calls our code makes — arguments, order, and count. They do *not* assert on Canvas's behaviour (storing, retrieving), because that is Canvas's responsibility, not ours.

### Tools

- `pytest` — test runner
- `pytest-mock` — `canvasapi` mocking
- `tomllib` (stdlib) — manifest fixture parsing in tests

## Possible Future Features

### `--rebuild-manifest`: re-sync manifest from Canvas

If the manifest file is lost, corrupted, or drifts out of sync with Canvas, a `--rebuild-manifest` flag would walk the live Canvas course and reconstruct `.canvas-manifest.toml` from what actually exists there.

How it would work:

- Query Canvas for all pages, assignments, discussions, files, and modules in the course
- For each item, match it back to a local file by title or URL slug
- Write the Canvas IDs into a fresh manifest
- Report any Canvas items that could not be matched to a local file (orphans), and any local files that have no corresponding Canvas item

This is a recovery/diagnostic tool, not part of the normal sync flow.

### `download` subcommand: download Canvas course to local Markdown structurez

A `download` subcommand would do the reverse of the main sync: pull content from an existing Canvas course and write it out as a local Markdown repo, suitable for then being managed by this tool.

How it would work:

- Fetch all pages, assignments, discussions, modules, and files from Canvas
- Convert HTML body content back to Markdown (e.g. via `pandoc --from html --to markdown`)
- Write each item as a `.md` file in the appropriate local directory (`pages/`, `assignments/`, etc.), with Canvas metadata written as YAML frontmatter
- Download files to `assets/`, preserving Canvas folder structure
- Write module definitions as module `.md` files with links to the downloaded content files
- Populate `.canvas-manifest.toml` with the Canvas IDs of all downloaded items

Useful for bootstrapping a repo from a course that was originally built directly in Canvas, or for creating a local backup.

**Implementation note — consider .imscc export instead of API scraping:**

Rather than fetching each item individually via the Canvas API (slow, many round-trips), it may be more practical to ask the user to export their course from Canvas first (Course Settings → Export Course Content → Common Cartridge). Canvas produces an `.imscc` file, which is a ZIP archive in IMS Common Cartridge format containing all content as HTML files plus a manifest. The tool would then parse the `.imscc` locally — no API calls needed for the download step. This is faster, works offline after the export, and avoids rate-limiting. The tradeoff is an extra manual step (triggering the export in the Canvas UI) before running the tool.

### End-to-end tests against a live Canvas sandbox

An optional smoke-test suite that runs the full tool against a real Canvas sandbox course and then queries Canvas via the API to verify that content landed correctly (HTML body, published state, module item order, etc.).

This is intentionally not part of the main test suite — it requires Canvas credentials, a dedicated sandbox course, and state cleanup between runs. It is slow and inherently network-dependent.

When implemented, the suggested approach:

- Maintain a dedicated Canvas sandbox course used only for testing
- Before each run, delete all pages/assignments/discussions/modules in the sandbox to get a clean slate
- Run the tool against `tests/fixtures/` pointed at the sandbox
- Use `canvasapi` directly in the test assertions to fetch each uploaded item and verify its content, metadata, and published state
- Run this suite manually or in a separate CI job gated on `CANVAS_API_TOKEN` being present — not on every push

### Only upload files when the local last-modified timestamp is later than the last_synced key in the manifest
Unless the --force-uploads is set as an optional CLI param