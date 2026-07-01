"""Main sync pipeline."""
from __future__ import annotations

import json
import re
import tomllib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import canvas_api as capi
from . import manifest as manifest_lib
from .config import Config
from .convert import (
    expand_frontmatter_snippets,
    find_referenced_snippets,
    markdown_to_html,
    preprocess_snippets,
)
from .ignore import IgnoreMatcher, load_ignore_matcher
from .link_rewrite import extract_local_refs, infer_canvas_type, rewrite_links
from .orphans import ResourceKey, extract_canvas_refs
from .quiz import parse_question_file, parse_quiz_file

_DATE_KEYS = ("unlock_at", "due_at", "lock_at")


def load_due_dates(repo_path: Path) -> list[dict[str, Any]]:
    """Load centralized due_dates from course_settings/course_settings.toml."""
    settings_path = repo_path / "course_settings" / "course_settings.toml"
    if not settings_path.exists():
        return []
    with settings_path.open("rb") as fh:
        settings = tomllib.load(fh)
    return settings.get("due_dates", [])


def _apply_due_dates_only(
    course,
    due_dates: list[dict[str, Any]],
    repo_path: Path,
    manifest: manifest_lib.ManifestDict,
    synced_keys: set[str],
    errors: list[str],
    matcher: IgnoreMatcher | None = None,
) -> None:
    """Apply due_dates overrides via dates-only API calls for items not already synced this run."""
    print("Applying due_dates...")
    for folder, ctype in (("assignments", "assignment"), ("discussions", "discussion")):
        content_dir = repo_path / folder
        if not content_dir.exists():
            continue
        for md_file in sorted(content_dir.rglob("*.md")):
            if matcher is not None and matcher.is_ignored(md_file, repo_path):
                continue
            local_key = md_file.relative_to(repo_path).as_posix()
            if local_key in synced_keys:
                continue
            existing = manifest.get(local_key)
            if not existing or "canvas_id" not in existing:
                continue
            try:
                fm, _ = parse_frontmatter(md_file.read_text())
            except Exception:
                continue
            title = fm.get("title", md_file.stem)
            override = find_due_date_override(due_dates, title, ctype)
            if override is None:
                continue
            canvas_id = existing["canvas_id"]
            date_fields = _resolve_date_overrides(override, canvas_id, local_key, errors)
            if not date_fields:
                continue
            print(f"  Updating dates: {local_key}")
            result = capi.update_dates(course, ctype, canvas_id, date_fields)
            if result.get("date_warning"):
                msg = _date_rejection_message(
                    local_key, title, date_fields.get("due_at"), result.get("html_url")
                )
                print(f"  {msg}")
                errors.append(msg)

    quizzes_dir = repo_path / "quizzes"
    if quizzes_dir.exists():
        for quiz_folder in sorted(d for d in quizzes_dir.iterdir() if d.is_dir()):
            if matcher is not None and matcher.is_ignored(quiz_folder, repo_path):
                continue
            quiz_md = quiz_folder / f"{quiz_folder.name}.md"
            if not quiz_md.exists():
                continue
            local_key = quiz_md.relative_to(repo_path).as_posix()
            if local_key in synced_keys:
                continue
            existing = manifest.get(local_key)
            if not existing or "canvas_id" not in existing:
                continue
            try:
                fm, _ = parse_frontmatter(quiz_md.read_text())
            except Exception:
                continue
            title = fm.get("title", quiz_folder.name)
            override = find_due_date_override(due_dates, title, "quiz")
            if override is None:
                continue
            canvas_id = existing["canvas_id"]
            date_fields = _resolve_date_overrides(override, canvas_id, local_key, errors)
            if not date_fields:
                continue
            print(f"  Updating dates: {local_key}")
            result = capi.update_dates(course, "quiz", canvas_id, date_fields)
            if result.get("date_warning"):
                msg = _date_rejection_message(
                    local_key, title, date_fields.get("due_at"), result.get("html_url")
                )
                print(f"  {msg}")
                errors.append(msg)


def _check_due_dates_coverage(
    due_dates: list[dict[str, Any]],
    repo_path: Path,
    matcher: IgnoreMatcher | None = None,
) -> None:
    """Scan all content files and print warnings for due_dates mismatches.

    - A due_dates entry whose name doesn't match any content file.
    - A content file (assignment/discussion/quiz) with no due_dates entry.
    """
    print("Checking due_dates coverage...")
    all_content: list[tuple[str, str]] = []  # (title, canvas_type)

    for folder, ctype in (("assignments", "assignment"), ("discussions", "discussion")):
        content_dir = repo_path / folder
        if not content_dir.exists():
            continue
        for md_file in sorted(content_dir.rglob("*.md")):
            if matcher is not None and matcher.is_ignored(md_file, repo_path):
                continue
            try:
                fm, _ = parse_frontmatter(md_file.read_text())
            except Exception:
                fm = {}
            all_content.append((fm.get("title", md_file.stem), ctype))

    quizzes_dir = repo_path / "quizzes"
    if quizzes_dir.exists():
        for quiz_folder in sorted(d for d in quizzes_dir.iterdir() if d.is_dir()):
            if matcher is not None and matcher.is_ignored(quiz_folder, repo_path):
                continue
            quiz_md = quiz_folder / f"{quiz_folder.name}.md"
            if quiz_md.exists():
                try:
                    fm, _ = parse_frontmatter(quiz_md.read_text())
                except Exception:
                    fm = {}
                all_content.append((fm.get("title", quiz_folder.name), "quiz"))

    # Check each due_dates entry against all content
    for entry in due_dates:
        name = entry.get("name", "")
        entry_type = entry.get("type", "")
        matched = any(
            title == name and (not entry_type or ctype == entry_type)
            for title, ctype in all_content
        )
        if not matched:
            type_msg = f" (type={entry_type})" if entry_type else ""
            print(f"  WARNING: due_dates entry {name!r}{type_msg} did not match any content file")

    # Check each content file against due_dates
    without_entry: list[tuple[str, str]] = []
    for title, ctype in all_content:
        if find_due_date_override(due_dates, title, ctype) is None:
            without_entry.append((title, ctype))
    if without_entry:
        print(
            "\nThe following content files have no entry in the centralized due_dates table:"
        )
        for title, canvas_type in without_entry:
            print(f"  {canvas_type}: {title}")


def find_due_date_override(
    due_dates: list[dict[str, Any]], title: str, canvas_type: str
) -> dict[str, Any] | None:
    """Find the matching centralized due_date entry for a content item."""
    for entry in due_dates:
        if entry.get("name") != title:
            continue
        entry_type = entry.get("type")
        if entry_type and entry_type != canvas_type:
            continue
        return entry
    return None


_DATE_SENTINELS = {"none", "keep", "create_none_then_keep"}


def _resolve_assignment_group_id(
    value: Any,
    assignment_group_ids: dict[str, int] | None,
    local_key: str,
    errors: list[str] | None,
) -> int | None:
    """Resolve an assignment_group_id frontmatter value (name or numeric ID) to a
    numeric Canvas ID. Prints and records a warning, returning None, if a name
    isn't found among the course's assignment groups."""
    if not isinstance(value, str):
        return value
    resolved = (assignment_group_ids or {}).get(value)
    if resolved is None:
        known = list(assignment_group_ids.keys()) if assignment_group_ids else []
        msg = (
            f"WARNING: {local_key}: assignment group '{value}' not found on Canvas "
            f"(known: {known}); skipping assignment_group_id"
        )
        print(f"  {msg}")
        if errors is not None:
            errors.append(msg)
        return None
    return resolved


def _resolve_date_overrides(
    override: dict[str, Any],
    canvas_id: int | None,
    local_key: str,
    errors: list[str] | None,
) -> dict[str, Any]:
    """Process date sentinel values from a due_dates override entry.

    Returns a dict of date keys to include in the Canvas API call.
    """
    result: dict[str, Any] = {}
    empty_fields: list[str] = []
    for dk in _DATE_KEYS:
        val = override.get(dk, "")
        if isinstance(val, str) and val.strip().lower() in _DATE_SENTINELS:
            sentinel = val.strip().lower()
            if sentinel == "none":
                result[dk] = ""
            elif sentinel == "keep":
                pass
            elif sentinel == "create_none_then_keep":
                if canvas_id is None:
                    result[dk] = ""
        elif val:
            result[dk] = val
        else:
            empty_fields.append(dk)
    if empty_fields:
        fields_str = ", ".join(empty_fields)
        msg = (
            f"WARNING: {local_key}: due_dates entry has empty value for {fields_str} "
            f"— treating as KEEP (use 'KEEP', 'NONE', 'CREATE_NONE_THEN_KEEP', or a date value to suppress this warning)"
        )
        print(f"  {msg}")
        if errors is not None:
            errors.append(msg)
    return result


def _date_rejection_message(
    local_key: str, title: str, due_at: str | None, html_url: str | None
) -> str:
    date_str = due_at or "(unknown)"
    parts = [
        f"ERROR: {local_key}: Could not set due date to {date_str} for \"{title}\"",
        " — it must fall between the Available From (unlock_at) and Available Until (lock_at)"
        " dates already set in Canvas.",
        " Content was synced without dates.",
    ]
    if html_url:
        parts.append(f" Check/fix in Canvas: {html_url}")
    parts.append(
        " (due date comes from the due_dates table in course_settings.toml,"
        " or from the file's frontmatter)"
    )
    return "".join(parts)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_text). Body excludes the frontmatter block."""
    if not text.startswith("---\n"):
        return {}, text
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return {}, text
    return yaml.safe_load(text[4:end]) or {}, text[end + 5 :]


def _file_referenced_snippets(path: Path, snippets_dir: Path) -> set[Path]:
    """Snippets referenced by a file's body, for staleness checks only.

    Swallows malformed frontmatter rather than raising — if the file does
    turn out to need a real sync, its own processing path reports the error.
    """
    try:
        _, body = parse_frontmatter(path.read_text())
    except yaml.YAMLError:
        return set()
    return find_referenced_snippets(body, path, snippets_dir)


def _question_files_referenced_snippets(questions_dir: Path, snippets_dir: Path) -> set[Path]:
    """Union of snippets referenced by every question file in questions_dir."""
    if not questions_dir.exists():
        return set()
    found: set[Path] = set()
    for q_path in questions_dir.glob("*.md"):
        found |= _file_referenced_snippets(q_path, snippets_dir)
    return found


_MODULE_LINK_RE = re.compile(r"^(\s*)-\s+\[([^\]]+)\]\(([^)]+)\)")
_MODULE_LINK_TITLE_RE = re.compile(r'''^(.*?)\s+["'].*["']\s*$''')
_MODULE_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)")
_MODULE_PLAIN_LIST_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.])\s+(.+)")
_INDENT_SPACES_PER_LEVEL = 2
MAX_CANVAS_INDENT = 5
_ITEM_ATTRS_RE = re.compile(r"<!--(.*?)-->")
_ITEM_ATTR_KV_RE = re.compile(r'(\w+)=["\']([^"\']*)["\']')
_RAW_NONHTML_BLOCK_RE = re.compile(
    r"^```\{=(?!html\})[\w-]+\}\s*\n.*?^```\s*\n?", re.MULTILINE | re.DOTALL
)


def _parse_item_attrs(comment_text: str) -> dict[str, str]:
    """Parse key="value" pairs from an HTML comment string."""
    return {m.group(1): m.group(2) for m in _ITEM_ATTR_KV_RE.finditer(comment_text)}


def _find_nested_key(obj: Any, target: str) -> str | None:
    """Return a dotted path to `target` if it appears anywhere inside obj, else None.

    Used to detect a top-level key (e.g. tab_configuration) that the user accidentally
    nested under a TOML [section] header.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == target:
                return key
            found = _find_nested_key(value, target)
            if found:
                return f"{key}.{found}"
    elif isinstance(obj, list):
        for item in obj:
            found = _find_nested_key(item, target)
            if found:
                return found
    return None


def parse_module_body(
    body: str, module_file: Path, course_root: Path
) -> list[dict[str, Any]]:
    """Parse a module body into an ordered list of item dicts."""
    body = _RAW_NONHTML_BLOCK_RE.sub("", body)
    items: list[dict[str, Any]] = []
    for line in body.splitlines():
        link_m = _MODULE_LINK_RE.match(line)
        if link_m:
            leading_spaces = len(link_m.group(1))
            indent = leading_spaces // _INDENT_SPACES_PER_LEVEL
            title, href = link_m.group(2), link_m.group(3)
            title_m = _MODULE_LINK_TITLE_RE.match(href)
            if title_m:
                href = title_m.group(1)
            if indent > MAX_CANVAS_INDENT:
                print(
                    f"  WARNING: indent level {indent} exceeds Canvas maximum"
                    f" ({MAX_CANVAS_INDENT}); clamping: {title}"
                )
                indent = MAX_CANVAS_INDENT
            attrs_m = _ITEM_ATTRS_RE.search(line)
            attrs = _parse_item_attrs(attrs_m.group(1)) if attrs_m else {}
            published_explicit = "published" in attrs
            published = attrs.get("published", "true").lower() != "false"

            # Detect absolute URLs → ExternalUrl item
            if href.startswith("http://") or href.startswith("https://"):
                target = attrs.get("target", "")
                new_tab = target != "_self"
                items.append(
                    {
                        "type": "ExternalUrl",
                        "title": title,
                        "url": href,
                        "new_tab": new_tab,
                        "indent": indent,
                        "published": published,
                    }
                )
            else:
                resolved = (module_file.parent / href).resolve()
                local_path = resolved.relative_to(course_root.resolve()).as_posix()
                items.append(
                    {
                        "type": "content", "title": title, "local_path": local_path,
                        "indent": indent,
                        # None (not explicit) is resolved in _sync_module from the
                        # referenced content file's own `published` frontmatter —
                        # Canvas republishes an item's content when a module item
                        # pointing at it is (re-)created with published=true, so
                        # defaulting to True here would silently re-publish content
                        # whose own frontmatter says published: false.
                        "published": published if published_explicit else None,
                    }
                )
            continue
        header_m = _MODULE_HEADER_RE.match(line)
        if header_m:
            items.append({"type": "SubHeader", "title": header_m.group(1),
                         "indent": 0})
            continue
        plain_m = _MODULE_PLAIN_LIST_RE.match(line)
        if plain_m:
            leading_spaces = len(plain_m.group(1))
            indent = leading_spaces // _INDENT_SPACES_PER_LEVEL + 1
            if indent > MAX_CANVAS_INDENT:
                print(
                    f"  WARNING: indent level {indent} exceeds Canvas maximum"
                    f" ({MAX_CANVAS_INDENT}); clamping: {plain_m.group(2).strip()}"
                )
                indent = MAX_CANVAS_INDENT
            items.append({"type": "SubHeader",
                         "title": plain_m.group(2).strip(), "indent": indent})
    return items


def _content_default_published(repo_root: Path, local_path: str, snippets_dir: Path) -> bool:
    """Published default for a module item with no explicit override.

    Mirrors content files' own `published` frontmatter so that re-syncing a
    module doesn't republish content the user explicitly unpublished.  Assets
    (non-.md targets, e.g. File items) have no such frontmatter, so they keep
    the historical default of True.
    """
    ref_path = repo_root / local_path
    if ref_path.suffix != ".md" or not ref_path.exists():
        return True
    try:
        ref_frontmatter, ref_body = parse_frontmatter(ref_path.read_text())
        ref_frontmatter, _ = expand_frontmatter_snippets(
            ref_frontmatter, ref_body, ref_path, snippets_dir
        )
    except yaml.YAMLError:
        return True
    return bool(ref_frontmatter.get("published", False))


def check_title_collisions(
    md_files: list[Path],
    repo_root: Path,
) -> list[str]:
    """Detect content files that would collide in Canvas's flat namespace.

    Canvas identifies pages by title (slug) and assignments/discussions by title,
    so two files with the same title (from frontmatter, or filename stem as
    fallback) would overwrite each other.  Returns a list of error strings,
    one per collision group.
    """
    from collections import defaultdict

    title_to_paths: dict[str, list[str]] = defaultdict(list)
    for md_file in md_files:
        try:
            fm, _ = parse_frontmatter(md_file.read_text())
        except Exception:
            fm = {}
        title = fm.get("title", md_file.stem)
        folder = md_file.relative_to(repo_root).parts[0]
        key = f"{folder}::{title}"
        title_to_paths[key].append(
            md_file.relative_to(repo_root).as_posix()
        )

    errors: list[str] = []
    for key, paths in title_to_paths.items():
        if len(paths) > 1:
            folder, title = key.split("::", 1)
            errors.append(
                f"ERROR: title collision in {folder}/: {len(paths)} files "
                f"share title \"{title}\": {', '.join(paths)}"
            )
    return errors


def _canvas_is_newer(
    course,
    local_key: str,
    local_mtime: datetime,
    manifest: manifest_lib.ManifestDict,
    newer_on_canvas: list[str],
) -> bool:
    """Check if the Canvas version of an existing item is newer than the local file.

    Returns True (and appends to newer_on_canvas) if Canvas should be preserved.
    Only called when force_overwrite is False and the item already exists in the manifest.
    """
    entry = manifest.get(local_key)
    if entry is None:
        return False
    canvas_type = entry.get("canvas_type")
    identifier = (
        entry.get("canvas_url") if canvas_type == "page" else entry.get("canvas_id")
    )
    if identifier is None:
        return False
    canvas_ts = capi.get_canvas_updated_at(course, canvas_type, identifier)
    if canvas_ts is not None and canvas_ts > local_mtime:
        print(f"Skipping (Canvas is newer): {local_key}")
        newer_on_canvas.append(local_key)
        return True
    return False


def sync_syllabus(
    course,
    repo_path: Path,
    manifest: dict,
    manifest_path: Path,
    course_id: int,
    errors: list[str] | None = None,
    force_uploads: bool = False,
    verbose: bool = False,
) -> None:
    """Upload course_settings/syllabus.md as the Canvas course syllabus body."""
    syllabus_md = repo_path / "course_settings" / "syllabus.md"
    if not syllabus_md.exists():
        return
    local_key = "course_settings/syllabus.md"
    if not manifest_lib.needs_sync(manifest, local_key, syllabus_md, force_uploads):
        if verbose:
            print(f"Skipping (up-to-date): {local_key}")
        return
    print("Syncing syllabus...")
    try:
        frontmatter, body = parse_frontmatter(syllabus_md.read_text())
    except yaml.YAMLError as exc:
        msg = f"WARNING: course_settings/syllabus.md: malformed frontmatter: {exc}"
        print(f"  {msg}")
        if errors is not None:
            errors.append(msg)
        return
    snippets_dir = repo_path / "snippets"
    body = preprocess_snippets(body, syllabus_md, snippets_dir, errors)
    html = markdown_to_html(body.strip()) if body.strip() else ""
    error_count_before = len(errors) if errors is not None else 0
    # Stub creator is not needed for the syllabus; pass a no-op
    html = rewrite_links(
        html, syllabus_md, repo_path, manifest, course_id, lambda *_: {}, errors
    )
    if errors is not None and len(errors) > error_count_before:
        print(f"  Skipping upload due to errors: {local_key}")
        return
    course.update(course={"syllabus_body": html})
    manifest_lib.record(manifest, manifest_path, local_key, course_id, "syllabus")


def sync_course_settings(
    course,
    repo_path: Path,
    manifest: dict,
    manifest_path: Path,
    force_uploads: bool = False,
    verbose: bool = False,
) -> tuple[dict[str, int], bool]:
    """Sync course_settings.toml: metadata, grading standards, assignment groups, policies, rubrics.

    Returns ({rubric_title: canvas_id}, settings_synced) where settings_synced is
    True when course_settings.toml was actually re-uploaded this run.
    """
    settings_path = repo_path / "course_settings" / "course_settings.toml"
    rubrics_path = repo_path / "course_settings" / "rubrics.toml"
    settings_key = "course_settings/course_settings.toml"
    rubrics_key = "course_settings/rubrics.toml"

    settings_stale = settings_path.exists() and manifest_lib.needs_sync(
        manifest, settings_key, settings_path, force_uploads
    )
    rubrics_stale = rubrics_path.exists() and manifest_lib.needs_sync(
        manifest, rubrics_key, rubrics_path, force_uploads
    )

    if not settings_stale and not rubrics_stale:
        if verbose and settings_path.exists():
            print(f"Skipping (up-to-date): {settings_key}")
        try:
            return capi.get_rubric_ids(course), False
        except Exception:
            return {}, False

    if settings_stale:
        print("Syncing course settings...")
        with settings_path.open("rb") as fh:
            settings = tomllib.load(fh)

        grading_standards = settings.get("grading_standards", [])
        assignment_groups = settings.get("assignment_groups", [])
        late_policy = settings.get("late_policy", {})
        default_post_policy = settings.get("default_post_policy", {})

        # §1c: grading standards (needed before §1a so we can set grading_standard_id)
        gs_id = capi.sync_grading_standards(course, grading_standards)

        # §1a: core course metadata
        capi.update_course_metadata(course, settings, grading_standard_id=gs_id)

        # Dashboard image
        dashboard_image = settings.get("dashboard_image")
        if dashboard_image:
            image_path = repo_path / dashboard_image
            if image_path.exists():
                print(f"  Uploading dashboard image: {dashboard_image}")
                try:
                    capi.upload_course_image(course, image_path)
                except Exception as exc:
                    print(f"  WARNING: dashboard image upload failed: {exc}")
            else:
                print(f"  WARNING: dashboard_image file not found: {dashboard_image}")

        # §1d: assignment groups
        capi.sync_assignment_groups(course, assignment_groups)

        # §1e: late policy
        if late_policy:
            try:
                capi.update_late_policy(course, late_policy)
            except Exception as exc:
                print(
                    f"  WARNING: late policy update failed (late_policy={late_policy!r}): {exc}"
                )

        # §1b: default post policy
        if "post_manually" in default_post_policy:
            post_manually = default_post_policy["post_manually"]
            try:
                capi.update_post_policy(course, post_manually)
            except Exception as exc:
                print(
                    f"  WARNING: post policy update failed (post_manually={post_manually!r}): {exc}"
                )

        # Course-navigation (left sidebar) order/visibility.
        tab_config_raw = settings.get("tab_configuration")
        if tab_config_raw is None:
            misplaced = _find_nested_key(settings, "tab_configuration")
            if misplaced:
                print(
                    f"  WARNING: 'tab_configuration' was found nested under {misplaced!r}, "
                    "not at the top level, so course navigation was NOT updated. In TOML a "
                    "top-level key must come BEFORE any [section]/[[section]] headers — move "
                    "the tab_configuration block above the first section in course_settings.toml."
                )
        elif tab_config_raw:
            try:
                tab_config = (
                    json.loads(tab_config_raw)
                    if isinstance(tab_config_raw, str)
                    else tab_config_raw
                )
            except (json.JSONDecodeError, TypeError) as exc:
                print(f"  WARNING: tab_configuration is not valid JSON; skipping: {exc}")
                tab_config = None
            if tab_config:
                try:
                    capi.sync_tab_configuration(course, tab_config)
                except Exception as exc:
                    print(f"  WARNING: tab configuration sync failed: {exc}")

        manifest_lib.record(manifest, manifest_path, settings_key, 0, "course_settings")

    # §15: rubrics (tracked independently from course_settings.toml)
    rubric_ids: dict[str, int] = {}
    if rubrics_stale:
        print("Syncing rubrics...")
        with rubrics_path.open("rb") as fh:
            rubrics_data = tomllib.load(fh)
        rubrics = rubrics_data.get("rubrics", [])
        if rubrics:
            try:
                rubric_ids, created = capi.sync_rubrics(course, rubrics)
                if created:
                    for t in created:
                        print(f"  Created rubric: {t}")
                    print(
                        "  WARNING: Re-run with --force-uploads to re-create rubric "
                        "associations on assignments that reference these rubrics."
                    )
            except Exception as exc:
                print(f"  WARNING: rubrics sync failed: {exc}")
        manifest_lib.record(manifest, manifest_path, rubrics_key, 0, "rubrics")
    if not rubric_ids:
        try:
            rubric_ids = capi.get_rubric_ids(course)
        except Exception:
            pass

    return rubric_ids, settings_stale


def run_sync(
    config: Config,
    repo_path: Path,
    force_uploads: bool = False,
    force_overwrite: bool = False,
    verbose: bool = False,
) -> bool:
    """Main sync pipeline: assets → content → modules. Returns True if any errors occurred."""
    manifest_path = repo_path / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    matcher = load_ignore_matcher(repo_path)
    course = capi.get_course(config)
    snippets_dir = repo_path / "snippets"
    newer_on_canvas: list[str] = []
    errors: list[str] = []

    # Read front_page setting up front so we can apply it after pages are synced.
    _cs_path = repo_path / "course_settings" / "course_settings.toml"
    _front_page_path: str | None = None
    if _cs_path.exists():
        with _cs_path.open("rb") as _fh:
            _front_page_path = tomllib.load(_fh).get("front_page")

    # 0. Course settings (metadata, grading standards, assignment groups, policies, rubrics)
    rubric_ids, settings_synced = sync_course_settings(course, repo_path, manifest, manifest_path, force_uploads, verbose=verbose)

    # Fetch assignment group IDs once so assignments can reference groups by name
    assignment_group_ids = capi.get_assignment_group_ids(course)

    # Load centralized due dates for override
    due_dates = load_due_dates(repo_path)

    # 0.5. Syllabus
    sync_syllabus(
        course,
        repo_path,
        manifest,
        manifest_path,
        config.course_id,
        errors,
        force_uploads,
        verbose=verbose,
    )

    # 1. Assets (depth-first, files before subdirs, alphabetical)
    assets_dir = repo_path / "assets"
    if assets_dir.exists():
        _walk_assets(
            course,
            assets_dir,
            assets_dir,
            repo_path,
            manifest,
            manifest_path,
            force_uploads,
            force_overwrite,
            newer_on_canvas,
            matcher=matcher,
            verbose=verbose,
        )

    # 2. Content folders (alphabetical, excl. assets, modules, quizzes, snippets, course_settings, hidden)
    synced_content_keys: set[str] = set()
    skip = {
        "assets",
        "modules",
        "quizzes",
        "snippets",
        "course_settings",
        "question_banks",
    }
    content_dirs = sorted(
        d
        for d in repo_path.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and d.name not in skip
        and not matcher.is_ignored(d, repo_path)
    )
    # Collect all content .md files (recursively, to support subfolders) and
    # check for title collisions before uploading anything.
    all_content_files: list[Path] = []
    for content_dir in content_dirs:
        for md_file in sorted(content_dir.rglob("*.md")):
            if matcher.is_ignored(md_file, repo_path):
                if verbose:
                    print(f"Ignoring: {md_file.relative_to(repo_path).as_posix()}")
                continue
            all_content_files.append(md_file)

    collision_errors = check_title_collisions(all_content_files, repo_path)
    if collision_errors:
        for msg in collision_errors:
            print(f"  {msg}")
            errors.append(msg)
        print("\nAborting: resolve title collisions before syncing.")
        _print_errors_summary(errors)
        return True

    for md_file in all_content_files:
        _sync_content_file(
            course,
            md_file,
            repo_path,
            snippets_dir,
            manifest,
            manifest_path,
            config.course_id,
            force_uploads,
            force_overwrite,
            newer_on_canvas,
            errors,
            assignment_group_ids=assignment_group_ids,
            rubric_ids=rubric_ids,
            synced_keys=synced_content_keys,
            verbose=verbose,
            due_dates=due_dates,
        )

    # 2.5. Quizzes (each quiz lives in its own sub-folder)
    quizzes_dir = repo_path / "quizzes"
    if quizzes_dir.exists():
        for quiz_folder in sorted(d for d in quizzes_dir.iterdir() if d.is_dir()):
            if matcher.is_ignored(quiz_folder, repo_path):
                if verbose:
                    print(f"Ignoring: {quiz_folder.relative_to(repo_path).as_posix()}")
                continue
            quiz_md = quiz_folder / f"{quiz_folder.name}.md"
            if quiz_md.exists():
                _sync_quiz(
                    course,
                    quiz_folder,
                    quiz_md,
                    repo_path,
                    manifest,
                    manifest_path,
                    config.course_id,
                    force_uploads,
                    force_overwrite,
                    newer_on_canvas,
                    errors,
                    synced_keys=synced_content_keys,
                    verbose=verbose,
                    due_dates=due_dates,
                    assignment_group_ids=assignment_group_ids,
                )

    # 2.6. Question banks
    _sync_question_banks(
        course, repo_path, manifest, manifest_path, force_uploads, matcher=matcher, verbose=verbose
    )

    # 2.7. Front page (only when course_settings.toml or the target page was re-synced)
    if _front_page_path and (
        settings_synced or _front_page_path in synced_content_keys
    ):
        entry = manifest.get(_front_page_path)
        if entry and "canvas_url" in entry:
            print(f"Setting front page: {_front_page_path}")
            try:
                capi.set_front_page(course, entry["canvas_url"])
            except Exception as exc:
                if "unpublished" in str(exc).lower():
                    msg = (
                        f"front_page '{_front_page_path}' must be published before it can be set "
                        f"as the front page. Add the following to its frontmatter and re-run:\n"
                        f"    published: true"
                    )
                else:
                    msg = f"set front page failed for '{_front_page_path}': {exc}"
                print(f"  WARNING: {msg}")
                errors.append(msg)
        else:
            msg = f"front_page '{_front_page_path}' not found in manifest — sync the page first"
            print(f"  WARNING: {msg}")
            errors.append(msg)

    # 3. Modules (alphabetical, with optional explicit position from module_order.toml)
    unpublishable_items: list[tuple[str, str]] = []
    _order_key = "course_settings/module_order.toml"
    _order_path = repo_path / _order_key
    position_map = _load_module_order(repo_path)
    order_changed = _order_path.exists() and manifest_lib.needs_sync(
        manifest, _order_key, _order_path, force_uploads
    )
    modules_dir = repo_path / "modules"
    if modules_dir.exists():
        # Determine which modules reference content that was just synced this run.
        modules_with_updated_refs: set[Path] = set()
        if synced_content_keys:
            for md_file in modules_dir.glob("*.md"):
                if matcher.is_ignored(md_file, repo_path):
                    continue
                try:
                    _, body = parse_frontmatter(md_file.read_text())
                except yaml.YAMLError:
                    continue
                body = preprocess_snippets(body, md_file, snippets_dir)
                items = parse_module_body(body, md_file, repo_path)
                refs = {i["local_path"] for i in items if i["type"] == "content"}
                if refs & synced_content_keys:
                    modules_with_updated_refs.add(md_file)

        for md_file in sorted(modules_dir.glob("*.md")):
            if matcher.is_ignored(md_file, repo_path):
                if verbose:
                    print(f"Ignoring: {md_file.relative_to(repo_path).as_posix()}")
                continue
            position = position_map.get(md_file.name)
            force_this = (
                force_uploads
                or md_file in modules_with_updated_refs
            )
            had_module_warnings = _sync_module(
                course,
                md_file,
                repo_path,
                manifest,
                manifest_path,
                force_this,
                force_overwrite,
                newer_on_canvas,
                position=position,
                verbose=verbose,
                unpublishable_items=unpublishable_items,
                errors=errors,
            )
            if had_module_warnings:
                errors.append(f"module {md_file.name}: some items could not be added")
    if order_changed:
        _reorder_modules(course, position_map, repo_path, manifest, errors)
        manifest_lib.record(manifest, manifest_path, _order_key, 0, "module_order")

    if due_dates and settings_synced:
        _apply_due_dates_only(
            course, due_dates, repo_path, manifest, synced_content_keys,
            errors, matcher,
        )

    if due_dates:
        _check_due_dates_coverage(due_dates, repo_path, matcher)

    _print_newer_on_canvas_summary(newer_on_canvas)
    _print_unpublishable_summary(unpublishable_items)
    _print_errors_summary(errors)
    return bool(errors)


def _entry_resource_key(canvas_type: str, entry: dict[str, Any]) -> ResourceKey | None:
    """Map a manifest entry to the ResourceKey used for in-use detection.

    Pages are keyed by URL slug; everything else by integer Canvas id. Returns
    None for entries with no addressable Canvas object (or missing fields).
    """
    if canvas_type == "page":
        url = entry.get("canvas_url")
        return ResourceKey("page", url) if url else None
    if canvas_type in ("assignment", "discussion", "quiz", "file", "module"):
        cid = entry.get("canvas_id")
        return ResourceKey(canvas_type, int(cid)) if cid is not None else None
    return None


def _in_use_resources(course) -> dict[ResourceKey, str]:
    """Resources Canvas is actively using outside of modules, mapped to a reason.

    Covers the course front page and anything linked from the syllabus body.
    Prune must not delete/unpublish these even when their local source file is
    gone. Front page wins over syllabus when a page is both.
    """
    in_use: dict[ResourceKey, str] = {}

    # Syllabus first so the front page reason takes precedence on overlap.
    try:
        response = course._requester.request(
            "GET",
            f"courses/{course.id}",
            _kwargs=[("include[]", "syllabus_body")],
        )
        syllabus_body = response.json().get("syllabus_body", "") or ""
        for ref in extract_canvas_refs(syllabus_body):
            in_use[ref] = "syllabus"
    except Exception:
        pass

    try:
        front_page = course.show_front_page()
        url = getattr(front_page, "url", None) if front_page else None
        if isinstance(url, str) and url:
            in_use[ResourceKey("page", url)] = "front page"
    except Exception:
        pass

    return in_use


def run_prune(config: Config, repo_path: Path, mode: str) -> bool:
    """Delete or unpublish Canvas items whose local source file no longer exists.

    An entry is orphaned when its local file (repo_path / local_key) is gone — which
    happens on both delete and rename. `mode` is "delete", "unpublish", or "manifest".

    In "manifest" mode, orphaned entries are simply removed from the local manifest
    and Canvas is never contacted — an escape hatch for entries left stranded by a
    manual Canvas cleanup, an unsupported type, or an in-use protection.

    For "delete"/"unpublish", types with no standalone deletable/unpublishable Canvas
    object (and question banks, which have no unpublish concept) are skipped with a
    warning and keep their manifest entry. Returns True if any errors occurred.
    """
    manifest_path = repo_path / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)

    orphans = [
        (key, entry)
        for key, entry in manifest.items()
        if not (repo_path / key).exists()
    ]
    if not orphans:
        print("No orphaned manifest entries found; nothing to prune.")
        return False

    if mode == "manifest":
        for key, _entry in orphans:
            del manifest[key]
            print(f"  Removed manifest entry: {key}")
        manifest_lib.flush(manifest_path, manifest)
        noun = "entry" if len(orphans) == 1 else "entries"
        print(
            f"\nPrune complete: {len(orphans)} manifest {noun} removed "
            f"(Canvas untouched)."
        )
        return False

    past = "deleted" if mode == "delete" else "unpublished"
    action = capi.delete_content if mode == "delete" else capi.unpublish_content
    supported_types = (
        capi.DELETABLE_TYPES if mode == "delete" else capi.UNPUBLISHABLE_TYPES
    )

    course = capi.get_course(config)
    in_use = _in_use_resources(course)

    pruned: list[str] = []
    skipped: list[str] = []
    protected: list[str] = []
    errors: list[str] = []

    for key, entry in orphans:
        canvas_type = entry.get("canvas_type", "")
        resource_key = _entry_resource_key(canvas_type, entry)
        reason = in_use.get(resource_key) if resource_key is not None else None
        if reason is not None:
            print(f"  Skipping (in use as {reason}): {key}")
            protected.append(key)
            continue
        if canvas_type not in supported_types:
            print(f"  Skipping (cannot {mode} type '{canvas_type}'): {key}")
            skipped.append(key)
            continue
        try:
            existed = action(course, canvas_type, entry)
        except Exception as exc:
            msg = f"failed to {mode} {key}: {exc}"
            print(f"  WARNING: {msg}")
            errors.append(msg)
            continue
        if existed:
            print(f"  {past.capitalize()} on Canvas: {key}")
        else:
            print(f"  Does not exist on Canvas: {key}")
        del manifest[key]
        manifest_lib.flush(manifest_path, manifest)
        pruned.append(key)

    print(
        f"\nPrune complete: {len(pruned)} {past}, "
        f"{len(protected)} kept (in use), "
        f"{len(skipped)} skipped, {len(errors)} errors."
    )
    _print_errors_summary(errors)
    return bool(errors)


def _walk_assets(
    course,
    dir_path: Path,
    assets_root: Path,
    repo_root: Path,
    manifest,
    manifest_path,
    force_uploads: bool = False,
    force_overwrite: bool = False,
    newer_on_canvas: list[str] | None = None,
    matcher: IgnoreMatcher | None = None,
    verbose: bool = False,
) -> None:
    if newer_on_canvas is None:
        newer_on_canvas = []
    entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_dir(), p.name))
    for entry in entries:
        if matcher is not None and matcher.is_ignored(entry, repo_root):
            if verbose:
                print(f"Ignoring: {entry.relative_to(repo_root).as_posix()}")
            continue
        if entry.is_file():
            local_key = entry.relative_to(repo_root).as_posix()
            if not manifest_lib.needs_sync(manifest, local_key, entry, force_uploads):
                continue
            if not force_overwrite:
                local_mtime = datetime.fromtimestamp(
                    entry.stat().st_mtime, tz=timezone.utc
                )
                if _canvas_is_newer(
                    course, local_key, local_mtime, manifest, newer_on_canvas
                ):
                    continue
            print(f"Uploading asset: {local_key}")
            canvas_entry = capi.upload_asset(course, entry, assets_root)
            manifest_lib.record(
                manifest,
                manifest_path,
                local_key,
                canvas_entry["canvas_id"],
                "file",
                extra={"canvas_url": canvas_entry["canvas_url"]},
            )
        elif entry.is_dir():
            _walk_assets(
                course,
                entry,
                assets_root,
                repo_root,
                manifest,
                manifest_path,
                force_uploads,
                force_overwrite,
                newer_on_canvas,
                matcher=matcher,
                verbose=verbose,
            )


def _sync_content_file(
    course,
    md_file: Path,
    repo_root: Path,
    snippets_dir: Path,
    manifest,
    manifest_path,
    course_id: int,
    force_uploads: bool = False,
    force_overwrite: bool = False,
    newer_on_canvas: list[str] | None = None,
    errors: list[str] | None = None,
    assignment_group_ids: dict[str, int] | None = None,
    rubric_ids: dict[str, int] | None = None,
    synced_keys: set[str] | None = None,
    verbose: bool = False,
    due_dates: list[dict[str, Any]] | None = None,
) -> None:
    if newer_on_canvas is None:
        newer_on_canvas = []
    local_key = md_file.relative_to(repo_root).as_posix()

    if not manifest_lib.needs_sync(
        manifest, local_key, md_file, force_uploads,
        extra_mtime_paths=lambda: _file_referenced_snippets(md_file, snippets_dir),
    ):
        if verbose:
            print(f"Skipping (up-to-date): {local_key}")
        return

    if not force_overwrite:
        local_mtime = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)
        for snippet_path in _file_referenced_snippets(md_file, snippets_dir):
            snippet_mtime = datetime.fromtimestamp(snippet_path.stat().st_mtime, tz=timezone.utc)
            if snippet_mtime > local_mtime:
                local_mtime = snippet_mtime
        if _canvas_is_newer(course, local_key, local_mtime, manifest, newer_on_canvas):
            return

    print(f"Processing: {local_key}")

    try:
        frontmatter, body = parse_frontmatter(md_file.read_text())
    except yaml.YAMLError as exc:
        hint = ""
        if "mapping values are not allowed here" in str(exc):
            hint = " (hint: values containing colons must be quoted, e.g. title: \"Unit 01: Intro\")"
        msg = f"WARNING: {local_key}: malformed frontmatter{hint}: {exc}"
        print(f"  {msg}")
        if errors is not None:
            errors.append(msg)
        return
    error_count_before_frontmatter_snippets = len(errors) if errors is not None else 0
    frontmatter, body = expand_frontmatter_snippets(frontmatter, body, md_file, snippets_dir, errors)
    if errors is not None and len(errors) > error_count_before_frontmatter_snippets:
        print(f"  Skipping upload due to errors: {local_key}")
        return
    error_count_before_snippets = len(errors) if errors is not None else 0
    body = preprocess_snippets(body, md_file, snippets_dir, errors)
    if errors is not None and len(errors) > error_count_before_snippets:
        print(f"  Skipping upload due to errors: {local_key}")
        return
    html = markdown_to_html(body)

    if re.search(r"<h1[\s>]", html, re.IGNORECASE):
        msg = (
            f"ERROR: {local_key}: contains <h1> heading — "
            f"Canvas silently converts H1 to a styled paragraph; "
            f"use H2 or deeper instead"
        )
        print(f"  {msg}")
        if errors is not None:
            errors.append(msg)
        return

    canvas_type = infer_canvas_type(local_key)

    def stub_creator(ref_local_path: str, ref_canvas_type: str) -> dict[str, Any]:
        title = Path(ref_local_path).stem.replace("-", " ").replace("_", " ").title()
        print(f"  Stub-creating: {ref_local_path} (referenced but not yet synced)")
        entry = capi.create_stub(course, ref_canvas_type, title)
        extra = {
            k: v for k, v in entry.items() if k not in ("canvas_id", "canvas_type")
        }
        manifest_lib.record(
            manifest,
            manifest_path,
            ref_local_path,
            entry["canvas_id"],
            ref_canvas_type,
            extra=extra or None,
        )
        return entry

    error_count_before = len(errors) if errors is not None else 0
    html = rewrite_links(
        html, md_file, repo_root, manifest, course_id, stub_creator, errors
    )
    if errors is not None and len(errors) > error_count_before:
        print(f"  Skipping upload due to errors: {local_key}")
        return

    existing = manifest.get(local_key)
    title = frontmatter.get("title", md_file.stem)
    published = frontmatter.get("published", False)

    print(f"  Uploading: {local_key}")
    if canvas_type == "page":
        canvas_url = existing.get("canvas_url") if existing else None
        entry = capi.create_or_update_page(
            course,
            canvas_url,
            title,
            html,
            published=published,
            editing_roles=frontmatter.get("editing_roles", "teachers"),
        )
        print(f"  Link: {entry['html_url']}")
        manifest_lib.record(
            manifest,
            manifest_path,
            local_key,
            entry["canvas_id"],
            "page",
            extra={"canvas_url": entry["canvas_url"]},
        )
        if synced_keys is not None:
            synced_keys.add(local_key)
    elif canvas_type == "assignment":
        canvas_id = existing["canvas_id"] if existing else None
        extra: dict[str, Any] = {}
        for key in (
            "points_possible",
            "due_at",
            "lock_at",
            "unlock_at",
            "submission_types",
            "allowed_extensions",
            "allowed_attempts",
            "grading_type",
            # Assignment group (grading category)
            "assignment_group_id",
            # Group assignment
            "group_category_id",
            "grade_group_students_individually",
            # Anonymous grading
            "anonymous_grading",
            # Moderated grading
            "moderated_grading",
            "grader_count",
            "final_grader_id",
            "grader_comments_visible_to_graders",
            "graders_anonymous_to_graders",
            "grader_names_visible_to_final_grader",
            # Peer reviews
            "peer_reviews",
            "automatic_peer_reviews",
            "peer_review_count",
            "peer_reviews_assign_at",
            "anonymous_peer_reviews",
            "intra_group_peer_reviews",
        ):
            if key in frontmatter:
                extra[key] = frontmatter[key]
        override = find_due_date_override(due_dates or [], title, "assignment")
        if override is not None:
            extra.update(
                _resolve_date_overrides(override, canvas_id, local_key, errors)
            )
        if "assignment_group_id" in extra:
            resolved = _resolve_assignment_group_id(
                extra["assignment_group_id"], assignment_group_ids, local_key, errors
            )
            if resolved is None:
                del extra["assignment_group_id"]
            else:
                extra["assignment_group_id"] = resolved
        uses_student_annotation = "student_annotation" in (
            frontmatter.get("submission_types") or []
        )
        if uses_student_annotation and "annotatable_attachment" not in frontmatter:
            msg = (
                f"WARNING: {local_key}: submission_types includes student_annotation "
                f"but annotatable_attachment is not set"
            )
            print(f"  {msg}")
            if errors is not None:
                errors.append(msg)
        elif "annotatable_attachment" in frontmatter:
            asset_path = frontmatter["annotatable_attachment"]
            if not (repo_root / asset_path).exists():
                msg = (
                    f"WARNING: {local_key}: annotatable_attachment '{asset_path}' "
                    f"does not exist on disk"
                )
                print(f"  {msg}")
                if errors is not None:
                    errors.append(msg)
            else:
                asset_entry = manifest.get(asset_path)
                if asset_entry is None or asset_entry.get("canvas_type") != "file":
                    msg = (
                        f"WARNING: {local_key}: annotatable_attachment '{asset_path}' not found "
                        f"in manifest — sync assets/ first, then re-run"
                    )
                    print(f"  {msg}")
                    if errors is not None:
                        errors.append(msg)
                else:
                    extra["annotatable_attachment_id"] = asset_entry["canvas_id"]
        entry = capi.create_or_update_assignment(
            course, canvas_id, title, html, published=published, **extra
        )
        if entry.get("date_warning"):
            msg = _date_rejection_message(
                local_key, title, extra.get("due_at"), entry.get("html_url")
            )
            print(f"  {msg}")
            if errors is not None:
                errors.append(msg)
        print(f"  Link: {entry['html_url']}")
        manifest_lib.record(
            manifest, manifest_path, local_key, entry["canvas_id"], "assignment"
        )
        rubric_ref = frontmatter.get("rubric")
        if rubric_ref is not None:
            rubric_canvas_id: int | None = None
            if isinstance(rubric_ref, str):
                resolved = (rubric_ids or {}).get(rubric_ref)
                if resolved is None:
                    known = list(rubric_ids.keys()) if rubric_ids else []
                    msg = (
                        f"WARNING: {local_key}: rubric '{rubric_ref}' not found on Canvas "
                        f"(known: {known}); skipping rubric association"
                    )
                    print(f"  {msg}")
                    if errors is not None:
                        errors.append(msg)
                else:
                    rubric_canvas_id = resolved
            else:
                rubric_canvas_id = int(rubric_ref)
            if rubric_canvas_id is not None:
                use_for_grading = frontmatter.get("use_for_grading", True)
                try:
                    capi.associate_rubric_with_assignment(
                        course, rubric_canvas_id, entry["canvas_id"], use_for_grading
                    )
                except Exception as exc:
                    msg = f"WARNING: {local_key}: rubric association failed: {exc}"
                    print(f"  {msg}")
                    if errors is not None:
                        errors.append(msg)
        elif entry.get("rubric_settings") is not None:
            rubric_settings = entry["rubric_settings"]
            rubric_id = (
                rubric_settings["id"]
                if isinstance(rubric_settings, dict)
                else rubric_settings.id
            )
            try:
                removed = capi.remove_rubric_from_assignment(
                    course, entry["canvas_id"], rubric_id
                )
                if removed:
                    print(f"  Removed rubric association: {local_key}")
            except Exception as exc:
                msg = f"WARNING: {local_key}: rubric removal failed: {exc}"
                print(f"  {msg}")
                if errors is not None:
                    errors.append(msg)
        if synced_keys is not None:
            synced_keys.add(local_key)
    elif canvas_type == "discussion":
        canvas_id = existing["canvas_id"] if existing else None
        extra = {}
        if "require_initial_post" in frontmatter:
            extra["require_initial_post"] = frontmatter["require_initial_post"]
        grading_keys = (
            "points_possible",
            "due_at",
            "lock_at",
            "unlock_at",
            # Assignment group (grading category)
            "assignment_group_id",
        )
        grading_params = {k: frontmatter[k] for k in grading_keys if k in frontmatter}
        override = find_due_date_override(due_dates or [], title, "discussion")
        if override is not None:
            grading_params.update(
                _resolve_date_overrides(override, canvas_id, local_key, errors)
            )
        if "assignment_group_id" in grading_params:
            resolved = _resolve_assignment_group_id(
                grading_params["assignment_group_id"],
                assignment_group_ids,
                local_key,
                errors,
            )
            if resolved is None:
                del grading_params["assignment_group_id"]
            else:
                grading_params["assignment_group_id"] = resolved
        if grading_params:
            extra["assignment"] = grading_params
        entry = capi.create_or_update_discussion(
            course, canvas_id, title, html, published=published, **extra
        )
        if entry.get("date_warning"):
            msg = _date_rejection_message(
                local_key, title, grading_params.get("due_at"), entry.get("html_url")
            )
            print(f"  {msg}")
            if errors is not None:
                errors.append(msg)
        print(f"  Link: {entry['html_url']}")
        manifest_lib.record(
            manifest, manifest_path, local_key, entry["canvas_id"], "discussion"
        )
        if synced_keys is not None:
            synced_keys.add(local_key)


def _load_module_order(repo_path: Path) -> dict[str, int]:
    """Return a filename→1-based-position map from course_settings/module_order.toml."""
    order_path = repo_path / "course_settings" / "module_order.toml"
    if not order_path.exists():
        return {}
    with order_path.open("rb") as fh:
        data = tomllib.load(fh)
    return {name: i for i, name in enumerate(data.get("order", []), start=1)}


def _reorder_modules(
    course,
    position_map: dict[str, int],
    repo_path: Path,
    manifest: dict,
    errors: list[str] | None,
) -> None:
    """Set module positions on Canvas without re-syncing content."""
    modules_dir = repo_path / "modules"
    for filename, position in position_map.items():
        local_key = f"modules/{filename}"
        local_path = modules_dir / filename
        if not local_path.exists():
            msg = f"module_order.toml lists '{filename}' but it was not found locally"
            print(f"  WARNING: {msg}")
            if errors is not None:
                errors.append(msg)
            continue
        entry = manifest.get(local_key)
        if entry is None or "canvas_id" not in entry:
            msg = f"module_order.toml lists '{filename}' but it has not been synced to Canvas yet"
            print(f"  WARNING: {msg}")
            if errors is not None:
                errors.append(msg)
            continue
        print(f"Reordering module: {local_key} → position {position}")
        capi.reposition_module(course, entry["canvas_id"], position)


def _sync_module(
    course,
    md_file: Path,
    repo_root: Path,
    manifest,
    manifest_path,
    force_uploads: bool = False,
    force_overwrite: bool = False,
    newer_on_canvas: list[str] | None = None,
    position: int | None = None,
    verbose: bool = False,
    unpublishable_items: list[tuple[str, str]] | None = None,
    errors: list[str] | None = None,
) -> bool:
    if newer_on_canvas is None:
        newer_on_canvas = []
    local_key = md_file.relative_to(repo_root).as_posix()
    snippets_dir = repo_root / "snippets"

    if not manifest_lib.needs_sync(
        manifest, local_key, md_file, force_uploads,
        extra_mtime_paths=lambda: _file_referenced_snippets(md_file, snippets_dir),
    ):
        if verbose:
            print(f"Skipping (up-to-date): {local_key}")
        return False

    if not force_overwrite:
        local_mtime = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)
        for snippet_path in _file_referenced_snippets(md_file, snippets_dir):
            snippet_mtime = datetime.fromtimestamp(snippet_path.stat().st_mtime, tz=timezone.utc)
            if snippet_mtime > local_mtime:
                local_mtime = snippet_mtime
        if _canvas_is_newer(course, local_key, local_mtime, manifest, newer_on_canvas):
            return False

    print(f"Syncing module: {local_key}")

    try:
        frontmatter, body = parse_frontmatter(md_file.read_text())
    except yaml.YAMLError as exc:
        hint = ""
        if "mapping values are not allowed here" in str(exc):
            hint = " (hint: values containing colons must be quoted, e.g. title: \"Unit 01: Intro\")"
        msg = f"WARNING: {local_key}: malformed frontmatter{hint}: {exc}"
        print(f"  {msg}")
        if errors is not None:
            errors.append(msg)
        return False
    frontmatter, body = expand_frontmatter_snippets(frontmatter, body, md_file, snippets_dir, errors)
    body = preprocess_snippets(body, md_file, snippets_dir)
    items = parse_module_body(body, md_file, repo_root)
    for item in items:
        if item.get("type") == "content" and item.get("published") is None:
            item["published"] = _content_default_published(repo_root, item["local_path"], snippets_dir)

    existing = manifest.get(local_key)
    title = frontmatter.get("title", md_file.stem)

    module_kwargs: dict[str, Any] = {}
    for key in ("published", "unlock_at", "require_sequential_progress"):
        if key in frontmatter:
            module_kwargs[key] = frontmatter[key]
    if position is not None:
        module_kwargs["position"] = position

    module = capi.create_or_update_module(
        course, existing["canvas_id"] if existing else None, title, **module_kwargs
    )
    capi.clear_module_items(module)

    had_warnings = False
    canvas_item_ids: dict[str, int] = {}
    for item in items:
        item_id, unpub_warn = capi.add_module_item(module, item, manifest)
        if unpub_warn is not None and unpublishable_items is not None:
            unpublishable_items.append((title, unpub_warn))
        if item["type"] == "content":
            if item_id is not None:
                canvas_item_ids[item["local_path"]] = item_id
            else:
                had_warnings = True

    manifest_lib.record(
        manifest,
        manifest_path,
        local_key,
        module.id,
        "module",
        extra={"canvas_item_ids": canvas_item_ids},
    )
    return had_warnings


def _quiz_needs_sync(
    quiz_md: Path,
    questions_dir: Path,
    local_key: str,
    manifest: manifest_lib.ManifestDict,
    force_uploads: bool,
    snippets_dir: Path,
) -> bool:
    """Return True if the quiz .md, any question file, or any snippet they
    reference is newer than last_synced."""
    if force_uploads:
        return True
    entry = manifest.get(local_key)
    if entry is None or "last_synced" not in entry:
        return True
    last_synced = datetime.fromisoformat(entry["last_synced"])
    all_files: list[Path] = [quiz_md]
    if questions_dir.exists():
        all_files.extend(questions_dir.glob("*.md"))
    for f in all_files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime > last_synced:
            return True
    for f in all_files:
        for snippet_path in _file_referenced_snippets(f, snippets_dir):
            snippet_mtime = datetime.fromtimestamp(snippet_path.stat().st_mtime, tz=timezone.utc)
            if snippet_mtime > last_synced:
                return True
    return False


def _sync_quiz(
    course,
    quiz_folder: Path,
    quiz_md: Path,
    repo_root: Path,
    manifest: manifest_lib.ManifestDict,
    manifest_path: Path,
    config_course_id: int = 0,
    force_uploads: bool = False,
    force_overwrite: bool = False,
    newer_on_canvas: list[str] | None = None,
    errors: list[str] | None = None,
    synced_keys: set[str] | None = None,
    verbose: bool = False,
    due_dates: list[dict[str, Any]] | None = None,
    assignment_group_ids: dict[str, int] | None = None,
) -> None:
    if newer_on_canvas is None:
        newer_on_canvas = []
    local_key = quiz_md.relative_to(repo_root).as_posix()
    questions_dir = quiz_folder / "questions"
    snippets_dir = repo_root / "snippets"

    if not _quiz_needs_sync(quiz_md, questions_dir, local_key, manifest, force_uploads, snippets_dir):
        if verbose:
            print(f"Skipping (up-to-date): {local_key}")
        return

    if not force_overwrite:
        all_files: list[Path] = [quiz_md]
        if questions_dir.exists():
            all_files.extend(questions_dir.glob("*.md"))
        local_max_mtime = max(
            datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            for f in all_files
        )
        for f in all_files:
            for snippet_path in _file_referenced_snippets(f, snippets_dir):
                snippet_mtime = datetime.fromtimestamp(snippet_path.stat().st_mtime, tz=timezone.utc)
                if snippet_mtime > local_max_mtime:
                    local_max_mtime = snippet_mtime
        if _canvas_is_newer(
            course, local_key, local_max_mtime, manifest, newer_on_canvas
        ):
            return

    print(f"Processing quiz: {local_key}")

    frontmatter, desc_html, question_paths = parse_quiz_file(quiz_md, snippets_dir)
    title = frontmatter.get("title", quiz_folder.name)
    published = frontmatter.get("published", False)

    quiz_kwargs: dict[str, Any] = {}
    for key in (
        "quiz_type",
        "time_limit",
        "allowed_attempts",
        "shuffle_answers",
        "show_correct_answers",
        "points_possible",
        "due_at",
        "lock_at",
        "unlock_at",
        # Assignment group (grading category)
        "assignment_group_id",
    ):
        if key in frontmatter:
            quiz_kwargs[key] = frontmatter[key]
    if "assignment_group_id" in quiz_kwargs:
        resolved = _resolve_assignment_group_id(
            quiz_kwargs["assignment_group_id"], assignment_group_ids, local_key, errors
        )
        if resolved is None:
            del quiz_kwargs["assignment_group_id"]
        else:
            quiz_kwargs["assignment_group_id"] = resolved
    def _stub_creator(ref_local_path: str, ref_canvas_type: str) -> dict[str, Any]:
        title_stub = (
            Path(ref_local_path).stem.replace("-", " ").replace("_", " ").title()
        )
        print(f"  Stub-creating: {ref_local_path} (referenced from quiz)")
        entry = capi.create_stub(course, ref_canvas_type, title_stub)
        extra = {
            k: v for k, v in entry.items() if k not in ("canvas_id", "canvas_type")
        }
        manifest_lib.record(
            manifest,
            manifest_path,
            ref_local_path,
            entry["canvas_id"],
            ref_canvas_type,
            extra=extra or None,
        )
        return entry

    error_count_before = len(errors) if errors is not None else 0

    # §7: rewrite links in quiz description
    if desc_html:
        desc_html = rewrite_links(
            desc_html,
            quiz_md,
            repo_root,
            manifest,
            config_course_id,
            _stub_creator,
            errors,
        )

    questions: list[dict[str, Any]] = []
    for q_path in question_paths:
        if not q_path.exists():
            print(f"  ERROR: question file not found: {q_path}")
            continue
        rel_path = q_path.relative_to(quiz_folder.resolve()).as_posix()
        q_data = parse_question_file(q_path, snippets_dir)
        # §7: rewrite links in question text
        if q_data.get("question_text"):
            q_data["question_text"] = rewrite_links(
                q_data["question_text"],
                q_path,
                repo_root,
                manifest,
                config_course_id,
                _stub_creator,
                errors,
            )
        q_data["rel_path"] = rel_path
        questions.append(q_data)

    if errors is not None and len(errors) > error_count_before:
        print(f"  Skipping upload due to errors: {local_key}")
        return

    existing = manifest.get(local_key)
    canvas_id = existing["canvas_id"] if existing else None

    override = find_due_date_override(due_dates or [], title, "quiz")
    if override is not None:
        quiz_kwargs.update(
            _resolve_date_overrides(override, canvas_id, local_key, errors)
        )

    print(f"  Uploading: {local_key}")
    result = capi.create_or_update_quiz(
        course, canvas_id, title, desc_html, published=published, **quiz_kwargs
    )
    if result.get("date_warning"):
        msg = _date_rejection_message(
            local_key, title, quiz_kwargs.get("due_at"), result.get("html_url")
        )
        print(f"  {msg}")
        if errors is not None:
            errors.append(msg)
    print(f"  Link: {result['html_url']}")
    quiz_obj = course.get_quiz(result["canvas_id"])
    q_id_map = capi.sync_quiz_questions(course, quiz_obj, questions)

    manifest_lib.record(
        manifest,
        manifest_path,
        local_key,
        result["canvas_id"],
        "quiz",
        extra={"canvas_question_ids": q_id_map},
    )
    if synced_keys is not None:
        synced_keys.add(local_key)


def _sync_question_banks(
    course,
    repo_root: Path,
    manifest: manifest_lib.ManifestDict,
    manifest_path: Path,
    force_uploads: bool = False,
    matcher: IgnoreMatcher | None = None,
    verbose: bool = False,
) -> None:
    """Sync all question banks from question_banks/ to Canvas."""
    banks_dir = repo_root / "question_banks"
    if not banks_dir.exists():
        return
    for bank_folder in sorted(d for d in banks_dir.iterdir() if d.is_dir()):
        if matcher is not None and matcher.is_ignored(bank_folder, repo_root):
            if verbose:
                print(f"Ignoring: {bank_folder.relative_to(repo_root).as_posix()}")
            continue
        toml_path = bank_folder / f"{bank_folder.name}.toml"
        if not toml_path.exists():
            continue
        local_key = toml_path.relative_to(repo_root).as_posix()
        snippets_dir = repo_root / "snippets"
        questions_dir = bank_folder / "questions"
        if not force_uploads and not manifest_lib.needs_sync(
            manifest, local_key, toml_path, False,
            extra_mtime_paths=lambda qd=questions_dir, sd=snippets_dir: _question_files_referenced_snippets(qd, sd),
        ):
            if verbose:
                print(f"Skipping (up-to-date): {local_key}")
            continue
        with toml_path.open("rb") as fh:
            bank_meta = tomllib.load(fh)
        bank_title = bank_meta.get("bank_title", bank_folder.name)
        questions: list[dict[str, Any]] = []
        if questions_dir.exists():
            for q_path in sorted(questions_dir.glob("*.md")):
                q_data = parse_question_file(q_path, snippets_dir)
                q_data["rel_path"] = q_path.relative_to(bank_folder).as_posix()
                questions.append(q_data)
        print(f"  Uploading question bank: {local_key}")
        canvas_id = capi.sync_question_bank(course, bank_title, questions)
        manifest_lib.record(
            manifest, manifest_path, local_key, canvas_id, "question_bank"
        )


def _get_file_refs(
    local_key: str, file_path: Path, repo_root: Path, snippets_dir: Path
) -> set[str]:
    """Return the set of locally-referenced file keys from any file type, without uploading."""
    folder = local_key.split("/")[0]
    if folder == "modules":
        try:
            _, body = parse_frontmatter(file_path.read_text())
        except yaml.YAMLError:
            return set()
        body = preprocess_snippets(body, file_path, snippets_dir)
        items = parse_module_body(body, file_path, repo_root)
        return {item["local_path"] for item in items if item["type"] == "content"}
    if folder in ("assets", "snippets"):
        return set()
    if folder == "quizzes":
        # TODO: quiz description HTML and question text can contain links to other Canvas
        # content (pages, assignments, etc.).  Currently BFS never follows those links and
        # _sync_quiz never rewrites them to Canvas URLs.  Investigate whether
        # _get_file_refs should parse the quiz .md + question files for local <a>/<img>
        # refs and return them here, and whether rewrite_links() should be called on the
        # converted quiz description / question HTML before upload.
        return set()
    try:
        _, body = parse_frontmatter(file_path.read_text())
    except yaml.YAMLError:
        return set()
    body = preprocess_snippets(body, file_path, snippets_dir)
    html = markdown_to_html(body)
    return extract_local_refs(html, file_path, repo_root)


def _resolve_target(target: str, repo_path: Path) -> str | None:
    """Resolve a user-supplied path to a repo-root-relative key, or None if outside repo."""
    p = Path(target)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        return p.resolve().relative_to(repo_path.resolve()).as_posix()
    except ValueError:
        return None


def _print_newer_on_canvas_summary(newer_on_canvas: list[str]) -> None:
    if not newer_on_canvas:
        return
    print(
        "\nThe following resources were NOT uploaded because Canvas has a newer version.\n"
        "Review these files and re-upload manually if needed (use --force-overwrite to skip this check):"
    )
    for key in newer_on_canvas:
        print(f"  {key}")


def _print_unpublishable_summary(items: list[tuple[str, str]]) -> None:
    if not items:
        return
    print(
        "\nThe following module items could not be unpublished because of an"
        " internal Canvas bug.\nYou will need to unpublish them in the Canvas"
        " web UI yourself, manually:"
    )
    for module_title, item_title in items:
        print(f"  In module \"{module_title}\": \"{item_title}\"")


def _print_errors_summary(errors: list[str]) -> None:
    if not errors:
        return
    print(f"\nThe following errors occurred during the update ({len(errors)} total):")
    for msg in errors:
        print(f"  {msg.strip()}")


def run_targeted_sync(
    config: Config,
    repo_path: Path,
    recursive_targets: list[str],
    single_targets: list[str],
    force_uploads: bool = False,
    force_overwrite: bool = False,
    verbose: bool = False,
) -> bool:
    """Sync only the specified targets. Returns True if any errors occurred.

    -t (recursive_targets) runs first: BFS each target plus all transitively referenced files.
    -s (single_targets) runs second, independently: no BFS, no dependency on -t's visited set.
    If -t already uploaded a file and updated its manifest timestamp, -s will skip it via needs_sync.
    """
    manifest_path = repo_path / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    course = capi.get_course(config)
    snippets_dir = repo_path / "snippets"
    assets_root = repo_path / "assets"
    newer_on_canvas: list[str] = []
    errors: list[str] = []
    unpublishable_items: list[tuple[str, str]] = []
    assignment_group_ids = capi.get_assignment_group_ids(course)
    rubric_ids = capi.get_rubric_ids(course)
    _targeted_position_map = _load_module_order(repo_path)
    due_dates = load_due_dates(repo_path)

    visited: set[str] = set()

    def _process(local_key: str) -> None:
        file_path = repo_path / local_key
        folder = local_key.split("/")[0]
        if folder == "assets":
            if manifest_lib.needs_sync(manifest, local_key, file_path, force_uploads):
                if not force_overwrite:
                    local_mtime = datetime.fromtimestamp(
                        file_path.stat().st_mtime, tz=timezone.utc
                    )
                    if _canvas_is_newer(
                        course, local_key, local_mtime, manifest, newer_on_canvas
                    ):
                        return
                print(f"Uploading asset: {local_key}")
                canvas_entry = capi.upload_asset(course, file_path, assets_root)
                manifest_lib.record(
                    manifest,
                    manifest_path,
                    local_key,
                    canvas_entry["canvas_id"],
                    "file",
                    extra={"canvas_url": canvas_entry["canvas_url"]},
                )
            else:
                if verbose:
                    print(f"Skipping (up-to-date): {local_key}")
        elif folder == "modules":
            had_module_warnings = _sync_module(
                course,
                file_path,
                repo_path,
                manifest,
                manifest_path,
                force_uploads,
                force_overwrite,
                newer_on_canvas,
                position=_targeted_position_map.get(file_path.name),
                verbose=verbose,
                unpublishable_items=unpublishable_items,
                errors=errors,
            )
            if had_module_warnings:
                errors.append(f"module {file_path.name}: some items could not be added")
        elif folder == "quizzes":
            quiz_folder = file_path.parent
            _sync_quiz(
                course,
                quiz_folder,
                file_path,
                repo_path,
                manifest,
                manifest_path,
                config.course_id,
                force_uploads,
                force_overwrite,
                newer_on_canvas,
                errors,
                verbose=verbose,
                due_dates=due_dates,
                assignment_group_ids=assignment_group_ids,
            )
        else:
            _sync_content_file(
                course,
                file_path,
                repo_path,
                snippets_dir,
                manifest,
                manifest_path,
                config.course_id,
                force_uploads,
                force_overwrite,
                newer_on_canvas,
                errors,
                assignment_group_ids=assignment_group_ids,
                rubric_ids=rubric_ids,
                verbose=verbose,
                due_dates=due_dates,
            )

    def _warn_missing(target: str) -> None:
        print(f"  WARNING: target not found or outside repo: {target}")

    # -t first: BFS over all transitively referenced local files.
    # Modules are deferred until after all content in the BFS is processed,
    # because modules require their referenced content to already have manifest entries.
    queue: deque[str] = deque()
    for target in recursive_targets:
        local_key = _resolve_target(target, repo_path)
        if local_key is None or not (repo_path / local_key).exists():
            _warn_missing(target)
            continue
        if local_key not in visited:
            queue.append(local_key)

    deferred_modules: list[str] = []

    while queue:
        local_key = queue.popleft()
        if local_key in visited:
            continue
        visited.add(local_key)
        file_path = repo_path / local_key
        if not file_path.exists():
            continue
        refs = _get_file_refs(local_key, file_path, repo_path, snippets_dir)
        if local_key.split("/")[0] == "modules":
            deferred_modules.append(local_key)
        else:
            _process(local_key)
        for ref in refs:
            if ref not in visited:
                queue.append(ref)

    for local_key in deferred_modules:
        _process(local_key)

    # -s second: each target processed independently, no BFS.
    # needs_sync prevents re-uploading anything -t just uploaded.
    for target in single_targets:
        local_key = _resolve_target(target, repo_path)
        if local_key is None or not (repo_path / local_key).exists():
            _warn_missing(target)
            continue
        _process(local_key)

    if due_dates:
        _check_due_dates_coverage(due_dates, repo_path)

    _print_newer_on_canvas_summary(newer_on_canvas)
    _print_unpublishable_summary(unpublishable_items)
    _print_errors_summary(errors)
    return bool(errors)
