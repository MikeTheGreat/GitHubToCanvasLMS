# `github-to-canvas import` — Feature Plan

## Command Interface

```
github-to-canvas import <imscc_path> <output_dir>
```

- `<imscc_path>`: `.imscc` zip file **or** a pre-extracted directory (auto-detected)
- `<output_dir>`: where the course repo is written (fails if non-empty)

## New Files

- `src/github_to_canvas/imscc_import.py` — all import logic (`import` is a Python reserved keyword)
- `src/github_to_canvas/cli.py` — add `import` subcommand entry point
- `tests/test_imscc_import.py` — unit + integration tests
- `tests/fixtures/imscc/` — small synthetic IMSCC fixture for testing

## Processing Phases

### Phase 0 — Input Normalization

Detect `.imscc` (zip) vs. directory. If zip, extract to a `tempfile.TemporaryDirectory()` and use that path for all subsequent phases.

### Phase 1 — Build Temp Manifest (in-memory only, never written to disk)

Parse `imsmanifest.xml` `<resources>`. For each entry, classify by type + href prefix:

| IMSCC type | href location | → category |
|---|---|---|
| `webcontent` | `wiki_content/` | `page` |
| `webcontent` | `web_resources/` | `asset` |
| `imsdt_xmlv1p1` | `gXXX.xml` | `discussion` |
| `associatedcontent/...` | `gXXX/` dir | `assignment` |
| `imswl_xmlv1p1` | `gXXX.xml` | `external_url` |
| `imsqti_xmlv1p2/...` | any | `quiz` (warn + skip content) |
| `imsbasiclti_xmlv1p3` | any | `lti` (warn + skip) |

Also handles the `_syllabus` resource (`course_settings/syllabus.html`) → `pages/syllabus.md`.

For each content item, read title from: `assignment_settings.xml` / topicMeta XML / HTML `<title>` element.

Derive `local_path`:
- Pages: `pages/{stem}.md` (stem = wiki_content filename without `.html`)
- Assignments: `assignments/{html-body-filename-stem}.md`
- Discussions: `discussions/{slugify(title)}.md`
- Assets: `assets/{path_within_web_resources}`

Result: `dict[imscc_id → {category, imscc_path, local_path, title, ...metadata}]`

### Phase 2 — Copy Assets

Walk `web_resources/` depth-first, copy every file to `assets/` preserving subdirectory structure.
Print: `Copying asset: assets/Images/foo.png`

### Phase 3 — Convert Pages

For each page:
1. Read HTML body, strip `<html>/<head>/<body>` wrapper
2. Rewrite internal links (see link rewriting section below)
3. Pandoc HTML → Markdown (`--from html --to markdown`)
4. Frontmatter: `title`, `published: true` (pages have no settings XML; default true since they were exported)
5. Write `pages/foo.md`

### Phase 4 — Convert Assignments

For each assignment:
1. Read `gXXX/assignment_settings.xml` → extract: title, points_possible, due_at, lock_at, unlock_at, submission_types, grading_type, workflow_state
2. Read HTML body from the `.html` file in that directory
3. Rewrite internal links
4. Pandoc HTML → Markdown
5. Frontmatter: all assignment fields + `published` (from workflow_state)
6. Write `assignments/foo.md`

### Phase 5 — Convert Discussions

For each discussion:
1. Read `gXXX.xml` `<text>` element (HTML, entity-encoded) — the body
2. Find paired topicMeta XML for: title, workflow_state, type (topic vs. announcement), require_initial_post
3. Skip announcements (`type="announcement"`) with a warning
4. Rewrite internal links, Pandoc HTML → Markdown
5. Write `discussions/{slugify(title)}.md`

### Internal Link Rewriting (used in phases 3–5)

Applied to raw HTML before calling Pandoc:

| Source link | Rewritten to |
|---|---|
| `$CANVAS_OBJECT_REFERENCE$/assignments/gXXX` | `../assignments/foo.md` |
| `$CANVAS_OBJECT_REFERENCE$/pages/gXXX` | `../pages/foo.md` |
| `$CANVAS_OBJECT_REFERENCE$/discussion_topics/gXXX` | `../discussions/foo.md` |
| `$CANVAS_OBJECT_REFERENCE$/modules/gXXX` | warn + leave as plain text |
| `$IMS-CC-FILEBASE$/path/to/file` | `../assets/path/to/file` |
| `https://...` | unchanged |

If a `gXXX` isn't in the temp manifest: warn and remove the href (keep link text).

### Phase 6 — Generate Module Files

Primary source: `course_settings/module_meta.xml` (has content_type, URL for external links, workflow_state per item).

For each module in position order:
1. Frontmatter: `title`, `published` (from workflow_state), `unlock_at`, `require_sequential_progress`
2. Body items in position order:
   - `ContextModuleSubHeader` → `## Title`
   - Page / Assignment / Discussion → `- [display_title](../type/file.md)` (temp manifest lookup)
   - `ExternalUrl` → `- [display_title](https://url)` (URL taken from the item)
   - `Quizzes::Quiz`, `LTI` → `# SKIPPED: Quiz - "Title" (gXXX)` + print warning
3. Write `modules/{slugify(title)}.md`

### Phase 7 — Write canvas.toml Skeleton

```toml
base_url = "https://yourschool.instructure.com"
course_id = 0  # TODO: set your course ID

[auth]
# Prefer env var CANVAS_API_TOKEN; this is a fallback for local use only
api_token = ""
```

## Output: No `.canvas-manifest.toml`

The IMSCC's `gXXX` identifiers are not real Canvas numeric IDs. The import produces a clean repo with no manifest; the first `sync` run creates all items in Canvas and populates the manifest with real Canvas IDs.

## Console Output Style

```
Extracting: course.imscc → /tmp/...
Copying asset: assets/Images/logo.png
Converting page: pages/syllabus.md
Converting assignment: assignments/week-1-problem-set.md
Converting discussion: discussions/week-01-forum.md
  WARNING: Skipping announcement: "Coding Exercises 07 has been graded"
  WARNING: Skipping quiz: "How would you like to be graded?" (gXXX) — appears in module "Getting Started In IT-CS 142"
Generating module: modules/getting-started-in-it-cs-142.md
Done. Wrote course repo to: ./my-course/
```

## Open Questions

1. **`Attachment` module items** — Canvas Files linked directly from a module. Should these be written as `- [title](../assets/file.pdf)` links in the module `.md`, or skipped?
2. **Graded discussions** — topicMeta can contain an embedded `<assignment>` element with points/due_at. Should those fields be pulled into the discussion frontmatter?
3. **`course_settings/syllabus.html`** — include as `pages/syllabus.md` alongside other pages?
