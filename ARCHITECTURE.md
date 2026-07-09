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
2.1. build ignore matcher               (from .gitignore + optional .canvasignore at repo root;
     any matched file/dir is skipped during discovery in every phase below)
2.5. if course_settings/course_settings.toml exists: apply course metadata to Canvas (name, dates, flags, grading
     standards, assignment groups, late policy, post policy, rubrics)
     → sections are change-detected individually via hashes cached in the manifest
       entry's section_hashes sub-table; only sections whose values changed are
       re-sent (see "Section-level change detection" below)
2.6. if course_settings/syllabus.md exists: convert body to HTML and set as course syllabus body
2.7. load centralized due_dates from course_settings.toml (if present);
     these override any due_at/lock_at/unlock_at in individual file frontmatter
3. upload assets/                       (see processing order below)
     → skip any file whose mtime ≤ manifest last_synced (unless --force-uploads)
4. for each content folder in alphabetical order (excludes assets/, course_settings/, modules/,
   question_banks/, quizzes/, snippets/, hidden dirs):
     check for title collisions across all .md files (including subfolders) → abort if any
     for each .md file in that folder (recursively, including subfolders), alphabetically:
       a. skip if mtime ≤ manifest last_synced (unless --force-uploads); print "Skipping (up-to-date)"
       b. snippet preprocessing: replace any [text](snippets/...) links with snippet file contents
       c. convert Markdown → HTML via Pandoc
          - accessibility post-pass (mark_decorative_images(), part of markdown_to_html()):
            any <img> with a missing or whitespace-only alt attribute gets alt="" and
            role="presentation" so Canvas's accessibility checker treats it as decorative;
            images with real alt text or an existing role attribute are untouched.
            Note: on import, _simplify_pandoc_attrs() drops role="presentation" from
            attribute blocks (id + style only), leaving ![](...) — this pass regenerates
            the decorative markup on upload, so the round trip is lossless.
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
     → parse quiz-level .md (frontmatter + ordered question list); numbered
       links inside code fences are literal text, not questions
     → parse each question .md file
     → run rewrite_links() on quiz description HTML and each question_text HTML (same as step 4d)
     → create or update quiz in Canvas (Classic Quizzes API), WITHOUT the publish state
     → delete all existing quiz questions, re-add in order
     → apply the publish state last (finalize_quiz_publish_state); warn if a manual
       "Save It Now" is needed (see "Quiz publish ordering" below)
     → update manifest dict and flush to disk
4c. for each question bank in question_banks/ alphabetically:
     → skip if bank .toml mtime ≤ manifest last_synced (unless --force-uploads)
     → parse bank metadata .toml; parse each question .md in questions/
     → create Canvas question bank and populate with questions
     → update manifest dict and flush to disk
4d. if front_page is set in course_settings.toml AND either the front_page value
     changed (its section hash) or the target page's .md was re-synced in this
     run → set_front_page (skipped otherwise, to avoid a redundant API call)
5. sync modules/ alphabetically         (all content IDs now guaranteed in manifest)
     → skip any module whose mtime ≤ manifest last_synced (unless --force-uploads)
6. due_dates pass (every run): for each gradeable item with a due_dates entry,
     compare the entry's symbolic resolution against the resolved_dates cache in
     the item's manifest entry; API calls only for items whose resolution changed
     (see "due_dates resolved-value caching" below)
```

**Processing order:**

Course settings and syllabus are applied first. Then `assets/`. Then regular content folders alphabetically. Then `quizzes/`. Then `question_banks/`. Finally `modules/`. All other content folders (`announcements/`, `assignments/`, `discussions/`, `pages/`, etc.) are processed in alphabetical order, with files within each folder also sorted alphabetically. `course_settings/`, `question_banks/`, `quizzes/`, `snippets/`, and `assets/` are excluded from the regular content pass — each has its own dedicated phase.

**Quiz publish ordering ("Save It Now"):**

Canvas Classic Quizzes keep a snapshot (`quiz_data`) of the questions that students actually see. Editing questions (create/update/delete, via UI or API) only sets `last_edited_at` on the quiz; the snapshot is regenerated when the quiz is *saved through the web UI* or when it *transitions* to published. While `last_edited_at > published_at`, the quiz page shows the "you have unsaved changes… Save It Now" banner and **students keep seeing the old questions**. The REST API has no equivalent of the "Save It Now" button: `PUT /quizzes/:id` with `published=true` on an already-published quiz is a no-op state-wise (Canvas only regenerates on a `workflow_state` *change*), and the unpublish→republish dance is rejected when the quiz has student submissions and re-sends "assignment created" notifications in a published course, so the tool deliberately does not attempt it.

What the tool does instead (`_sync_quiz` in `sync.py` + `finalize_quiz_publish_state` in `canvas_api.py`):

- The initial create/edit call omits `published`, so new quizzes are created unpublished.
- Questions are synced next.
- The publish state from frontmatter is applied **last** (with `notify_of_update: false`). For a quiz being published this run (new, or previously unpublished), the unpublished→published transition regenerates the snapshot — no banner, no manual step.
- If the quiz was **already published and stays published**, nothing programmatic can absorb the question changes: the tool prints a warning (also collected into the end-of-run error summary) with the quiz's `html_url` telling the user to open it and click "Save It Now".

**Fenced code blocks in Markdown input:**

All Markdown-input parsing respects fenced code blocks, built on shared primitives in `convert.py` (`split_fenced_segments`, `iter_lines_with_fence_info`, `apply_outside_fences`):

- **Code fences** are literal text: numbered `[x](y.md)` links inside them are not quiz questions (`quiz.split_quiz_body`, shared by sync and the publish study guide), `- [x](y.md)` / `# heading` lines are not module items (`parse_module_body`), `## Answers`-lookalikes do not split question files (`_split_on_headings`), and numbered lines are not answers. Raw-attribute blocks (```` ```{=html} ```` etc.) are fences like any other; there is no special handling beyond what Pandoc itself does with them. (Content is *excluded* via course-flag conditionals, not via raw-attribute "comment" blocks — that older convention has been removed.)
- **Snippet expansion** (`preprocess_snippets`) and the staleness probe (`find_referenced_snippets`) skip fence content, so `$path.md$` / snippet links in a code block stay literal.
- **publish's Pandoc-syntax cleanup** (`_strip_pandoc_syntax`, `_rewrite_quiz_links`) applies only outside fences, so literal `{#id}` / span / quiz-link examples in code blocks survive staging.
- Fence matching follows CommonMark closely enough for course content: ` ``` ` or `~~~` (3+, up to 3 leading spaces), closed by an equal-or-longer fence of the same character; an unclosed fence runs to end of file; fence-lookalikes inside a longer outer fence (e.g. a ``` ``` ``` example shown inside a ` ````markdown ` block) are content, not fences. Indented (4-space) code blocks and inline backtick spans are *not* considered fences by these parsers.

Not fence-aware by design: `mv`'s link updating (renames also fix example links in code blocks, keeping them valid), orphan detection's "in use" scan (a Canvas URL in a code block still protects content from `prune` — conservative), and `link_rewrite.py` (operates on Pandoc's HTML output, where code content is already escaped).

**Content subfolder support:**

Content folders (`pages/`, `assignments/`, `discussions/`, and any other content directories) support arbitrary subdirectory nesting. Files are discovered recursively (`rglob`) and sorted alphabetically by their full relative path. Canvas itself uses a flat namespace (pages are identified by title/slug, assignments and discussions by title), so the subfolder structure is purely for local organisation — all files are flattened when uploaded to Canvas.

Before any content is uploaded, the tool checks for **title collisions**: two or more `.md` files within the same content type that would resolve to the same Canvas title (from frontmatter `title`, or the filename stem as fallback). If a collision is detected, the sync aborts with an error listing the conflicting files. Titles are scoped per content type — `pages/intro.md` and `assignments/intro.md` sharing the title "Introduction" is fine, but `pages/week1/intro.md` and `pages/week2/intro.md` both titled "Introduction" is an error.

The manifest tracks each file by its full repo-relative path (e.g. `pages/week1/notes.md`), so subfolder files get their own manifest entries and can be targeted with `-t` and `-s`.

The `publish` subcommand discovers content in subfolders the same way, and stages files preserving their subfolder structure (e.g. `docs/pages/week1/notes.md`).

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

**Ignore files:**

A `.gitignore` and/or an optional `.canvasignore` at the repo root control which files are uploaded. Patterns use git's `gitwildmatch` syntax (via the [`pathspec`](https://pypi.org/project/pathspec/) library), so negation (`!`), `**`, anchoring, and directory-only patterns (`build/`) all behave as in git. The two files are layered together: `.gitignore` keeps a repo's existing rules authoritative, while `.canvasignore` adds Canvas-only exclusions. Matching is applied at every discovery point (assets, content folders, quizzes, question banks, modules), matching repo-root-relative POSIX paths; a matched directory is pruned entirely (its contents are never walked). With neither file present, nothing is matched and every file is processed as before. The tool's own `.canvas-manifest.toml` is always excluded. Ignoring a file only stops future uploads — it does **not** prune anything already on Canvas (the file still exists locally and keeps its manifest entry; see the `prune` subcommand for removing content). Implemented in `ignore.py`.

**Asset upload detail:**

The `assets/` folder hierarchy is mirrored into Canvas Files. `assets/images/fig.png` is uploaded into a Canvas folder named `images` (not into the course root). This keeps the Canvas Files area organised and avoids name collisions.

**Post-Pandoc link-rewriting detail:**

- `<img src="../assets/images/x.png">` → look up Canvas file URL from manifest → rewrite src
- `<a href="../pages/foo.md">` → look up or stub-create → rewrite to `/courses/:id/pages/slug`
- `<a href="../assignments/bar.md">` → look up or stub-create → rewrite to `/courses/:id/assignments/:id`
- `<a href="../discussions/baz.md">` → look up or stub-create → rewrite to `/courses/:id/discussion_topics/:id`
- `<a href="../announcements/qux.md">` → look up or stub-create → rewrite to `/courses/:id/discussion_topics/:id` (announcements are discussion topics)
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

  --delete         Delete the orphaned items from Canvas.
  --unpublish      Unpublish (set published=False) the orphaned items on Canvas.
  --manifest-only  Remove orphaned entries from the local manifest only; never
                   touch Canvas.
  --config PATH    Path to canvas.toml (default: <repo>/course_settings/canvas.toml)
```

Removes Canvas items whose local source file no longer exists. Because the
manifest is the only record of what the tool created, an entry is treated as an
**orphan** when `REPO / <local_key>` is gone from disk — which covers both
deleting a file and renaming one (a rename leaves the old path orphaned while the
new path syncs as a fresh item, per the path-keyed manifest).

Exactly one of `--delete`, `--unpublish`, or `--manifest-only` is required; there
is no default, so the intent is always explicit. Changes are applied immediately
(no preview or confirmation prompt). Pandoc is **not** required for this
subcommand.

`--manifest-only` is a local escape hatch: it drops every orphaned manifest entry
without contacting Canvas at all (no API token or course connection needed). Use
it to clear entries that the Canvas-touching modes leave stranded — items already
deleted on Canvas by hand, unsupported `canvas_type`s, or in-use protected
resources. It ignores the in-use protection and type-support rules below because
it never changes anything on Canvas; it only forgets the local bookkeeping.

When the Canvas object for a `--delete`/`--unpublish` orphan is **already gone**
(deleted manually or by an earlier run), Canvas returns a not-found error. The
desired end state is already reached, so this is treated as success and the stale
manifest entry is dropped rather than failing on every subsequent run.

### How it works

1. Load the manifest and compute the orphan set: every entry whose local file is missing.
2. Query Canvas for the resources it is **actively using** outside of modules — the
   course front page (`course.show_front_page()`) and anything linked from the
   syllabus body (`syllabus_body` scanned for `/courses/.../pages|assignments|...`
   references). These are protected: even when their local file is gone, prune
   keeps them rather than removing content Canvas still relies on.
3. For each orphan, dispatch by `canvas_type`:
   - If the item is covered by a `pinned_resources` entry in
     `course_settings.toml`, skip it (kept, manifest entry retained) — a pin
     means "never touch this on Canvas", and deleting a pinned quiz would
     destroy the student submissions the pin protects. `--manifest-only` is
     exempt (it never contacts Canvas).
   - If the item is in use as the front page or syllabus, skip it (kept, manifest entry retained).
   - `--delete`: fetch the object and call `.delete()`, then drop the manifest key.
   - `--unpublish`: set `published=False` on the object, then drop the manifest key.
   - The Canvas object is addressed by URL slug (`canvas_url`) for pages and by `canvas_id` for everything else — the same identifier rule used by the timestamp check.
4. Flush the manifest after each successful prune, so an interrupted run leaves a consistent file.
5. Print a `N deleted/unpublished, P kept (in use or pinned), M skipped, K errors` summary.

A failure on one item is caught, reported as a warning, and does **not** abort the
run — that entry keeps its manifest key so it can be retried.

### Type support

| `canvas_type` | `--delete` | `--unpublish` |
| --- | --- | --- |
| `page`, `assignment`, `discussion`, `announcement`, `quiz`, `module` | ✅ deleted | ✅ unpublished |
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
| `imsdt_xmlv1p1` | `gXXX.xml` (topicMeta `<type>` = `topic`) | `discussion` |
| `imsdt_xmlv1p1` | `gXXX.xml` (topicMeta `<type>` = `announcement`) | `announcement` |
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
6. **Discussions:** parse topic XML body and paired topicMeta for title, published, require_initial_post; if `<attachments>` block present, append a `## Attachments` section with `../assets/{href}` links; write `discussions/{slugify(title)}.md`. (Announcements — topicMeta `<type>` = `announcement` — are routed to phase 6a instead.)
6a. **Announcements:** a discussion topic whose topicMeta `<type>` is `announcement`. Only the announcement body itself is imported — student replies/likes/comments are never present in an IMSCC export, so there is nothing to drop. The body is read exactly like a discussion (shared `_read_topic_body()`, including any `## Attachments`). Frontmatter carries only two active fields: `title` and `published: false` (**always** false on import, regardless of the export's `workflow_state`). Because Canvas has no draft state for announcements, `published: false` means `update` **does not post it yet** — it stays staged in the repo until you set `published: true` (e.g. post the midterm reminder when the midterm is near); see the "Content mapping" announcements entry. Every other topicMeta leaf element (`type`, `workflow_state`, `discussion_type`, `delayed_post_at`, `posted_at`, …) is written as a **commented** frontmatter line (`# key: value`) — the settings Canvas actually accepts (`canvas_api.ANNOUNCEMENT_SETTABLE_FIELDS`) take effect if the user uncomments them, and the rest are reference-only. The topicMeta `<position>` element is **dropped entirely** (not active, not commented): Canvas orders announcements by post date, not position, so exposing it would falsely imply announcement ordering is controllable from the repo. Written to `announcements/{slugify(title)}.md`. See `parse_announcement_meta()` / `convert_announcement()`. On `update`, a `published: false` announcement is skipped (not posted); see the announcements entry under "Content mapping" and `_upload_announcement()` in the sync pipeline.
6b. **Quizzes:** read `gXXX/assessment_meta.xml` for quiz settings; parse QTI 1.2 XML (`gXXX/gXXX.xml`) for questions; write `quizzes/{slug}/{slug}.md` and one file per question under `quizzes/{slug}/questions/`; unsupported question types emit a warning and are skipped

**Assignment group / rubric association:** Assignments, graded discussions, and quizzes each carry an `assignment_group_identifierref` (top-level for assignments/quizzes, nested under `<assignment>` for discussions); assignments and discussions additionally carry `rubric_identifierref` + `rubric_use_for_grading`. `_resolve_assignment_group_and_rubric()` resolves these IMSCC identifiers against identifier→title maps built once in `run_import()` from `assignment_groups.xml`/`rubrics.xml`, writing `assignment_group_id`/`rubric` frontmatter fields by title (matching how `sync.py` resolves them) and `rubric_use_for_grading` → `use_for_grading` (only when a rubric is present). Refs that don't resolve print a warning and are dropped rather than written as unresolvable raw identifiers.
6c. **Question banks:** parse `non_cc_assessments/*.xml.qti` objectbank files; read bank metadata (bank_title, bank_context_uuid, bank_state) from `<qtimetadata>`; parse all `<item>` children as questions (same QTI format as quizzes, plus `original_answer_ids` metadata); write `question_banks/{slug}/{slug}.toml` and one question file per item under `question_banks/{slug}/questions/`
7. **Modules:** read `course_settings/module_meta.xml`; emit items in position order (see below); write `modules/{slugify(title)}.md`
8. **Course settings:** collect data from all `course_settings/*.xml` files and `imsmanifest.xml` metadata; write:
   - `course_settings/course_settings.toml` — all course-level settings (see below)
   - `course_settings/syllabus.md` — syllabus HTML converted to Markdown
   - `course_settings/events.md` — calendar events (if any exist in `events.xml`)
   - `course_settings/rubrics.toml` — rubric definitions with criteria and ratings (if any exist in `rubrics.xml`)
   - `course_settings/files_meta.toml` — file visibility/lock metadata (from `files_meta.xml`)
   - `course_settings/canvas.toml` — connection config skeleton pre-populated from `context.xml`
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

### Heading level handling

Canvas LMS silently converts any `<h1>` in page/assignment/discussion content into a styled paragraph, which looks like a heading but is invisible to screen readers (an accessibility regression). To keep imported content H1-free, `_shift_headings_down()` (applied to converted Markdown in phases 4–6 and to the syllabus/events) shifts every ATX heading down one level (H1→H2, H2→H3, …).

This shift is **conditional**: it runs **only when the converted Markdown actually contains an H1**. Canvas itself already prevents H1s in the content it exports, so the common case is that there is nothing to do and headings keep their original levels — an H2 stays an H2 rather than drifting to H3 on every round-trip. The shift kicks in only when an H1 slips through, demoting it (and everything below it) by one. Existing H6 headings cannot be shifted deeper; they are left at H6 with a printed warning.

The complementary check on the **upload** side (`sync.py`) is unconditional: any content whose rendered HTML contains an `<h1>` is rejected with an error so the author fixes the source rather than letting Canvas silently mangle it.

### Pandoc attribute simplification

Pandoc's HTML→Markdown conversion (`_html_to_markdown()`, used by every content type during import) attaches curly-brace attribute blocks to headings, links, images, spans, code, and fenced divs (`::: {...}`) — e.g. `## Heading {#my-id .some-class data-foo="bar" style="color:red"}`. Most of these attributes are Canvas RCE cruft with no meaning outside Canvas. `_simplify_pandoc_attrs()` strips every such block down to just:

- `#id` — kept because in-document anchor links (e.g. a table of contents) may target it
- `style="..."` — kept because it's user-authored formatting

Everything else (classes, `data-*`, `target`, etc.) is dropped. If a block has nothing left after filtering, the `{...}` (and its braces) are removed entirely — including the bare single-class shorthand Pandoc emits for divs with exactly one class and no other attributes (`::: classname`).

Fenced divs get one further step: if a div's attribute block ends up empty, the div wrapper itself (both the opening and closing `:::` fence lines) is removed and its content unwrapped, recursively for nested divs. Since Pandoc only ever emits fenced-div syntax for a `<div>` that has at least one attribute (an attribute-less div is passed through as raw HTML instead), fence pairing is done on the pre-simplification text — every opening fence line is guaranteed to have content, and every bare (attribute-less) `:::` line is unambiguously a closing fence.

### Module file generation

- `ContextModuleSubHeader` indent 0 → `## Title` heading
- `ContextModuleSubHeader` indent ≥ 1 → `- Title` plain-text list item (with `(indent-1)*2` leading spaces)
- `WikiPage` / `Assignment` / `Discussion` / `DiscussionTopic` / `Quizzes::Quiz` → `- [display_title](../type/file.md)` (`DiscussionTopic` is the name used in real Canvas IMSCC exports; `Discussion` is the IMS CC name — both are handled)
- `ExternalUrl` → `- [display_title](https://url)` — if the linked webLink resource has `target` or `windowFeatures` attributes on its `<url>` element, they are appended as an HTML comment: `<!-- target="_blank" windowFeatures="width=800" -->`
- `ContextExternalTool` (LTI embedded tool) → `- [display_title](url)` (URL comes from the module item's own `url` field, not the LTI resource XML)
- `Attachment` (Canvas File) → `# SKIPPED: Attachment - "title"` comment line + printed warning (no local file equivalent)
- **Per-item published state:** Items with `workflow_state` != `active` in `module_meta.xml` get `<!-- published="false" -->` appended. During sync, the comment is parsed and the tool attempts to set `published=false` on the Canvas module item via a follow-up PUT (Canvas ignores the flag on the initial create call). **Known Canvas bug:** the API returns 500 for File-type module items, so those cannot be unpublished programmatically — the tool prints a summary of affected items at the end of the run. The `publish` subcommand skips unpublished items entirely (not rendered in the HTML, not followed for reachability — including any assets reachable only through unpublished links).

### Course settings output (`course_settings/course_settings.toml`)

A TOML file written inside the `course_settings/` folder capturing all course-level metadata. Sources and content:

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
- **`course_settings/` directory** — `course_settings.toml`, `canvas.toml`, syllabus, events, and any other converted content land here.
- **`course_settings/course_settings.toml`** — all course-wide settings, alongside the rest of the course-settings files.
- **Centralized due dates** — during import, `due_at`/`lock_at`/`unlock_at` fields from assignments, discussions, and quizzes are collected into a `due_dates` inline-table array in `course_settings/course_settings.toml`. The individual `.md` files have these fields commented out (with a note pointing to the centralized file). During `update`/`publish`, centralized `due_dates` entries override any frontmatter dates; matching is by title (with an optional `type` field for disambiguation). Unmatched entries produce a warning. Each date field supports sentinel values (case-insensitive): `"NONE"` actively clears the date on Canvas; `"KEEP"` leaves whatever Canvas currently has; `""` (empty string) behaves like KEEP but prints a warning; `"CREATE_NONE_THEN_KEEP"` clears the date when creating a new item but leaves it alone on subsequent updates. If Canvas rejects due dates (e.g. due_at outside availability window), the tool retries without dates and prints a warning.
- **Graded discussion metadata captured** — `points_possible`, `due_at`, etc. written to frontmatter even if not currently used by sync.
- **Quiz question slugification** — question filenames are derived from the QTI `title` attribute via `_slugify()`. Special characters (e.g. `+`) are stripped, so "What is 2+2?" becomes `what-is-22.md`.
- **Course-navigation tabs humanised** — `course_settings.xml`'s `tab_configuration` (an escaped JSON string of Canvas-internal numeric ids and `context_external_tool_<resource-id>` ids) is rewritten into a readable `tab_configuration` inline array. Numeric ids become string ids (`3` → `id = "assignments"`); external-tool ids are resolved to a human `label` via the tool's BLTI `<blti:title>` (with the original id kept for provenance). See `_convert_tab_configuration()`. **Caveat:** Canvas only exports a BLTI resource for tools that ship as course *content*; tools used **only in course navigation** have no resource (and no name) in the cartridge — verified against real Canvas exports. Those tabs are written with an empty `label = ""` fill-in slot plus the id, and one summary warning is printed counting them. Sync skips an unfilled placeholder (it can't match a nameless tool); run `create-tool-aliases` against a Canvas course URL to resolve these labels automatically (see README), or supply them by hand.

### Console output style

```text
Extracting: course.imscc → /tmp/...
Copying asset: assets/Images/logo.png
Converting page: pages/syllabus.md
Converting assignment: assignments/week-1-problem-set.md
Converting discussion: discussions/week-01-forum.md
Converting announcement: announcements/midterm-reminder.md
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

1. Determine the site name from `course_settings/course_settings.toml` (`title` / `name` /
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

## `mv` Subcommand

Moves or renames a file or directory within the course repo, updating all
internal references so nothing breaks on the next sync.

```text
github-to-canvas mv [--noop/-n] [--verbose/-v] SRC DEST
```

**What it updates:**

1. **Physical move** — uses `git mv` when inside a git repo and the source has
   git-tracked content (falls back to a plain filesystem move otherwise, e.g.
   for files/directories that haven't been `git add`ed yet). Handles
   case-only renames (e.g. `Unit-01` → `unit-01`) via a temporary
   intermediate name.
2. **`.canvas-manifest.toml`** — renames top-level keys for moved files and
   updates `canvas_item_ids` sub-tables inside module entries.
3. **All `.md` files in the repo** — rewrites relative Markdown links
   (`[text](path)`, `![alt](path)`) and inline snippet references
   (`$path.md$`) that point to moved files. Also adjusts outbound links inside
   a moved file when its directory depth changes.
4. **`course_settings/module_order.toml`** — updates filenames in the `order`
   array when a module file is renamed.
5. **`course_settings/course_settings.toml`** — updates the `dashboard_image`
   and `front_page` fields when the file they point to is moved, and rewrites
   `pinned_resources` entries covering the moved path (a stale pin would
   silently unpin the resource, letting the next update re-upload the exact
   thing the pin protects). File pins follow `path_map` (including quiz/qbank
   inner-file renames); folder pins follow the moved directory by prefix.

**Special cases:**

- **Quiz folder renames** — when renaming `quizzes/old-name/` to
  `quizzes/new-name/`, the inner `.md` file that must match the folder name
  is also renamed (`old-name.md` → `new-name.md`).
- **Question bank folder renames** — same as quizzes but for the inner `.toml`
  file.

**Validation (errors before any work is done):**

- Source must exist, destination must not (except for case-only renames).
- Both paths must be within the course repo.
- No cross-content-type moves (e.g. `pages/` → `assignments/`).

**Auto-detects the repo root** by walking up from the source path looking for
`course_settings/course_settings.toml`.

This subcommand is purely local — it never contacts Canvas. Run `update` after
moving files to push the changes.

## Configuration

### Tool config file (`course_settings/canvas.toml`)

Provided once per course repo (in the `course_settings/` folder), checked into git:

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
announcements/  → Canvas Announcements (discussion topics with is_announcement)
quizzes/        → Canvas Quizzes (Classic) — nested structure, see below
modules/        → Canvas Modules (special — see below)
```

The directory name maps directly to the Canvas content type. A frontmatter `canvas_type` field can override the default if a file lives outside these directories.

**Announcements** (`_upload_announcement()` → `capi.create_or_update_announcement()`): a Canvas announcement is a discussion topic created with `is_announcement=True`, so it reuses the discussion-topic API, manifest addressing (`discussion_topics/:id`), timestamp check, prune, and link-rewriting — the only differences are the `is_announcement` flag and a distinct `canvas_type = "announcement"` recorded in the manifest. **Canvas has no unpublished/draft state for announcements** — creating one posts it immediately (`create_discussion_topic(..., published=False)` is rejected with *"This topic cannot be set to draft state because it is an announcement"*). So `published` acts as an on/off switch for whether the announcement is sent at all: a `published: false` announcement is **skipped** in `_sync_content_file()` (with a printed warning) — it stays staged in the repo until you set `published: true`, at which point it posts. The skip happens *before* link rewriting, so a not-yet-posted announcement never stub-creates the content it links to. If a `published: false` file already has a manifest entry (it was posted on a previous run), the tool warns that Canvas cannot un-post an announcement and leaves it (use `prune` or delete it in Canvas). Because posting is implicit, `published` itself is never sent to Canvas; a future `delayed_post_at` schedules the post instead of publishing immediately. Announcements cannot be graded, so — unlike discussions — no due-date, assignment-group, or rubric parameters are sent; but any discussion-topic settings in `capi.ANNOUNCEMENT_SETTABLE_FIELDS` (`delayed_post_at`, `lock_at`, `locked`, `discussion_type`, `require_initial_post`, `allow_rating`, `only_graders_can_rate`, `sort_by_rating`, `podcast_enabled`, `pinned`) that appear as active frontmatter are forwarded verbatim. Any other active frontmatter key (other than the handled `title`/`published`/`canvas_type`) is **not** silently dropped: `_upload_announcement()` prints a `WARNING: … ignoring frontmatter field '…'` as it happens and appends `(local_key, field)` to `ctx.ignored_fields`, which `_print_ignored_fields_summary()` lists at the end of the run (alongside the newer-on-Canvas and unpublishable-items summaries). There is no `position` handling — Canvas orders announcements by post date, so it is neither imported nor sent (and an explicit `position:` in a file is reported as ignored). Announcements are **not** valid Canvas module items; if one is referenced from a module the item is skipped with a warning. The `publish` (MkDocs) subcommand does not stage announcements (they are not module content).

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
assignment_group_id: "Labs"   # same name/numeric-ID resolution as assignments and quizzes
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
assignment_group_id: "Labs"   # name (resolved via course_settings.toml) or numeric
                              #   Canvas ID; shared resolution logic with
                              #   assignments and discussions (sync.py:
                              #   _resolve_assignment_group_id). Only affects
                              #   grading when quiz_type is assignment or
                              #   graded_survey.
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
- A question file listed in the quiz `.md` but missing on disk is a hard error: the whole quiz upload is skipped (nothing recorded in the manifest, so the quiz is retried next run) and the run's error summary fails the run.

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
| `course_settings/course_settings.toml` | Applied via `course.update()` for flat metadata; dedicated API calls for grading standards, assignment groups, late policy, post policy, course-navigation tabs, and rubrics |
| `course_settings/syllabus.md` | Body converted to HTML and set as `course.syllabus_body` via `course.update()` |
| `course_settings/events.md` | Not yet uploaded (future feature) |
| `course_settings/rubrics.toml` | Each rubric created or updated in place (matched by title) via `course.create_rubric()` / `PUT rubrics/:id`; supports `long_description`, `reusable`, `read_only` |
| `course_settings/files_meta.toml` | Not yet uploaded (requires matching Canvas file IDs after asset upload) |

**`course_settings/course_settings.toml` upload detail:**

The following sections are handled separately from the flat `course.update()` call:

- `[[grading_standards]]` — each entry created via `course.create_grading_standard()` if title not already present; first standard's ID passed as `grading_standard_id` in the course update
- `[[assignment_groups]]` — each entry created or updated via `course.create_assignment_group()` / `ag.edit()`, matched by name; processed in `position` order. Both calls must pass **flat top-level params** (`name`, `position`, `group_weight`, `rules`): Canvas's assignment-group endpoints `params.permit` only those names and silently drop anything nested under `assignment_group[...]` (unlike most other Canvas endpoints, which expect the nested form). Per-group `group_weight` values only take effect (and only display on the Assignments page) when the course-level `apply_assignment_group_weights` flag is on, so `update_course_metadata()` sets that flag in the `course.update()` call: `group_weighting_scheme = "percent"` (the Canvas IMSCC export name, round-tripped by the importer) maps to `true`, any other value to `false`, and when the key is absent the flag is inferred as `true` if any group has a `group_weight`. When neither the key nor any weight is present the flag is not sent, leaving the course's existing setting alone. **Drop rules deferral:** a group's `rules` (`drop_lowest`/`drop_highest`) are validated by Canvas against the *current* number of assignments in the group ("Drop rules cannot be higher than the number of assignments"). Because course settings are applied in phase 0 — before any assignments exist — a fresh course would reject every drop rule. So `sync_assignment_groups()` catches that specific `BadRequest`, creates/updates the group **without** the rules (warning `Deferring drop rules for assignment group …`), and returns the affected group names; `run_sync()` then re-applies them via `capi.apply_assignment_group_rules()` after the content and quiz phases (phase 2.65), once the groups contain their assignments. A group that still lacks enough assignments at that point is warned about, not fatal.
- `[late_policy]` — applied via `PATCH /api/v1/courses/:id/late_policy` (raw requester call, not wrapped in `canvasapi`). This genuinely is a REST resource, so it stays REST.
- `[default_post_policy]` — applied via the **GraphQL** `setCoursePostPolicy` mutation (`POST /api/graphql`), issued through the shared requester (`canvas_api.graphql()` / `update_post_policy()`). Post policies are GraphQL-only in Canvas; the former `PUT /api/v1/courses/:id/post_policies` REST route does not exist and returned 404.
- `tab_configuration` — controls the **course-navigation sidebar** (the left-hand "Assignments", "Modules", … links). An inline array, one entry per tab, in display order. Each entry is applied via `tab.update(position=…, hidden=…)` against `course.get_tabs()`; `hidden` defaults to false. Positions are assigned in list order **starting at 2**, because Canvas pins **Home** at position 1 and rejects any other tab placed there (`"That tab location is invalid"`).

  Each entry names one tab via `id` **or** `label` — the two are interchangeable (a non-empty `label` is used as the name when present, else `id`). `_resolve_tab_entry()` matches the typed name, **case-insensitively**, against: (1) a built-in tab id (`home`, `syllabus`, `pages`, `assignments`, `modules`, `announcements`, …); then (2) any live tab's **display label**, which covers external tools (`Zoom`, `Panopto Recordings`) and renamed built-ins (Conferences shown as `BigBlueButton`). This means a user can simply type the sidebar name without knowing whether it's a built-in or a tool. An entry may still carry both keys (e.g. the importer writes `label = "Zoom"` plus the original `id = "context_external_tool_g…"` for provenance); the tool id is not used for matching because the cartridge's id never equals a live course's Canvas-assigned tool id.

  Only reordering and hiding are supported — new tabs cannot be created. Entries that don't resolve to any tab in the course (no built-in or installed tool by that name) are **warned about and skipped**, never created. The unmovable `home` and `settings` tabs are silently left alone. Also accepted for backward compatibility: Canvas's internal **numeric** tab ids (`0`=home, `3`=assignments, …; see `NUMERIC_TAB_IDS`) and a single JSON-encoded string, the forms older imports/exports produced.

Read-only or infrastructure fields (`storage_quota`, `root_account_uuid`, `image_identifier_ref`, `last_modified`, `copyright_restrictions`, `copyright_description`, and others) are present in the TOML for round-trip fidelity but are silently ignored by the uploader.

**Section-level change detection** (`DESIGN-settings-caching.md`): the settings file is split into named sections — `metadata` (every top-level key not claimed by another section, so unknown keys conservatively count as metadata), `grading_standards`, `dashboard_image`, `assignment_groups`, `late_policy`, `default_post_policy`, `tab_configuration`, and `front_page` — and each section's canonical-JSON SHA-256 hash (first 16 hex chars; `compute_settings_section_hashes()`) is cached in the manifest entry's `section_hashes` sub-table. When the file's mtime is stale, only sections whose hash differs are re-applied; a comment-only edit re-runs nothing. `due_dates`, `[course_flags]`, and `pinned_resources` are excluded from every section (including metadata): due_dates changes are handled per-item by the dates pass, flag changes per-file via `flags_used`, and `pinned_resources` only gates uploads locally (there is nothing on Canvas to update), so editing any of them re-sends no settings section. Couplings: a `grading_standards` or `assignment_groups` change also forces the `metadata` action (the metadata call carries `grading_standard_id` and the group-weight inference); when only metadata changed, `gs_id` is `None` and the course's existing grading standard is left alone. The dashboard image **file's own mtime** joins the staleness check (via `extra_mtime_paths` against the settings entry's `last_synced`), so editing the image alone re-uploads it — and conversely, unrelated settings edits no longer re-upload it. `front_page` has no action inside `sync_course_settings()`; `run_sync` keys the front-page phase off `"front_page" in changed_sections` (or the page itself having re-synced). **Failure/retry:** a failed section is recorded via `warn(..., errors)` (fails the run) and its hash is *not* updated; the entry is recorded with `mark_synced=False`, so the next run re-parses and retries exactly the failed sections while skipping the succeeded ones. The `tab_configuration` misplaced-nested-key lint runs on every stale parse regardless of section gates (the misplaced key lives inside some *other* section's value, so its own hash never trips). Migration: an entry without `section_hashes` (older tool version) re-runs all sections once when stale, then records hashes.

**due_dates resolved-value caching** (`DESIGN-settings-caching.md`): the dates-only pass (`_apply_due_dates_only`, phase 6) runs on **every** update — not just when settings changed — and each gradeable item's manifest entry caches the **symbolic resolution** of its due_dates entry in a `resolved_dates` sub-table: per date key, a concrete date string (verbatim), `"NONE"` (clear), or `"KEEP"` (leave alone; also what empty values and — for existing items — `CREATE_NONE_THEN_KEEP` resolve to). Computed by `resolve_dates_symbolic()`; comparison is verbatim-string, so reformatting the same instant re-sends once (harmless). Per item: skip if synced this run (the full-sync upload paths record `resolved_dates` themselves, via `extra=` since `record()` rebuilds entries wholesale); if no entry matches but a cache exists, print a one-time `NOTICE` and drop the cache (deleting an entry means "stop managing", never "clear dates"); if the fresh resolution equals the cache, skip with no API call (`--force-uploads` bypasses); otherwise send the changed fields via `update_dates()` and advance the cache — including when there is nothing to send because everything resolved `KEEP` (the cache must advance or the entry re-triggers forever). A Canvas date rejection (`date_warning`) leaves the cache un-written so the item is retried (and re-warned, failing the run) every run until fixed. This structurally fixes the old crash hole where the settings entry was recorded before the dates pass ran, so an interrupted run silently skipped the remaining date updates. Deliberately **not** in scope: the cache never triggers a body re-sync, and frontmatter-only dates are not cached (the dates pass has never applied them). First run after upgrading seeds the cache by applying every matched entry once.

**Pinned resources (`pinned_resources`)**: a top-level array of repo-relative paths (file or folder; a folder entry covers everything under it) whose content is **never uploaded** — the motivating case is a quiz students have started, since `sync_quiz_questions()` deletes and re-creates every question, which disconnects existing submissions. Loaded once per run by `load_pinned_resources(repo_path, settings=None)` in `sync.py` (mirrors `load_course_flags`; non-list, non-string entry, or absolute path raises `ValueError` → `die()`; entries are normalized: trimmed, backslashes → `/`, trailing `/` stripped), threaded through `SyncContext.pinned`. An entry pointing *inside* a `quizzes/<name>/` or `question_banks/<name>/` folder that isn't the folder itself or its main file (`<name>.md` / `<name>.toml`) — e.g. a single question file — is also `ValueError` → `die()`: those sync as one unit, so such a pin could only be silently ineffective (the quiz would still sync and still delete/re-create the "pinned" question). `run_sync` validates pins *before* phase 0 so the hard stop happens before anything is applied to Canvas; `run_targeted_sync` and `run_prune` load (and thus validate) them before processing anything as well. Matching is `find_pinned_match()`: exact key or `entry + "/"` prefix. The check (`_skip_if_pinned()`) sits in every upload path — `_sync_content_file`, `_sync_quiz`, `_sync_module`, `_sync_question_banks`, `sync_syllabus`, `_walk_assets`, and the targeted-sync assets branch — **after** the `needs_sync`/`_quiz_needs_sync` staleness gate (an up-to-date pinned resource stays silent, and no Canvas call is made for a pinned skip) and **before** `_canvas_is_newer`. Placement after the staleness gate means the pin wins over `--force-uploads` and explicit `-t`/`-s` targeting by construction (both merely force the gate open). The skip is a **soft warning**: printed inline and accumulated in `ctx.pinned_skips` for `_print_pinned_summary()`, never appended to `ctx.errors`, so the run still succeeds. `check_pinned_resources_coverage()` warns (never errors) about entries matching nothing on disk. `run_prune` also protects pinned orphans in `delete`/`unpublish` modes (counted as "kept"; `manifest` mode is exempt since it never contacts Canvas), and `mv` rewrites pin entries via `compute_pinned_resources_updates()` + `_replace_toml_quoted_string()` (file pins resolve through `path_map`, which includes quiz/qbank inner-file renames; folder pins by prefix against the moved directory) so a rename can't silently unpin a live quiz. `publish` and `import` are unaffected (neither uploads to Canvas). Not manifest state: the pin list lives only in `course_settings.toml`, so unpinning simply lets the normal mtime staleness check re-upload on the next run.

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

The `target` attribute is mapped to Canvas's `new_tab` boolean; `windowFeatures` is discarded (no Canvas equivalent). External URL items default to `new_tab: true` (opens in a new window); add `<!-- target="_self" -->` to embed in an iframe instead.

**Section sub-headers** within a module (Canvas calls these `SubHeader` items) can be written two ways:

1. A `## heading` line becomes a SubHeader at **indent 0**. These stand out visually in both the Markdown source and Canvas.
2. A **plain-text list item** (a bullet with no link) becomes a SubHeader starting at **indent 1**. Nesting with leading spaces increases the indent level (2 spaces per level, same as link items).

```markdown
---
title: "Week 1: Introduction"
published: true
---

## Readings                                         <!-- SubHeader indent 0 -->

- [Week 1 Lecture Notes](../pages/week1-lecture.md)

## Work                                             <!-- SubHeader indent 0 -->

- [Week 1 Assignment](../assignments/week1.md)
- [Week 1 Discussion](../discussions/week1-intro.md)
- Please read the instructions carefully            <!-- SubHeader indent 1 -->
  - And bring your textbook                         <!-- SubHeader indent 2 -->
```

**Item indentation:** Leading spaces on list items control the Canvas module item `indent` parameter (0-5). Every 2 spaces = 1 indent level. `parse_module_body()` captures this from the Markdown and stores it as an `"indent"` key on each item dict. `add_module_item()` passes it through to `module.create_module_item()`. Values exceeding Canvas's maximum of 5 are clamped with a warning. `## headings` always get indent 0; plain-text list items start at indent 1. For the publish website, `render_module_overview()` renders indent-0 SubHeaders as `## headings` and indented SubHeaders as bold `<li>` elements.

**Synchronisation notes:**

- All content files linked from a module must already exist in Canvas (i.e., have entries in the manifest) before the module can be synced. The tool should sync content first, modules second.
- Canvas modules hold a flat ordered list of items. The tool syncs this by comparing the desired item list (derived from the Markdown) against the current Canvas module items and adding, removing, or reordering as needed.
- The manifest tracks both the module's Canvas ID and the Canvas item IDs within it (needed to reorder or delete individual items).
- If any item cannot be added (e.g., its file was never synced so it has no manifest entry), the module's manifest entry is written **without** `last_synced` (`record(..., mark_synced=False)`) — the Canvas ID is kept so the next run updates the same module instead of duplicating it, but the module stays stale and is retried on the next `update`.

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

# flags_used records the [course_flags] values in effect at this file's last
# successful sync — every flag the file references directly or via any snippet
# it includes. Omitted entirely for files that reference no flags. See
# "Course flags (conditional content)" below.
["assignments/week1.md".flags_used]
in_person_class = true

# resolved_dates caches the symbolic resolution of this item's due_dates entry
# as of its last successful date application (concrete date / "NONE" / "KEEP";
# all three keys always present). Omitted when no due_dates entry matches. The
# every-run dates pass skips items whose fresh resolution equals this cache.
# See "due_dates resolved-value caching" above.
["assignments/week1.md".resolved_dates]
due_at = "2025-02-01T23:59:00-05:00"
unlock_at = "NONE"
lock_at = "KEEP"

# section_hashes (settings entry only) caches each course_settings.toml
# section's content hash as of its last successful application; only sections
# whose hash changed are re-applied. See "Section-level change detection" above.
["course_settings/course_settings.toml"]
canvas_id = 0
canvas_type = "course_settings"
last_synced = "2025-02-01T10:00:30"

["course_settings/course_settings.toml".section_hashes]
metadata = "9f2b4c1a0d3e5f67"
grading_standards = "e3b0c44298fc1c14"
assignment_groups = "a1b2c3d4e5f60718"
late_policy = "0123456789abcdef"
default_post_policy = "fedcba9876543210"
tab_configuration = "00112233445566aa"
dashboard_image = "bb4455667788ccdd"
front_page = "5566778899aabbcc"

["modules/week-1.md"]
canvas_id = 55555
canvas_type = "module"
last_synced = "2025-02-01T10:02:00"
# canvas_item_ids maps each linked file path to its Canvas module item ID
# (needed to reorder or delete individual items within the module)
canvas_item_ids = {"pages/syllabus.md" = 201, "assignments/week1.md" = 202}
```

Source Markdown files stay clean — no tool-written fields mixed in with author-written frontmatter. On first publish the tool creates the item, records the Canvas ID in the manifest, and writes the updated manifest back to disk. On subsequent runs it looks up the Canvas ID from the manifest and updates the existing item.

**Error-retry semantics:** `last_synced` is only stamped on a fully successful upload — every "Skipping upload due to errors" path returns before `manifest.record()`, so an errored file stays stale and is retried on the next `update`. Partial cases use `record(..., mark_synced=False)`, which records the `canvas_id` (so the next run updates rather than duplicates) but omits `last_synced`, leaving the entry stale for retry: a module some of whose items could not be added (`_sync_module()`), and `course_settings.toml` when any settings section failed (the succeeded sections' hashes *are* recorded, so the retry re-runs only the failed ones). Similarly, an item whose due-date update Canvas rejected keeps its `resolved_dates` cache un-advanced, so the dates pass retries it every run until fixed.

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
- **Editing a snippet automatically re-syncs files that include it, on the next full `update`/`publish` run.** See [Snippet dependency staleness](#snippet-dependency-staleness) below for how.

### Frontmatter snippets (`PASTE_SNIPPET_INTO_FRONTMATTER`)

The body-content snippet mechanism above can't reach into a file's YAML frontmatter — `parse_frontmatter()` splits the frontmatter block off and parses it before `preprocess_snippets()` ever sees the body, so a `[text](path)` link living inside the YAML block would just be plain text. `expand_frontmatter_snippets()` (`convert.py`) closes that gap with a separate, special-cased mechanism for **merging shared frontmatter values** (e.g. `points_possible`, `rubric` shared by every "worksheet" assignment) rather than reusing body text.

**Syntax:**

```markdown
---
title: "Worksheet 1"
canvas_type: assignment
---
[PASTE_SNIPPET_INTO_FRONTMATTER](../snippets/worksheet-defaults.md)
[PASTE_SNIPPET_INTO_FRONTMATTER](../snippets/another-snippet.md)

Do the worksheet...
```

The link text must be the literal string `PASTE_SNIPPET_INTO_FRONTMATTER` — this is what makes the reference visually distinct from a normal link, and (unlike a bare YAML key) it's clickable in VS Code, since VS Code's Markdown link provider only recognizes `[text](path)` inside the parsed body, never inside frontmatter (the frontmatter block is consumed as a single opaque token before inline parsing runs).

**Resolution algorithm** (`expand_frontmatter_snippets()`):

1. Scan the body line by line from the top. Blank/whitespace-only lines are skipped without stopping the scan.
2. If the first non-blank line isn't a `PASTE_SNIPPET_INTO_FRONTMATTER` link, the body and frontmatter are returned completely unchanged (no marker mode at all — this is a cheap lookahead so ordinary files pay no cost and are never mutated).
3. Otherwise, keep consuming `PASTE_SNIPPET_INTO_FRONTMATTER` lines (and blank lines between them) until hitting the first line that is neither. That line, and everything after it, becomes the returned body.
4. Each referenced file is loaded relative to the *including* file (same convention as body snippets) and validated to resolve inside `snippets_dir` (same path-escape guard as `preprocess_snippets`).
5. Each referenced file's content is parsed with `yaml.safe_load()` — it must be a YAML mapping, not Markdown prose. An error is reported (and that reference skipped) if the path escapes `snippets/`, the file is missing, the YAML is malformed, or it doesn't parse to a mapping.
6. Matched snippets are merged into a `defaults` dict in order — later references override earlier ones for shared keys.
7. The final frontmatter is `{**defaults, **frontmatter}` — the file's own frontmatter always wins over snippet values, so a single file can override one or two fields from a shared default without losing the rest.

**Call sites:** wired in everywhere frontmatter is parsed for actual content sync — `_sync_content_file()` (pages/assignments/discussions) and the module sync path in `sync.py`, plus `parse_quiz_file()` and `parse_question_file()` in `quiz.py` (so quiz-level settings like `time_limit`, or per-question settings like `question_type`/`points_possible`, can be shared across a question bank too). It always runs *before* `preprocess_snippets()`, since the latter only needs to see the real Markdown body.

`publish.py`'s `stage_content_markdown()` (pages/assignments/discussions in the static site) also calls `expand_frontmatter_snippets()` before `preprocess_snippets()`, for the same reason. Skipping it there was a bug: `preprocess_snippets()`'s block-snippet regex (`[text](path)`) matches the `PASTE_SNIPPET_INTO_FRONTMATTER` marker too, so without the frontmatter pass running first, the marker got treated as an ordinary body snippet include and the *raw YAML* of the referenced snippet was spliced straight into the published page body.

**Differences from centralized `due_dates`:** `due_dates` (in `course_settings/course_settings.toml`) is for fields that are mostly *unique per item* but worth reviewing in one place, matched by title. `PASTE_SNIPPET_INTO_FRONTMATTER` is for fields that should be *identical* across many files — edit the snippet once, and every file that references it picks up the change on its next full `update`/`publish` run (see below).

### Snippet dependency staleness

Editing a snippet file (body or frontmatter form) automatically marks every file that references it as stale, without `--force-uploads` or `touch`. No new manifest bookkeeping is involved — `last_synced` is never rewritten, and snippets still have no manifest entries of their own.

**Mechanism:**

- `find_referenced_snippets(text, source_file, snippets_dir)` (`convert.py`) is a passive discovery pass: it reuses `_INLINE_SNIPPET_RE` and `_SNIPPET_LINK_RE` (the same regexes `preprocess_snippets` uses) to resolve every `$path.md$` / `[text](path)` candidate in `text` to an absolute path, keeping the ones that land inside `snippets_dir` and exist. Since `PASTE_SNIPPET_INTO_FRONTMATTER` is just `[text](path)` syntax, it's caught automatically — no separate handling needed. Unlike `preprocess_snippets`/`expand_frontmatter_snippets`, it never reports errors; it's just a staleness probe.
- `manifest.needs_sync()` gained an optional `extra_mtime_paths: Callable[[], Iterable[Path]] | None` parameter. It's a zero-arg *callable*, not a plain list, so the (file-reading) work of resolving referenced snippets is only done when the file's own mtime didn't already settle the staleness question — most files that need syncing for the usual reason never pay the extra cost.
- `sync.py`'s `_file_referenced_snippets(path, snippets_dir)` reads a file, parses its frontmatter (swallowing `yaml.YAMLError` — if the file does need a real sync, its normal processing path reports the error properly), and delegates to `find_referenced_snippets()` on the body.
- Every staleness check that gates a real sync passes this in: `_sync_content_file()` and `_sync_module()` call `manifest_lib.needs_sync(..., extra_mtime_paths=lambda: _file_referenced_snippets(...))`; `_quiz_needs_sync()` folds snippets referenced by the quiz file *and* every question file into its existing mtime comparison; `_sync_question_banks()` does the same via `_question_files_referenced_snippets()`, unioning snippet references across all question files in a bank.

**Scope:** covers pages, assignments, discussions, modules, quiz-level files, question files, and question banks. **Targeted syncs (`-s`/`-t`) are intentionally excluded** — they only ever call the per-file sync functions for files already in their explicit/BFS-derived target set, so this logic never causes a narrow `-s` run to reach outside the files you named. If a snippet shared by two files changes and you `-s` one of them, only that one re-syncs; the other picks up the change on the next full `update`/`publish`.

**Out of scope:** snippet deletion or rename gets no special handling — a file referencing a now-missing snippet still surfaces the existing "snippet not found" error the next time it's actually processed. `_sync_question_banks()` also still doesn't compare individual question file mtimes against the bank's own `last_synced` for non-snippet edits (a pre-existing gap, unrelated to snippets — see TODO.md).

**Not supported:** nested includes (a frontmatter snippet referencing another snippet) — snippet files are parsed as flat YAML, so there is no recursive expansion to support in the first place.

## Course flags (conditional content)

Boolean flags in the optional `[course_flags]` table of `course_settings.toml` gate regions of Markdown body content via C-preprocessor-style HTML-comment directives (`<!-- #if flag -->` / `#elif` / `#else` / `#endif`, each alone on its own line). Implemented in `conditionals.py`; the original design rationale (including settled decisions and future work) is in `DESIGN-course-flags.md`.

**Module surface (`conditionals.py`):**

- `apply_conditionals(text, flags, source_desc, errors=None, *, quiet=False) -> str | None` — evaluates directives, returns the filtered text, or `None` if any directive error occurred (caller must skip the file). Errors are reported through the usual `warn(msg, errors)` convention; `quiet=True` suppresses reporting for passive probes (used by `publish.collect_reachable`, which must not double-report errors that staging will report anyway). Directive lines and false-branch lines are removed **entirely** (no blank line left behind — that's what keeps a conditional list item inside one tight list).
- `find_referenced_flags(text) -> set[str]` — lexical probe: every flag name in any directive, any branch, taken or not. Never errors (malformed directives contribute nothing); the flag-usage analogue of `find_referenced_snippets()`.

**Parsing rules:** only a line that is *nothing but* an HTML comment is considered, and only when its content starts with `#` followed by one of the four keywords or the five reserved misspellings (`#ifdef`, `#ifndef`, `#elseif`, `#elsif`, `#fi` — each a hard error with a "did you mean" hint). Everything else (`<!-- #region -->`, `<!-- published:false -->`, ordinary comments, directive-lookalikes not alone on their line) passes through untouched. Conditions are one flag name, optionally preceded by `not`; an undefined flag is a hard error in **any** directive, taken branch or not (the point of hard errors is catching typos *before* the flag flip that would expose them). Nesting is a plain stack; evaluation is standard preprocessor logic (`parent_active` captured at `#if`, `any_taken` across `#elif` arms, at most one `#else`, last).

**Fence-awareness:** processing iterates `split_fenced_segments()` output. Plain segments are scanned line by line; fenced segments are never scanned and are kept/dropped wholesale by the conditional state in force when the fence opened. This also covers raw-attribute blocks such as ```` ```{=html} ```` (a directive inside one is literal; the block as a whole follows the enclosing conditional).

**Pipeline placement:** the pass runs on each body immediately after `parse_frontmatter`/`expand_frontmatter_snippets` and **before** `preprocess_snippets` — so a snippet ref inside a false branch is removed before snippet expansion (a broken snippet path in a dormant branch reports nothing), and before all structural line parsers (module items, quiz question lists, question sections), which is what makes conditional module items / quiz questions work with no parser changes. `preprocess_snippets()` additionally takes a `flags` param and applies the pass to each snippet's content at insertion time (both block and `$inline.md$` forms; a snippet whose directives error contributes an error and the ref is left unexpanded). Consequence: directives must be balanced within each file and within each snippet file independently. Frontmatter is never processed (YAML; no use case). Loaded once per run by `load_course_flags(repo_path, settings=None)` in `sync.py` (mirrors `load_due_dates`; invalid flag name or non-boolean value raises `ValueError` → `die()`), threaded through `SyncContext.flags`.

**Call sites:** `_sync_content_file()` (pages/assignments/discussions/announcements), `_sync_module()`, `sync_syllabus()`, `parse_quiz_file()`/`parse_question_file()` (via `flags`/`source_desc`/`errors` params, covering quizzes and question banks), and in `publish.py`: `stage_content_markdown()`, `render_module_overview()`, `render_quiz_study_guide()`, `collect_reachable()`, `_render_index()`. `publish` uses the **same** flag values as `update` (no per-target overrides in v1). Two passive probes intentionally do *not* apply conditionals and work on the conservative superset instead: the `_phase_modules` pre-scan and `_get_file_refs()` (targeted-sync BFS) — at worst they re-sync/visit a file that active content no longer references, and the real per-file pass reports any errors exactly once.

**Error handling:** every directive problem (undefined flag, misspelled keyword, missing/unexpected/malformed argument, unbalanced structure — see the README's syntax guide or `DESIGN-course-flags.md` §7 for the full table) is a per-file hard error: reported via `warn()` with the repo-relative path and the offending line, the file is skipped (no upload / not staged), the run continues, and the end-of-run summary fails the run. In `publish`, staging errors are collected into the `stage()` info dict and `run_publish()` raises `ValueError` (→ `die()`) before building the site. Fatal whole-run errors (via `ValueError` → `die()`): non-boolean value or invalid name in `[course_flags]`.

**Manifest caching / selective re-sync:** each entry for a file that references flags gains a `flags_used` sub-table recording the flag **values** in effect at its last successful sync — per-file values, not a global snapshot, so a file that errored/was skipped during the run that flipped a flag stays correctly stale. Computed by `_flags_used_for()` as `find_referenced_flags(body) ∪ find_referenced_flags(each referenced snippet's content)` (snippets enumerated with `find_referenced_snippets()`, same as the snippet-mtime path). `manifest.needs_sync()` gained `current_flags`/`verbose` params: after the mtime checks, an entry is stale when any recorded flag is missing from `current_flags` or differs in value (`manifest.flag_change()` finds the first difference; in verbose mode the reason is printed, e.g. ``re-syncing: flag 'in_person_class' changed true → false``). A flag *deleted* from the TOML makes referencing files stale → they re-sync → they hit the undefined-flag hard error (desired loud failure). Snippet-borne flags need no extra machinery: adding a directive to a snippet bumps the snippet's mtime, which the existing `extra_mtime_paths` check catches, and the re-sync refreshes `flags_used`. `_quiz_needs_sync()` folds the flag check in the same way it folds question-file mtimes, and the quiz entry's `flags_used` unions the quiz `.md` and **all** question files (globbed, not just currently-listed ones — a question excluded by a false flag must still make the quiz stale when the flag flips); quiz + questions sync as one unit. Question banks apply conditionals to question content but get no `flags_used` until their pre-existing staleness gap is fixed (see TODO.md). `mv` needs no changes — `flags_used` lives inside the per-file entry, which `mv` re-keys wholesale (covered by a test).

**Unused-flag warning:** after the content pass, `check_course_flags_coverage()` (mirroring `_check_due_dates_coverage()`) scans every `.md` in the repo (including snippets — a flag referenced only inside a snippet counts as used), unions `find_referenced_flags()`, and warns once per defined-but-unreferenced flag. Warning only, never an error (defining a flag before writing the content that uses it is legitimate). Runs in `update` (full and targeted) and `publish`.

**Subcommand matrix:** `update` — full feature; `publish` — same pass, same flag values, same hard errors (file skipped from the site) and unused-flag warning, no manifest/staleness work; `import` — no change (the importer never generates directives); `mv` — no change (entry re-keyed wholesale); `find-orphans`/`prune` — no change (manifest/file-presence based).

**Future work** (spec'd in `DESIGN-course-flags.md` §12, tracked in TODO.md): whole-resource exclusion via delete/prune (as opposed to the unpublish-based `published_if` below), `--flag` CLI overrides, richer conditions (`and`/`or`, non-boolean values), and a `list-flags` report.

### `published_if` (frontmatter) and due_dates `only_if`

Structured, per-key evaluation of a flag condition — deliberately narrower than the general `#if` pass above: it computes exactly one value (`published`, or whether a `due_dates` entry applies) instead of filtering arbitrary text, so it sidesteps the call-site-sprawl and per-variant-YAML-breakage risks that ruled out a general `#if` in frontmatter/TOML (see TODO.md).

**`resolve_published_if(frontmatter, flags, source_desc, errors=None, *, quiet=False, forbid=False, forbid_reason="") -> bool | None`** (`conditionals.py`) — resolves the effective `published` boolean for one content file. Absent `published_if`, it's just `frontmatter.get("published", False)`. With `published_if: flag_name` (or `not flag_name`, same grammar as `#if`, via the shared `parse_condition()`/`_parse_condition()` helper), it computes `published` from the flag instead. Returns `None` on any problem (caller skips the file): `published_if` combined with a literal `published` key (ambiguous — hard error), a malformed/missing condition, an undefined flag, or `forbid=True` (set for announcements — Canvas can never un-post one, so the whole feature is disallowed there; message names `forbid_reason`). `quiet=True` suppresses reporting for passive probes, same convention as `apply_conditionals`. `find_referenced_flags_in_frontmatter(frontmatter) -> set[str]` is the frontmatter analogue of `find_referenced_flags()` — the `published_if` flag name if well-formed, else empty; never errors.

**Call sites (loud, primary):** `_sync_content_file()` computes `canvas_type` up front (pure function of `local_key`) so the `forbid=(canvas_type == "announcement")` check runs right after frontmatter/frontmatter-snippet expansion, before body conditionals or HTML conversion — fail fast, and the existing "skip unpublished announcement" block downstream now reads the resolved `published` instead of `frontmatter.get("published", ...)` directly. `_sync_quiz()` resolves it the same way from `parse_quiz_file()`'s (snippet-expanded) frontmatter. Both skip the upload (`Skipping upload due to errors: …`) when resolution returns `None`.

**Call sites (quiet, passive probes):** `_content_default_published()` (module-item default when there's no explicit `{published:...}` override) and `publish.py`'s `_published_title()`/`_item_published()` resolve quietly (`quiet=True`) and default to *not published* on any problem — the referenced file reports the error loudly when it syncs/discovers on its own; these probes must not double-report. `flags` is threaded through the publish call graph that didn't need it before: `_discover_type()`, `_find_syllabus()`, `build_nav()`, `_item_published()`, `_content_default_published()`.

**`flags_used`:** `_flags_used_for()` now expands frontmatter snippets (`expand_frontmatter_snippets()`) before scanning, and unions `find_referenced_flags_in_frontmatter(fm)` with the existing body/snippet scan — so a `published_if` flag (including one delivered via `PASTE_SNIPPET_INTO_FRONTMATTER`) is recorded and a flip re-syncs (and thus unpublishes/republishes) exactly the right files, via the same `manifest.flag_change()` machinery `#if` already uses. `check_course_flags_coverage()` also unions `find_referenced_flags_in_frontmatter()` so a flag used only via `published_if` doesn't spuriously warn as unused.

**`filter_due_dates_by_flags(due_dates, flags) -> list[dict]`** (`sync.py`) — drops `due_dates` entries whose `only_if` (or `not only_if`) condition is unmet, called immediately after `load_due_dates()` in `run_sync`/`run_targeted_sync`/`list-titles` (flags loaded first so they're available). No TOML preprocessing: entries without `only_if` are always kept, so `course_settings.toml` stays valid TOML in every flag configuration. An undefined flag or malformed condition is a whole-run config error — raises `ValueError` → `die()`, same convention as `load_course_flags()`. Filtered-out entries are simply absent from `ctx.due_dates`, so `_apply_due_dates_only()` never sees them (no API call, no cache write) and needs no changes itself.

**Due-dates coverage interaction:** `_check_due_dates_coverage()` gained a `flags` param; for each gradeable content file with a `published_if` key it quietly resolves it, and an item resolving to *not published* (this offering doesn't include it) is treated as expected-to-have-no-`due_dates`-entry — excluded from the "no due_dates entry" warning list. This is orthogonal to `only_if`: an entry dropped by `only_if` still leaves its content file subject to the normal "no entry" warning unless that file is itself `published_if`-excluded.

**Why not a general `#if` in frontmatter/TOML instead:** considered and rejected (2026-07-06) in favor of the narrower structured-key approach above. A full "process `#if` everywhere" design would need conditional YAML to stay valid under *every* flag combination (nothing checks unselected combinations — breakage surfaces a quarter later), would touch dozens of `parse_frontmatter` call sites beyond the handful `apply_conditionals` hits today (title-collision check, due-dates coverage, `list-titles`, `mv`, …) risking silent `update`/`publish` drift if one site is missed, and `<!-- #if flag -->` is invalid YAML/TOML syntax (unlike in a Markdown body), so VSCode would flag conditional frontmatter as broken. `published_if`/`only_if` sidestep all three by computing exactly one value per key, post-parse, from the same settings dict already loaded — no flags-file migration, no per-site drift risk, no invalid-syntax problem.
