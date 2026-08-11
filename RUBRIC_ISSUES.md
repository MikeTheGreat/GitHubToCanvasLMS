# Canvas Rubrics — Verified Behaviour and Open Issues

Canvas's rubric API behaves in several ways that are not documented and are actively
misleading (the web UI shows rubrics the API says don't exist; "update" silently
creates a copy instead). This file records what was **measured**, so nobody has to
re-run the experiments.

**Provenance:** every numbered fact below was verified against a live Canvas
(`cascadia.instructure.com`) on 2026-08-08/09, using throwaway course **2766160**
for the write experiments. Anything not directly measured is labelled *inference*.
The investigation started from a real failure in course 2735320 (IT-CS 115):

```text
WARNING: assignments/worksheets/01-b-unit-worksheets.md: rubric '01 Coding Exercise Rubric'
not found on Canvas (known: [...30 other titles...]); skipping rubric association
```

…for a rubric that was plainly visible on the course's Rubrics page.

---

## 1. How Canvas actually works

### 1.1 The API rubric list and the UI Rubrics page show different things

`GET /api/v1/courses/:id/rubrics` returns only rubrics whose `workflow_state` is
`active` **and** whose context is the course. The web Rubrics page shows something
else entirely. In course 2735320 the API returned 30 rubrics while the page showed 5,
and each list contained entries the other did not.

- Rubrics the page showed but the API omitted: soft-deleted rubrics that assignments
  still point at.
- Rubrics the API returned but the page omitted: Canvas's own orphan `X (n)` copies
  (see §1.6), which have no association.

*Inference:* the page renders the course's rubric **associations**, not the rubric
list, which is why deletion state doesn't remove an entry from it.

**Consequence:** "the rubric is right there in Canvas" is not evidence that the API
can see it. Always check via the API before assuming a lookup bug.

### 1.2 Canvas soft-deletes a rubric when its LAST association is destroyed

Two identical rubrics were created, one with a course-level association of
`purpose: "grading"` and one with `purpose: "bookmark"`. Deleting each rubric's last
remaining association flipped **both** to `workflow_state: "deleted"` and dropped
both from the REST index and from GraphQL's `rubricsConnection`.

- `purpose` is irrelevant to this. A `bookmark` association gives no protection.
- Deleting *some* associations is safe: a rubric with a course association survived
  having both of its assignment associations deleted.
- This is why removing a rubric from the last assignment that used it can silently
  destroy the rubric itself.

### 1.3 A deleted rubric can still be attached to assignments, and stays deleted

`POST /courses/:id/rubric_associations` against a soft-deleted rubric **succeeds and
does not revive it**. The assignment then reports a full `rubric_settings` block,
the Rubrics page lists the rubric, GraphQL reports `workflowState: "deleted"`, and
the REST index omits it.

This is exactly the production state of the three broken rubrics in course 2735320,
and it was reproduced from scratch in the lab course.

### 1.4 Re-creating the same title does NOT restore the deleted rubric

Creating a rubric whose title matches a soft-deleted one produces a **brand-new
rubric with a new id**. The old rubric stays `deleted` and stays attached to its
assignments.

> This contradicts what ARCHITECTURE.md, README.md and TODO.md previously claimed
> ("Canvas restores the soft-deleted rubric (same ID) rather than duplicating it").
> That claim was wrong and has been corrected in all three files.

### 1.5 Re-associating cleanly re-points an assignment

Associating an assignment with the new rubric replaces its existing association —
no duplicate, no manual cleanup. Verified: an assignment bound to deleted rubric
6823807 moved to active rubric 6823814 in one call, and the ghost dropped off the
Rubrics page (its last association was gone, per §1.2).

**This is the supported recovery path.** There is no "undelete".

### 1.6 `PUT /rubrics/:id` forks the rubric once it has 2+ grading associations

Instead of updating in place, Canvas creates a **new** rubric titled `X (1)`,
associated with nothing, and leaves the original's criteria untouched. The
assignments keep showing the old content. The API returns `200` with the new
rubric's id — there is no error.

The threshold is the number of associations with `purpose: "grading"`, and a
course-level `grading` association counts toward it:

| Course-level assoc | Assignments attached | grading assocs | `PUT` result |
| --- | --- | --- | --- |
| `grading` | 0 | 1 | in place |
| `bookmark` | 0 | 0 | in place |
| `grading` | 1 | 2 | **fork** |
| `bookmark` | 1 | 1 | in place |
| `grading` | 2 | 3 | **fork** |
| `bookmark` | 2 | 2 | **fork** |

Every row was measured. In the fork cases the assignment still reported the *old*
`points_possible` afterwards, confirming the edit never landed.

- Passing `rubric_association_id` on the PUT does **not** avoid the fork.
- The `X (1)` copy records its parent in a `rubric_id` field.
- Long runs of `Class Notes (1) … (11)` in a real course are one fork per sync.

### 1.7 Deleting a rubric through the API also detaches it from assignments

`DELETE /courses/:id/rubrics/:id` (what the UI's delete button hits) soft-deletes the
rubric **and** destroys its assignment associations — the assignment ends up with
`rubric_settings: null`. So a UI deletion alone cannot produce the §1.3 state; that
state requires a re-association after the deletion.

### 1.8 Orphan `X (n)` copies cannot be deleted directly

A rubric with no associations at all is unreachable through the obvious routes:

- `GET /courses/:id/rubrics/:id` → `404 {"message": "Rubric not found"}`
- `DELETE /courses/:id/rubrics/:id` → `500 internal_server_error`
- `POST /rubric_associations` with `association_type: "Course"` → `500`

…even though the rubric is listed by `GET /courses/:id/rubrics`.

**Workaround that works:** associate it with any assignment, then delete the rubric.
The delete then succeeds and takes the temporary association with it.

### 1.9 `read_only` and `reusable` are ignored on create

`create_rubric` with `read_only: true, reusable: true` returned a rubric with both
`false` — on a **first** create, not just a re-create. A follow-up PUT doesn't fix it
either (verified 2026-06).

*Inference:* Canvas derives `read_only` from the association count. A rubric with 17
assignment associations read back `read_only: true`, while freshly created ones read
back `false`. Not confirmed directly.

---

## 2. How to diagnose a rubric

REST hides deleted rubrics, so **GraphQL is the only way to see the real state**.

```bash
# What the tool can see (active, course-context rubrics only):
curl -s -H "Authorization: Bearer $CANVAS_API_TOKEN" \
  "https://<host>/api/v1/courses/<course>/rubrics?per_page=100"

# What an assignment is ACTUALLY bound to, deleted or not:
curl -s -H "Authorization: Bearer $CANVAS_API_TOKEN" \
     -H "Content-Type: application/json" -X POST https://<host>/api/graphql \
  -d '{"query":"{ assignment(id: \"<assignment_id>\") { name rubric { _id title workflowState } } }"}'

# Associations of an active rubric (no list-associations endpoint exists):
curl -s -H "Authorization: Bearer $CANVAS_API_TOKEN" \
  "https://<host>/api/v1/courses/<course>/rubrics/<rubric>?include[]=associations"
```

Finding which assignments use a rubric: there is no reverse index. Either read
`rubric_settings.id` from `GET /courses/:id/assignments?per_page=100`, or use
`include[]=associations` on the rubric when it is still active.

A rubric that is `workflowState: "deleted"` but still attached to assignments is the
signature failure — §1.3.

---

## 3. What this tool now does about it

| Behaviour | Where |
| --- | --- |
| Course-level association created with `purpose: "bookmark"`, so a rubric on one assignment updates in place instead of forking (§1.6) | `canvas_api.sync_rubrics` |
| Every run compares `rubrics.toml` against the live rubric list; a previously-synced title Canvas no longer lists is announced and re-created, bypassing the per-rubric hash cache | `sync.sync_course_settings` |
| Assignments referencing a re-created title are re-associated with the new id — including assignments whose own `.md` is unchanged and therefore never reaches `_apply_rubric` | `sync._repair_rubric_associations` (phase 4a) |

The detection deliberately treats a title Canvas has *never* seen as new rather than
deleted; otherwise every first sync (and every `--check-all`, whose simulated course
starts empty) would announce all rubrics as deleted.

End-to-end verification in the lab course: the §1.3 state was reproduced, then a plain
`update` with nothing changed on disk emitted

```text
  NOTICE: rubric 'ZZ E2E Repair Rubric' is in rubrics.toml but no longer on Canvas (deleted there); re-creating it
Syncing rubrics...
  Created rubric: ZZ E2E Repair Rubric
Repairing rubric associations...
  Re-associated rubric 'ZZ E2E Repair Rubric': assignments/zz-e2e-lab.md
```

…leaving the assignment on an `active` rubric. A third run was silent.

---

## 4. Remaining issues

### 4.1 Editing a rubric used by 2+ assignments silently does nothing

**Open.** Per §1.6, Canvas forks instead of updating, and `sync_rubrics` still prints
`Updated rubric: …` as though it worked. The user's edit is lost and an orphan
accumulates.

- **Practical impact:** `--force-uploads` is currently *harmful* on a course with
  shared rubrics — it re-PUTs every rubric and mints one orphan per multi-assignment
  rubric. Prefer clearing individual entries from the manifest's `rubric_hashes`.
- **Fix sketch:** the response carries a different rubric id than the one requested,
  so the fork is detectable. On detecting it, re-point every association from the old
  rubric to the new one and delete the old.
  `_repair_rubric_associations` already implements the re-pointing half.
- **Cheap interim:** warn when the returned id differs from the id sent, instead of
  reporting success.

### 4.2 Orphan `X (n)` rubrics accumulate and need manual cleanup

**Open.** Course 2735320 has ~21 (`Class Notes (1..11)`, `Worksheets (1..10)`,
`Pre-Class Notes (n)`). They clutter the rubric list and can only be removed via the
§1.8 attach-then-delete trick. Worth a small cleanup subcommand if they keep piling up.

### 4.3 `read_only` / `reusable` accepted in `rubrics.toml` but ignored

**Open.** Per §1.9 these silently do nothing. Either confirm they're unsettable and
warn when they appear in `rubrics.toml`, or drop them from the schema.

### 4.4 Numeric `rubric:` references are not repaired

**By design.** A numeric `rubric: 12345` names a Canvas id directly; if that id is a
deleted rubric there is no title to re-resolve, so the repair pass skips it. Such
assignments must be fixed by hand (switch them to a title reference).

### 4.5 The original cause of the production deletions is unknown

**Unresolved.** The three broken rubrics in course 2735320 reached the §1.3 state
some time before this investigation. Reaching it requires *all* associations to be
destroyed at some instant (§1.2) followed by a re-association (§1.3), and the tool's
own `remove_rubric_from_assignment` cannot do that on its own (it only removes
assignment associations, and the course association survives — verified). Most likely
those rubrics predate `rubrics.toml` and arrived via course copy / IMSCC import with
assignment associations only. The API keeps no history that would settle it.

The detection in §3 makes this self-healing regardless of cause, which is why chasing
it further was dropped.
