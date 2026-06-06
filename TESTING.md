# Testing Strategy

Tests are organised in three layers. All tests live in `tests/` in this repo; no external test repos or live Canvas instances are required for the normal test suite.

## Layer 1 — Pure unit tests

Test individual functions in isolation, with no network or Canvas dependency. Each test passes in a string or small data structure and asserts on the output:

- **Snippet preprocessor** (`test_convert.py`): given Markdown text with `[text](../snippets/...)` links, assert the correct substitution; test nested-include error behaviour.
- **Link rewriter** (`test_link_rewrite.py`): given an HTML fragment and a manifest dict, assert that `<img src>` and `<a href>` are rewritten to the correct Canvas URLs; test each link type (page, assignment, discussion, asset, quiz, external, anchor).
- **Manifest** (`test_manifest.py`): TOML round-trips, flush-on-every-write behaviour, create-vs-update lookup logic, `needs_sync` timestamp comparisons.
- **Quiz parsing** (`test_quiz.py`): quiz-level file parsing (frontmatter, question order, description), MCQ/essay/true-false question file parsing, answer weight assignment, correct-answer detection.
- **IMSCC parsing** (`test_imscc_convert.py`): unit tests for each XML parser — `_parse_manifest_metadata`, `_parse_course_settings_full`, `_parse_grading_standards`, `_parse_assignment_groups`, `_parse_late_policy`, `_parse_context`, `_parse_events`, `_parse_rubrics`, `_parse_files_meta`; HTML body extraction; frontmatter rendering; `parse_qti_questions` with both Canvas-extended format (`question_type` labels) and IMS CC format (`cc_profile` labels).
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
- `course_settings/rubrics.xml` — two rubrics with criteria and ratings (tests extraction to `rubrics.toml`)
- `course_settings/files_meta.xml` — one hidden folder and three files with varied settings (tests extraction to `files_meta.toml`)
- `course_settings/media_tracks.xml` — empty (skipped; no round-trip content)
- `course_settings/canvas_export.txt` — export metadata text (skipped; no round-trip content)
- `course_settings/syllabus.html` — syllabus HTML content
- `wiki_content/`, `g_assignment_1/`, `g_discussion_1*.xml`, `g_quiz_1/`, `g_exturl_1.xml`, `web_resources/` — one of each content type
- `g_exturl_1.xml` — webLink with `target="_blank"` and `windowFeatures` on the `<url>` element (tests §4.8 attribute extraction and module comment output)
- `g_discussion_attach.xml` + `g_discussion_attach_meta.xml` — discussion with `<attachments>` block (tests §4.7 attachment extraction and `## Attachments` section)
- `g_quiz_1/g_quiz_1.xml` — updated with `<itemfeedback>` elements on MCQ (general, correct, incorrect, per-answer) and `<itemfeedback ident="solution">` on essay question (tests §4.10.7 feedback and §4.10.11.2 sample solution)
- `g_quiz_types.xml` — standalone QTI file with `cc.multiple_response.v0p1`, `cc.fib.v0p1`, `cc.pattern_match.v0p1` questions (unit tests for new question type parsing and output format)
- `lti_resource_links/g_lti_1.xml` — LTI 1.3 resource XML (tests that `imscc_path` is populated from `<file>` children, not from the empty `href=""` attribute)
- `g_quiz_2/` with `assessment_meta.xml` and `assessment_qti.xml` + `non_cc_assessments/g_quiz_2.xml.qti` — quiz in Canvas export format (tests the `href="" + <dependency>` detection path; also tests that `non_cc_assessments/*.xml.qti` is preferred for question parsing because it carries `points_possible`)
- `non_cc_assessments/g_bank_1.xml.qti` — QTI objectbank with 2 questions (MCQ with `original_answer_ids`, essay), `bank_context_uuid`, and `bank_state` (tests question bank category detection, metadata extraction, and `question_banks/` output)
- Module updated to include a `ContextExternalTool` item (tests that it is emitted as a URL link, not as a SKIPPED comment)

**What to assert on:** The integration tests assert on the `canvasapi` calls our code makes — arguments, order, and count. They do *not* assert on Canvas's behaviour (storing, retrieving), because that is Canvas's responsibility, not ours.

## Tools

- `pytest` — test runner
- `pytest-mock` — `canvasapi` mocking
- `tomllib` (stdlib) — manifest fixture parsing in tests
