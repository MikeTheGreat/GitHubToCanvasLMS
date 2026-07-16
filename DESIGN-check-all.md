# DESIGN: `update --check-all` (offline dry-run of a fresh full sync)

Status: **planned, not yet implemented.** This doc is the implementation plan;
when the feature ships, fold the user-facing parts into README.md, the
internals into ARCHITECTURE.md, and delete this file (per the TODO.md rule).

## Goal

`gg update <repo> --check-all` runs the *entire* update pipeline as if syncing
to a brand-new, completely empty Canvas course — every file converted, every
link resolved, every rubric/assignment-group/due-date reference checked — but:

- **never contacts Canvas** (works offline, no API token required), and
- **never writes anything** (no Canvas changes, no `.canvas-manifest.toml`
  changes).

Use case: develop a course repo over a break, run `--check-all` periodically to
catch broken links / missing rubrics / malformed frontmatter, then deploy the
whole course with a plain `update` when the quarter starts.

## CLI surface

New flag on `update`:

```text
--check-all    Validate the whole repo as if uploading it for the first time
               to a brand-new empty Canvas course. Reports every problem a
               real full sync would hit, but contacts Canvas for nothing and
               writes nothing (Canvas and .canvas-manifest.toml untouched).
```

Interactions (all enforced with `die()` in `cli.py`):

- `--check-all` + `-t`/`-s` → error ("check-all always checks everything").
- `--check-all` + `--force-uploads` / `--force-overwrite` → error (both are
  meaningless: check-all already treats every file as new and never consults
  Canvas timestamps).
- `--verbose` works normally.
- `canvas.toml` is still required for `course_id`/`base_url` (link rewriting
  bakes `course_id` into URLs), but the API-token check is skipped:
  `config.load()` grows a `require_token: bool = True` parameter.

Exit code: nonzero when any error/warning summary is non-empty, so the check
can run in a script or pre-deploy hook. (Plain `update` keeps its current
behavior of exiting 0 with a yellow summary; changing that is out of scope.)

## Semantics: "fresh empty course" simulation

- The on-disk manifest is **ignored**: the run starts from an empty in-memory
  manifest, so every asset, page, assignment, discussion, announcement, quiz,
  question bank, module, the syllabus, course settings, and rubrics all take
  the full "needs sync" path — exactly what a first deploy will do.
- The in-memory manifest **is still populated during the run** (with fake
  Canvas ids). This is required, not incidental: link rewriting, annotatable-
  attachment resolution, and module-item resolution all read manifest entries
  written by earlier phases.
- Nothing is flushed to disk; `.canvas-manifest.toml` is byte-identical after
  the run.

### What gets checked (all existing pipeline diagnostics, on every file)

- course_settings.toml / rubrics.toml parse errors, misplaced
  `tab_configuration`, invalid `[course_flags]` / `pinned_resources` /
  `due_dates only_if` (already hard errors before any upload)
- malformed YAML frontmatter; unknown announcement frontmatter fields
- missing snippets; conditional (`#if`) errors; `published_if` errors
- Pandoc conversion; the `<h1>` heading error
- broken local links/images (`rewrite_links` "local file not found")
- title collisions
- rubric references that name no rubric in rubrics.toml (`_apply_rubric`
  resolves against the fake ids created from rubrics.toml — an undefined name
  warns exactly as on a real fresh sync)
- assignment_group references that name no group in course_settings.toml
  (same mechanism via fake `sync_assignment_groups`)
- `annotatable_attachment` files missing from disk
- quiz .md / question-file parse errors; question banks
- module items referencing nonexistent files; module_order.toml names
- due_dates coverage (entries matching no assignment/discussion/quiz)
- unused course flags; pinned_resources entries matching nothing on disk

### What can NOT be checked (document in README)

Anything only the Canvas server decides: date rejections (e.g. due dates
outside the term), quizzes needing a manual save, publish-state rejections,
API permission errors. `--check-all` is "everything knowable locally".

## Mechanism

### 1. API indirection: `ctx.api`

All Canvas traffic in the sync pipeline already goes through `capi.*`
functions (module `canvas_api`) with one exception (syllabus, below).
Plan: dependency-inject at that boundary.

- `SyncContext` gains `api: Any = capi` (a module duck-types fine).
- Mechanical replace of `capi.foo(...)` → `ctx.api.foo(...)` throughout the
  `run_sync` call graph in sync.py.
- Functions that don't take ctx get an `api=capi` keyword parameter:
  `sync_course_settings`, `_make_stub_creator`, `_canvas_is_newer`.
- `run_prune` / `run_targeted_sync` / orphans keep calling real `capi`
  directly (out of scope).

### 2. Fix the syllabus bypass (and a latent bug)

[sync.py:947](src/github_to_canvas/sync.py#L947) calls
`ctx.course.update(course={"syllabus_body": html})` directly. Add a
`capi.update_syllabus_body(course, html)` wrapper and call it via `ctx.api`,
restoring the "no direct canvasapi calls in sync.py" invariant.

Latent bug found while reading: `sync_syllabus` passes a no-op stub creator
(`lambda *_: {}`) to `rewrite_links`. On a genuinely fresh course (empty
manifest), a syllabus that links to a not-yet-synced page makes
`canvas_content_url({})` raise `KeyError("canvas_type")`. check-all always
simulates a fresh course, so it would hit this every time. Fix as part of this
feature: give the syllabus the real stub creator (it runs before the content
phases, same as quizzes' stub usage) — this also fixes real first deploys.

### 3. `DryRunCanvas` fake (new module, e.g. `src/github_to_canvas/dryrun.py`)

A small stateful class implementing the same public names/signatures as the
`canvas_api` functions the pipeline uses. State: a fake-id counter plus dicts
for assignment groups and rubrics so name-resolution behaves like a real fresh
sync (settings sync "creates" them, later phases resolve against them).

Surface needed (from `grep capi\. sync.py`, run_sync graph only) and return
shapes to mirror (verify each against canvas_api.py when implementing):

- creates returning manifest-entry dicts: `create_or_update_page`
  (`canvas_id`, `canvas_url`, `html_url`), `create_or_update_assignment` /
  `create_or_update_discussion` / `create_or_update_announcement`
  (`canvas_id`, `html_url`, `date_warning=False`, no `rubric_settings`),
  `create_or_update_quiz`, `upload_asset` (`canvas_id`, `canvas_url`),
  `create_stub`, `create_or_update_module`, `sync_question_bank`
- settings: `sync_grading_standards` (fake id or None),
  `update_course_metadata`, `upload_course_image`, `sync_assignment_groups`
  (records names; returns `[]` deferred rules — or all names, to mirror the
  fresh-course defer path; either is harmless), `update_late_policy`,
  `update_post_policy`, `sync_tab_configuration`, `sync_rubrics` (returns
  `({title: fake_id}, created_titles, [], [])`), `update_syllabus_body`
- reads: `get_assignment_group_ids` / `get_rubric_ids` (return recorded fakes),
  `get_canvas_updated_at` (return None — moot with empty manifest but safe)
- modules/quizzes: `clear_module_items` (no-op), `add_module_item` (returns
  `(fake_id, None)`), `reposition_module`, `sync_quiz_questions` (fake id
  map), `finalize_quiz_publish_state` (False), `apply_assignment_group_rules`,
  `set_front_page`, `associate_rubric_with_assignment`,
  `remove_rubric_from_assignment`, `update_dates`

The fake never imports `canvasapi`/`requests` paths that touch the network;
`ctx.course` is a tiny `DryRunCourse` placeholder (only needed because
signatures pass it through; the fake ignores it).

### 4. Manifest write suppression

`manifest_lib.flush()` and `record()` accept `manifest_path: Path | None`;
`None` = update the in-memory dict but skip the disk write. check-all passes
`manifest={}`, `manifest_path=None`.

### 5. `run_sync` / CLI wiring

- `run_sync(..., check_all: bool = False)`. When set: build
  `DryRunCanvas()` + `DryRunCourse()` instead of `capi.get_course(config)`,
  empty manifest, `manifest_path=None`, print a banner
  (`CHECK MODE: simulating first sync to an empty course; nothing will be
  uploaded`), and print `Update successful` variant as
  `Check complete — no problems found (nothing uploaded)` /
  `Check complete; please fix the problems listed above`.
- Per-item lines: gate the action verbs on check mode where cheap
  ("Would upload:" instead of "Uploading:", mirroring `mv --noop`'s
  `prefix = "Would " if noop else ""` style); the banner carries the rest.
- `cli.py update`: validate flag combos, skip `_ensure_pandoc`? **No** — Pandoc
  is required (conversion is half the point); keep the check. Skip
  `get_course` and the `Course: <name>` line in check mode.

## Testing plan (tests/test_check_all.py or extend test_sync.py)

Reuse the existing fixtures-copy pattern (`course_root`), but no MagicMock
course is needed — that's the feature:

1. Full check-all run over fixtures: completes, exit path OK, and
   `.canvas-manifest.toml` is never created/modified (assert file absent /
   bytes unchanged when pre-seeded).
2. Network isolation: run with a `mocker.patch` on `canvas_api.get_course`
   (and/or `canvasapi.Canvas`) asserting it is never called.
3. A pre-existing stale manifest on disk is ignored (everything still
   processed) and untouched afterwards.
4. Detects seeded problems: broken link, rubric name not in rubrics.toml,
   assignment_group name not in settings, missing snippet, `<h1>`, title
   collision, due_dates entry matching nothing — each surfaces in errors and
   flips the exit/summary.
5. Syllabus-links-on-fresh-course regression test (the latent KeyError above)
   for both check-all and the real stub-creator path.
6. Flag-combination errors (`--check-all -t x` etc.).
7. Existing test suite still green (the `ctx.api` refactor is the risky part;
   the mocked-course tests cover it since real `capi` remains the default).

## Docs to update when implemented

- README.md: `update` options table + a short "Checking a course before the
  quarter starts" workflow section (including the "server-side-only failures
  still possible" caveat).
- ARCHITECTURE.md: sync-pipeline section (ctx.api indirection, DryRunCanvas,
  manifest_path=None convention, syllabus stub-creator fix).
- TODO.md: remove the `--check-all` entry; delete this DESIGN file.
