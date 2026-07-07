# DESIGN: Course flags — conditional Markdown content (`#if` / `#elif` / `#else` / `#endif`)

**Status: implemented (v1, 2026-07-06).** §1–§11 and §13 are shipped; see
README ("Course flags — conditional content") for user docs and
ARCHITECTURE.md ("Course flags (conditional content)") for internals. §12's
future-work items remain unimplemented and are tracked in TODO.md. All open
questions were settled with the user (2026-07-06); do not re-litigate the
settled decisions without asking them.

## 1. Motivation

The same course repo is often taught in multiple variants (in-person vs. online,
different quarters, etc.). Today that means duplicating files or hand-editing
between offerings. This feature adds C-preprocessor-style conditional regions to
Markdown content, driven by boolean flags defined once per course, so a single
repo can produce different Canvas content per offering by flipping flags in
`course_settings.toml`.

Requirements set by the user:

- Flags are defined in `course_settings/course_settings.toml`; boolean values
  only (for v1).
- The Markdown inside conditional regions must remain fully editable in VSCode —
  normal syntax highlighting, preview rendering, heading sizes, etc. (This rules
  out putting conditional content inside fenced blocks, which VSCode renders as
  monospace code.)
- Flag values are cached in `.canvas-manifest.toml` so that changing a flag
  re-syncs **only** the files whose output could change — not the whole course.
- Longer term (NOT v1 — see §12.3), the same value-caching idea should extend to
  the `due_dates` array so editing one due date doesn't trigger API calls for
  every dated item.

## 2. Configuration: `[course_flags]` in course_settings.toml

```toml
# course_settings/course_settings.toml
[course_flags]
in_person_class = true
hybrid = false
```

Rules:

- A plain TOML table (not an array of tables) — TOML itself then rejects
  duplicate flag names.
- Flag names are **case-sensitive identifiers**: `[A-Za-z_][A-Za-z0-9_]*`.
  A name that doesn't match is a fatal config error.
- Values must be TOML booleans. Any other type (string, int, …) is a **fatal
  config error** that aborts the run with a clear message (this is a config
  problem, not a per-file problem). Use the existing `die()` convention.
- The table is optional. Absent table == no flags defined (any `#if` in content
  then hits the undefined-flag error, §7).
- Loader: add `load_course_flags(repo_path, settings=None) -> dict[str, bool]`
  in `sync.py`, mirroring `load_due_dates()` (same optional pre-loaded
  `settings` dict so the file is read once). `publish.py` calls the same loader.

## 3. Directive syntax

Directives are HTML comments, each **alone on its own line** (leading/trailing
whitespace allowed, nothing else on the line):

```markdown
<!-- #if in_person_class -->
Bring your laptop to Room 302.

We will pair up during the first hour.
<!-- #elif hybrid -->
Attend in person **or** on Zoom — your choice this week.
<!-- #else -->
Join the Zoom link posted in the module.
<!-- #endif -->
```

Why HTML comments: the enclosed content is ordinary top-level Markdown, so
VSCode highlights and previews it at full fidelity; the markers themselves show
as grey comments in the editor and are invisible in Markdown preview. Note the
accepted trade-off: in GitHub/VSCode *preview*, all branches render (nothing
evaluates flags there).

### Grammar

```text
directive      := "<!--" WS? "#" keyword ( WS argument )? WS? "-->"
keyword        := "if" | "elif" | "else" | "endif"
argument       := [ "not" WS ] flag_name          ; only for if/elif
flag_name      := [A-Za-z_][A-Za-z0-9_]*
```

- `#if` / `#elif` take exactly one argument: a flag name, optionally preceded by
  the word `not` (whitespace-separated). No `and`/`or`/parentheses in v1
  (future work, §12.4). No `!` operator — the word `not` only.
- `#else` / `#endif` take **no** argument; an argument present is an error.
- `#if flag` is true when the flag is defined **and** its value is `true`.
  `#if not flag` is true when the flag is defined and `false`. A flag that is
  not defined in `[course_flags]` is an error in both forms (§7) — there is no
  "undefined means false".
- Nesting is supported (plain stack); `#elif`/`#else`/`#endif` bind to the
  innermost open `#if`. Any number of `#elif`s; at most one `#else`, last.
- **Reserved-misspelling errors:** a comment line whose content matches
  `#ifdef`, `#ifndef`, `#elseif`, `#elsif`, or `#fi` as its first word is a hard
  error with a "did you mean …" hint. Every **other** `<!-- ... -->` comment —
  including `<!-- #region -->` / `<!-- #endregion -->` (VSCode folding markers)
  and the tool's own `<!-- published:false -->` module convention — passes
  through completely untouched. Only the four keywords + five misspellings are
  ever interpreted.

### Removal semantics

- Directive lines are removed entirely (no blank line left behind).
- Lines in false branches are removed entirely.
- Consequence to document for users: because no blank line is left behind,
  adjacent text can merge into one paragraph / one tight list. That is a
  feature (conditional list items stay a single tight list):

  ```markdown
  - Always shown
  <!-- #if in_person_class -->
  - In-person only item
  <!-- #endif -->
  - Also always shown
  ```

  Users who want separate paragraphs should include blank lines *inside* the
  branch.

### Fence-awareness

Directives inside fenced code blocks are literal example text, consistent with
every other parser in this tool. Implementation: process
`split_fenced_segments()` output — plain segments are scanned line-by-line
against the directive regex; fenced segments are never scanned, and are kept or
dropped **wholesale** according to the conditional state in force when the
fence opened. (This also covers ```` ```{=comment} ```` raw blocks: a directive
inside one is literal; the block as a whole is subject to the enclosing
conditional; `strip_raw_nonhtml_blocks` runs later as today.)

## 4. Where conditionals apply (scope)

**Body content only.** Frontmatter is YAML; HTML comments have no meaning
there, and the user confirmed no frontmatter use case. Conditional frontmatter
values are explicitly out of scope (not even future work unless requested).

The pass applies to the body of every content type that goes through the
Markdown pipeline:

- pages, assignments, discussions, announcements
- quizzes: the quiz-level `.md` (description **and** the ordered question
  list — so a whole question can be conditional) and individual question files
- question banks' question files
- module `.md` files (item lists — so a module item can be conditional)
- `course_settings/syllabus.md`
- snippet files (`snippets/…`), both block and `$inline.md$` forms
- everything `publish` stages (same files, same flags)

Excluding a quiz question or module item via a flag behaves exactly as if the
user had deleted that text: existing sync logic then deletes the question /
module item from Canvas. **Confirmed desired behavior** — flipping a flag off
genuinely removes the in-person-only question from the online section's quiz.

If a file's entire body is excluded, the resource still exists on Canvas with
an empty body. Whole-resource exclusion is future work (§12.1).

## 5. Pipeline placement

Every conversion path in this codebase follows the same shape:

```text
parse_frontmatter → expand_frontmatter_snippets → preprocess_snippets
    → (strip_raw_nonhtml_blocks where structural parsing happens) → Pandoc / parsers
```

The conditional pass slots in **immediately after frontmatter parsing, before
snippet expansion**, plus **on each snippet's content at insertion time**:

1. New module `src/github_to_canvas/conditionals.py`:

   ```python
   def apply_conditionals(
       text: str,
       flags: dict[str, bool],
       source_desc: str,                # repo-relative path, for error messages
       errors: list[str] | None = None,
   ) -> str | None:
       """Evaluate #if/#elif/#else/#endif directives. Returns the filtered
       text, or None if any directive error occurred (caller must skip the
       file). Fence-aware; non-directive comments untouched."""

   def find_referenced_flags(text: str) -> set[str]:
       """Lexical probe: every flag name appearing in any directive, in any
       branch, taken or not. Fence-aware. Never errors (passive, like
       find_referenced_snippets). Malformed directives contribute nothing."""
   ```

2. Call `apply_conditionals(body, …)` right after
   `parse_frontmatter`/`expand_frontmatter_snippets` at each body call site in
   `sync.py`, `quiz.py`, and `publish.py` (grep for `preprocess_snippets(` and
   the module/quiz body parse sites — every one of them gets the call; that is
   the complete list of insertion points).

3. `preprocess_snippets()` gains an optional `flags: dict[str, bool] | None`
   parameter; when set, `_load_snippet` applies `apply_conditionals` to the
   snippet's content before insertion (both block and inline forms; a snippet
   whose directives error contributes an error and the ref is left unexpanded,
   matching existing snippet-error behavior). Frontmatter snippets
   (`PASTE_SNIPPET_INTO_FRONTMATTER`) are YAML → no conditional processing.

Consequences of this placement, all intentional:

- Directives must be **balanced within each file** and **within each snippet
  file** independently. An `#if` opened in a page and "closed" inside an
  included snippet is an unclosed-`#if` error in the page. This keeps every
  file independently checkable.
- A snippet reference inside a false branch is removed before snippet expansion
  runs, so a broken snippet path in a dormant branch reports nothing.
- The pass runs before `strip_raw_nonhtml_blocks` and before all structural
  line parsers (module items, quiz question lists, question sections), which is
  what makes conditional module items / quiz questions work with no changes to
  those parsers.

## 6. Ordering / evaluation notes

- `#elif` arms are evaluated in order; first true arm wins; `#else` when none.
- Directives in *not-taken* branches are still parsed for balance (the stack
  must track them to find the matching `#endif`) but their flags are still
  looked up only lexically — an undefined flag in **any** directive of the
  file, taken branch or not, is an error (see next section). Rationale: the
  whole point of hard errors is catching typos before the flag flip that would
  expose them.

## 7. Error handling

All of the following are **per-file hard errors**: report via the existing
`warn(msg, errors)` / `ERROR:` convention with the repo-relative path, **skip
the file** (no upload / no staging), continue the run, and let the end-of-run
error summary fail the run as it does today. Message text should name the file,
the line's content, and the problem.

| Condition | Example |
| --- | --- |
| Undefined flag in any directive (taken or not) | `#if in_persn_class` (typo) |
| Flag deleted from TOML while still referenced | (same error, triggered on the re-sync that the staleness check forces, §9) |
| Unknown-but-reserved keyword | `#ifdef x` → error + "did you mean #if" |
| Missing argument | `<!-- #if -->` |
| Unexpected argument | `<!-- #endif foo -->` |
| Malformed argument | `#if not` (no name), `#if 2cool` (bad identifier), `#if a or b` (v1 has no expressions) |
| `#elif`/`#else`/`#endif` with no open `#if` | stray `<!-- #endif -->` |
| `#elif` or second `#else` after `#else` | |
| Unclosed `#if` at end of file / end of snippet | |

Fatal (whole-run, via `die()`): non-boolean flag value or invalid flag name in
`[course_flags]` (§2).

## 8. Unused-flag warning

After the content pass, warn (once per flag, not per file) about flags defined
in `[course_flags]` but referenced by **no** content file:

```text
WARNING: course flag 'hybrid' is defined in course_settings.toml but not used by any content file
```

Implementation: mirror `_check_due_dates_coverage()` — a scan pass that reads
each content file's body (plus snippet files) and unions
`find_referenced_flags()`. A flag referenced only inside a snippet counts as
used. Runs in both `update` and `publish`. Warning only — never an error
(defining a flag before writing the content that uses it is legitimate).

## 9. Manifest caching and selective re-sync

### What's recorded

Each per-file manifest entry gains an optional sub-table recording the flag
**values in effect at that file's last successful sync**, covering every flag
the file references directly *or via any snippet it includes*:

```toml
["assignments/lab1.md"]
canvas_id = 123
canvas_type = "assignment"
last_synced = "2026-07-06T20:15:00+00:00"

["assignments/lab1.md".flags_used]
in_person_class = true
```

- Written via the existing `record(..., extra=...)` mechanism; omitted entirely
  when the file references no flags (keeps the manifest tidy).
- Values, not just names, and **per-file, not a global snapshot**. Rationale: a
  global "flags as of last run" key would mark a file clean even if that file
  errored/was skipped during the run that flipped the flag. Per-file values
  make each entry self-describing. (No `[_course_flags]` global section is
  needed; the underscore-prefixed reserved-key convention remains available —
  content keys are relative paths, so no collision is possible.)
- `flags_used` is computed at sync time as
  `find_referenced_flags(body) ∪ find_referenced_flags(each referenced snippet's content)`
  — reuse `find_referenced_snippets()` to enumerate the snippets, exactly as
  the snippet-mtime staleness path already does.

### Staleness check

Extend `manifest.needs_sync()` with an optional `current_flags:
dict[str, bool] | None = None`. After the existing mtime checks, if the entry
has a `flags_used` table, the file is stale when any recorded flag is **missing
from** `current_flags` or **differs in value**. Notes:

- A flag *newly added* to a file doesn't need this check — editing the file
  bumped its mtime.
- A flag *deleted* from the TOML makes referencing files stale → they re-sync →
  they hit the undefined-flag hard error (§7). That's the desired loud failure.
- Snippet-borne flags: adding a directive to a snippet bumps the snippet's
  mtime, which the existing `extra_mtime_paths` check already catches; the
  re-sync then refreshes `flags_used`. No extra machinery.
- In verbose mode, when the flag check (not mtime) is what triggered the
  re-sync, print why (with the standard two-space indent):
  `re-syncing: flag 'in_person_class' changed true → false`.
- `--force-uploads` behaves as today (forces everything, flags included).

### Quiz composite staleness

`_quiz_needs_sync` folds question-file mtimes into the quiz decision; the flag
check must be folded in the same way: the quiz re-syncs if the flag check
trips for the quiz `.md` **or any of its question files**. `flags_used` on the
quiz's manifest entry should therefore union the flags referenced by the quiz
file and all its question files (they sync as one unit). Same idea for question
banks *when* their staleness gap is fixed (see TODO.md; don't fix it here).

## 10. Subcommand matrix (per the CLAUDE.md keep-in-sync rule)

| Subcommand | Impact |
| --- | --- |
| `update` | Full feature: conditional pass, hard errors, unused-flag warning, `flags_used` caching, flag-aware `needs_sync`. |
| `publish` | Same conditional pass with the **same flag values** (user decision — no per-target overrides in v1, see §12.2). Same hard errors (file skipped from the site) and unused-flag warning. Publish has no manifest staleness, so no caching work. |
| `import` | No change — the importer never generates directives. |
| `mv` | No change needed — `flags_used` lives inside the per-file entry, which `mv` re-keys wholesale. Verify with a test. |
| `find-orphans` / `prune` | No change — orphan detection is manifest/file-presence based, not body-based. |

## 11. Testing plan (see TESTING.md conventions)

New `tests/test_conditionals.py` (unit, no Pandoc needed for most):

- Evaluation matrix: true/false × `#if`/`#if not`; `#elif` chain order;
  `#else`; nesting (2+ deep, inner/outer flag combinations).
- Directive lines removed with no blank line left; tight-list example survives.
- Fence-awareness: directive inside ``` block is literal; a fenced block inside
  a false branch is dropped wholesale; directive-lookalike inside
  `{=comment}` block untouched.
- Pass-through: `<!-- #region -->`, `<!-- published:false -->`, ordinary
  comments, and a directive-lookalike *not* alone on its line.
- Every error row in §7's table, asserting the file is skipped and the message
  names file + problem (parametrized test).
- `find_referenced_flags`: all branches counted, fenced ignored, malformed
  ignored.

Fixture additions (`tests/fixtures/`): add `[course_flags]` to the fixture
`course_settings.toml` and a small number of flagged files — at minimum one
page, one quiz question that is conditionally included, one conditional module
item, and one snippet containing a conditional. **Watch out:** several existing
integration tests assert on counts/lists of synced items; adding fixture files
will require touching those assertions — budget for it.

Integration (`test_sync.py` style, mocked canvasapi):

- Flag flip re-syncs exactly the referencing files (assert others skipped);
  verbose message present.
- `flags_used` written correctly, including snippet-contributed flags; absent
  for flag-free files.
- Flag flip removes a conditional quiz question / module item from Canvas
  (existing deletion paths fire).
- Undefined flag: file skipped, error in summary, other files still sync.
- Deleted flag: previously-synced referencing file goes stale and errors.
- Unused-flag warning fires once; not for snippet-only-used flags.
- `mv` a file with `flags_used` → entry moves intact.
- `publish`: flagged content included/excluded to match flags.

## 12. Future work (spec'd, NOT v1)

### 12.1 Whole-resource exclusion

Excluding an entire page/assignment/quiz from the course when a flag is off
(not just emptying its body). Sketch: frontmatter key `only_if:
in_person_class` (or `only_if: not in_person_class`). Needs semantics for the
flag-flip case: the resource exists on Canvas and must be deleted or
unpublished — likely route through the existing prune machinery (treat as
manifest-orphan when its condition is false) rather than ad-hoc deletion, and
decide unpublish-vs-delete deliberately. Also needs: modules that reference an
excluded file (skip the item with a note?), due_dates coverage warnings
(suppress for excluded items), and `publish` reachability. The v1 body-only
design deliberately keeps frontmatter untouched so this can be added without
changing any v1 syntax.

### 12.2 CLI flag overrides

`--flag name=true/false` (repeatable) on `update`/`publish` to preview the
other variant without editing the TOML. Interaction to decide then: overridden
runs should probably **not** write `flags_used` values from the override into
the manifest (or should force-resync affected files on the next normal run).
Deferred per user decision; publish per-target flag overrides live here too.

### 12.3 due_dates value caching (the same idea, applied to dates)

Spec'd and **implemented** (2026-07-07) — see **DESIGN-settings-caching.md**,
which supersedes the sketch that used to live here. It covers per-item
resolved-dates caching for the dates-only pass *and* section-level change
detection for the rest of `course_settings.toml` (metadata, grading
standards, dashboard image, etc.).

### 12.4 Richer conditions and values

`and`/`or`/parentheses in `#if`; non-boolean flag values (strings/numbers) with
`#if flag == "value"`; possibly `$flag$`-style value substitution in text.
Requires a real expression parser — keep out until a concrete need appears.

### 12.5 `list-flags` report

A small report subcommand/flag: each flag, its value, and the files referencing
it (the data is already computed by the unused-flag scan). Complements §8.

## 13. Documentation updates at implementation time

Per CLAUDE.md: document the shipped feature in **ARCHITECTURE.md** (pipeline
placement, manifest schema, error table) and **README.md** (user-facing syntax
guide, incl. the paragraph-merge note and the GitHub-preview caveat, plus the
`[course_flags]` config reference); remove the pointer entry from **TODO.md**;
keep §12's future-work items represented in TODO.md or leave them here with a
TODO.md pointer.

README.md's "adding comments to Markdown files" tip (near the `{=comment}`
raw-attribute-block docs) already contains a *planned-feature* note steering
users who toggle content between offerings toward this feature — when
implementing, rewrite that note to describe the shipped feature and point at
the README's own syntax guide instead of this design doc.
