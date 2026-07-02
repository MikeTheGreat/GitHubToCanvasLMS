"""Canvas upload logic via the canvasapi library."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from canvasapi import Canvas
from canvasapi.exceptions import BadRequest, ResourceDoesNotExist
from canvasapi.rubric import RubricAssociation
from canvasapi.util import combine_kwargs

from .config import Config


def get_course(config: Config):
    canvas = Canvas(config.base_url, config.api_token)
    return canvas.get_course(config.course_id)


# ---------------------------------------------------------------------------
# Course-level settings
# ---------------------------------------------------------------------------

_COURSE_METADATA_KEYS = {
    "title": "name",
    "course_code": "course_code",
    "start_at": "start_at",
    "conclude_at": "conclude_at",
    "default_view": "default_view",
    "license": "license",
    "is_public": "is_public",
    "is_public_to_auth_users": "is_public_to_auth_users",
    "public_syllabus": "public_syllabus",
    "public_syllabus_to_auth": "public_syllabus_to_auth",
    "grading_standard_enabled": "grading_standard_enabled",
    "grading_standard_id": "grading_standard_id",
    "hide_final_grade": "hide_final_grade",
    "hide_distribution_graphs": "hide_distribution_graphs",
    "allow_student_wiki_edits": "allow_student_wiki_edits",
    "allow_student_discussion_topics": "allow_student_discussion_topics",
    "allow_student_discussion_editing": "allow_student_discussion_editing",
    "allow_student_forum_attachments": "allow_student_forum_attachments",
    "lock_all_announcements": "lock_all_announcements",
    "restrict_student_future_view": "restrict_student_future_view",
    "restrict_student_past_view": "restrict_student_past_view",
    "restrict_enrollments_to_course_dates": "restrict_enrollments_to_course_dates",
    "syllabus_course_summary": "syllabus_course_summary",
    "show_announcements_on_home_page": "show_announcements_on_home_page",
    "home_page_announcement_limit": "home_page_announcement_limit",
    "usage_rights_required": "usage_rights_required",
    "open_enrollment": "open_enrollment",
    "self_enrollment": "self_enrollment",
    "enable_course_paces": "enable_course_paces",
}

_COURSE_METADATA_SKIP = {
    "storage_quota", "root_account_uuid", "image_identifier_ref",
    "last_modified", "copyright_restrictions", "copyright_description",
    "conditional_release", "content_library", "homeroom_course",
    "horizon_course", "career_learning_library_only",
    # nested sections handled separately:
    "default_post_policy", "late_policy", "grading_standards", "assignment_groups",
    # handled by upload_course_image:
    "dashboard_image",
}


def update_course_metadata(course, settings: dict[str, Any], grading_standard_id: int | None = None) -> None:
    """Apply flat course settings to Canvas via course.update()."""
    params: dict[str, Any] = {}
    for toml_key, api_key in _COURSE_METADATA_KEYS.items():
        if toml_key in settings and toml_key not in _COURSE_METADATA_SKIP:
            params[api_key] = settings[toml_key]
    # Canvas only applies (and displays) assignment-group weights when the
    # course-level flag is on. The IMSCC export name is group_weighting_scheme
    # ("percent" = weighted); if it's absent, the presence of any group_weight
    # implies the user wants weighting.
    if "group_weighting_scheme" in settings:
        params["apply_assignment_group_weights"] = (
            settings["group_weighting_scheme"] == "percent"
        )
    elif any(
        "group_weight" in g for g in settings.get("assignment_groups", [])
    ):
        params["apply_assignment_group_weights"] = True
    if grading_standard_id is not None:
        params["grading_standard_id"] = grading_standard_id
    if params:
        course.update(course=params)


def sync_grading_standards(course, standards: list[dict[str, Any]]) -> int | None:
    """Create missing grading standards. Returns the ID of the first standard created/found."""
    if not standards:
        return None
    existing = {gs.title: gs.id for gs in course.get_grading_standards()}
    first_id: int | None = None
    for std in standards:
        title = std.get("title", "")
        if title in existing:
            gs_id = existing[title]
        else:
            data_raw = std.get("data", [])
            scheme = [{"name": row[0], "value": row[1]} for row in data_raw if len(row) == 2]
            kwargs: dict[str, Any] = {"title": title, "grading_scheme_entry": scheme}
            if std.get("points_based") is not None:
                kwargs["points_based"] = std["points_based"]
            if std.get("scaling_factor") is not None:
                kwargs["scaling_factor"] = std["scaling_factor"]
            gs = course.create_grading_standard(**kwargs)
            gs_id = gs.id
        if first_id is None:
            first_id = gs_id
    return first_id


def sync_assignment_groups(course, groups: list[dict[str, Any]]) -> None:
    """Create or update assignment groups in position order."""
    if not groups:
        return
    existing = {ag.name: ag for ag in course.get_assignment_groups()}
    for group in sorted(groups, key=lambda g: g.get("position", 9999)):
        name = group.get("title", "")
        rules: dict[str, Any] = {}
        for r in group.get("rules", []):
            if r.get("drop_type") == "drop_lowest":
                rules["drop_lowest"] = r.get("drop_count", 0)
            elif r.get("drop_type") == "drop_highest":
                rules["drop_highest"] = r.get("drop_count", 0)
        kwargs: dict[str, Any] = {}
        if "group_weight" in group:
            kwargs["group_weight"] = group["group_weight"]
        if "position" in group:
            kwargs["position"] = group["position"]
        if rules:
            kwargs["rules"] = rules
        if name in existing:
            # The update endpoint only permits flat top-level params; anything
            # nested under assignment_group[...] is silently dropped by Canvas.
            existing[name].edit(name=name, **kwargs)
        else:
            course.create_assignment_group(name=name, **kwargs)


def get_assignment_group_ids(course) -> dict[str, int]:
    """Return {name: canvas_id} for all assignment groups in the course."""
    return {ag.name: ag.id for ag in course.get_assignment_groups()}


def update_late_policy(course, late_policy: dict[str, Any]) -> None:
    """Create or update the course late policy via raw API calls."""
    if not late_policy:
        return
    flat = [(f"late_policy[{k}]", v) for k, v in late_policy.items()]
    try:
        course._requester.request("GET", f"courses/{course.id}/late_policy")
        course._requester.request("PATCH", f"courses/{course.id}/late_policy", _kwargs=flat)
    except Exception:
        course._requester.request("POST", f"courses/{course.id}/late_policy", _kwargs=flat)


_SET_COURSE_POST_POLICY = """
mutation SetCoursePostPolicy($courseId: ID!, $postManually: Boolean!) {
  setCoursePostPolicy(input: {courseId: $courseId, postManually: $postManually}) {
    postPolicy { postManually }
    errors { attribute message }
  }
}
"""


def graphql(course, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Run a GraphQL query/mutation against /api/graphql and return the parsed JSON.

    canvasapi only exposes ``graphql()`` on the top-level ``Canvas`` object, but the
    shared requester is reachable from any object, so we issue it via ``course``.
    Raises on transport errors, GraphQL-level ``errors``, or mutation-payload errors
    (GraphQL returns HTTP 200 even when the operation failed).
    """
    response = course._requester.request(
        "POST",
        "graphql",
        headers={"Content-Type": "application/json"},
        _url="graphql",  # GraphQL lives at /api/graphql, not /api/v1/...
        json={"query": query, "variables": variables},
    )
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data


def update_post_policy(course, post_manually: bool) -> None:
    """Set the course default post policy via the GraphQL ``setCoursePostPolicy`` mutation.

    Post policies are a GraphQL-only feature in Canvas — there is no REST endpoint
    (the old ``PUT /courses/:id/post_policies`` route returns 404), so we issue the
    mutation directly.
    """
    data = graphql(
        course,
        _SET_COURSE_POST_POLICY,
        {"courseId": course.id, "postManually": post_manually},
    )
    payload = (data.get("data") or {}).get("setCoursePostPolicy") or {}
    if payload.get("errors"):
        raise RuntimeError(f"setCoursePostPolicy failed: {payload['errors']}")


TOOL_TAB_PREFIX = "context_external_tool_"

# The repo's `tab_configuration` uses the live Tabs API's string ids for built-in
# tabs ("assignments", "modules", ...). For backward compatibility we also accept
# Canvas's internal numeric tab constants (the form found in an IMSCC export's
# course_settings.xml) and translate them here.
NUMERIC_TAB_IDS: dict[int, str] = {
    0: "home",
    1: "syllabus",
    2: "pages",
    3: "assignments",
    4: "quizzes",
    5: "grades",
    6: "people",
    7: "groups",
    8: "discussions",
    9: "chat",
    10: "modules",
    11: "files",
    12: "conferences",
    13: "settings",
    14: "announcements",
    15: "outcomes",
    16: "collaborations",
    18: "collaborations",  # "new" collaborations tab; same live id as 16
}

# Canvas refuses to move or hide these (see canvasapi Tab.update docstring), so we
# skip update calls for them rather than emitting a warning on every sync.
UNMANAGEABLE_TABS: frozenset[str] = frozenset({"home", "settings"})


def _resolve_tab_entry(
    entry: dict[str, Any], by_id: dict, by_id_ci: dict, by_label: dict
) -> tuple[Any, str | None, str]:
    """Resolve one tab_configuration entry to ``(live_tab_or_None, dedup_key, desc)``.

    ``id`` and ``label`` are interchangeable: the user types a single human-readable
    name in either, and we don't make them know whether it's a built-in tab or an
    external tool. A non-empty ``label`` is used as the name when present, else ``id``.
    Resolution order for a plain name:

      1. a built-in tab id, matched exactly then case-insensitively
         (so ``"Assignments"`` finds the built-in ``"assignments"``);
      2. any tab's display label, case-insensitively (so ``"Panopto Recordings"`` or
         ``"BigBlueButton"`` find the external tool / built-in by its sidebar name).

    Legacy numeric ids and literal ``context_external_tool_…`` ids are also accepted.
    Returns ``(None, None, "")`` (after warning) for entries we can't interpret.
    """
    raw_id = entry.get("id")
    label = entry.get("label")

    if isinstance(raw_id, bool):  # guard: bool is a subclass of int
        print(f"  WARNING: course-navigation tab has non-id value {raw_id!r}; skipping")
        return None, None, ""

    # Legacy numeric built-in id (IMSCC export form).
    if isinstance(raw_id, int):
        api_id = NUMERIC_TAB_IDS.get(raw_id)
        if api_id is None:
            print(f"  WARNING: unknown course-navigation tab id {raw_id!r}; skipping")
            return None, None, ""
        tab = by_id.get(api_id)
        return tab, (tab.id if tab else api_id), repr(api_id)

    has_label = isinstance(label, str)
    label_text = label.strip() if has_label else ""
    id_is_tool = isinstance(raw_id, str) and raw_id.startswith(TOOL_TAB_PREFIX)

    # Importer's unfilled empty-label placeholder on an external-tool tab.
    if id_is_tool and has_label and not label_text:
        print(
            f"  WARNING: external-tool navigation tab {raw_id!r} has no label; add "
            'label = "<tool name>" in course_settings.toml to position it; skipping'
        )
        return None, None, ""

    # The name to match: a non-empty label wins, otherwise the id.
    name = label_text or (raw_id if isinstance(raw_id, str) else "")
    if not name:
        print("  WARNING: course-navigation entry has neither a usable id nor label; skipping")
        return None, None, ""

    # A literal external-tool id (provenance / legacy) only matches by exact id.
    if name.startswith(TOOL_TAB_PREFIX):
        tab = by_id.get(name)
        return tab, (tab.id if tab else name), f"external tool {name!r}"

    # A human name: built-in id (exact → case-insensitive), then any tab's label.
    tab = by_id.get(name) or by_id_ci.get(name.lower()) or by_label.get(name.lower())
    return tab, (tab.id if tab else name.lower()), repr(name)


def sync_tab_configuration(course, tab_config: list[dict[str, Any]]) -> None:
    """Apply course-navigation (left-sidebar) order/visibility from tab_configuration.

    Each entry is a dict in display order naming one tab via ``id`` or ``label`` (the
    two are interchangeable — built-in tabs and external tools are both matched by
    name, case-insensitively); ``hidden`` defaults to false. We can only reorder/hide
    tabs the course already exposes — entries that don't resolve to an existing tab
    (e.g. an external tool not installed in this course) are warned about and skipped,
    never created.
    """
    if not tab_config:
        return
    live = list(course.get_tabs())
    by_id = {tab.id: tab for tab in live}
    by_id_ci = {str(tid).lower(): tab for tid, tab in by_id.items()}
    by_label: dict[str, Any] = {}  # all live tabs, keyed by lowercased label
    for tab in live:
        label = (getattr(tab, "label", "") or "").strip().lower()
        if label:
            by_label.setdefault(label, tab)

    seen: set[str] = set()
    # Canvas pins Home at position 1 and rejects any other tab placed there
    # ("That tab location is invalid"), so movable tabs are numbered from 2 up.
    position = 1
    for entry in tab_config:
        hidden = bool(entry.get("hidden", False))
        tab, key, desc = _resolve_tab_entry(entry, by_id, by_id_ci, by_label)
        if key is None:
            continue  # _resolve_tab_entry already warned
        if key in seen:
            continue  # first occurrence wins
        seen.add(key)
        if key in UNMANAGEABLE_TABS:
            continue  # Canvas pins home/settings; nothing to do
        if tab is None:
            print(
                f"  WARNING: course-navigation {desc} is not present in this course "
                "(no built-in tab or installed tool by that name); skipping — it will "
                "not appear in the nav"
            )
            continue
        position += 1
        try:
            tab.update(position=position, hidden=hidden)
        except Exception as exc:
            print(f"  WARNING: could not update course-navigation {desc}: {exc}")


def read_tab_configuration(course) -> list[dict[str, Any]]:
    """Read live course tabs and return them as a ``tab_configuration`` list.

    For external-tool tabs the migration hash is computed so the output can be
    matched against IMSCC-derived settings on the same Canvas instance.
    """
    tabs = sorted(course.get_tabs(), key=lambda t: getattr(t, "position", 0))
    result: list[dict[str, Any]] = []
    for tab in tabs:
        out: dict[str, Any] = {}
        tab_id = str(tab.id)
        label = (getattr(tab, "label", "") or "").strip()

        if tab_id.startswith(TOOL_TAB_PREFIX):
            out["label"] = label
            g_hash = "g" + hashlib.md5(tab_id.encode()).hexdigest()
            out["id"] = TOOL_TAB_PREFIX + g_hash
        else:
            out["id"] = tab_id

        if getattr(tab, "hidden", False):
            out["hidden"] = True

        result.append(out)
    return result


def get_canvas_updated_at(course, canvas_type: str, identifier) -> datetime | None:
    """Return the updated_at datetime for an existing Canvas item, or None if unavailable.

    identifier is the page URL slug (str) for pages, canvas_id (int) for all other types.
    """
    try:
        if canvas_type == "page":
            obj = course.get_page(identifier)
        elif canvas_type == "assignment":
            obj = course.get_assignment(identifier)
        elif canvas_type == "discussion":
            obj = course.get_discussion_topic(identifier)
        elif canvas_type == "quiz":
            obj = course.get_quiz(identifier)
        elif canvas_type == "module":
            obj = course.get_module(identifier)
        elif canvas_type == "file":
            obj = course.get_file(identifier)
        else:
            return None
        val = getattr(obj, "updated_at", None)
        if val is None:
            return None
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return None


# Manifest types prune can act on. Other types (syllabus, course_settings,
# module_order, question_bank) have no standalone deletable/unpublishable object.
DELETABLE_TYPES = frozenset(
    {"page", "assignment", "discussion", "quiz", "module", "file"}
)
UNPUBLISHABLE_TYPES = frozenset(
    {"page", "assignment", "discussion", "quiz", "module"}
)


def _get_object(course, canvas_type: str, entry: dict[str, Any]):
    """Fetch the canvasapi object for a manifest entry, or None if unsupported.

    Pages are addressed by URL slug (canvas_url); everything else by canvas_id.
    """
    if canvas_type == "page":
        return course.get_page(entry["canvas_url"])
    if canvas_type == "assignment":
        return course.get_assignment(entry["canvas_id"])
    if canvas_type == "discussion":
        return course.get_discussion_topic(entry["canvas_id"])
    if canvas_type == "quiz":
        return course.get_quiz(entry["canvas_id"])
    if canvas_type == "module":
        return course.get_module(entry["canvas_id"])
    if canvas_type == "file":
        return course.get_file(entry["canvas_id"])
    return None


def delete_content(course, canvas_type: str, entry: dict[str, Any]) -> bool:
    """Delete the Canvas object for an orphaned manifest entry.

    Returns True if a delete was actually issued against an existing object, and
    False if the object was already gone on Canvas (deleted manually or by a prior
    run). Either way the desired end state — the item absent from Canvas — is
    reached, so the caller can drop the stale manifest entry; the return value only
    lets it report which case happened. Callers must pre-filter to DELETABLE_TYPES.
    """
    if canvas_type not in DELETABLE_TYPES:
        return False
    try:
        obj = _get_object(course, canvas_type, entry)
        if obj is None:
            return False
        obj.delete()
    except ResourceDoesNotExist:
        return False
    return True


def unpublish_content(course, canvas_type: str, entry: dict[str, Any]) -> bool:
    """Set published=False on the Canvas object for an orphaned manifest entry.

    Returns True if an unpublish was actually issued against an existing object, and
    False if the object was already gone on Canvas (by definition no longer
    published). Either way the caller can drop the stale manifest entry; the return
    value only distinguishes the two for reporting. Callers must pre-filter to
    UNPUBLISHABLE_TYPES.
    """
    if canvas_type not in UNPUBLISHABLE_TYPES:
        return False
    try:
        if canvas_type == "page":
            course.get_page(entry["canvas_url"]).edit(wiki_page={"published": False})
        elif canvas_type == "assignment":
            course.get_assignment(entry["canvas_id"]).edit(
                assignment={"published": False}
            )
        elif canvas_type == "discussion":
            course.get_discussion_topic(entry["canvas_id"]).update(published=False)
        elif canvas_type == "quiz":
            course.get_quiz(entry["canvas_id"]).edit(quiz={"published": False})
        elif canvas_type == "module":
            course.get_module(entry["canvas_id"]).edit(module={"published": False})
    except ResourceDoesNotExist:
        return False
    return True


def upload_course_image(course, local_path: Path) -> int:
    """Upload an image and set it as the course dashboard/card image.

    Returns the Canvas file ID of the uploaded image.
    """
    _success, response = course.upload(str(local_path), parent_folder_path="course files")
    file_id = response["id"]
    course.update(course={"image_id": file_id})
    return file_id


def upload_asset(course, local_path: Path, assets_root: Path) -> dict[str, Any]:
    """Upload a file to Canvas Files. Returns manifest entry.

    Passes on_duplicate="overwrite" so that re-uploading a changed asset replaces
    the existing file in-place, preserving its Canvas file ID and URL. Without this,
    Canvas renames the new upload (e.g. image-1.png), leaving every page/assignment
    that already links to the old file pointing at stale content.
    """
    rel = local_path.relative_to(assets_root)
    parent = rel.parent
    canvas_folder = "course files" if parent == Path(".") else f"course files/{parent}"
    _success, response = course.upload(
        str(local_path),
        parent_folder_path=canvas_folder,
        on_duplicate="overwrite",
    )
    return {
        "canvas_type": "file",
        "canvas_id": response["id"],
        "canvas_url": response["url"],
    }


def create_or_update_page(
    course, canvas_url: str | None, title: str, body: str, **kwargs
) -> dict[str, Any]:
    """Create or update a Canvas Page. `canvas_url` is the page URL slug."""
    if canvas_url is not None:
        try:
            page = course.get_page(canvas_url)
            page = page.edit(wiki_page={"title": title, "body": body, **kwargs})
        except ResourceDoesNotExist:
            print(f"  Canvas page '{canvas_url}' was deleted; re-creating")
            page = course.create_page(wiki_page={"title": title, "body": body, **kwargs})
    else:
        page = course.create_page(wiki_page={"title": title, "body": body, **kwargs})
    return {"canvas_type": "page", "canvas_id": page.page_id, "canvas_url": page.url, "html_url": page.html_url}


def set_front_page(course, page_url: str) -> None:
    """Designate an existing Canvas page as the course front page."""
    page = course.get_page(page_url)
    page.edit(wiki_page={"front_page": True})


_DATE_KEYS = ("unlock_at", "due_at", "lock_at")


def update_dates(
    course, canvas_type: str, canvas_id: int, date_fields: dict[str, Any]
) -> dict[str, Any]:
    """Update only the date fields on an existing Canvas item. Returns result dict."""
    try:
        if canvas_type == "assignment":
            obj = course.get_assignment(canvas_id)
            obj.edit(assignment=date_fields)
        elif canvas_type == "discussion":
            obj = course.get_discussion_topic(canvas_id)
            obj.update(assignment=date_fields)
        elif canvas_type == "quiz":
            obj = course.get_quiz(canvas_id)
            obj.edit(quiz=date_fields)
        else:
            return {"error": f"unsupported type: {canvas_type}"}
        return {"html_url": obj.html_url}
    except BadRequest as exc:
        if "availability dates" not in str(exc) and "due_at" not in str(exc):
            raise
        return {"date_warning": str(exc), "html_url": getattr(obj, "html_url", None)}


def create_or_update_assignment(
    course, canvas_id: int | None, title: str, body: str, **kwargs
) -> dict[str, Any]:
    try:
        return _do_assignment(course, canvas_id, title, body, **kwargs)
    except BadRequest as exc:
        if "availability dates" not in str(exc) and "due_at" not in str(exc):
            raise
        stripped = {k: v for k, v in kwargs.items() if k not in _DATE_KEYS}
        print(f"  WARNING: Canvas rejected due dates; retrying without date fields ({exc})")
        result = _do_assignment(course, canvas_id, title, body, **stripped)
        result["date_warning"] = str(exc)
        return result


def _do_assignment(
    course, canvas_id: int | None, title: str, body: str, **kwargs
) -> dict[str, Any]:
    if canvas_id is not None:
        try:
            assignment = course.get_assignment(canvas_id)
            assignment = assignment.edit(assignment={"name": title, "description": body, **kwargs})
        except ResourceDoesNotExist:
            print(f"  Canvas assignment {canvas_id} was deleted; re-creating")
            assignment = course.create_assignment(
                assignment={"name": title, "description": body, **kwargs}
            )
    else:
        assignment = course.create_assignment(
            assignment={"name": title, "description": body, **kwargs}
        )
    return {
        "canvas_type": "assignment",
        "canvas_id": assignment.id,
        "html_url": assignment.html_url,
        # Passed back so callers can detect and remove a stale rubric association
        # without an extra GET — the edit/create response already includes it.
        "rubric_settings": getattr(assignment, "rubric_settings", None),
    }


def create_or_update_discussion(
    course, canvas_id: int | None, title: str, body: str, **kwargs
) -> dict[str, Any]:
    try:
        return _do_discussion(course, canvas_id, title, body, **kwargs)
    except BadRequest as exc:
        if "availability dates" not in str(exc) and "due_at" not in str(exc):
            raise
        stripped = dict(kwargs)
        if "assignment" in stripped and isinstance(stripped["assignment"], dict):
            stripped["assignment"] = {
                k: v for k, v in stripped["assignment"].items() if k not in _DATE_KEYS
            }
        print(f"  WARNING: Canvas rejected due dates; retrying without date fields ({exc})")
        result = _do_discussion(course, canvas_id, title, body, **stripped)
        result["date_warning"] = str(exc)
        return result


def _do_discussion(
    course, canvas_id: int | None, title: str, body: str, **kwargs
) -> dict[str, Any]:
    if canvas_id is not None:
        try:
            topic = course.get_discussion_topic(canvas_id)
            topic = topic.update(title=title, message=body, **kwargs)
        except ResourceDoesNotExist:
            print(f"  Canvas discussion {canvas_id} was deleted; re-creating")
            topic = course.create_discussion_topic(title=title, message=body, **kwargs)
    else:
        topic = course.create_discussion_topic(title=title, message=body, **kwargs)
    return {"canvas_type": "discussion", "canvas_id": topic.id, "html_url": topic.html_url}


def create_stub(course, canvas_type: str, title: str) -> dict[str, Any]:
    """Create a minimal unpublished Canvas item. Returns manifest entry."""
    if canvas_type == "page":
        page = course.create_page(wiki_page={"title": title, "body": "", "published": False})
        return {"canvas_type": "page", "canvas_id": page.page_id, "canvas_url": page.url}
    if canvas_type == "assignment":
        assignment = course.create_assignment(
            assignment={"name": title, "description": "", "published": False}
        )
        return {"canvas_type": "assignment", "canvas_id": assignment.id}
    if canvas_type == "discussion":
        topic = course.create_discussion_topic(title=title, message="", published=False)
        return {"canvas_type": "discussion", "canvas_id": topic.id}
    raise ValueError(f"Cannot create stub for canvas_type: {canvas_type!r}")


def create_or_update_quiz(
    course, canvas_id: int | None, title: str, description: str, **kwargs
) -> dict[str, Any]:
    try:
        return _do_quiz(course, canvas_id, title, description, **kwargs)
    except BadRequest as exc:
        if "availability dates" not in str(exc) and "due_at" not in str(exc):
            raise
        stripped = {k: v for k, v in kwargs.items() if k not in _DATE_KEYS}
        print(f"  WARNING: Canvas rejected due dates; retrying without date fields ({exc})")
        result = _do_quiz(course, canvas_id, title, description, **stripped)
        result["date_warning"] = str(exc)
        return result


def _do_quiz(
    course, canvas_id: int | None, title: str, description: str, **kwargs
) -> dict[str, Any]:
    params = {"title": title, "description": description, **kwargs}
    if canvas_id is not None:
        try:
            quiz = course.get_quiz(canvas_id)
            quiz = quiz.edit(quiz=params)
        except ResourceDoesNotExist:
            print(f"  Canvas quiz {canvas_id} was deleted; re-creating")
            quiz = course.create_quiz(quiz=params)
    else:
        quiz = course.create_quiz(quiz=params)
    return {"canvas_type": "quiz", "canvas_id": quiz.id, "html_url": quiz.html_url}


def _build_question_params(q: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "question_name": q["title"],
        "question_type": q["question_type"],
        "question_text": q["question_text"],
        "points_possible": q["points_possible"],
    }
    if q.get("answers"):
        params["answers"] = q["answers"]
    for key in ("neutral_comments", "correct_comments", "incorrect_comments"):
        if q.get(key):
            params[key] = q[key]
    return params


def sync_quiz_questions(
    course,
    quiz,
    questions: list[dict[str, Any]],
) -> dict[str, int]:
    """Delete all existing quiz questions and re-add in order.

    Returns a dict mapping each question's rel_path to its new Canvas question ID.
    """
    for existing_q in quiz.get_questions():
        existing_q.delete()
    result: dict[str, int] = {}
    for q in questions:
        canvas_q = quiz.create_question(question=_build_question_params(q))
        result[q["rel_path"]] = canvas_q.id
    return result


def create_or_update_module(course, canvas_id: int | None, title: str, **kwargs):
    """Return the canvasapi Module object (created or updated)."""
    if canvas_id is not None:
        module = course.get_module(canvas_id)
        return module.edit(module={"name": title, **kwargs})
    return course.create_module(module={"name": title, **kwargs})


def reposition_module(course, canvas_id: int, position: int):
    """Set only the position of an existing module without re-syncing its content."""
    module = course.get_module(canvas_id)
    module.edit(module={"position": position})


_CANVAS_ITEM_TYPE = {
    "page": "Page",
    "assignment": "Assignment",
    "discussion": "Discussion",
    "quiz": "Quiz",
    "file": "File",
}


def clear_module_items(module) -> None:
    for item in module.get_module_items():
        item.delete()


def _set_module_item_published(module, mi, title: str, published: bool) -> str | None:
    """Set published state on a just-created module item.

    Canvas ignores the published flag during create_module_item, so we follow
    up with a PUT.  We use the module's requester directly (with JSON encoding)
    because create_module_item does not always populate module_id on the
    returned ModuleItem, which makes mi.edit() construct a broken URL.

    Canvas's API returns 500 for File-type module items — a known server-side
    bug.  For those we return a warning string; for all others we return None
    on success.
    """
    canvas_type = getattr(mi, "type", None)
    if canvas_type == "File":
        return title

    # Kept as a local import on purpose: this is the one raw-requests call in the
    # module (a deliberate workaround for a canvasapi/Canvas bug). tests/conftest.py
    # blocks real HTTP by monkeypatching the `requests.put` attribute; because this
    # binds the same `requests` module object, that patch still applies here.
    import requests as _requests

    req = module._requester
    full_url = "{}courses/{}/modules/{}/items/{}".format(
        req.base_url, module.course_id, module.id, mi.id
    )
    r = _requests.put(
        full_url,
        headers={"Authorization": "Bearer {}".format(req.access_token)},
        json={"module_item": {"published": published}},
    )
    if r.status_code >= 400:
        action = "publish" if published else "unpublish"
        print(f"  WARNING: could not {action} module item '{title}': HTTP {r.status_code}")
    return None


def add_module_item(
    module, item: dict[str, Any], manifest: dict
) -> tuple[int | None, str | None]:
    """Add one item to a Canvas module.

    Returns ``(canvas_item_id, unpublish_warning)``.  *unpublish_warning* is
    the item title when Canvas cannot unpublish it (File-type bug), else None.
    """
    indent = item.get("indent", 0)

    if item["type"] == "SubHeader":
        mi = module.create_module_item(
            module_item={"type": "SubHeader", "title": item["title"],
                         "indent": indent}
        )
        _set_module_item_published(module, mi, item["title"], True)
        return mi.id, None

    published = item.get("published", True)

    if item["type"] == "ExternalUrl":
        mi = module.create_module_item(
            module_item={
                "type": "ExternalUrl",
                "title": item["title"],
                "external_url": item["url"],
                "new_tab": item.get("new_tab", False),
                "indent": indent,
                "published": published,
            }
        )
        warn = _set_module_item_published(module, mi, item["title"], False) if not published else None
        return mi.id, warn

    local_path = item["local_path"]
    if local_path not in manifest:
        print(f"  WARNING: module item not in manifest (skipping): {local_path}")
        return None, None
    entry = manifest[local_path]
    canvas_type = _CANVAS_ITEM_TYPE.get(entry.get("canvas_type", ""))
    if canvas_type is None:
        print(f"  WARNING: unsupported canvas_type for module item (skipping): {local_path}")
        return None, None
    if canvas_type == "Page":
        module_item_params = {
            "type": canvas_type,
            "page_url": entry["canvas_url"],
            "title": item["title"],
            "indent": indent,
            "published": published,
        }
        stale_id = entry.get("canvas_url")
    else:
        module_item_params = {
            "type": canvas_type,
            "content_id": entry["canvas_id"],
            "title": item["title"],
            "indent": indent,
            "published": published,
        }
        stale_id = entry.get("canvas_id")
    try:
        mi = module.create_module_item(module_item=module_item_params)
    except (BadRequest, ResourceDoesNotExist) as exc:
        print(
            f"  WARNING: could not add module item '{item['title']}' ({local_path}): {exc}\n"
            f"  The Canvas ID {stale_id!r} may be stale or belong to a different course.\n"
            f"  Re-sync '{local_path}' first, then re-sync this module."
        )
        return None, None
    # Canvas ignores published=false on create; patch it afterwards.
    warn = _set_module_item_published(module, mi, item["title"], False) if not published else None
    return mi.id, warn


# ---------------------------------------------------------------------------
# Rubrics
# ---------------------------------------------------------------------------

def _build_criteria_dict(criteria: list[dict[str, Any]]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for i, c in enumerate(criteria):
        entry: dict[str, Any] = {
            "description": c.get("description", ""),
            "points": c.get("points", 0),
        }
        if c.get("long_description"):
            entry["long_description"] = c["long_description"]
        ratings: dict[str, dict] = {}
        for j, rat in enumerate(c.get("ratings", [])):
            r_entry: dict[str, Any] = {
                "description": rat.get("description", ""),
                "points": rat.get("points", 0),
            }
            if rat.get("long_description"):
                r_entry["long_description"] = rat["long_description"]
            ratings[str(j)] = r_entry
        entry["ratings"] = ratings
        result[str(i)] = entry
    return result


def get_rubric_ids(course) -> dict[str, int]:
    """Return {title: canvas_id} for all rubrics in the course."""
    return {r.title: r.id for r in course.get_rubrics()}


def sync_rubrics(
    course, rubrics: list[dict[str, Any]]
) -> tuple[dict[str, int], list[str]]:
    """Create or update rubrics (matched by title).

    Returns (id_mapping, created) where id_mapping is {title: canvas_id}
    and created is a list of titles that were newly created (not updated).
    """
    existing = {r.title: r.id for r in course.get_rubrics()}
    if not rubrics:
        return existing, []
    created: list[str] = []
    for r in rubrics:
        title = r.get("title", "")
        criteria_dict = _build_criteria_dict(r.get("criteria", []))
        rubric_params: dict[str, Any] = {"title": title, "criteria": criteria_dict}
        if "reusable" in r:
            rubric_params["reusable"] = r["reusable"]
        if "read_only" in r:
            rubric_params["read_only"] = r["read_only"]
        if title in existing:
            flat = combine_kwargs(rubric=rubric_params)
            course._requester.request(
                "PUT",
                f"courses/{course.id}/rubrics/{existing[title]}",
                _kwargs=flat,
            )
        else:
            result = course.create_rubric(
                rubric=rubric_params,
                rubric_association={
                    "association_type": "Course",
                    "association_id": course.id,
                    "purpose": "grading",
                },
            )
            if "rubric" in result:
                new_id = result["rubric"].id
                existing[title] = new_id
                # Canvas may restore a soft-deleted rubric with stale
                # attributes; follow up with a PUT to force them.
                # NOTE: as of 2026-06, this does not fix read_only —
                # Canvas still ignores the flag on re-created rubrics.
                # See TODO.md for details.
                if "read_only" in r or "reusable" in r:
                    flat = combine_kwargs(rubric=rubric_params)
                    course._requester.request(
                        "PUT",
                        f"courses/{course.id}/rubrics/{new_id}",
                        _kwargs=flat,
                    )
            created.append(title)
    return existing, created


def associate_rubric_with_assignment(
    course, rubric_id: int, assignment_id: int, use_for_grading: bool = True
) -> None:
    """Associate a rubric with an assignment."""
    course.create_rubric_association(
        rubric_association={
            "rubric_id": rubric_id,
            "association_id": assignment_id,
            "association_type": "Assignment",
            "purpose": "grading",
            "use_for_grading": use_for_grading,
        },
    )


def remove_rubric_from_assignment(course, assignment_id: int, rubric_id: int) -> bool:
    """Delete the rubric association between a rubric and an assignment.

    Canvas has no list-associations endpoint; instead we fetch the rubric with
    include[]=associations, find the association for this assignment, and delete it.
    Returns True if an association was deleted.
    """
    rubric = course.get_rubric(rubric_id, include=["associations"])
    raw_associations = getattr(rubric, "associations", None) or []
    deleted = False
    for assoc in raw_associations:
        if isinstance(assoc, dict):
            atype = assoc.get("association_type")
            aid = assoc.get("association_id")
            assoc_id = assoc.get("id")
        else:
            atype = getattr(assoc, "association_type", None)
            aid = getattr(assoc, "association_id", None)
            assoc_id = getattr(assoc, "id", None)
        if atype == "Assignment" and aid == assignment_id and assoc_id is not None:
            ra = RubricAssociation(course._requester, {"id": assoc_id, "course_id": course.id})
            ra.delete()
            deleted = True
    return deleted


# ---------------------------------------------------------------------------
# Question banks
# ---------------------------------------------------------------------------

def sync_question_bank(course, bank_title: str, questions: list[dict[str, Any]]) -> int:
    """Create a question bank and populate it. Returns the Canvas bank ID.

    KNOWN LIMITATION — re-syncing creates a duplicate bank instead of updating the
    existing one, and orphans the old bank on Canvas. This cannot currently be fixed
    cleanly because:
      * canvasapi (3.6.0) exposes no question-bank methods at all; the
        course.create_question_bank() / bank.create_assessment_question() calls below
        are not part of the library and will AttributeError against a real Canvas
        (the unit tests only pass because the course object is a MagicMock).
      * The documented Canvas REST API for Assessment Question Banks is GET-only
        (list banks / get bank / list questions) — there is no public POST/PUT/DELETE
        for banks or for the assessment questions inside them. GraphQL doesn't cover
        them either. See /doc/api/assessment_question_banks.html.
      * The only write path is the undocumented UI controller routes
        (POST/PUT/DELETE /courses/:id/question_banks...), which are unstable across
        Canvas versions and may not honor bearer-token auth.
    To support update/delete-then-recreate (mirroring the other content types) we would
    need those undocumented routes to work on the target instance; revisit if/when that
    is verified. Verified against canvasapi 3.6.0 and Canvas API docs, 2026-06.
    """
    bank = course.create_question_bank(
        assessment_question_bank={"name": bank_title}
    )
    for q in questions:
        bank.create_assessment_question(
            assessment_question=_build_question_params(q)
        )
    return bank.id
