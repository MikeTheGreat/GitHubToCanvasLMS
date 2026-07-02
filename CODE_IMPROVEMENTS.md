# Code Improvement Report

**Audience:** a future Claude Code instance (any model, including Sonnet/Opus) doing
incremental cleanup work on this repo.
**Generated:** 2026-07-01, from a full read of `src/`, a skim of `tests/`, and the
tool runs recorded below. Line numbers refer to the working tree on that date
(uncommitted changes to `convert.py`/`test_convert.py` included) — **re-verify line
numbers with grep before editing; they will drift.**

**Prime directive:** the code currently works — 831 tests pass. Every item below is
intended to be behavior-preserving. If an item turns out to require a behavior
change to complete, stop and ask the user instead of pushing through.
A "Deliberately left alone" section at the end lists things that look like debt
but should **not** be touched.

---

## 0. Ground rules for working from this report

1. **Run the baseline first.** `uv run pytest -q` → expect **831 passed** (~4–5 min;
   see item T1 for why it's slow).
2. **One item per change.** Do one report item, run the full suite, stop. Don't
   batch unrelated items into one edit session.
3. **Never commit** (per CLAUDE.md) unless the user explicitly asks.
4. **Keep the three core subcommands in sync** (per CLAUDE.md): changes to shared
   behavior of `update` / `import` / `publish` must be reflected in all three.
5. Tests may be edited when an item says so (e.g., updating call sites of a private
   function). Fixture *content* under `tests/fixtures/` should not change unless an
   item says so.
6. When an item is done, check whether TODO.md / ARCHITECTURE.md / README.md need a
   matching edit (several items below overlap with TODO.md entries — update, don't
   duplicate).

### Baseline measurements (2026-07-01, updated 2026-07-02 after E1 fix)

| Check | Result |
| --- | --- |
| `uv run pytest -q` | 831 passed, 202 warnings, **~250–310 s** (varies by machine load) |
| `uvx ruff check src/ tests/` | 32 findings: 16×F401, 7×E402, 4×F841, 2×F541, 2×F811, 1×E741 |
| `uvx radon cc src -n D` | 18 functions rated D–F (worst: `parse_imsmanifest` F/87, `_sync_content_file` F/73, `_parse_qti_items` F/62, `run_sync` F/47) |
| `uvx vulture src --min-confidence 80` | 3 findings (2 unused imports, 1 unused variable) |
| Functions with ≥10 params | `_sync_content_file` (16), `_sync_quiz` (15), `_sync_module` (12), `_walk_assets` (11) |
| Test-time hotspot | `test_imscc_import.py` + `test_imscc_convert.py` = **~200 s of the total** (see T1) |

---

## 1. Environment fix — FIXED 2026-07-02

### E1. ~~The project venv is broken~~ — RESOLVED

- **Was:** console-script shebangs pointed at a path that no longer exists.
  `uv run pytest` failed with `Failed to spawn: pytest`. Every script in
  `.venv/bin/` had shebang `#!/home/mike/.../GitHubToCanvasLMS/.venv/bin/python3`
  (the repo's *parent* directory) — the repo was evidently moved one level deeper
  after the venv was created, and `uv sync` did not rewrite the scripts.
- **Fix applied:** deleted `.venv/` and re-ran `uv sync --extra dev`. New shebangs
  correctly point at
  `.../GitHubToCanvasLMS/GitHubToCanvasLMS/.venv/bin/python`.
- **Verified:** `uv run pytest -q` now runs directly (no `python -m` workaround
  needed) and reports **831 passed**, matching the pre-fix baseline exactly — no
  behavior change, only the broken symlink/shebang chain was repaired.
- No further action needed on this item.

---

## 2. Zero-risk hygiene (mechanical; do in one pass each)

### H1. Ruff findings in `src/` (6 items)

- `canvas_api.py:11` — unused import `PaginatedList` (F401).
- `imscc_import.py:11` — unused import `HTMLParser` (F401).
- `imscc_import.py:495` — unused local `content_type` inside
  `rewrite_imscc_links._replace_canvas_ref` (F841).
- `imscc_import.py:1764` — unused local `ns` in `_parse_course_settings_full` (F841).
- `imscc_import.py:2572` — f-string with no placeholder (F541).
- `mv.py:177` — unused local `src_str` in `_compute_case_rename_dest_rel` (F841).
- **Effort:** minutes. **Risk:** none. Run full suite after.

### H2. Adopt a ruff config so the remaining findings stop being noise

- Add `[tool.ruff]` / `[tool.ruff.lint]` to `pyproject.toml`. The 7 E402 findings in
  `cli.py` are **intentional** (`load_dotenv()` must run before the `.config` import
  reads `CANVAS_API_TOKEN`): add a per-file ignore for `cli.py:E402` *and* a code
  comment at `cli.py:13` explaining why the imports come after `load_dotenv`.
  The stray `#` comment on `cli.py:10` looks like a leftover; replace it with that
  explanation.
- Fix or ignore the test-file findings (`tests/test_due_dates.py:642` unused var,
  `:693` E741 ambiguous name, `tests/test_imscc_import.py:1004` F541, and
  `tests/test_imscc_import.py:1071/1091` F811 — both are redundant function-local
  `import shutil` statements shadowing the module-level import; just delete them).
- **Effort:** ~30 min. **Risk:** none.

### H3. Dead parameter and dead conditional in `mv.py`

- `_describe_changes(... manifest_updates ...)` (`mv.py:549-558`): the
  `manifest_updates` parameter is always passed `None` (`mv.py:645`) and never used.
  Remove the parameter and update the one call site.
- `mv.py:200`: `dest_name = dest.name if not _is_case_only_rename(src, dest) else dest.name`
  — both branches are identical. Replace with `dest_name = dest.name`.
- **Effort:** minutes. **Risk:** none.

### H4. Unused parameter in `imscc_import._qti_text`

- `_qti_text(el, ns)` (`imscc_import.py:1108`) never uses `ns`; all ~10 call sites
  pass `""`. Drop the parameter.
- **Effort:** minutes. **Risk:** none.

### H5. Function-local imports that belong at module top

- `sync.py:468` — `from collections import defaultdict` inside
  `check_title_collisions`.
- `cli.py:509` — `from datetime import datetime` inside `_format_concise_date`.
- `mv.py:486` and `mv.py:504` — `import uuid` twice inside `_do_move`.
- (`canvas_api.py:774`'s `import requests as _requests` is **deliberate** — see
  "left alone" section — add a comment there instead of moving it, or move it and
  keep the conftest patch target `requests.put` working; verify
  `tests/conftest.py` still blocks it either way.)
- **Effort:** minutes. **Risk:** none except the canvas_api one — treat that one
  as read-only.

### H6. `cli.py` — pointless re-raise clauses

- Four commands end their `try` with `except Exception as e: raise e` (`cli.py:299,
  343, 422, 625`). `raise e` from an except block is equivalent to letting it
  propagate, but resets nothing and adds noise. Replace with a comment-only
  `# unknown errors: let them traceback for debugging` and delete the clause
  (identical behavior), or keep the clause but use bare `raise`.
- Note the contradiction with the comment at `cli.py:25` ("all commands must use
  die() ... no tracebacks") and TODO.md line 25 which tracks that as future work —
  do **not** convert these to `die()` now; that changes behavior on unknown errors.
- **Effort:** minutes. **Risk:** none.

---

## 3. Test-suite improvements (big wins, no product code touched)

### T1. Cut the test suite from ~4 min to well under 1 min — DONE 2026-07-02

- **Was:** `tests/test_imscc_import.py` contained **124 calls** to
  `run_import(FIXTURE_DIR, output_dir)`, each a full pipeline run spawning many
  Pandoc subprocesses (~1.6 s each). `test_imscc_convert.py` turned out to be fast
  already (2.5 s, no `run_import` calls at all — it only unit-tests pure parsers);
  the report's estimate of it being part of the hotspot was wrong. `test_sync.py`
  (166 tests) takes only ~5 s — it was never the problem.
- **Fix applied:** added a `scope="module"` fixture `imported_dir` (in
  `test_imscc_import.py`, using `tmp_path_factory`) that runs `run_import` once per
  module and shares the output directory; ~124 read-only tests were switched from a
  per-test `run_import` call to consuming this shared fixture. Kept per-test
  `run_import` for the 4 tests that need their own directory: failure-mode
  (`test_run_import_fails_if_output_nonempty`), zip-input equivalence
  (`test_run_import_from_zip`), and the two tests that mutate a copied fixture
  (`test_run_import_no_domain_no_snippet`, `test_run_import_no_course_id_no_snippet`).
  One test (`test_lti_resource_imscc_path_set`) had an unused `output_dir` param
  (it only calls `parse_imsmanifest` directly) — dropped the dead param. Applied the
  same module-scoped-fixture pattern to the 2 `run_import` calls in
  `test_due_dates.py` (`due_dates_imported_dir`).
- **Verified:** 831 passed, 202 warnings, stable at **~39 s** across repeated runs
  (was 226 s baseline on this machine). No test count change, no behavior change —
  all switched tests were confirmed read-only against the output directory before
  the fixture scope changed.
- **Note for TODO.md/TESTING.md:** TESTING.md now documents the `uv run pytest -q`
  invocation and the fast-suite expectation near the top.

### T2. `pathspec` deprecation warning (86+ occurrences per run)

- `ignore.py:48` uses `PathSpec.from_lines("gitwildmatch", ...)`, which pathspec has
  deprecated in favor of `GitIgnoreSpec`. **Do not blind-swap**: `GitIgnoreSpec`
  changes `!`-negation semantics to match git more closely.
- Approach: change to `pathspec.GitIgnoreSpec.from_lines(lines)` in a scratch
  branch, run `tests/test_ignore.py` + the ignore-related tests in `test_sync.py`.
  If green, adopt (the tests cover globs, `~$*`, dir-only `build/`, `!` negation).
  If anything fails, instead add a targeted `filterwarnings` entry in
  `pyproject.toml`'s pytest config and leave the code alone.
- **Effort:** ~30 min. **Risk:** low-medium (semantics), fully fenced by tests.

### T3. Optional: block *all* outbound HTTP in tests, not just `requests.put`

- `tests/conftest.py` patches only `requests.put` (the one raw-requests call in
  `canvas_api._set_module_item_published`). All other network calls go through the
  mocked `canvasapi`. A socket-level guard (e.g., autouse fixture that raises on
  `socket.connect` to non-local addresses) would make "a test forgot to mock" fail
  loudly instead of hitting a real Canvas.
- **Effort:** ~30 min. **Risk:** none in principle; skip if it fights with pypandoc.

---

## 4. The parameter-bloat fix (flagship refactor)

### P1. Introduce a `SyncContext` dataclass in `sync.py`

This is the direct answer to "functions with 10+ parameters".

- **What:** a frozen-ish dataclass bundling the values threaded through every
  internal sync function:
  `course, repo_path, snippets_dir, manifest, manifest_path, course_id,
  force_uploads, force_overwrite, verbose, matcher, newer_on_canvas, errors,
  due_dates, assignment_group_ids, rubric_ids, synced_keys, unpublishable_items`.
  (Mutable accumulators — `newer_on_canvas`, `errors`, `synced_keys`,
  `unpublishable_items` — live in the context as plain lists/sets; that replaces
  today's `param: list | None = None` + local-fallback dance.)
- **Which functions:** `_sync_content_file` (16 params → ~3: `ctx, md_file`, plus
  nothing else), `_sync_quiz` (15), `_sync_module` (12, keeps `position` and
  `force_this` as explicit args since they vary per call), `_walk_assets` (11),
  `_apply_due_dates_only`, `_sync_question_banks`, `sync_syllabus`.
- **Keep public surface stable:** `run_sync`, `run_targeted_sync`, `run_prune`
  signatures unchanged (CLI and most tests call only these). They construct the
  context internally.
- **Test call sites to update (this is allowed):** `tests/test_due_dates.py` calls
  `_sync_content_file(...)` directly at ~4 places (lines ~303, 342, 374, 408);
  grep for `_sync_content_file`, `_resolve_date_overrides`, `_content_default_published`
  in `tests/` first — the latter two are pure functions and should stay standalone.
- **`publish.py` dependency:** it imports `_content_default_published`,
  `parse_frontmatter`, `parse_module_body` from sync — all three are pure and
  should NOT be absorbed into the context.
- **Sequencing:** do this **before** the duplication items D1–D4 below; the context
  makes those extractions much smaller.
- **Effort:** 2–4 h. **Risk:** medium (touches the heart of the tool) but purely
  mechanical; the 166 sync tests + 731 others are a strong net. Do it as one
  focused change with no other edits mixed in.

### P2. Same treatment for `imscc_import.py` converters (smaller)

- `convert_page/assignment/discussion/quiz` all take
  `(entry, imscc_dir, temp_manifest, output_dir, course_id, base_url[,
  due_dates_collector])`. An `ImportContext` dataclass with those six fields
  reduces each to `(ctx, entry)`. `run_import` builds it once.
- **Effort:** ~1 h. **Risk:** low; integration tests cover every content type.

---

## 5. Duplication consolidation (behavior-preserving, medium effort)

Ordered by value. Each is independent; run the suite between items.

### D1. The `print + errors.append` idiom (~25 occurrences)

- Pattern: `print(f"  {msg}")` followed by `if errors is not None: errors.append(msg)`
  appears throughout `sync.py`, and as *nested closures* `_report_error` in
  `convert.py:61` and `convert.py:163`.
- Add one module-level helper (e.g., in `convert.py` or a tiny new `_report.py`):
  `def warn(msg: str, errors: list[str] | None) -> None`. Replace call sites
  mechanically. After P1, sync.py call sites become `warn(msg, ctx.errors)`.
- **Effort:** ~1 h. **Risk:** very low — but keep the *exact* message strings and
  the two-space indent; several tests assert on printed output.

### D2. Duplicate `parse_frontmatter` implementations

- `sync.parse_frontmatter` (`sync.py:286`) and `quiz._parse_frontmatter`
  (`quiz.py:13`) are character-for-character the same algorithm.
- Move the function to `convert.py` (which has no intra-package imports, avoiding
  the cycle: `sync` imports `quiz`, so `quiz` must not import `sync`). Re-export
  from `sync` (`parse_frontmatter = ...`) because tests and `publish.py` import it
  from `github_to_canvas.sync`.
- **Effort:** ~30 min. **Risk:** very low.

### D3. Duplicate stub-creator closures

- `_sync_content_file`'s `stub_creator` (`sync.py:1228`) and `_sync_quiz`'s
  `_stub_creator` (`sync.py:1714`) differ only in the print suffix
  ("referenced but not yet synced" vs "referenced from quiz").
- Extract `def _make_stub_creator(course, manifest, manifest_path, note: str)`
  returning the closure. Keep both messages verbatim.
- **Effort:** ~20 min. **Risk:** very low.

### D4. Triplicated "effective mtime including snippets" logic

- The `max(file mtime, all referenced-snippet mtimes)` computation appears in
  `_sync_content_file` (`sync.py:1181-1186`), `_sync_module` (`sync.py:1540-1545`),
  and `_sync_quiz` (`sync.py:1666-1678`, over multiple files).
- Extract `def _effective_mtime(paths: Iterable[Path], snippets_dir) -> datetime`.
- Also: the malformed-frontmatter handler with the "values containing colons must
  be quoted" hint is duplicated verbatim (`sync.py:1192-1202` and `1551-1561`) —
  extract alongside.
- **Effort:** ~45 min. **Risk:** low.

### D5. Triplicated content-enumeration walk (assignments + discussions + quizzes)

- The "walk `assignments/**/*.md` and `discussions/**/*.md`, then
  `quizzes/*/<name>.md`; parse frontmatter; get title" loop is written out three
  times: `_apply_due_dates_only` (`sync.py:52-119` — the quiz half is itself a
  near-copy of the assignment/discussion half), `_check_due_dates_coverage`
  (`sync.py:135-159`), and `cli.list_titles` (`cli.py:446-479`).
- Extract a generator, e.g.
  `iter_gradeable_content(repo_path, matcher=None) -> Iterator[(local_key, path, title, ctype)]`.
  **Behavior note to preserve:** `list_titles` currently does *not* apply the
  ignore matcher — pass `matcher=None` there to keep that behavior (or ask the
  user whether list-titles *should* respect ignores; that'd be a behavior change).
- **Effort:** 1–2 h. **Risk:** low-medium; `test_due_dates.py` covers all three
  call sites including list-titles CLI output.

### D6. Triplicated Canvas type-dispatch in `canvas_api.py`

- `get_canvas_updated_at` (`:389-400`), `_get_object` (`:423-440`), and
  `unpublish_content` (`:476-487`) each hand-roll the same
  `canvas_type → course.get_*` dispatch.
- One `_GETTERS: dict[str, Callable]` map (pages keyed by `canvas_url`, others by
  `canvas_id`) serves all three. `unpublish_content` additionally needs the
  per-type edit kwarg shape (`wiki_page=` / `assignment=` / `quiz=` /
  `module=` / discussion `.update(published=False)`) — a second small map.
- **Effort:** ~1 h. **Risk:** low; prune tests cover delete/unpublish paths.

### D7. Triplicated date-rejection retry wrapper in `canvas_api.py`

- `create_or_update_assignment` (`:575`), `create_or_update_discussion` (`:616`),
  and `create_or_update_quiz` (`:666`) share the identical
  "catch BadRequest matching availability-dates, strip `_DATE_KEYS`, retry, tag
  `date_warning`" wrapper; only the strip step differs (discussion strips inside
  the nested `assignment` dict).
- Extract `_retry_without_dates(do_call, kwargs, strip_fn)`. Keep the warning
  message verbatim.
- **Effort:** ~45 min. **Risk:** low; date-rejection paths are covered in
  `test_sync.py`/`test_due_dates.py`.

### D8. Duplicated syllabus-body fetch (raw request)

- `sync._in_use_resources` (`sync.py:982-991`) and `orphans.find_orphans`
  (`orphans.py:162-171`) issue the same raw
  `GET courses/:id?include[]=syllabus_body` via `course._requester`.
- Add `canvas_api.get_syllabus_body(course) -> str` and use it in both. Keep the
  broad `except Exception: pass` behavior at the call sites (it is intentional:
  prune/find-orphans must degrade gracefully).
- **Effort:** ~30 min. **Risk:** very low.

### D9. `imscc_import.py`: many local `_text` closures re-implement `_el_text`

- `parse_assignment_settings`, `parse_topic_meta`, `parse_quiz_meta`, and
  `parse_module_meta` each define nested `_text`/`_child_text`/`_atext` helpers
  that duplicate `_el_text` (`imscc_import.py:1707`) plus `_coerce_xml_value`-style
  bool/int parsing (`_bool`, `_int` at `:782-795`).
- Hoist `_bool_text` / `_int_text` module-level helpers built on `_el_text`, and
  replace the closures. Keep `.//` vs direct-child `find` semantics per call site
  (**careful:** `parse_assignment_settings._text` uses `.//` descendant search,
  `parse_topic_meta._text` uses direct-child — do not unify those two behaviors).
- **Effort:** 1–2 h. **Risk:** medium-low; `test_imscc_convert.py` has per-parser
  unit tests.

### D10. `mv.py`: case-rename move logic duplicated; inner-rename logic duplicated

- `_do_move` (`mv.py:478-516`) repeats the "git-mv/rename via temp name" two-step
  once for the main move and once inside the inner-renames loop → extract
  `_move_one(src, dest, use_git, repo_root)`.
- `_add_quiz_qbank_inner_renames` (`:193`) and `_get_inner_renames` (`:519`)
  encode the same "quizzes → `<name>.md`, question_banks → `<name>.toml`" rule
  twice → extract the suffix rule (`{"quizzes": ".md", "question_banks": ".toml"}`).
- **Effort:** ~1 h. **Risk:** low; `test_mv.py` (791 lines) covers case-only
  renames, git and non-git paths, quiz/qbank folder renames.

### D11. `publish.py`: `discover_published` vs `_discover_type`; double file reads

- `discover_published` (`publish.py:242`) is used **only by tests** — product code
  uses `_discover_type`. Either reimplement it as a thin loop over
  `_discover_type` (keeps its tests meaningful) or leave with a comment noting
  it's a test/debug helper.
- `_is_published` parses a file, then every caller immediately re-reads and
  re-parses the same file for the title (`:257-262`, `:282-287`, `:311-317`).
  Fold into one `_published_title(md_file, repo) -> str | None` helper.
- **Effort:** ~45 min. **Risk:** very low.

---

## 6. Structural readability (higher effort; best done after P1)

These make the code dramatically easier for an LLM (or human) to navigate. All are
mechanical splits — no logic changes.

### S1. Split `_sync_content_file` by content type

- `sync.py:1151-1471`, CC 73. After the shared prefix (staleness check, frontmatter
  parse, snippet expansion, H1 check, link rewrite), the body is one branch per
  type. Extract `_upload_page(ctx, ...)`, `_upload_assignment(ctx, ...)`,
  `_upload_discussion(ctx, ...)`; within assignment, extract the rubric
  association/removal block (`:1375-1421`) as `_apply_rubric(ctx, ...)` and the
  annotatable-attachment validation (`:1328-1360`) as its own helper.
- **Effort:** 2–3 h. **Risk:** medium; mechanically preserve order of operations
  (manifest `record` timing matters — it flushes to disk per item).

### S2. Split `run_sync` into named phases

- `sync.py:708-953`, CC 47. The numbered comments (`# 0.`, `# 0.5`, `# 1.` …)
  already define the seams: `_phase_course_settings`, `_phase_assets`,
  `_phase_content`, `_phase_quizzes`, `_phase_front_page`, `_phase_modules`,
  `_phase_due_dates`. Keep the phase order and the three summary printers at the
  end exactly as-is.
- Also fold the triple-read of `course_settings.toml` (front_page pre-read at
  `:725-729`, `sync_course_settings` at `:732`, `load_due_dates` at `:738`) into
  **one** early `tomllib.load` whose dict is passed where needed. (Careful:
  `sync_course_settings` re-reads only when stale — preserve that laziness or
  accept one extra read per run; reading the file is cheap, so simplest is to load
  once for `front_page`+`due_dates` and leave `sync_course_settings` unchanged.)
- **Effort:** 2–3 h. **Risk:** medium-low.

### S3. Split `parse_imsmanifest` into per-resource-type classifiers

- `imscc_import.py:137-417`, CC 87 — the single worst function. The body is a chain
  of independent `if res_type ...: continue` blocks. Extract each into
  `_classify_syllabus(...)`, `_classify_question_bank(...)`, `_classify_page(...)`,
  etc., each returning `TempEntry | None`; the loop tries them in the **same
  order** (order matters: syllabus intended_use must win before the generic
  associatedcontent checks).
- **Effort:** 2 h. **Risk:** low-medium; `test_imscc_temp_manifest.py` +
  `test_imscc_import.py` cover every branch.

### S4. Split `_parse_qti_items` per question type

- `imscc_import.py:1135-1287`, CC 62. Extract `_extract_choice_answers`,
  `_extract_multiple_response_answers`, `_extract_fib_answers`,
  `_extract_pattern_answers`, `_extract_feedback` — the seams are already marked
  by the `elif question_type ==` blocks.
- Same for its output-side twin `_write_question_file` (`:1303`, CC 32).
- **Effort:** 2 h. **Risk:** low; heavy unit coverage in `test_imscc_convert.py`
  and `test_quiz.py` (round-trip).

### S5. `cli.py`: shared exception-ladder decorator

- `prune` (`:293-300`), `find_orphans_cmd` (`:337-344`), `update` (`:619-626`) and
  `create_tool_aliases` (`:420-423`) repeat the same
  `FileNotFoundError / TOMLDecodeError / (ValueError, KeyError) / Exception`
  ladder. Extract a decorator (e.g., `@handle_cli_errors`).
- Keep the (admittedly clunky) user-facing string `"KeyError or ValueError:" + str(e)`
  **verbatim** — rewording it is a user-visible change; note it as a candidate to
  clean up *with user approval*.
- Also `list_titles`'s three copy-pasted blocks collapse via D5.
- **Effort:** ~1 h. **Risk:** low.

---

## 7. Latent bugs found during review (report-only — ask the user before fixing)

Per CLAUDE.md, diagnose-before-fix applies. None of these break the current test
suite; several are already tracked in TODO.md (cross-referenced).

1. **`canvas_api.update_dates` can raise `NameError` instead of returning a
   warning** (`canvas_api.py:552-572`): if `course.get_assignment/quiz/...`
   itself raises a `BadRequest` whose text contains "due_at", the `except` block
   evaluates `getattr(obj, ...)` before `obj` is bound. One-line fix
   (`obj = None` before the `try`); extremely unlikely in practice.
2. **`publish._run_mkdocs`'s friendly "mkdocs is not installed" message is mostly
   unreachable** (`publish.py:799-810`): the command is
   `[sys.executable, "-m", "mkdocs", ...]`, so a missing mkdocs yields
   `CalledProcessError` ("No module named mkdocs", generic message), not
   `FileNotFoundError`. The tests pass because they patch `subprocess.run` to raise
   `FileNotFoundError` directly. Fix = detect the module before running, or catch
   the CalledProcessError and sniff stderr. User-visible message change → ask.
3. **`run_publish` never cleans up its staging dir** (`publish.py:822` —
   `tempfile.mkdtemp` with no cleanup). Leaks one directory per publish run.
   Wrap in `try/finally` with `shutil.rmtree`, or keep-and-print by design (it
   currently prints the path — maybe intentional for debugging; ask).
4. **Question-bank upload cannot work against real Canvas** — already thoroughly
   documented in `canvas_api.sync_question_bank`'s docstring and TODO.md ("Re-sync
   is not idempotent for question banks"). Nothing to do here except *not* build
   new code on top of `course.create_question_bank`.
5. **F811 shadowed tests** (see H2) — two test functions in
   `tests/test_imscc_import.py` are redefinitions; the first definitions never run.
6. **`cli.publish` catches only `ValueError`** (`cli.py:209-212`) while `mv`/
   `import` also catch generic `Exception` → an unexpected error in publish
   tracebacks. Consistent with "unknown errors traceback for debugging", so
   arguably fine — just noting the inconsistency.
7. **TODO.md bug entry may be stale:** "Changing module_order.toml doesn't
   re-arrange the modules in Canvas" — `run_sync` *does* now handle this
   (`sync.py:887-939`: `order_changed` → `_reorder_modules`). Verify against a
   real course, then delete the TODO entry if fixed.

---

## 8. Documentation drift (safe to fix anytime)

1. **CLAUDE.md repo-structure tree is stale:** lists `INTERNAL_DOCUMENTATION.md`
   (doesn't exist — superseded by `ARCHITECTURE.md`); omits `publish.py`, `mv.py`,
   `orphans.py`, `ignore.py`, `scripts/check_imscc_coverage.py`, and the test files
   `test_publish.py`, `test_mv.py`, `test_orphans.py`, `test_config.py`,
   `test_ignore.py`, `test_imscc_temp_manifest.py`, `test_imscc_link_rewrite.py`.
   Tech-stack section says "CLI: `click` or `argparse`" — it's click.
2. **TODO.md references `UPLOADER_CHANGES.md §3` and `§16`** (lines 77–78) — that
   file does not exist. Find where that content went (likely ARCHITECTURE.md) or
   drop the references.
3. **TESTING.md has no "how to run" line.** Add the invocation (`uv run pytest`,
   once E1 is fixed) near the top, since that's where people (and Claude) look.
4. After T1 lands, note the fast-suite expectation in TESTING.md.

---

## 9. Deliberately left alone (risk outweighs benefit)

Do **not** "improve" these without an explicit user request:

1. **`imscc_import._build_frontmatter`** (hand-rolled YAML emitter with a quoting
   heuristic, `:2456`). Swapping to `yaml.safe_dump` would change output formatting
   across every imported repo and break the many tests that assert exact
   frontmatter strings. It is ugly and it is fine.
2. **`_simplify_pandoc_attrs`** (`imscc_import.py:581`) — subtle fence-pairing
   logic, recently worked on (commit 8ff4183), well-tested. Leave it.
3. **Manifest flush-on-every-record** (`manifest.py:62-79`) — looks wasteful, is a
   deliberate crash-safety property (interrupted-sync resume depends on it;
   TESTING.md asserts it).
4. **Regex-based HTML rewriting** in `link_rewrite.py` / `convert.py` — "parse HTML
   with regex" is normally a smell, but the input is Pandoc-generated (predictable)
   HTML; swapping in an HTML parser is a rewrite with new failure modes and no
   user-visible gain.
5. **`canvas_api._set_module_item_published`'s raw `requests.put`** — works around
   a real canvasapi/Canvas bug (missing `module_id` on returned items; File-item
   500s). `tests/conftest.py`'s HTTP blocker targets exactly this call.
6. **mv.py case-only-rename path computation** (`_compute_case_rename_dest_rel`,
   `validate_move`'s dest_rel gymnastics) — convoluted but battle-tested against
   case-insensitive-filesystem edge cases; tidy only the dead bits (H3), don't
   restructure.
7. **`fill_in_blank`/`pattern_match` → `short_answer_question` mapping and
   `patterns[0]`-only upload** (`quiz.py:174-196`) — known, documented in README
   and TODO.md; behavior decision belongs to the user.
8. **Broad `except Exception: pass` in prune/find-orphans support paths**
   (`sync.py:596, 702, 990, 998`; `orphans.py:136, 158, 170`) — intentional
   degrade-gracefully behavior for optional data. Converting to logging would be
   fine but is cosmetic; don't convert to raises.
9. **`_sync_quiz`'s `config_course_id: int = 0` default** — odd, but every real
   caller passes it; goes away naturally with P1.

---

## 10. Suggested execution order

For a future session doing this work top-down:

1. ~~E1 (venv)~~ — already fixed; run `uv run pytest -q` to confirm baseline 831 passed.
2. H1–H6 (one pass, ~1 h total).
3. ~~T1 (test speed)~~ — done 2026-07-02, 226 s → ~39 s.
4. T2 (pathspec) — small, fenced.
5. P1 (`SyncContext`) — the flagship; do alone, full suite after.
6. D1–D4 (small dedups, now easier post-P1).
7. D5–D11 in any order.
8. S1–S5 as appetite allows.
9. Section 8 doc fixes whenever convenient.
10. Section 7 items: raise with the user, don't just fix.
