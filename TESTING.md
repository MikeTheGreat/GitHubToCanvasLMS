# Testing Strategy

Tests are organised in three layers. All tests live in `tests/` in this repo; no external test repos or live Canvas instances are required for the normal test suite.

## Layer 1 — Pure unit tests

Test individual functions in isolation, with no network or Canvas dependency. Each test passes in a string or small data structure and asserts on the output:

- **Snippet preprocessor** (`test_convert.py`): given Markdown text with `[text](../snippets/...)` links, assert the correct substitution; test nested-include error behaviour.
- **Link rewriter** (`test_link_rewrite.py`): given an HTML fragment and a manifest dict, assert that `<img src>` and `<a href>` are rewritten to the correct Canvas URLs; test each link type (page, assignment, discussion, asset, quiz, external, anchor).
- **Manifest** (`test_manifest.py`): TOML round-trips, flush-on-every-write behaviour, create-vs-update lookup logic, `needs_sync` timestamp comparisons.
- **Quiz parsing** (`test_quiz.py`): quiz-level file parsing (frontmatter, question order, description), MCQ/essay/true-false question file parsing, answer weight assignment, correct-answer detection.
- **IMSCC parsing** (`test_imscc_convert.py`): unit tests for each XML parser — `_parse_manifest_metadata`, `_parse_course_settings_full`, `_parse_grading_standards`, `_parse_assignment_groups`, `_parse_late_policy`, `_parse_context`, `_parse_events`; HTML body extraction; frontmatter rendering.
- **Processing order**: asset-first, module-last, alphabetical sorting of folders and files within folders.
- **Frontmatter parsing**: all content types and their specific fields.

## Layer 2 — Integration tests with mocked Canvas (`test_sync.py`)

Mock the `canvasapi` library with `pytest-mock`. Run the full sync pipeline against the fixture course and assert on which `canvasapi` methods were called, with what arguments, and in what order. Key scenarios to cover:

- First sync (no manifest): all items created, manifest written after each item
- Second sync (manifest present): all items updated, no creates called
- Stub creation: an assignment that links to a page not yet synced triggers a stub create before the assignment upload, followed by a real page upload that overwrites the stub
- Interrupted sync: pre-populated partial manifest causes only the remaining items to be synced
- Missing local file: tool prints an error, removes the tag, and continues (no stub, no crash)
- Module sync: module items created in the correct order; SubHeader items interleaved correctly
- Timestamp skip: a file whose mtime is older than `last_synced` is skipped; `--force-uploads` overrides
- Quiz sync: quiz created/updated; questions deleted and re-created in order; skipped when all files up-to-date; re-synced when any question file is newer than `last_synced`; quiz module items use type `"Quiz"`
- `--target-recursively`: BFS from a module reaches all referenced content; module deferred until content is in manifest
- `--single-target`: only specified files processed; no BFS
- Combined `-t` + `-s`: `-t` uploads first, `-s` skips anything already uploaded via `needs_sync`

## Layer 3 — Test fixtures (`tests/fixtures/`)

A minimal but complete course repo committed directly into this tool repo. It covers every case the tests need — cross-links between content types, snippet includes, nested assets, modules with SubHeaders, files with every frontmatter variant. Fixtures are plain files; no special tooling needed to use them.

The IMSCC fixture (`tests/fixtures/imscc/`) is a synthetic `.imscc`-style directory covering all content types. It includes:

- `imsmanifest.xml` — with lom lifecycle (last_modified) and rights (copyright) metadata
- `course_settings/course_settings.xml` — all course settings fields including booleans, ints, and nested `default_post_policy`
- `course_settings/grading_standards.xml` — one grading standard with threshold data array
- `course_settings/assignment_groups.xml` — two groups, one with drop-lowest rules
- `course_settings/late_policy.xml` — late submission deduction settings
- `course_settings/context.xml` — canvas_domain and course_id (used to pre-fill `canvas.toml`)
- `course_settings/events.xml` — two events (one all-day, one with HTML description)
- `course_settings/module_meta.xml` — one module with items of every supported type
- `course_settings/syllabus.html` — syllabus HTML content
- `wiki_content/`, `g_assignment_1/`, `g_discussion_1*.xml`, `g_quiz_1/`, `g_exturl_1.xml`, `web_resources/` — one of each content type

**What to assert on:** The integration tests assert on the `canvasapi` calls our code makes — arguments, order, and count. They do *not* assert on Canvas's behaviour (storing, retrieving), because that is Canvas's responsibility, not ours.

## Tools

- `pytest` — test runner
- `pytest-mock` — `canvasapi` mocking
- `tomllib` (stdlib) — manifest fixture parsing in tests
