# DESIGN: course_settings.toml value caching — selective re-sync for due_dates and settings sections

**Status: implemented (2026-07-07).** See ARCHITECTURE.md ("Section-level
change detection" and "due_dates resolved-value caching" under the
course-settings upload detail) for internals and README.md ("`course_settings.toml`"
and "Centralized due dates") for user docs. Tests: `tests/test_settings_sections.py`
plus the resolved-dates cases in `tests/test_due_dates.py`. This is the
follow-on to DESIGN-course-flags.md §9 (per-file flag-value caching) and the
full spec of the idea sketched there in §12.3. Open questions were settled
with the user (2026-07-07); do not re-litigate the settled decisions without
asking them. Settled decisions:

- **Scope:** both parts below — per-item `due_dates` resolved-value caching
  *and* section-level change detection for the rest of course_settings.toml.
- **Dates pass trigger:** the dates pass runs on **every** `update` run (no
  settings-mtime gate); the per-item cache is what makes unchanged items free.
- **Removed due_dates entry:** keep today's do-nothing semantics, but print an
  explicit one-time notice; drop the cached values.
- **Spec home:** this doc; DESIGN-course-flags.md §12.3 points here.

## 1. Motivation

The course-flags work made *content* re-syncs selective: flipping a flag
re-syncs only files whose output could change. But `course_settings.toml`
staleness is still a single mtime-based boolean (`settings_synced`), and that
boolean fans out to everything the settings file controls:

- `_phase_due_dates` → `_apply_due_dates_only()` makes an `update_dates` call
  (two API round-trips: `get_*` + `edit`) for **every** gradeable item with a
  matching `due_dates` entry.
- `sync_course_settings()` re-runs **every** section: course metadata, grading
  standards, assignment groups, late policy, default post policy, tab
  configuration — and re-uploads the **dashboard image**.
- `_phase_front_page` re-sets the front page.

Concrete triggers that today cause the full fan-out despite changing nothing
date- or settings-related:

- Flipping a course flag (the flags feature's own config lives in this file).
- `gg mv` of the front page or dashboard image (mv rewrites the `front_page` /
  `dashboard_image` path values in course_settings.toml).
- Editing one due date out of dozens (every dated item still gets API calls).
- Reordering tabs, tweaking a policy, fixing a typo in the course name.

There is also a **latent robustness hole**: `sync_course_settings()` records
the settings manifest entry *before* the due-dates phase runs. If a run is
interrupted during the dates pass, the next run sees settings as up-to-date
and silently skips the remaining date updates. Per-item value caching fixes
this structurally — see §3.

And a **latent staleness gap**: editing the dashboard image *file* without
touching course_settings.toml never re-uploads it (only the settings mtime is
checked). §4 fixes this in passing.

## 2. Design principles (carried over from the flags work)

- **Cache values, not timestamps, per consumer.** Each manifest entry records
  the settings-derived values in effect at *its* last successful sync. A
  global "settings as of last run" snapshot would mark an item clean even if
  that item errored or the run died mid-pass. (Same rationale as
  DESIGN-course-flags.md §9.)
- **Readable manifest.** Per-item resolved dates are stored as a readable
  table (the manifest is a debugging aid). Section-level snapshots are hashes
  — the sections (grading standards, assignment groups) are too large to
  duplicate readably, and "changed?" is the only question asked of them.
- **Record after success, never before.** A failed API call must leave the
  cache un-updated so the next run retries exactly the failed work.
- **Never widen body re-syncs.** None of this makes any file's *content*
  re-upload; it only gates the dates-only pass and the settings sections.

## 3. Part A — due_dates: per-item resolved-value caching

### 3.1 Canonical resolution

At comparison time, compute each matched item's **resolved dates**: for each
of `due_at`, `lock_at`, `unlock_at`, one of:

| Resolved value | Meaning | Produced from |
| --- | --- | --- |
| a date string, verbatim | "set Canvas to this" | a concrete value in the entry |
| `"NONE"` | "clear the date on Canvas" | the `NONE` sentinel |
| `"KEEP"` | "leave Canvas alone" | `KEEP`; empty value (today's warn-and-keep case); `CREATE_NONE_THEN_KEEP` when the item already exists on Canvas |

Notes:

- Sentinels stay **symbolic** (per §12.3): `KEEP` never resolves to a concrete
  date, because its instruction is *leave alone*, not any particular value.
- `CREATE_NONE_THEN_KEEP` resolves to `KEEP` for an existing item. Its
  clear-on-create meaning only fires in the full-sync creation path, which is
  not gated by this cache. Consequence: editing an entry between `KEEP` and
  `CREATE_NONE_THEN_KEEP` on an already-created item is correctly a no-op.
- Date strings are compared **verbatim** (no ISO normalization). Rewriting the
  same instant in a different format re-sends once — harmless, and keeps the
  implementation trivial.
- Implementation: a small pure function alongside `_resolve_date_overrides()`
  (which stays as-is for building the actual API payload), e.g.
  `resolve_dates_symbolic(override) -> dict[str, str]` returning all three
  keys always (self-describing entries).

### 3.2 Manifest schema

A new optional sub-table on each gradeable item's entry, written via the
existing `record(..., extra=...)` mechanism:

```toml
["assignments/lab1.md"]
canvas_id = 123
canvas_type = "assignment"
last_synced = "2026-07-07T20:15:00+00:00"

["assignments/lab1.md".resolved_dates]
due_at = "2026-01-15T23:59:00-08:00"
lock_at = "KEEP"
unlock_at = "NONE"
```

- Present **only** when the item had a matching `due_dates` entry at its last
  successful date application; all three keys always present when the table
  is. Absent table == "no override was in effect" (keeps flag-free… er,
  date-free entries tidy, same convention as `flags_used`).
- **`record()` rebuilds entries wholesale**, so every call site that records a
  gradeable item must thread `resolved_dates` through `extra=` or the cache is
  silently dropped (not incorrect — just causes one wasted re-send next run —
  but sloppy). Call sites: `_upload_assignment`, `_upload_discussion`,
  `_sync_quiz`, and the dates-only pass. `flags_used` and `resolved_dates`
  can coexist in one `extra` dict.

### 3.3 The dates pass (`_apply_due_dates_only`), reworked

Runs on **every** `update` run — `_phase_due_dates` loses its
`settings_synced` parameter. Per item from `iter_gradeable_content()`:

1. `local_key in ctx.synced_keys` → skip (the full-sync path already applied
   and recorded dates this run, §3.4).
2. No manifest entry / no `canvas_id` → skip (never uploaded; unchanged from
   today).
3. Find the override by title/type as today. **If no override matches** but
   the manifest entry *has* a `resolved_dates` table: print
   `  NOTICE: due_dates entry for "<title>" (<local_key>) was removed — leaving Canvas dates as-is`
   and delete the `resolved_dates` sub-table (flush via manifest). Not added
   to `errors` — informational, and one-time by construction (cache gone →
   silent next run). No override and no cache → skip silently (today's
   behavior).
4. Compute the symbolic resolution (§3.1). Equal to the cached table → skip;
   in verbose mode print `  Skipping (dates unchanged): <local_key>`.
5. Changed, or no cached table, or `--force-uploads`: build the API payload as
   today (`KEEP` fields omitted, `NONE` → `""`, concrete → value).
   - Payload non-empty → `capi.update_dates()` as today. On success, record
     the entry with the new `resolved_dates` (preserving
     `canvas_id`/`canvas_type`/etc. and any `flags_used` — see §3.2 caveat).
   - Payload **empty** (everything resolved `KEEP`, e.g. the entry changed
     from a concrete date to `KEEP`) → **no API call**; just record the new
     `resolved_dates`. `KEEP` means leave alone, so there is nothing to send —
     but the cache must move forward or step 5 re-triggers forever.
6. On `date_warning` (Canvas rejected the dates): warn into `errors` exactly
   as today and **do not record** `resolved_dates`. The run fails via the
   error summary, and every subsequent run retries + re-warns until the user
   fixes the window in Canvas or the TOML. Loud-until-fixed is intentional
   and matches the recent partial-failure retry semantics.

Keep the `Applying due_dates...` phase header. The per-item
`  Updating dates: <local_key>` line now only appears for items actually
sent — which is the new user-visible signal that the pass got cheap.

### 3.4 Full-sync path recording

`_upload_assignment` (sync.py ~1488), `_upload_discussion` (~1545), and
`_sync_quiz` (~2051) already fold the override into their create/update call.
Each must now also compute the symbolic resolution and pass it via `extra`:

- Override matched + no `date_warning` → record `resolved_dates`.
- Override matched + `date_warning` → record **without** `resolved_dates`
  (entry is in `synced_keys` so this run's dates pass skips it; next run's
  pass sees the missing cache and retries — same loud-until-fixed loop as
  §3.3.6).
- No override → record without the table (which also naturally erases a stale
  table when the entry was removed *and* the file body re-synced in the same
  run; the §3.3.3 notice then never fires for it — fine).

For `CREATE_NONE_THEN_KEEP` on a **newly created** item, the full-sync path
sends `""` (clears on create) but records the *symbolic* resolution, which for
the now-existing item is `KEEP` — consistent with how the dates pass will
resolve it on every later run.

### 3.5 What this deliberately does not do

- No body re-sync is ever triggered by date changes (`needs_sync()` is
  untouched by this part).
- Frontmatter dates are out of scope: the dates-only pass has never applied
  them (it skips items with no override), so they are not cached. An item
  whose dates come only from frontmatter continues to update via body
  re-syncs only.
- Entry matching is still by title/type at run time. A renamed content file
  gets a full re-sync anyway (mtime), which refreshes the cache under the
  re-keyed manifest entry.

## 4. Part B — section-level change detection in `sync_course_settings`

### 4.1 Sections and their actions

`course_settings.toml` is split into **sections**, each with a stable name,
a value, and an action:

| Section name | TOML value | Action when changed |
| --- | --- | --- |
| `metadata` | the whole settings dict **minus** every other section's keys (unknown top-level keys therefore count as metadata — conservative, and correct because `update_course_metadata` reads the whole dict) | `capi.update_course_metadata(...)` |
| `grading_standards` | `grading_standards` | `capi.sync_grading_standards(...)`, **and** forces the `metadata` action too (the resulting `grading_standard_id` is applied via the metadata call) |
| `dashboard_image` | `dashboard_image` (a path string) | upload the image |
| `assignment_groups` | `assignment_groups` | `capi.sync_assignment_groups(...)` + the existing deferred-drop-rules dance |
| `late_policy` | `late_policy` | `capi.update_late_policy(...)` |
| `default_post_policy` | `default_post_policy` | `capi.update_post_policy(...)` |
| `tab_configuration` | `tab_configuration` | `capi.sync_tab_configuration(...)` (incl. the existing nested-key warning) |
| `front_page` | `front_page` | gate for `_phase_front_page` (see §4.4) |
| — | `due_dates` | **no section**: Part A runs every run regardless |
| — | `course_flags` | **no section**: already cached per-file (`flags_used`) |

Ordering within `sync_course_settings` is unchanged (grading standards before
metadata; the § labels in the code comments stay) — each block just gains an
"is my section stale?" gate.

### 4.2 Manifest schema

A sub-table on the existing settings entry:

```toml
["course_settings/course_settings.toml"]
canvas_id = 0
canvas_type = "course_settings"
last_synced = "2026-07-07T20:15:00+00:00"

["course_settings/course_settings.toml".section_hashes]
metadata = "9f2b4c1a0d3e5f67"
grading_standards = "e3b0c44298fc1c14"
assignment_groups = "..."
late_policy = "..."
default_post_policy = "..."
tab_configuration = "..."
dashboard_image = "..."
front_page = "..."
```

Hash = first 16 hex chars of SHA-256 over the canonical JSON of the section's
value: `json.dumps(value, sort_keys=True, separators=(",", ":"))`, with a
fixed marker (e.g. `"<absent>"`) for a section not present in the file at all.
16 chars is plenty for change detection and keeps the manifest readable-ish.
TOML values round-trip to JSON cleanly here (tables/arrays/strings/bools/ints;
no datetime values are used in these sections — `due_dates` values are
strings — but note it in the implementation: `json.dumps(..., default=str)`
as a safety net).

### 4.3 Staleness algorithm

```text
if settings file missing → as today (skip; still fetch rubric ids)
fast path: mtime fresh AND dashboard-image file mtime fresh AND not force_uploads
    → skip all sections (as today), but Part A still runs later
else:
    parse settings (reuse run_sync's already-parsed _settings_dict —
        pass it in, matching the load_due_dates(settings=...) pattern,
        so the file is read once per run)
    compute current hash per section
    for each section, in today's order:
        stale = force_uploads
                or entry has no section_hashes        (migration, §4.6)
                or recorded hash != current hash
                or (dashboard_image only) image file mtime > last_synced
        if stale: run the action; on success, update that section's hash
                  in an accumulating dict
        else (verbose): print "  Skipping section (unchanged): <name>"
    recording (§4.5)
```

The dashboard-image mtime check also joins the **fast path** (it's cheap:
`_settings_dict` is already parsed in `run_sync`, so the image path is known
without extra I/O). This fixes the latent gap where editing the image file
alone never re-uploaded it. Side note: `gg mv` of the image rewrites the path
string → hash changes → one re-upload of an unchanged image. Accepted.

### 4.4 Consumers of `settings_synced`

`sync_course_settings` stops returning a boolean and returns the set of
section names actually (re)applied this run, e.g. `changed_sections:
set[str]` (empty on the fast path):

- `_phase_front_page` gate becomes:
  `front_page and ("front_page" in changed_sections or front_page_path in synced_keys)`.
  Semantics otherwise unchanged (including the unpublished/missing-entry
  warnings).
- `_phase_due_dates` no longer takes the flag at all (Part A).
- Nothing else consumes it (verified by grep at spec time — re-verify at
  implementation time).

### 4.5 Failure handling and recording (the crash-hole fix)

Today a failed section prints a WARNING and is **never retried** until the
file's mtime changes again. New behavior, consistent with the recent
partial-failure retry semantics:

- After each section succeeds, its new hash goes into the accumulating dict.
- If **all** attempted sections succeeded: `record(settings_key, ...,
  extra={"section_hashes": all_hashes}, mark_synced=True)` — one entry write,
  fresh `last_synced`, fast path restored.
- If **any** section failed: record with `mark_synced=False` and the
  accumulated hashes (succeeded sections' new hashes + failed sections' *old*
  hashes... which, since `record()` rebuilds wholesale, means passing the
  merged dict: start from the entry's previous `section_hashes` and overlay
  successes). No `last_synced` → the mtime fast path stays off → next run
  parses again, hash comparison skips the succeeded sections and retries
  exactly the failed ones. Also append the section failure to `errors` (today
  it's print-only) so the run fails loudly — **behavior change, intentional**:
  a silently half-applied settings file is exactly what this design exists to
  prevent.
- An interrupted run behaves identically to a failed section: succeeded
  sections' hashes were not yet flushed (single record at the end), so they
  re-run — idempotent and cheap, and strictly better than today's
  skip-everything outcome. (If implementation prefers per-section
  `record(...)` flushes instead of one end-of-phase write, that's also
  acceptable — it trades manifest-write churn for even less redundant work
  after an interrupt; decide at implementation time, test either way.)

### 4.6 Migration

An existing manifest has no `section_hashes` (and no `resolved_dates`).
First run with the new version:

- Settings entry mtime-fresh → fast path → sections skipped, same as today.
  Hashes get written the next time the file actually changes. (Do **not**
  force a one-time full section run just to seed hashes.)
- Dates pass: every item's cache is missing → resolution computed → payload
  sent once for every matched item (one full pass, same cost as today's
  settings-edit case), caches seeded, cheap thereafter. Acceptable one-time
  cost; note it in the release notes / README changelog line.

## 5. Subcommand matrix (per the CLAUDE.md keep-in-sync rule)

| Subcommand | Impact |
| --- | --- |
| `update` | Full feature: Part A every-run dates pass with per-item cache + removal notice; Part B sectional settings sync; front-page gate change; new failure/retry semantics. |
| `publish` | **No change.** Publish never consumed `due_dates` or synced settings, and has no manifest. |
| `import` | **No change.** Import generates `due_dates` entries into course_settings.toml but touches no live manifest. |
| `mv` | **No code change** — `resolved_dates` lives inside the per-file entry, which mv re-keys wholesale; `section_hashes` lives on the settings entry, untouched by re-keying. mv's rewrite of `front_page`/`dashboard_image` now (correctly) dirties only those sections. Verify both with tests. |
| `find-orphans` / `prune` | **No change** — manifest/file-presence based. A pruned item's entry disappears entirely, cache included. |
| `list-titles` (cli.py due-dates helper) | **No change** — reads the TOML directly, no manifest. |

## 6. Testing plan (see TESTING.md conventions)

Unit — extend `tests/test_due_dates.py`:

- Symbolic resolution matrix: concrete / `NONE` / `KEEP` / empty /
  `CREATE_NONE_THEN_KEEP` × item-exists / item-new; all three keys always
  present; verbatim string comparison (same instant, different format ⇒
  "changed").
- Payload-from-resolution: `KEEP` omitted, `NONE` → `""`, concrete passed
  through; all-`KEEP` ⇒ empty payload.

Unit — new `tests/test_settings_sections.py`:

- Section hash: stable under key reordering (sort_keys); `metadata` excludes
  every claimed key; unknown top-level key changes only `metadata`; absent
  section hashes to the absent-marker, not a crash.
- `grading_standards` change implies `metadata` action.

Integration — `test_sync.py` style, mocked canvasapi; count API calls:

- Edit **only** `[course_flags]` in the fixture settings → zero
  `update_dates` calls, zero metadata/grading/groups/policy/tab calls, no
  image upload. (The headline assertion of the whole feature.)
- Edit one due_dates entry → exactly that item gets `update_dates`; verbose
  run shows `Skipping (dates unchanged)` for the others.
- Entry changed concrete → `KEEP` → no API call, cache updated, no re-trigger
  on the following run.
- Entry removed → notice printed, cache dropped, no API call; **second** run
  silent (one-time-ness).
- Entry removed + file body also edited → full-sync path records without
  `resolved_dates`; no notice; no stray dates call.
- `date_warning` from Canvas → error recorded, cache **not** written, next
  run retries (call count proves it).
- Dates pass runs (and applies a TOML date edit) even when
  course_settings.toml's mtime is fresh-but-content-changed via touch games —
  i.e., assert the pass is unconditional, not settings-gated.
- Section failure (mock one `capi` call to raise) → run fails, entry not
  `mark_synced`, next run retries only that section (call counts on both
  runs).
- Dashboard image file touched, settings untouched → image re-uploaded
  (the §4.3 gap fix); nothing else runs.
- `gg mv` of front page → next update re-sets front page, zero
  `update_dates` calls; `resolved_dates` survives re-keying on a moved
  assignment.
- `--force-uploads` → all sections + all matched items sent.
- Migration: pre-existing manifest without the new tables → first run seeds
  caches, second run is quiet (call counts).

**Watch out** (same trap as the flags work): existing integration tests
assert on call counts / printed output around `Applying due_dates...` and
`Syncing course settings...`; making the dates pass unconditional and the
sections selective **will** break several of them — budget for it.

## 7. Future work (NOT this feature)

- **rubrics.toml per-rubric hashing** — rubrics are already tracked
  independently; per-rubric value caching could skip unchanged rubrics within
  a changed file. Low value until rubric counts grow.
- **§12.2 CLI flag overrides interplay** (DESIGN-course-flags.md) — if/when
  `--flag` overrides land, decide the analogous question for a hypothetical
  `--due-date` override; nothing here blocks it.
- **Announcements/pages dates** — out of scope; `iter_gradeable_content` is
  authoritative for what the dates system manages.

## 8. Documentation updates at implementation time

Per CLAUDE.md:

- **ARCHITECTURE.md**: manifest schema additions (`resolved_dates`,
  `section_hashes`), the reworked phase diagram (`_phase_due_dates`
  unconditional; `sync_course_settings` sectional), failure/retry semantics,
  the symbolic-sentinel resolution table.
- **README.md**: user-visible behavior — editing one due date now touches one
  item; the removed-entry notice; settings edits no longer re-upload the
  dashboard image; image-file edits now *do* re-upload it; the one-time
  first-run cache-seeding pass.
- **TODO.md**: remove the due_dates-caching pointer entry.
- **DESIGN-course-flags.md §12.3**: replace the sketch with a one-line pointer
  to this doc.
