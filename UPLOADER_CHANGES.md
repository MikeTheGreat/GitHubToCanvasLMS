# Uploader Changes Needed

This file tracks gaps between what the local Markdown/TOML repo contains
and what `sync.py` / `canvas_api.py` currently uploads to Canvas.
It covers work from multiple chat sessions; read all sections regardless
of which session you are continuing from.

**Key files to read before starting:**
- `src/github_to_canvas/sync.py` — main sync pipeline
- `src/github_to_canvas/canvas_api.py` — Canvas API wrapper functions
- `src/github_to_canvas/link_rewrite.py` — folder→type mapping, link rewriting
- `ARCHITECTURE.md` — full behaviour spec

---

## 0. Immediate breakage: `course_settings/` folder

**Current behaviour:** `sync.py` scans every non-skipped subdirectory of the
repo root for `*.md` files and sends them through `_sync_content_file()`.
The folder `course_settings/` is not in the skip list, so
`course_settings/syllabus.md` and `course_settings/events.md` (if present)
are synced as Canvas **Pages**, which is wrong.

**Fix required:**
Add `"course_settings"` to the `skip` set in `run_sync()` (and to the
parallel logic in `run_targeted_sync()`):

```python
skip = {"assets", "modules", "quizzes", "snippets", "course_settings"}
```

Each file in `course_settings/` needs purpose-built sync logic described
in the sections below. Nothing in that folder should be treated as a Canvas Page.

---

## 1. `course_settings.toml` — Course-wide settings

**Source file:** `course_settings.toml` (repo root)  
**Current behaviour:** Completely ignored by sync.  
**Canvas API:** `course.update(course={...})`

This TOML file has several logical sections that map to different Canvas API
endpoints. Add a `sync_course_settings(course, repo_path)` function called
once at the start of `run_sync()` (before assets, before content).

### 1a. Core course metadata

Fields to read from the top-level of `course_settings.toml` and pass to
`course.update()`:

| TOML key | Canvas API param | Notes |
| --- | --- | --- |
| `title` | `course[name]` | |
| `course_code` | `course[course_code]` | |
| `start_at` | `course[start_at]` | ISO 8601 string |
| `conclude_at` | `course[conclude_at]` | ISO 8601 string |
| `default_view` | `course[default_view]` | `"feed"`, `"wiki"`, `"modules"`, `"assignments"`, `"syllabus"` |
| `license` | `course[license]` | |
| `is_public` | `course[is_public]` | |
| `is_public_to_auth_users` | `course[is_public_to_auth_users]` | |
| `public_syllabus` | `course[public_syllabus]` | |
| `public_syllabus_to_auth` | `course[public_syllabus_to_auth]` | |
| `grading_standard_enabled` | `course[grading_standard_enabled]` | |
| `grading_standard_id` | `course[grading_standard_id]` | set after grading standard is created — see §1c |
| `hide_final_grade` | `course[hide_final_grade]` | |
| `hide_distribution_graphs` | `course[hide_distribution_graphs]` | |
| `allow_student_wiki_edits` | `course[allow_student_wiki_edits]` | |
| `allow_student_discussion_topics` | `course[allow_student_discussion_topics]` | |
| `allow_student_discussion_editing` | `course[allow_student_discussion_editing]` | |
| `allow_student_forum_attachments` | `course[allow_student_forum_attachments]` | |
| `lock_all_announcements` | `course[lock_all_announcements]` | |
| `restrict_student_future_view` | `course[restrict_student_future_view]` | |
| `restrict_student_past_view` | `course[restrict_student_past_view]` | |
| `restrict_enrollments_to_course_dates` | `course[restrict_enrollments_to_course_dates]` | |
| `syllabus_course_summary` | `course[syllabus_course_summary]` | |
| `show_announcements_on_home_page` | `course[show_announcements_on_home_page]` | |
| `home_page_announcement_limit` | `course[home_page_announcement_limit]` | |
| `usage_rights_required` | `course[usage_rights_required]` | |
| `open_enrollment` | `course[open_enrollment]` | |
| `self_enrollment` | `course[self_enrollment]` | |
| `enable_course_paces` | `course[enable_course_paces]` | |

Fields to **skip** (read-only or infrastructure, not settable via API):
`storage_quota`, `root_account_uuid`, `image_identifier_ref`,
`last_modified`, `copyright_restrictions`, `copyright_description`,
`conditional_release`, `content_library`, `homeroom_course`,
`horizon_course`, `career_learning_library_only`.

### 1b. Default post policy

Stored in `course_settings.toml` as `[default_post_policy]` with
`post_manually = true/false`.

Canvas API: `POST /api/v1/courses/:id/post_policies` with
`{"post_policy": {"post_manually": true/false}}`.

The `canvasapi` Python library does not have a wrapper for this — call it
via `requests` or by using the canvasapi `requester` directly:

```python
course._requester.request(
    "PUT",
    f"courses/{course.id}/post_policies",
    _kwargs={"post_policy[post_manually]": value},
)
```

### 1c. Grading standards

Stored in `course_settings.toml` as `[[grading_standards]]` (array of
tables). Each entry has: `title`, `data` (list of `[letter, threshold]`
pairs), `points_based`, `scaling_factor`.

Canvas API: `course.create_grading_standard(title=..., grading_scheme_entry=[...])`

The `grading_scheme_entry` param is a list of dicts:
`[{"name": "A", "value": 0.93}, ...]` — convert from the `data` list.

After creation, the returned `id` should be set as `grading_standard_id`
in the course update (§1a). Note: if a grading standard with the same
title already exists, avoid creating a duplicate — check
`course.get_grading_standards()` first.

### 1d. Assignment groups

Stored in `course_settings.toml` as `[[assignment_groups]]`. Each entry
has: `title`, `position`, `group_weight`, and optionally `rules`
(list of `{drop_type, drop_count}` dicts).

Canvas API: `course.create_assignment_group(name=..., group_weight=...,
position=..., rules={...})`

The Canvas `rules` dict uses keys `drop_lowest` and `drop_highest`
(not `drop_type`/`drop_count`). Convert:
```python
rules = {}
for r in group.get("rules", []):
    if r["drop_type"] == "drop_lowest":
        rules["drop_lowest"] = r["drop_count"]
    elif r["drop_type"] == "drop_highest":
        rules["drop_highest"] = r["drop_count"]
```

**Ordering matters:** create/update in position order. Before creating,
check `course.get_assignment_groups()` to avoid duplicates — match by
`name` and update if it exists.

### 1e. Late policy

Stored in `course_settings.toml` as `[late_policy]`. Keys:
`missing_submission_deduction_enabled`, `missing_submission_deduction`,
`late_submission_deduction_enabled`, `late_submission_deduction`,
`late_submission_interval`, `late_submission_minimum_percent_enabled`,
`late_submission_minimum_percent`.

Canvas API: `PATCH /api/v1/courses/:id/late_policy`

Not wrapped in `canvasapi` — use the requester directly:

```python
course._requester.request(
    "PATCH",
    f"courses/{course.id}/late_policy",
    _kwargs={f"late_policy[{k}]": v for k, v in late_policy.items()},
)
```

First check whether a late policy exists with
`GET /api/v1/courses/:id/late_policy` — create with `POST` if not,
update with `PATCH` if it does.

### 1f. Tab configuration

Stored in `course_settings.toml` as `tab_configuration` (raw JSON
string, or parsed list of `{id, hidden}` dicts depending on what was
implemented in the import session).

Canvas API: `course.list_tabs()` returns tab objects; call
`tab.update(hidden=True/False)` per tab.

Match tabs by `id`. LTI tool tabs have string IDs like
`"context_external_tool_xxx"` — the Canvas API accepts these as-is.

This is lower-priority than §1a–1e; skip if the JSON string is complex
to parse reliably.

---

## 2. `course_settings/syllabus.md` — Syllabus body

**Source file:** `course_settings/syllabus.md`  
**Current behaviour:** Incorrectly processed as a Canvas Page (or
silently skipped if `course_settings/` is added to the skip list).  
**Canvas API:** `course.update(course={"syllabus_body": html})`

Add a dedicated `sync_syllabus(course, repo_path)` function:

1. Read `course_settings/syllabus.md`
2. Parse frontmatter, convert body Markdown → HTML via Pandoc
3. Run link rewriting on the HTML (to resolve local refs to Canvas URLs)
4. Call `course.update(course={"syllabus_body": html})`

The syllabus has no Canvas ID in the manifest — it is always a full
overwrite of the course syllabus body. No manifest entry needed.

---

## 3. `course_settings/events.md` — Calendar events

**Source file:** `course_settings/events.md`  
**Current behaviour:** Would be incorrectly processed as a Canvas Page.  
**Canvas API:** `canvas.create_calendar_event(calendar_event={...})`
(top-level `canvas` object, not `course` — events span the whole account)

The events file has a frontmatter header and then one `## Title` section
per event with a `**Date:**` line and optional body.

Canvas calendar event params:
```python
{
    "context_code": f"course_{course.id}",
    "title": "...",
    "start_at": "2025-11-27T00:00:00Z",
    "end_at": "2025-11-27T00:00:00Z",
    "all_day": True,
    "description": "...",  # HTML
}
```

**Recommended approach:** Rather than parsing the events.md Markdown back
into individual events (lossy), add a `course_settings/events.toml` file
during import that preserves structured event data, and sync from that.
The `.md` file is then human-readable documentation only.

If re-parsing the Markdown is unavoidable, parse `## Title` sections and
extract the `**Date:**` line for the date.

Calendar event sync is complex (detecting existing events to avoid
duplicates, updating vs. creating). This is **lower priority** than §1
and §2.

---

## 4. Missing assignment frontmatter fields

**Source files:** `assignments/*.md`  
**Current behaviour:** `_sync_content_file()` only passes `points_possible`,
`due_at`, `submission_types` to `capi.create_or_update_assignment()`.
These frontmatter fields exist in imported files but are **silently dropped**:

| Frontmatter key | Canvas API param | Notes |
| --- | --- | --- |
| `lock_at` | `assignment[lock_at]` | |
| `unlock_at` | `assignment[unlock_at]` | |
| `grading_type` | `assignment[grading_type]` | `"points"`, `"percent"`, `"letter_grade"`, etc. |

**Fix:** Add these three keys to the `extra` dict loop in `_sync_content_file()`:

```python
for key in ("points_possible", "due_at", "lock_at", "unlock_at",
            "submission_types", "grading_type"):
    if key in frontmatter:
        extra[key] = frontmatter[key]
```

---

## 5. Missing discussion frontmatter fields

**Source files:** `discussions/*.md`  
**Current behaviour:** Only `require_initial_post` is passed. These fields
exist in imported (graded discussion) files but are dropped:

| Frontmatter key | Canvas API param | Notes |
| --- | --- | --- |
| `points_possible` | `assignment[points_possible]` | graded discussions only |
| `due_at` | `assignment[due_at]` | graded discussions only |
| `lock_at` | `assignment[lock_at]` | graded discussions only |
| `unlock_at` | `assignment[unlock_at]` | graded discussions only |

For graded discussions these are passed as nested assignment params:
`discussion_topic[assignment][points_possible]`, etc.
The `canvasapi` `create_discussion_topic()` / `topic.update()` accepts
`assignment` as a nested dict.

**Fix:** In `_sync_content_file()` and `capi.create_or_update_discussion()`,
collect the grading fields and pass them as:
```python
extra["assignment"] = {
    "points_possible": ...,
    "due_at": ...,
    ...
}
```

---

## 6. Module items: ExternalUrl not implemented

**Source files:** `modules/*.md` (lines like `- [title](https://...)`
that come from external URLs)

**Current behaviour:** `add_module_item()` only handles `SubHeader` and
content types (Page, Assignment, Discussion, Quiz). A line like
`- [Resource](https://example.com)` is parsed into `item["type"] = "content"`
with an absolute URL as `local_path` — causing a manifest lookup crash or
silent skip.

**Fix:** In `parse_module_body()`, detect absolute URLs and produce
`{"type": "ExternalUrl", "title": ..., "url": ...}`.
In `add_module_item()`, handle `ExternalUrl`:

```python
if item["type"] == "ExternalUrl":
    mi = module.create_module_item(module_item={
        "type": "ExternalUrl",
        "title": item["title"],
        "external_url": item["url"],
        "new_tab": item.get("new_tab", False),
    })
    return mi.id
```

---

## 7. Quiz HTML link rewriting (existing TODO in sync.py)

**Source files:** `quizzes/*/` — quiz description and question body text  
**Current behaviour:** `_sync_quiz()` uploads quiz descriptions and
question text as-is, without running link rewriting. Local file links
in quiz HTML will appear as broken relative paths in Canvas.

**Fix:** Before uploading:
1. Run `preprocess_snippets()` + `markdown_to_html()` on the quiz description body
2. Run `rewrite_links()` on the resulting HTML
3. Do the same for each question's `question_text` field

Also update `_get_file_refs()` (used by BFS in `run_targeted_sync`) to
extract local refs from quiz description/question HTML so BFS can follow
them.

---

## 8. `course_settings/` files — status tracking

This section tracks which `course_settings/` files the importer produces
and whether a corresponding upload path exists. Update this table when
import sessions add new files or sync sessions implement upload paths.

| File | Import status | Upload section |
| --- | --- | --- |
| `course_settings.toml` | ✅ produced | §1 (not yet implemented) |
| `course_settings/syllabus.md` | ✅ produced | §2 (not yet implemented) |
| `course_settings/events.md` | ✅ produced | §3 (not yet implemented) |
| `course_settings/rubrics.toml` | ✅ produced | §15 (not yet implemented) |
| `course_settings/files_meta.toml` | ✅ produced | §16 (not yet implemented) |
| `course_settings/media_tracks.toml` | ❌ not produced (empty in source) | n/a — skip |
| `lti_resource_links/*.xml` | ✅ handled (written as URL links in modules/*.md) | §6 (ExternalUrl) |
| `non_cc_assessments/*.xml.qti` | ✅ handled (quiz companion QTI → per-question points_possible; standalone objectbanks → question_banks/) | §12 |

If future import sessions produce new files, add them to this table and create
corresponding upload sections.

---

---

## 9. New quiz question types: multiple_response, fill_in_blank, pattern_match

**Source files:** `quizzes/*/questions/*.md`, `question_banks/*/questions/*.md`  
**Current behaviour:** `quiz.py::parse_question_file()` only handles `multiple_choice_question`,
`true_false_question`, and the essay fallback. The three types below are now produced by
the importer but will fall through to the essay branch (producing empty answers).

### 9a. multiple_response_question

Frontmatter has `correct: [1, 3]` (1-based list of correct answer indices).
The `## Answers` section is the same as MCQ.

**Fix in `quiz.py::parse_question_file()`:**
```python
if question_type == "multiple_response_question":
    # parse ## Answers section same as MCQ
    answers = [
        {"text": text, "weight": 100 if (i + 1) in correct else 0}
        for i, text in enumerate(answer_texts)
    ]
```

Canvas API answer format is the same as MCQ: each answer has `text` and `weight`
(100 = correct, 0 = incorrect). Multiple answers may have weight 100.

### 9b. fill_in_blank_question

Frontmatter has `answers: [ans1, ans2, ...]` (list of accepted strings, case-insensitive).

Canvas API `question_type` is `"short_answer_question"` (Canvas's name for FIB).
Answer format:
```python
answers = [{"text": a, "weight": 100} for a in frontmatter["answers"]]
```

### 9c. pattern_match_question

Frontmatter has `answers: [pattern1, ...]` and `match_type: substring`.

Canvas API `question_type` is `"text_only_question"` — Canvas does not have a
native pattern-match question type. Best effort: import as `short_answer_question`
with the first pattern as the accepted answer, and note the discrepancy in a
comment field.

---

## 10. Quiz / question bank question feedback fields

**Source files:** `quizzes/*/questions/*.md`, `question_banks/*/questions/*.md`  
**Current behaviour:** `_build_question_params()` in `canvas_api.py` and
`parse_question_file()` in `quiz.py` ignore feedback entirely.

The importer (after the audit prompt is implemented) will write a `## Feedback`
section with subsections `### General`, `### Correct`, `### Incorrect`, and
`### Per-answer`. These must be uploaded.

**Canvas API params on `question`:**

| Frontmatter/section | Canvas API param | Notes |
| --- | --- | --- |
| `### General` text | `question[neutral_comments]` | Shown always |
| `### Correct` text | `question[correct_comments]` | Shown on correct answer |
| `### Incorrect` text | `question[incorrect_comments]` | Shown on wrong answer |
| `### Per-answer` items | `answer[comments]` per answer | Add to each answer dict |

**Fix in `quiz.py::parse_question_file()`:** parse the `## Feedback` section and
add `neutral_comments`, `correct_comments`, `incorrect_comments` keys to the
returned dict. For per-answer feedback, add `"comments"` to each answer dict.

**Fix in `canvas_api.py::_build_question_params()`:**

```python
for key in ("neutral_comments", "correct_comments", "incorrect_comments"):
    if q.get(key):
        params[key] = q[key]
# per-answer feedback is embedded in each answer dict already
```

---

## 11. Essay sample solution

**Source files:** `quizzes/*/questions/*.md`, `question_banks/*/questions/*.md`  
**Current behaviour:** The `## Sample Solution` section (written by the importer
for essay questions) is ignored — it merges into `question_text` via the essay
fallback path.

**Fix in `quiz.py::parse_question_file()`:** parse the `## Sample Solution`
heading and extract its text. Store as `"neutral_comments"` in the returned dict
(Canvas stores essay sample solutions in the `neutral_comments` field, which it
labels "Comments" or "Sample Answer" in the quiz editor UI).

This is handled by the same `_build_question_params()` fix in §10.

---

## 12. Canvas question banks (new content type)

**Source files:** `question_banks/{slug}/{slug}.toml` + `question_banks/{slug}/questions/*.md`  
**Current behaviour:** The `question_banks/` folder does not exist yet (import not
yet implemented) and there is no upload path for it.

This is a **new content type** that needs a new sync path in `run_sync()`.

**Canvas API:**
```python
# Create the bank
bank = course.create_question_bank(
    assessment_question_bank={"name": toml["bank_title"]}
)

# Add each question  
for q in questions:
    bank.create_assessment_question(
        assessment_question=_build_question_params(q)
    )
```

**TOML format** (`question_banks/{slug}/{slug}.toml`):

```toml
bank_title = "Unfiled Questions"
bank_context_uuid = "SRI51UyJjHbdsdzYFn1LYxMYjMYh4GITEORKR38K"
bank_state = "active"
```

**Implementation notes:**

- `bank_context_uuid` and `original_answer_ids` are Canvas-internal tracking
  fields. They **cannot be set** via the Canvas REST API — Canvas assigns new IDs
  on create. Preserve them in the TOML/markdown for auditability, but do not
  attempt to pass them to the API.
- `bank_state = "deleted"` banks should still be created (Canvas can recreate
  deleted banks); check if this matters in practice.
- Question bank questions use the same `.md` format as quiz questions. Reuse
  `parse_question_file()` directly.
- The `question_banks/` directory should be added to the `skip` set in `run_sync()`
  alongside `quizzes`, and processed separately in a new `_sync_question_banks()`
  phase.
- Canvas question banks are course-level, not module-linked — no manifest entry
  needed for cross-linking, but store `canvas_id` in the manifest for idempotent
  re-syncs.

---

## 13. Discussion attachments

**Source files:** `discussions/*.md`  
**Current behaviour:** The `## Attachments` section (written by the importer when
a discussion has `<attachments>` in its IMSCC XML) is treated as body text and
uploaded as part of the discussion message HTML.

**Canvas API:** Discussion topics support a single file attachment via the
`attachment_id` parameter. Multiple attachments are not supported via the standard
API.

**Fix options (pick one):**

1. **Preferred:** Resolve each attachment relative link to a Canvas file URL
   (from the manifest) and rewrite the `## Attachments` section into inline
   HTML links in the body — Canvas will display them as clickable links.
2. **Alternative:** Pass the first attachment's `canvas_id` as
   `discussion_topic[attachment_id]` to the API.

If option 1 is chosen, the link rewriting logic in `_sync_content_file()` already
handles `../assets/` relative links — ensure discussions go through the same
`rewrite_links()` call as pages and assignments.

---

## 14. External URL `new_tab` attribute (module items)

**Source files:** `modules/*.md`  
**Current behaviour:** §6 describes adding `ExternalUrl` support. When that is
implemented, note that the `target` and `windowFeatures` attributes from the
importer need to be converted to Canvas's `new_tab` boolean.

**Module file format** (written by the importer):

```markdown
- [Resource Title](https://example.com) <!-- target="_blank" windowFeatures="width=800,height=600" -->
```

The HTML comment is appended to the same line as the link. Not all ExternalUrl
lines have a comment; the comment only appears when the source webLink had
non-empty `target` or `windowFeatures` attributes.

**Fix in `parse_module_body()`:** After extracting the link, check for a trailing
HTML comment with a regex such as:

```python
_EXTURL_ATTRS_RE = re.compile(r'<!--(.*?)-->')
# on the raw line, before splitting into title/href:
m = _EXTURL_ATTRS_RE.search(line)
attrs_comment = m.group(1).strip() if m else ""
# parse target="..." and windowFeatures="..." from attrs_comment
```

Store parsed values in the item dict. In `add_module_item()`:

**Canvas API:** `module_item[new_tab]` is a boolean. Map:

```python
new_tab = item.get("target", "") in ("_blank", "_new")
```

`windowFeatures` (e.g. `"width=800,height=600"`) has no Canvas equivalent —
discard it silently. Pass `new_tab` to `module.create_module_item()`.

---

## 15. Rubrics (`course_settings/rubrics.toml`)

**Source file:** `course_settings/rubrics.toml`  
**Current behaviour:** §8 listed this as a future placeholder. The importer now
produces this file. Promote to a full implementation task.

**TOML format:** `{"rubrics": [{title, criteria: [{description, points, ratings: [{description, points}]}]}]}`

**Canvas API:**

```python
rubric = course.create_rubric(
    rubric={
        "title": r["title"],
        "criteria": {
            str(i): {
                "description": c["description"],
                "points": c["points"],
                "ratings": {
                    str(j): {"description": rat["description"], "points": rat["points"]}
                    for j, rat in enumerate(c.get("ratings", []))
                },
            }
            for i, c in enumerate(r.get("criteria", []))
        },
    },
    rubric_association={
        "association_type": "Course",
        "association_id": course.id,
        "purpose": "grading",
    },
)
```

Note: the Canvas rubric criteria dict uses **string-keyed integer indices**
(`"0"`, `"1"`, ...) not a list — this is a quirk of the Canvas API.

Before creating, check `course.get_rubrics()` to avoid duplicates (match by
`title`). If a rubric with that title exists, update it.

---

## 16. `course_settings/files_meta.toml` — File visibility and lock settings

**Source file:** `course_settings/files_meta.toml`  
**Current behaviour:** File and folder produced by importer; completely ignored by sync.  
**Canvas API:** `course.get_file(canvas_id).update(locked=..., hidden=..., display_name=..., unlock_at=...)`  
for files; and `course.resolve_path(folder_path).update(locked=..., hidden=...)` for folders.

This file has two sections: `[[folders]]` (folder visibility) and `[[files]]`
(per-file lock/hide/display_name/unlock_at settings). Both must be applied
**after** assets are uploaded, because Canvas IDs are needed.

### 16a. Folder visibility

`[[folders]]` entries have `path` (relative to `course files/`) and `hidden`.

```python
for folder in meta.get("folders", []):
    # Canvas path is relative to "course files/"
    canvas_folder = course.resolve_path(f"course files/{folder['path']}")
    canvas_folder.update(hidden=folder.get("hidden", False))
```

### 16b. Per-file settings

`[[files]]` entries have `identifier` (IMSCC resource ID), and optionally
`locked`, `hidden`, `display_name`, `unlock_at`.

**Matching challenge:** `files_meta.toml` currently stores IMSCC identifiers,
not local asset paths. The sync code cannot directly look up a Canvas file ID
from an IMSCC identifier.

**Recommended fix (import-side):** Enhance `_write_files_meta_toml()` to also
write `local_path` for each file by looking up the identifier in `temp_manifest`:

```python
entry = temp_manifest.get(identifier)
fi["local_path"] = entry.local_path if entry else ""
```

Then sync-side can do `manifest[fi["local_path"]]["canvas_id"]` to get the
Canvas file ID.

**Fallback (sync-side only):** Match by `display_name` against filenames of
already-uploaded assets in the manifest. Less reliable; prefer the import-side fix.

**Canvas API for a file:**

```python
canvas_file = course.get_file(canvas_id)
updates = {}
if "locked" in fi: updates["locked"] = fi["locked"]
if "hidden" in fi: updates["hidden"] = fi["hidden"]
if "display_name" in fi: updates["display_name"] = fi["display_name"]
if "unlock_at" in fi: updates["unlock_at"] = fi["unlock_at"]
if updates:
    canvas_file.update(**updates)
```

**Note:** Apply folder settings before file settings so that folder
visibility takes effect first.

---

## 17. COMPLETED: `non_cc_assessments/` and `lti_resource_links/` folder audit

This audit was completed by an import session. Summary of findings and upload impact:

- **`non_cc_assessments/*.xml.qti`** — Two distinct categories:
  - **Quiz companion files** (e.g. `g_quiz_2.xml.qti`): Canvas-extended QTI with
    per-question `question_type` and `points_possible`. Detected via `<dependency>`
    links in the manifest; used as the preferred question source for the corresponding
    `quizzes/` entry (because it carries `points_possible`). The importer writes
    `points_possible` into each question's frontmatter.
    `canvas_api.py::_build_question_params()` already passes `points_possible` to
    the Canvas question API — no uploader change needed.
  - **Question banks** (e.g. `g_bank_1.xml.qti`): QTI `<objectbank>` files. Now
    classified as `category="bank"` and written to `question_banks/{slug}/`. Upload
    path is §12.

- **`lti_resource_links/*.xml`** — LTI 1.3 tool launch files. The importer reads
  `imscc_path` from `<file>` child elements (the resource `href` attribute is empty)
  and writes these as URL links in `modules/*.md` pointing to the
  `<blti:secure_launch_url>`. The ExternalUrl fix in §6 handles them at upload time.
  Registering LTI tool placements in Canvas is admin-level and out of scope.

§8's status table has been updated. No new upload sections are needed beyond §18.

---

## 18. DiscussionTopic module item bug (import-side; blocks uploader)

**Bug location:** `imscc_import.py::generate_module_file()`  
**Impact on upload:** Discussion items are silently dropped from `modules/*.md`,
so the uploader never creates module items for discussions.

**Root cause:** `generate_module_file()` checks `ct == "Discussion"` but real
Canvas IMSCC exports write `<content_type>DiscussionTopic</content_type>` (not
`Discussion`). The unmatched items fall through to the "Unknown module item type"
warning and are written as commented-out `SKIPPED` lines.

**Fix (import-side, one line):**

```python
if ct in ("WikiPage", "Assignment", "Discussion", "DiscussionTopic",
          "Quizzes::Quiz"):
```

This is an import bug, not an upload bug. Once fixed, the module file will contain
the correct `- [title](../discussions/slug.md)` link, and the existing uploader
code in `add_module_item()` will create a Canvas module item with type `"Discussion"`
without further changes.

**Verification:** After fixing, re-import a course with discussions, check that
`modules/*.md` contains links to `discussions/` entries, then run `sync.py` and
confirm `module.create_module_item(module_item={"type": "Discussion", ...})` is
called.

---

## Implementation order (recommended)

1. **§0** — Add `course_settings` to the skip set (prevents bad page creation; safe to do immediately)
2. **§18** — Fix DiscussionTopic import bug (one-line change; unblocks discussion module items)
3. **§4** — Add `lock_at`, `unlock_at`, `grading_type` to assignment sync (one-line change, high value)
4. **§5** — Add graded discussion fields (small change, high value)
5. **§6** — Fix ExternalUrl module items (prevents crashes on imported courses)
6. **§14** — Add `new_tab` to ExternalUrl items (do alongside §6)
7. **§2** — Syllabus upload (straightforward `course.update()` call)
8. **§1a** — Core course metadata update (high value, simple API call)
9. **§1d** — Assignment groups (needed for grading weights to be correct)
10. **§1c** — Grading standards (needed for letter grades to display correctly)
11. **§9** — New question types in `parse_question_file()` (needed before question banks work)
12. **§10 + §11** — Question feedback and essay sample solution (add to same pass as §9)
13. **§7** — Quiz HTML link rewriting (existing TODO)
14. **§13** — Discussion attachments (depends on asset upload being complete)
15. **§12** — Question banks (new content type; do after quiz question parsing is solid)
16. **§15** — Rubrics (low priority; complex API shape)
17. **§16a** — Folder visibility from `files_meta.toml` (do after assets uploaded)
18. **§16b** — Per-file lock/hide/display_name from `files_meta.toml` (requires import-side fix first)
19. **§1e** — Late policy (nice-to-have, requires raw HTTP)
20. **§1b** — Default post policy (nice-to-have, requires raw HTTP)
21. **§1f** — Tab configuration (optional / complex)
22. **§3** — Calendar events (complex, lower priority)
