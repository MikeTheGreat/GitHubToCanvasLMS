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

## Resolved Design Questions

1. **`Attachment` module items** (Canvas Files linked from a module) → write as `- [title](../assets/file.pdf)` links in the module `.md`. External URL items also written as absolute links (easy to spot; update logic can ensure they stay).

2. **Graded discussions** — capture all available config from frontmatter (points_possible, due_at, etc.) even if not currently used by sync. May be useful later.

3. **`course_settings/syllabus.html`** → a new top-level `course_settings/` folder in the output repo. Also write `course_settings/course_settings.md` (mostly frontmatter: start/end dates, homepage type, etc.) and the `canvas.toml` skeleton in the repo root.

## Implementation Todo List

Work in small, independently testable increments. Write tests alongside each piece.

### Group 1 — Foundation

- [ ] Create synthetic IMSCC test fixture (`tests/fixtures/imscc/`) — minimal but covers all content types: page, assignment, discussion, asset, quiz (to be skipped), external URL, module with SubHeaders. Also a zipped version (`tests/fixtures/test_course.imscc`) for zip-input tests.
- [ ] Implement + test: input normalization — `open_imscc(path) -> Path` returns a usable directory (extracts zip to temp dir, or passes directory through)
- [ ] Implement + test: imsmanifest.xml parser → `temp_manifest: dict[str, TempEntry]` (classifies each resource by type, derives `local_path`, extracts title)

### Group 2 — Assets

- [ ] Implement + test: asset copier — `copy_assets(imscc_dir, output_dir)` copies `web_resources/` → `assets/` preserving subdirectory structure

### Group 3 — Link Rewriting (new direction: IMSCC tokens → local relative paths)

- [ ] Implement + test: IMSCC link rewriter — `rewrite_imscc_links(html, temp_manifest, output_local_path) -> str` handles `$CANVAS_OBJECT_REFERENCE$` and `$IMS-CC-FILEBASE$` tokens

### Group 4 — Pages

- [ ] Implement + test: page converter — strip HTML wrapper, rewrite links, Pandoc HTML→Markdown, write frontmatter (`title`, `published: true` default), write `pages/foo.md`

### Group 5 — Assignments

- [ ] Implement + test: `parse_assignment_settings(xml_path) -> dict` — extracts title, points_possible, due_at, lock_at, unlock_at, submission_types, grading_type, published from `assignment_settings.xml`
- [ ] Implement + test: assignment converter — settings → frontmatter + HTML body → Markdown, write `assignments/foo.md`

### Group 6 — Discussions

- [ ] Implement + test: `parse_topic_meta(xml_path) -> dict` — extracts title, published, require_initial_post, type (topic vs. announcement), and graded-discussion fields (points_possible, due_at) from embedded `<assignment>` if present
- [ ] Implement + test: discussion converter — topic XML body + topicMeta → Markdown, write `discussions/{slugify(title)}.md`; announcements warn + skip

### Group 7 — Modules

- [ ] Implement + test: `parse_module_meta(imscc_dir) -> list[ModuleData]` — parses `course_settings/module_meta.xml` for module and item metadata
- [ ] Implement + test: module file generator — frontmatter + ordered item list (SubHeaders, content links, external URL links, quiz skip with warning), write `modules/{slugify(title)}.md`

### Group 8 — Course Settings

- [ ] Implement + test: `create_course_settings(imscc_dir, temp_manifest, output_dir)` — converts `syllabus.html` → `course_settings/syllabus.md`, writes `course_settings/course_settings.md` (frontmatter from `course_settings/course_settings.xml`: start/end dates, homepage type, etc.), writes `canvas.toml` skeleton in repo root

### Group 9 — Integration & CLI

- [ ] Write integration test: run full import from synthetic fixture, assert all expected output files exist with correct content (see test plan below)
- [ ] Implement full orchestrator: `run_import(imscc_path, output_dir)` calling all phases in order
- [ ] Wire up CLI: add `import` subcommand to `cli.py`

## Test Plan

Follows the same layered approach as the existing tests. No live Canvas required.

### New test files

| File | Analogous to | What it tests |
| --- | --- | --- |
| `tests/test_imscc_temp_manifest.py` | `test_manifest.py` | imsmanifest.xml parsing → temp manifest entries |
| `tests/test_imscc_link_rewrite.py` | `test_link_rewrite.py` | `$CANVAS_OBJECT_REFERENCE$` and `$IMS-CC-FILEBASE$` rewriting |
| `tests/test_imscc_convert.py` | `test_convert.py` | Frontmatter extraction from XML, slugification, HTML body extraction |
| `tests/test_imscc_import.py` | `test_sync.py` | Full import pipeline integration test |

### Layer 1 — Unit tests

**`test_imscc_temp_manifest.py`**

- page resource → type `page`, `local_path` = `pages/{stem}.md`
- assignment resource → type `assignment`, `local_path` = `assignments/{stem}.md`
- discussion resource → type `discussion`, `local_path` = `discussions/{slugified_title}.md`
- asset resource (`web_resources/`) → type `asset`, `local_path` = `assets/{relative_path}`
- quiz resource → type `quiz`
- external URL resource → type `external_url`
- `_syllabus` resource → `local_path` = `course_settings/syllabus.md`
- Unknown resource type → warn + skip

**`test_imscc_link_rewrite.py`**

- `$CANVAS_OBJECT_REFERENCE$/assignments/gXXX` → `../assignments/foo.md`
- `$CANVAS_OBJECT_REFERENCE$/pages/gXXX` → `../pages/foo.md`
- `$CANVAS_OBJECT_REFERENCE$/discussion_topics/gXXX` → `../discussions/foo.md`
- `$CANVAS_OBJECT_REFERENCE$/modules/gXXX` → warn + leave as plain text (no href)
- `$IMS-CC-FILEBASE$/Images/logo.png` → `../assets/Images/logo.png`
- `https://external.com` → unchanged
- Unknown `gXXX` not in temp manifest → warn + remove href, keep link text

**`test_imscc_convert.py`**

- `parse_assignment_settings`: title, points_possible, due_at, submission_types, grading_type, published extracted correctly; empty `due_at` → `None`
- `parse_topic_meta`: title, published, require_initial_post extracted; `type="announcement"` flagged; graded discussion pulls points_possible from embedded `<assignment>`
- Slugification: spaces→hyphens, special chars stripped, multiple hyphens collapsed, lowercased
- HTML body extraction: `<html><head><body>` wrapper stripped, body content returned
- Module item line generation: SubHeader → `## Title`; content → `- [title](../type/foo.md)`; ExternalUrl → `- [title](https://url)`; quiz → `# SKIPPED...` comment

### Layer 2 — Integration test (`test_imscc_import.py`)

Run `run_import(synthetic_fixture_dir, tmp_path / "output")` and assert:

- `pages/my-page.md` exists with correct frontmatter and Pandoc-converted body
- `assignments/my-assignment.md` exists with assignment frontmatter fields (points_possible, submission_types, etc.)
- `discussions/week-01-forum.md` exists with discussion frontmatter
- `assets/images/logo.png` copied correctly (preserving subdir structure)
- `modules/week-1.md` exists: correct frontmatter, SubHeaders as `##` headings, content links as `- [title](../type/foo.md)`, external URL as absolute link
- `$CANVAS_OBJECT_REFERENCE$` links in bodies rewritten to relative local paths
- `$IMS-CC-FILEBASE$` image links rewritten to `../assets/...`
- Quiz module item skipped: warning printed, no corresponding line in module file
- `course_settings/syllabus.md` exists with converted Markdown body
- `course_settings/course_settings.md` exists with frontmatter
- `canvas.toml` skeleton written in output root
- `.canvas-manifest.toml` NOT written
- Rerun with `.imscc` zip input → identical output (zip path also accepted)
