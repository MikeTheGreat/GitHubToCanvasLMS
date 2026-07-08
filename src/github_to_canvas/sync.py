"""Main sync pipeline."""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import canvas_api as capi
from . import manifest as manifest_lib
from .conditionals import (
    apply_conditionals,
    find_referenced_flags,
    find_referenced_flags_in_frontmatter,
    parse_condition,
    resolve_published_if,
)
from .config import Config
from .convert import (
    expand_frontmatter_snippets,
    find_referenced_snippets,
    iter_lines_with_fence_info,
    markdown_to_html,
    parse_frontmatter,
    preprocess_snippets,
    warn,
)
from .ignore import IgnoreMatcher, load_ignore_matcher
from .link_rewrite import extract_local_refs, infer_canvas_type, rewrite_links
from .orphans import ResourceKey, extract_canvas_refs
from .quiz import parse_question_file, parse_quiz_file

_DATE_KEYS = ("unlock_at", "due_at", "lock_at")


@dataclass
class SyncContext:
    """Values threaded through every internal sync function.

    The public entry points (`run_sync`, `run_targeted_sync`) build one of these
    and pass it down instead of the previous 10-to-16 positional parameters. The
    mutable accumulators (`newer_on_canvas`, `errors`, `synced_keys`,
    `unpublishable_items`, `ignored_fields`) live here as plain lists/sets; the
    entry points read them back after the run to print the summaries.
    """

    course: Any
    repo_path: Path
    snippets_dir: Path
    manifest: manifest_lib.ManifestDict
    manifest_path: Path
    course_id: int
    force_uploads: bool = False
    force_overwrite: bool = False
    verbose: bool = False
    matcher: IgnoreMatcher | None = None
    newer_on_canvas: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    due_dates: list[dict[str, Any]] = field(default_factory=list)
    # [course_flags] values driving #if/#elif/#else/#endif conditionals.
    flags: dict[str, bool] = field(default_factory=dict)
    assignment_group_ids: dict[str, int] = field(default_factory=dict)
    rubric_ids: dict[str, int] = field(default_factory=dict)
    synced_keys: set[str] = field(default_factory=set)
    unpublishable_items: list[tuple[str, str]] = field(default_factory=list)
    # (local_key, field_name) for announcement frontmatter fields that were
    # dropped because Canvas has no matching announcement setting.
    ignored_fields: list[tuple[str, str]] = field(default_factory=list)


def load_due_dates(repo_path: Path, settings: dict | None = None) -> list[dict[str, Any]]:
    """Load centralized due_dates from course_settings/course_settings.toml.

    If settings dict is provided (pre-loaded), use it; otherwise read from disk.
    """
    if settings is None:
        settings_path = repo_path / "course_settings" / "course_settings.toml"
        if not settings_path.exists():
            return []
        with settings_path.open("rb") as fh:
            settings = tomllib.load(fh)
    return settings.get("due_dates", [])


_FLAG_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def load_course_flags(repo_path: Path, settings: dict | None = None) -> dict[str, bool]:
    """Load the [course_flags] table from course_settings/course_settings.toml.

    If settings dict is provided (pre-loaded), use it; otherwise read from
    disk. The table is optional (absent == no flags defined). An invalid flag
    name or a non-boolean value is a whole-run config error: raises ValueError,
    which the CLI reports via die().
    """
    if settings is None:
        settings_path = repo_path / "course_settings" / "course_settings.toml"
        if not settings_path.exists():
            return {}
        with settings_path.open("rb") as fh:
            settings = tomllib.load(fh)
    table = settings.get("course_flags", {})
    if not isinstance(table, dict):
        raise ValueError(
            "[course_flags] in course_settings.toml must be a table of "
            "flag_name = true/false entries"
        )
    for name, value in table.items():
        if not _FLAG_NAME_RE.match(name):
            raise ValueError(
                f"invalid course flag name {name!r} in [course_flags] — names "
                f"must match [A-Za-z_][A-Za-z0-9_]* (letters, digits, "
                f"underscores; not starting with a digit)"
            )
        if not isinstance(value, bool):
            raise ValueError(
                f"course flag '{name}' in [course_flags] must be a TOML "
                f"boolean (true/false), got {value!r}"
            )
    return dict(table)


def filter_due_dates_by_flags(
    due_dates: list[dict[str, Any]], flags: dict[str, bool]
) -> list[dict[str, Any]]:
    """Drop due_dates entries whose ``only_if`` condition is not met.

    ``only_if = "flag_name"`` (or ``"not flag_name"``) is an optional
    per-entry key, evaluated against ``[course_flags]`` right after
    ``load_due_dates()`` — no TOML preprocessing, entries without the key are
    always kept. An undefined flag or malformed condition is a whole-run
    config error: raises ValueError, which the CLI reports via die() (same
    convention as ``load_course_flags``).
    """
    filtered: list[dict[str, Any]] = []
    for entry in due_dates:
        if "only_if" not in entry:
            filtered.append(entry)
            continue
        name_for_errors = entry.get("name", "?")
        value = entry["only_if"]
        if not isinstance(value, str):
            raise ValueError(
                f"due_dates entry {name_for_errors!r}: 'only_if' must be a "
                f"flag name string, optionally preceded by 'not', got {value!r}"
            )
        cond = parse_condition(value)
        if cond in ("missing", "malformed"):
            raise ValueError(
                f"due_dates entry {name_for_errors!r}: invalid 'only_if' "
                f"value {value!r} — expected a single flag name, optionally "
                f"preceded by 'not' (no and/or/parentheses)"
            )
        negate, name = cond
        if name not in flags:
            raise ValueError(
                f"due_dates entry {name_for_errors!r}: undefined course flag "
                f"'{name}' in 'only_if' — flags must be defined under "
                f"[course_flags] in course_settings.toml"
            )
        keep = (not flags[name]) if negate else flags[name]
        if keep:
            filtered.append(entry)
    return filtered


def _flags_used_for(
    paths: Iterable[Path], snippets_dir: Path, flags: dict[str, bool]
) -> dict[str, bool]:
    """Flag values to record in a manifest entry's ``flags_used`` sub-table.

    Unions every flag referenced by any of the given files' bodies (all
    branches, taken or not), by ``published_if`` in their (snippet-expanded)
    frontmatter, or by any snippet they reference — the same snippet
    enumeration the snippet-mtime staleness path uses — and maps each to its
    current value.
    """
    names: set[str] = set()
    snippet_paths: set[Path] = set()
    for path in paths:
        try:
            fm, body = parse_frontmatter(path.read_text())
        except yaml.YAMLError:
            continue
        fm, body = expand_frontmatter_snippets(fm, body, path, snippets_dir)
        names |= find_referenced_flags(body)
        names |= find_referenced_flags_in_frontmatter(fm)
        snippet_paths |= find_referenced_snippets(body, path, snippets_dir)
    for snippet_path in snippet_paths:
        names |= find_referenced_flags(snippet_path.read_text())
    return {name: flags[name] for name in sorted(names) if name in flags}


def check_course_flags_coverage(
    flags: dict[str, bool],
    repo_path: Path,
    matcher: IgnoreMatcher | None = None,
) -> None:
    """Warn (once per flag) about [course_flags] entries no content file uses.

    Scans every .md file in the repo (content, modules, quizzes, question
    banks, syllabus, snippets — a flag referenced only inside a snippet counts
    as used) and unions find_referenced_flags(). Warning only — never an error:
    defining a flag before writing the content that uses it is legitimate.
    """
    if not flags:
        return
    used: set[str] = set()
    for md_file in sorted(repo_path.rglob("*.md")):
        rel_parts = md_file.relative_to(repo_path).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if matcher is not None and matcher.is_ignored(md_file, repo_path):
            continue
        try:
            text = md_file.read_text()
        except UnicodeDecodeError:
            continue
        try:
            fm, body = parse_frontmatter(text)
        except yaml.YAMLError:
            fm, body = {}, text  # malformed frontmatter — probe the raw text instead
        used |= find_referenced_flags(body)
        used |= find_referenced_flags_in_frontmatter(fm)
        if used >= flags.keys():
            return
    for name in flags:
        if name not in used:
            print(
                f"WARNING: course flag '{name}' is defined in "
                f"course_settings.toml but not used by any content file"
            )


def iter_gradeable_content(
    repo_path: Path, matcher: IgnoreMatcher | None = None
) -> Iterator[tuple[str, Path, str]]:
    """Yield ``(local_key, md_path, canvas_type)`` for every assignment,
    discussion, and quiz in the repo, honoring ``matcher`` when given.

    Assignments and discussions are found by recursive glob; a quiz is the
    ``<name>.md`` file inside each ``quizzes/<name>/`` folder. Callers parse
    frontmatter themselves — their malformed-YAML handling differs — and can use
    ``md_path.stem`` as the default title (for quizzes that equals the folder
    name)."""
    for folder, ctype in (("assignments", "assignment"), ("discussions", "discussion")):
        content_dir = repo_path / folder
        if not content_dir.exists():
            continue
        for md_file in sorted(content_dir.rglob("*.md")):
            if matcher is not None and matcher.is_ignored(md_file, repo_path):
                continue
            yield md_file.relative_to(repo_path).as_posix(), md_file, ctype

    quizzes_dir = repo_path / "quizzes"
    if quizzes_dir.exists():
        for quiz_folder in sorted(d for d in quizzes_dir.iterdir() if d.is_dir()):
            if matcher is not None and matcher.is_ignored(quiz_folder, repo_path):
                continue
            quiz_md = quiz_folder / f"{quiz_folder.name}.md"
            if not quiz_md.exists():
                continue
            yield quiz_md.relative_to(repo_path).as_posix(), quiz_md, "quiz"


def _apply_due_dates_only(ctx: SyncContext) -> None:
    """Apply due_dates overrides via dates-only API calls for items not already synced this run.

    Runs on every update. Each item's symbolic date resolution is cached in
    its manifest entry (``resolved_dates``); items whose resolution is
    unchanged are skipped without any API call, so the pass is O(changed
    entries) rather than O(all dated items).
    """
    course = ctx.course
    repo_path = ctx.repo_path
    manifest = ctx.manifest
    matcher = ctx.matcher
    errors = ctx.errors
    print("Applying due_dates...")
    for local_key, md_path, ctype in iter_gradeable_content(repo_path, matcher):
        if local_key in ctx.synced_keys:
            continue
        existing = manifest.get(local_key)
        if not existing or "canvas_id" not in existing:
            continue
        try:
            fm, _ = parse_frontmatter(md_path.read_text())
        except Exception:
            continue
        title = fm.get("title", md_path.stem)
        override = find_due_date_override(ctx.due_dates, title, ctype)
        if override is None:
            if "resolved_dates" in existing:
                print(
                    f'  NOTICE: due_dates entry for "{title}" ({local_key}) was '
                    f"removed — leaving Canvas dates as-is"
                )
                del existing["resolved_dates"]
                manifest_lib.flush(ctx.manifest_path, manifest)
            continue
        resolved = resolve_dates_symbolic(override)
        if not ctx.force_uploads and existing.get("resolved_dates") == resolved:
            if ctx.verbose:
                print(f"  Skipping (dates unchanged): {local_key}")
            continue
        canvas_id = existing["canvas_id"]
        date_fields = _resolve_date_overrides(override, canvas_id, local_key, errors)
        if date_fields:
            print(f"  Updating dates: {local_key}")
            result = capi.update_dates(course, ctype, canvas_id, date_fields)
            if result.get("date_warning"):
                msg = _date_rejection_message(
                    local_key, title, date_fields.get("due_at"), result.get("html_url")
                )
                warn(msg, errors)
                # Cache deliberately not updated: the next run retries (and
                # re-warns) until the dates are fixed in Canvas or the TOML.
                continue
        # Recorded even when there was nothing to send (everything resolved
        # KEEP) — the cache must advance or the entry re-triggers forever.
        existing["resolved_dates"] = resolved
        manifest_lib.flush(ctx.manifest_path, manifest)


def _check_due_dates_coverage(
    due_dates: list[dict[str, Any]],
    repo_path: Path,
    matcher: IgnoreMatcher | None = None,
    flags: dict[str, bool] | None = None,
) -> None:
    """Scan all content files and print warnings for due_dates mismatches.

    - A due_dates entry whose name doesn't match any content file.
    - A content file (assignment/discussion/quiz) with no due_dates entry —
      except one excluded from this offering by ``published_if`` (evaluates
      to False), which is expected to have no entry.
    """
    print("Checking due_dates coverage...")
    all_content: list[tuple[str, str]] = []  # (title, canvas_type)
    published_if_excluded: set[tuple[str, str]] = set()

    for local_key, md_path, ctype in iter_gradeable_content(repo_path, matcher):
        try:
            fm, _ = parse_frontmatter(md_path.read_text())
        except Exception:
            fm = {}
        title = fm.get("title", md_path.stem)
        all_content.append((title, ctype))
        if flags is not None and "published_if" in fm:
            if resolve_published_if(fm, flags, local_key, quiet=True) is not True:
                published_if_excluded.add((title, ctype))

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
        if (title, ctype) in published_if_excluded:
            continue
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
        warn(msg, errors)
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
        warn(msg, errors)
    return result


def resolve_dates_symbolic(override: dict[str, Any]) -> dict[str, str]:
    """Compute the symbolic date resolution of a due_dates entry, for caching.

    For each date key: a concrete date string (verbatim), "NONE" (clear the
    date on Canvas), or "KEEP" (leave Canvas alone). All three keys are always
    present so cached entries are self-describing. Sentinels stay symbolic —
    KEEP is the instruction *leave alone*, never a concrete date — and
    CREATE_NONE_THEN_KEEP resolves to KEEP because its clear-on-create meaning
    only applies while the item is first created (the full-sync path handles
    that; this resolution describes the steady state of an existing item).
    """
    resolved: dict[str, str] = {}
    for dk in _DATE_KEYS:
        val = override.get(dk, "")
        if isinstance(val, str) and val.strip().lower() in _DATE_SENTINELS:
            resolved[dk] = "NONE" if val.strip().lower() == "none" else "KEEP"
        elif val:
            resolved[dk] = str(val)
        else:
            resolved[dk] = "KEEP"
    return resolved


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


def _quiz_manual_save_message(local_key: str, title: str, html_url: str | None) -> str:
    where = html_url or "the quiz page in Canvas"
    return (
        f'WARNING: {local_key}: "{title}" is already published, so Canvas holds the'
        " question changes as a pending draft — students keep seeing the old questions"
        f' until the quiz is saved in the web UI. Open {where} and click "Save It Now".'
    )


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


def _make_stub_creator(course, manifest, manifest_path, note: str):
    """Build a stub-creator closure for rewrite_links.

    When a link references content that has not been synced yet, Canvas needs a
    placeholder object to point at; this records it in the manifest. ``note`` is
    the parenthetical shown in the "Stub-creating" line (it differs between the
    content and quiz call sites)."""

    def stub_creator(ref_local_path: str, ref_canvas_type: str) -> dict[str, Any]:
        title = Path(ref_local_path).stem.replace("-", " ").replace("_", " ").title()
        print(f"  Stub-creating: {ref_local_path} ({note})")
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

    return stub_creator


def _effective_mtime(paths: Iterable[Path], snippets_dir: Path) -> datetime:
    """Latest mtime among the given files and every snippet they reference.

    Editing an included snippet counts as changing each file that includes it,
    so the "is Canvas newer than local?" comparison uses this combined mtime."""
    result: datetime | None = None
    for path in paths:
        for p in (path, *_file_referenced_snippets(path, snippets_dir)):
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            if result is None or mtime > result:
                result = mtime
    assert result is not None  # callers always pass at least one path
    return result


def _parse_frontmatter_or_warn(
    md_file: Path, local_key: str, errors: list[str] | None
) -> tuple[dict[str, Any], str] | None:
    """Parse a file's frontmatter, or warn and return None on malformed YAML."""
    try:
        return parse_frontmatter(md_file.read_text())
    except yaml.YAMLError as exc:
        hint = ""
        if "mapping values are not allowed here" in str(exc):
            hint = " (hint: values containing colons must be quoted, e.g. title: \"Unit 01: Intro\")"
        warn(f"WARNING: {local_key}: malformed frontmatter{hint}: {exc}", errors)
        return None


_MODULE_LINK_RE = re.compile(r"^(\s*)-\s+\[([^\]]+)\]\(([^)]+)\)")
_MODULE_LINK_TITLE_RE = re.compile(r'''^(.*?)\s+["'].*["']\s*$''')
_MODULE_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)")
_MODULE_PLAIN_LIST_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.])\s+(.+)")
_INDENT_SPACES_PER_LEVEL = 2
MAX_CANVAS_INDENT = 5
_ITEM_ATTRS_RE = re.compile(r"<!--(.*?)-->")
_ITEM_ATTR_KV_RE = re.compile(r'(\w+)=["\']([^"\']*)["\']')
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
    items: list[dict[str, Any]] = []
    for line, is_fenced in iter_lines_with_fence_info(body):
        if is_fenced:
            # Links/headings inside a code fence are literal text, not items.
            continue
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


def _content_default_published(
    repo_root: Path,
    local_path: str,
    snippets_dir: Path,
    flags: dict[str, bool] | None = None,
) -> bool:
    """Published default for a module item with no explicit override.

    Mirrors content files' own `published` (or `published_if`) frontmatter so
    that re-syncing a module doesn't republish content the user explicitly
    unpublished.  Assets (non-.md targets, e.g. File items) have no such
    frontmatter, so they keep the historical default of True. A
    `published_if` problem (undefined flag, ambiguous with `published`) is
    resolved quietly — it is loudly reported when the referenced file syncs
    on its own — and defaults to not-published.
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
    return bool(
        resolve_published_if(ref_frontmatter, flags or {}, local_path, quiet=True)
    )


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


def sync_syllabus(ctx: SyncContext) -> None:
    """Upload course_settings/syllabus.md as the Canvas course syllabus body."""
    repo_path = ctx.repo_path
    manifest = ctx.manifest
    errors = ctx.errors
    course_id = ctx.course_id
    syllabus_md = repo_path / "course_settings" / "syllabus.md"
    if not syllabus_md.exists():
        return
    local_key = "course_settings/syllabus.md"
    if not manifest_lib.needs_sync(
        manifest, local_key, syllabus_md, ctx.force_uploads,
        extra_mtime_paths=lambda: _file_referenced_snippets(syllabus_md, ctx.snippets_dir),
        current_flags=ctx.flags, verbose=ctx.verbose,
    ):
        if ctx.verbose:
            print(f"Skipping (up-to-date): {local_key}")
        return
    print("Syncing syllabus...")
    try:
        frontmatter, body = parse_frontmatter(syllabus_md.read_text())
    except yaml.YAMLError as exc:
        msg = f"WARNING: course_settings/syllabus.md: malformed frontmatter: {exc}"
        warn(msg, errors)
        return
    body = apply_conditionals(body, ctx.flags, local_key, errors)
    if body is None:
        print(f"  Skipping upload due to errors: {local_key}")
        return
    body = preprocess_snippets(body, syllabus_md, ctx.snippets_dir, errors, flags=ctx.flags)
    html = markdown_to_html(body.strip()) if body.strip() else ""
    error_count_before = len(errors)
    # Stub creator is not needed for the syllabus; pass a no-op
    html = rewrite_links(
        html, syllabus_md, repo_path, manifest, course_id, lambda *_: {}, errors
    )
    if len(errors) > error_count_before:
        print(f"  Skipping upload due to errors: {local_key}")
        return
    ctx.course.update(course={"syllabus_body": html})
    flags_used = _flags_used_for([syllabus_md], ctx.snippets_dir, ctx.flags)
    manifest_lib.record(
        manifest, ctx.manifest_path, local_key, course_id, "syllabus",
        extra={"flags_used": flags_used} if flags_used else None,
    )


# course_settings.toml keys with a section of their own for change detection.
_SETTINGS_SECTION_KEYS = (
    "grading_standards",
    "dashboard_image",
    "assignment_groups",
    "late_policy",
    "default_post_policy",
    "tab_configuration",
    "front_page",
)
# Excluded from the metadata section but with no section of their own:
# due_dates changes are handled per-item by the dates pass (which runs every
# update), and course_flags changes are handled per-file via flags_used.
_NON_METADATA_SETTINGS_KEYS = _SETTINGS_SECTION_KEYS + ("due_dates", "course_flags")


def _section_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_settings_section_hashes(settings: dict[str, Any]) -> dict[str, str]:
    """Hash each course_settings.toml section for change detection.

    "metadata" covers every top-level key not claimed by another section, so
    unknown keys conservatively count as metadata (update_course_metadata
    reads the whole dict). A section absent from the file hashes a fixed
    marker rather than being omitted, so adding/removing it is a change like
    any other.
    """
    hashes = {
        key: _section_hash(settings.get(key, "<absent>"))
        for key in _SETTINGS_SECTION_KEYS
    }
    metadata = {
        k: v for k, v in settings.items() if k not in _NON_METADATA_SETTINGS_KEYS
    }
    hashes["metadata"] = _section_hash(metadata)
    return hashes


def sync_course_settings(
    course,
    repo_path: Path,
    manifest: dict,
    manifest_path: Path,
    force_uploads: bool = False,
    verbose: bool = False,
    settings: dict | None = None,
    errors: list[str] | None = None,
) -> tuple[dict[str, int], set[str], list[str]]:
    """Sync course_settings.toml: metadata, grading standards, assignment groups, policies, rubrics.

    Sections of the settings file are change-detected individually via hashes
    cached in the manifest entry's ``section_hashes`` sub-table, so editing
    one section (or [course_flags], or due_dates) re-runs only the affected
    actions. If ``settings`` is provided (pre-loaded), the file is not re-read.

    Returns ({rubric_title: canvas_id}, changed_sections, deferred_ag_rules):
    changed_sections is the set of section names actually (re)applied this run
    (run_sync's front-page phase keys off "front_page"), and deferred_ag_rules
    lists assignment-group names whose drop rules Canvas rejected (too few
    assignments) and must be re-applied after content.
    """
    settings_path = repo_path / "course_settings" / "course_settings.toml"
    rubrics_path = repo_path / "course_settings" / "rubrics.toml"
    settings_key = "course_settings/course_settings.toml"
    rubrics_key = "course_settings/rubrics.toml"
    deferred_ag_rules: list[str] = []
    changed_sections: set[str] = set()

    if settings is None and settings_path.exists():
        with settings_path.open("rb") as fh:
            settings = tomllib.load(fh)

    # The dashboard image file's own mtime joins the staleness check: editing
    # the image alone must re-upload it, not just editing the TOML.
    image_rel = (settings or {}).get("dashboard_image")
    image_path = repo_path / image_rel if image_rel else None

    def _image_paths() -> list[Path]:
        return [image_path] if image_path is not None and image_path.exists() else []

    settings_stale = settings_path.exists() and manifest_lib.needs_sync(
        manifest, settings_key, settings_path, force_uploads,
        extra_mtime_paths=_image_paths,
    )
    rubrics_stale = rubrics_path.exists() and manifest_lib.needs_sync(
        manifest, rubrics_key, rubrics_path, force_uploads
    )

    if not settings_stale and not rubrics_stale:
        if verbose and settings_path.exists():
            print(f"Skipping (up-to-date): {settings_key}")
        try:
            return capi.get_rubric_ids(course), changed_sections, deferred_ag_rules
        except Exception:
            return {}, changed_sections, deferred_ag_rules

    if settings_stale:
        assert settings is not None  # settings_path exists when settings_stale
        entry = manifest.get(settings_key) or {}
        recorded_hashes = entry.get("section_hashes")
        current_hashes = compute_settings_section_hashes(settings)
        if force_uploads or recorded_hashes is None:
            stale_sections = set(current_hashes)
        else:
            stale_sections = {
                name
                for name, digest in current_hashes.items()
                if recorded_hashes.get(name) != digest
            }
            # Image file newer than the last sync: the path string (and its
            # hash) is unchanged, but the upload must re-run.
            if image_path is not None and image_path.exists() and manifest_lib.needs_sync(
                manifest, settings_key, image_path, False
            ):
                stale_sections.add("dashboard_image")
        # update_course_metadata's output also depends on grading standards
        # (grading_standard_id) and assignment groups (group_weight inference).
        if stale_sections & {"grading_standards", "assignment_groups"}:
            stale_sections.add("metadata")

        if stale_sections:
            print("Syncing course settings...")
        elif verbose:
            print(f"Skipping (no section changed): {settings_key}")
        if verbose:
            for name in sorted(set(current_hashes) - stale_sections):
                print(f"  Skipping section (unchanged): {name}")

        # Hashes for sections that succeeded this run; a failed section keeps
        # its old hash (or none), so the next run retries exactly that section.
        new_hashes = dict(recorded_hashes or {})
        section_failures: list[str] = []

        def _section_done(name: str) -> None:
            changed_sections.add(name)
            new_hashes[name] = current_hashes[name]

        def _section_failed(name: str, exc: Exception, detail: str = "") -> None:
            warn(
                f"WARNING: course_settings section '{name}' failed{detail}: {exc}",
                errors,
            )
            section_failures.append(name)

        grading_standards = settings.get("grading_standards", [])
        assignment_groups = settings.get("assignment_groups", [])
        late_policy = settings.get("late_policy", {})
        default_post_policy = settings.get("default_post_policy", {})

        # §1c: grading standards (needed before §1a so we can set grading_standard_id)
        gs_id = None
        if "grading_standards" in stale_sections:
            gs_id = capi.sync_grading_standards(course, grading_standards)
            _section_done("grading_standards")

        # §1a: core course metadata. When only metadata changed, gs_id is None
        # and update_course_metadata leaves the course's grading standard alone.
        if "metadata" in stale_sections:
            capi.update_course_metadata(course, settings, grading_standard_id=gs_id)
            _section_done("metadata")

        # Dashboard image
        if "dashboard_image" in stale_sections:
            image_ok = True
            if image_rel:
                if image_path is not None and image_path.exists():
                    print(f"  Uploading dashboard image: {image_rel}")
                    try:
                        capi.upload_course_image(course, image_path)
                    except Exception as exc:
                        _section_failed("dashboard_image", exc)
                        image_ok = False
                else:
                    print(f"  WARNING: dashboard_image file not found: {image_rel}")
            if image_ok:
                _section_done("dashboard_image")

        # §1d: assignment groups. Drop rules that Canvas rejects because the
        # group has no assignments yet (always so on a fresh course) are deferred
        # and re-applied after the content phase by run_sync.
        if "assignment_groups" in stale_sections:
            deferred_ag_rules[:] = capi.sync_assignment_groups(course, assignment_groups)
            _section_done("assignment_groups")

        # §1e: late policy
        if "late_policy" in stale_sections:
            late_ok = True
            if late_policy:
                try:
                    capi.update_late_policy(course, late_policy)
                except Exception as exc:
                    _section_failed(
                        "late_policy", exc, f" (late_policy={late_policy!r})"
                    )
                    late_ok = False
            if late_ok:
                _section_done("late_policy")

        # §1b: default post policy
        if "default_post_policy" in stale_sections:
            post_ok = True
            if "post_manually" in default_post_policy:
                post_manually = default_post_policy["post_manually"]
                try:
                    capi.update_post_policy(course, post_manually)
                except Exception as exc:
                    _section_failed(
                        "default_post_policy",
                        exc,
                        f" (post_manually={post_manually!r})",
                    )
                    post_ok = False
            if post_ok:
                _section_done("default_post_policy")

        # Course-navigation (left sidebar) order/visibility. The misplaced-key
        # lint runs on every parse (not just when the section changed) — the
        # misplaced key lives inside some *other* section's value, so its own
        # section hash never trips.
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
            if "tab_configuration" in stale_sections:
                _section_done("tab_configuration")
        elif "tab_configuration" in stale_sections:
            tabs_ok = True
            if tab_config_raw:
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
                        _section_failed("tab_configuration", exc)
                        tabs_ok = False
            if tabs_ok:
                _section_done("tab_configuration")

        # front_page has no action here; run_sync's front-page phase keys off
        # its presence in changed_sections.
        if "front_page" in stale_sections:
            _section_done("front_page")

        # mark_synced=False on any section failure leaves the entry stale, so
        # the next run re-checks hashes and retries exactly the failed sections.
        manifest_lib.record(
            manifest,
            manifest_path,
            settings_key,
            0,
            "course_settings",
            extra={"section_hashes": new_hashes},
            mark_synced=not section_failures,
        )

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

    return rubric_ids, changed_sections, deferred_ag_rules


def _phase_assets(ctx: SyncContext) -> None:
    """Phase 1: Upload assets (depth-first, files before subdirs, alphabetical)."""
    assets_dir = ctx.repo_path / "assets"
    if assets_dir.exists():
        _walk_assets(ctx, assets_dir, assets_dir)


def _phase_content(ctx: SyncContext) -> bool:
    """Phase 2: Upload content (pages, assignments, discussions).

    Returns False if collision check passes; True if aborted due to collisions.
    """
    repo_path = ctx.repo_path
    matcher = ctx.matcher
    errors = ctx.errors
    verbose = ctx.verbose
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
            warn(msg, errors)
        print("\nAborting: resolve title collisions before syncing.")
        return True

    for md_file in all_content_files:
        _sync_content_file(ctx, md_file)
    return False


def _phase_quizzes(ctx: SyncContext) -> None:
    """Phase 2.5: Upload quizzes (each quiz lives in its own sub-folder)."""
    repo_path = ctx.repo_path
    matcher = ctx.matcher
    verbose = ctx.verbose
    quizzes_dir = repo_path / "quizzes"
    if quizzes_dir.exists():
        for quiz_folder in sorted(d for d in quizzes_dir.iterdir() if d.is_dir()):
            if matcher.is_ignored(quiz_folder, repo_path):
                if verbose:
                    print(f"Ignoring: {quiz_folder.relative_to(repo_path).as_posix()}")
                continue
            quiz_md = quiz_folder / f"{quiz_folder.name}.md"
            if quiz_md.exists():
                _sync_quiz(ctx, quiz_folder, quiz_md)


def _phase_question_banks(ctx: SyncContext) -> None:
    """Phase 2.6: Sync question banks."""
    _sync_question_banks(ctx)


def _phase_front_page(
    ctx: SyncContext, front_page_path: str | None, front_page_changed: bool
) -> None:
    """Phase 2.7: Set front page (only when the front_page setting or the target page was re-synced)."""
    course = ctx.course
    manifest = ctx.manifest
    errors = ctx.errors
    synced_keys = ctx.synced_keys
    if front_page_path and (front_page_changed or front_page_path in synced_keys):
        entry = manifest.get(front_page_path)
        if entry and "canvas_url" in entry:
            print(f"Setting front page: {front_page_path}")
            try:
                capi.set_front_page(course, entry["canvas_url"])
            except Exception as exc:
                if "unpublished" in str(exc).lower():
                    msg = (
                        f"front_page '{front_page_path}' must be published before it can be set "
                        f"as the front page. Add the following to its frontmatter and re-run:\n"
                        f"    published: true"
                    )
                else:
                    msg = f"set front page failed for '{front_page_path}': {exc}"
                print(f"  WARNING: {msg}")
                errors.append(msg)
        else:
            msg = f"front_page '{front_page_path}' not found in manifest — sync the page first"
            print(f"  WARNING: {msg}")
            errors.append(msg)


def _phase_modules(ctx: SyncContext, force_uploads: bool) -> None:
    """Phase 3: Sync modules (alphabetical, with optional explicit position from module_order.toml)."""
    repo_path = ctx.repo_path
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    matcher = ctx.matcher
    errors = ctx.errors
    verbose = ctx.verbose
    course = ctx.course
    snippets_dir = ctx.snippets_dir
    synced_keys = ctx.synced_keys
    _order_key = "course_settings/module_order.toml"
    _order_path = repo_path / _order_key
    position_map = _load_module_order(repo_path)
    order_changed = _order_path.exists() and manifest_lib.needs_sync(
        manifest, _order_key, _order_path, force_uploads
    )
    modules_dir = repo_path / "modules"
    if modules_dir.exists():
        modules_with_updated_refs: set[Path] = set()
        if synced_keys:
            for md_file in modules_dir.glob("*.md"):
                if matcher.is_ignored(md_file, repo_path):
                    continue
                try:
                    _, body = parse_frontmatter(md_file.read_text())
                except yaml.YAMLError:
                    continue
                # Passive probe: conditionals are NOT applied here, so refs in
                # false branches still count — a conservative superset that at
                # worst re-syncs a module unnecessarily. The real _sync_module
                # pass applies (and reports errors for) the directives.
                body = preprocess_snippets(body, md_file, snippets_dir)
                items = parse_module_body(body, md_file, repo_path)
                refs = {i["local_path"] for i in items if i["type"] == "content"}
                if refs & synced_keys:
                    modules_with_updated_refs.add(md_file)

        for md_file in sorted(modules_dir.glob("*.md")):
            if matcher.is_ignored(md_file, repo_path):
                if verbose:
                    print(f"Ignoring: {md_file.relative_to(repo_path).as_posix()}")
                continue
            position = position_map.get(md_file.name)
            force_this = force_uploads or md_file in modules_with_updated_refs
            had_module_warnings = _sync_module(
                ctx, md_file, position=position, force_this=force_this
            )
            if had_module_warnings:
                errors.append(f"module {md_file.name}: some items could not be added")
    if order_changed:
        _reorder_modules(course, position_map, repo_path, manifest, errors)
        manifest_lib.record(manifest, manifest_path, _order_key, 0, "module_order")


def _phase_due_dates(ctx: SyncContext, due_dates: list) -> None:
    """Phase 4: Apply and check due dates.

    Runs on every update; the per-item resolved_dates cache keeps unchanged
    items API-free. Also runs when the due_dates table is empty but cached
    resolutions remain, so entry removals still get their one-time notice.
    """
    have_cached = any(
        isinstance(e, dict) and "resolved_dates" in e for e in ctx.manifest.values()
    )
    if due_dates or have_cached:
        _apply_due_dates_only(ctx)
    if due_dates:
        _check_due_dates_coverage(due_dates, ctx.repo_path, ctx.matcher, ctx.flags)


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
    synced_content_keys: set[str] = set()
    unpublishable_items: list[tuple[str, str]] = []
    ignored_fields: list[tuple[str, str]] = []

    _cs_path = repo_path / "course_settings" / "course_settings.toml"
    _front_page_path: str | None = None
    _settings_dict: dict | None = None
    if _cs_path.exists():
        with _cs_path.open("rb") as _fh:
            _settings_dict = tomllib.load(_fh)
            _front_page_path = _settings_dict.get("front_page")

    # 0. Course settings (metadata, grading standards, assignment groups, policies, rubrics)
    rubric_ids, changed_sections, deferred_ag_rules = sync_course_settings(
        course, repo_path, manifest, manifest_path, force_uploads,
        verbose=verbose, settings=_settings_dict, errors=errors,
    )

    assignment_group_ids = capi.get_assignment_group_ids(course)

    course_flags = load_course_flags(repo_path, _settings_dict)
    due_dates = filter_due_dates_by_flags(load_due_dates(repo_path, _settings_dict), course_flags)

    ctx = SyncContext(
        course=course,
        repo_path=repo_path,
        snippets_dir=snippets_dir,
        manifest=manifest,
        manifest_path=manifest_path,
        course_id=config.course_id,
        force_uploads=force_uploads,
        force_overwrite=force_overwrite,
        verbose=verbose,
        matcher=matcher,
        newer_on_canvas=newer_on_canvas,
        errors=errors,
        due_dates=due_dates,
        flags=course_flags,
        assignment_group_ids=assignment_group_ids,
        rubric_ids=rubric_ids,
        synced_keys=synced_content_keys,
        unpublishable_items=unpublishable_items,
        ignored_fields=ignored_fields,
    )

    # 0.5. Syllabus
    sync_syllabus(ctx)

    # 1. Assets
    _phase_assets(ctx)

    # 2. Content
    if _phase_content(ctx):
        _print_errors_summary(errors)
        return True

    # 2.5. Quizzes
    _phase_quizzes(ctx)

    # 2.6. Question banks
    _phase_question_banks(ctx)

    # 2.65. Assignment-group drop rules deferred from course-settings sync
    # (the groups now have their assignments, so Canvas accepts the rules).
    if deferred_ag_rules and _settings_dict:
        capi.apply_assignment_group_rules(
            course, _settings_dict.get("assignment_groups", []), deferred_ag_rules
        )

    # 2.7. Front page
    _phase_front_page(ctx, _front_page_path, "front_page" in changed_sections)

    # 3. Modules
    _phase_modules(ctx, force_uploads)

    # 4. Due dates
    _phase_due_dates(ctx, due_dates)

    # 5. Unused course flags (warning only, once per flag)
    check_course_flags_coverage(course_flags, repo_path, matcher)

    _print_newer_on_canvas_summary(newer_on_canvas)
    _print_unpublishable_summary(unpublishable_items)
    _print_ignored_fields_summary(ignored_fields)
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
    if canvas_type in ("assignment", "discussion", "announcement", "quiz", "file", "module"):
        cid = entry.get("canvas_id")
        if cid is None:
            return None
        # Announcements are discussion topics in Canvas; a syllabus/front-page
        # link to one is a /discussion_topics/:id URL, so key it as "discussion"
        # to match the refs extracted from those bodies.
        key_type = "discussion" if canvas_type == "announcement" else canvas_type
        return ResourceKey(key_type, int(cid))
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
        syllabus_body = capi.get_syllabus_body(course)
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


def _walk_assets(ctx: SyncContext, dir_path: Path, assets_root: Path) -> None:
    course = ctx.course
    repo_root = ctx.repo_path
    manifest = ctx.manifest
    matcher = ctx.matcher
    entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_dir(), p.name))
    for entry in entries:
        if matcher is not None and matcher.is_ignored(entry, repo_root):
            if ctx.verbose:
                print(f"Ignoring: {entry.relative_to(repo_root).as_posix()}")
            continue
        if entry.is_file():
            local_key = entry.relative_to(repo_root).as_posix()
            if not manifest_lib.needs_sync(manifest, local_key, entry, ctx.force_uploads):
                continue
            if not ctx.force_overwrite:
                local_mtime = datetime.fromtimestamp(
                    entry.stat().st_mtime, tz=timezone.utc
                )
                if _canvas_is_newer(
                    course, local_key, local_mtime, manifest, ctx.newer_on_canvas
                ):
                    continue
            print(f"Uploading asset: {local_key}")
            canvas_entry = capi.upload_asset(course, entry, assets_root)
            manifest_lib.record(
                manifest,
                ctx.manifest_path,
                local_key,
                canvas_entry["canvas_id"],
                "file",
                extra={"canvas_url": canvas_entry["canvas_url"]},
            )
        elif entry.is_dir():
            _walk_assets(ctx, entry, assets_root)


def _validate_annotatable_attachment(
    ctx: SyncContext, frontmatter: dict, local_key: str, extra: dict
) -> None:
    """Check annotatable attachment consistency and add id to extra dict."""
    repo_root = ctx.repo_path
    manifest = ctx.manifest
    errors = ctx.errors
    uses_student_annotation = "student_annotation" in (
        frontmatter.get("submission_types") or []
    )
    if uses_student_annotation and "annotatable_attachment" not in frontmatter:
        msg = (
            f"WARNING: {local_key}: submission_types includes student_annotation "
            f"but annotatable_attachment is not set"
        )
        warn(msg, errors)
    elif "annotatable_attachment" in frontmatter:
        asset_path = frontmatter["annotatable_attachment"]
        if not (repo_root / asset_path).exists():
            msg = (
                f"WARNING: {local_key}: annotatable_attachment '{asset_path}' "
                f"does not exist on disk"
            )
            warn(msg, errors)
        else:
            asset_entry = manifest.get(asset_path)
            if asset_entry is None or asset_entry.get("canvas_type") != "file":
                msg = (
                    f"WARNING: {local_key}: annotatable_attachment '{asset_path}' not found "
                    f"in manifest — sync assets/ first, then re-run"
                )
                warn(msg, errors)
            else:
                extra["annotatable_attachment_id"] = asset_entry["canvas_id"]


def _apply_rubric(
    ctx: SyncContext, entry: dict, frontmatter: dict, local_key: str
) -> None:
    """Handle rubric association or removal."""
    course = ctx.course
    errors = ctx.errors
    rubric_ids = ctx.rubric_ids
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
                warn(msg, errors)
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
                warn(msg, errors)
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
            warn(msg, errors)


def _upload_page(
    ctx: SyncContext, existing: dict | None, frontmatter: dict, html: str, title: str,
    published: bool, local_key: str, flags_used: dict[str, bool] | None = None
) -> None:
    """Upload a page to Canvas."""
    course = ctx.course
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    synced_keys = ctx.synced_keys
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
    extra: dict[str, Any] = {"canvas_url": entry["canvas_url"]}
    if flags_used:
        extra["flags_used"] = flags_used
    manifest_lib.record(
        manifest,
        manifest_path,
        local_key,
        entry["canvas_id"],
        "page",
        extra=extra,
    )
    if synced_keys is not None:
        synced_keys.add(local_key)


def _upload_assignment(
    ctx: SyncContext, existing: dict | None, frontmatter: dict, html: str, title: str,
    published: bool, local_key: str, flags_used: dict[str, bool] | None = None
) -> None:
    """Upload an assignment to Canvas."""
    course = ctx.course
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    due_dates = ctx.due_dates
    errors = ctx.errors
    assignment_group_ids = ctx.assignment_group_ids
    synced_keys = ctx.synced_keys
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
    _validate_annotatable_attachment(ctx, frontmatter, local_key, extra)
    entry = capi.create_or_update_assignment(
        course, canvas_id, title, html, published=published, **extra
    )
    if entry.get("date_warning"):
        msg = _date_rejection_message(
            local_key, title, extra.get("due_at"), entry.get("html_url")
        )
        warn(msg, errors)
    print(f"  Link: {entry['html_url']}")
    extra_record: dict[str, Any] = {}
    if flags_used:
        extra_record["flags_used"] = flags_used
    # record() rebuilds the entry wholesale, so the resolved-dates cache must
    # be re-supplied here or it is silently dropped. Skipped on date_warning
    # so the dates-only pass retries on the next run.
    if override is not None and not entry.get("date_warning"):
        extra_record["resolved_dates"] = resolve_dates_symbolic(override)
    manifest_lib.record(
        manifest, manifest_path, local_key, entry["canvas_id"], "assignment",
        extra=extra_record or None,
    )
    _apply_rubric(ctx, entry, frontmatter, local_key)
    if synced_keys is not None:
        synced_keys.add(local_key)


def _upload_discussion(
    ctx: SyncContext, existing: dict | None, frontmatter: dict, html: str, title: str,
    published: bool, local_key: str, flags_used: dict[str, bool] | None = None
) -> None:
    """Upload a discussion to Canvas."""
    course = ctx.course
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    due_dates = ctx.due_dates
    errors = ctx.errors
    assignment_group_ids = ctx.assignment_group_ids
    synced_keys = ctx.synced_keys
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
        warn(msg, errors)
    print(f"  Link: {entry['html_url']}")
    extra_record: dict[str, Any] = {}
    if flags_used:
        extra_record["flags_used"] = flags_used
    if override is not None and not entry.get("date_warning"):
        extra_record["resolved_dates"] = resolve_dates_symbolic(override)
    manifest_lib.record(
        manifest, manifest_path, local_key, entry["canvas_id"], "discussion",
        extra=extra_record or None,
    )
    if synced_keys is not None:
        synced_keys.add(local_key)


# Announcement frontmatter keys handled outside the Canvas-settings pass-through:
# title/published drive the create call, canvas_type is the directory override.
# Anything not here and not in capi.ANNOUNCEMENT_SETTABLE_FIELDS is dropped with
# a warning (see _upload_announcement).
_ANNOUNCEMENT_HANDLED_KEYS = frozenset({"title", "published", "canvas_type"})


def _upload_announcement(
    ctx: SyncContext, existing: dict | None, frontmatter: dict, html: str, title: str,
    published: bool, local_key: str, flags_used: dict[str, bool] | None = None
) -> None:
    """Upload (post) an announcement to Canvas.

    An announcement is a discussion topic created with ``is_announcement=True``.
    Canvas has **no** unpublished/draft state for announcements — creating one
    posts it — so this is only ever reached for ``published: true`` files
    (unpublished ones are skipped upstream in ``_sync_content_file``). ``published``
    is therefore not sent to Canvas: posting is implicit. Announcements cannot be
    graded, so (unlike discussions) there are no due-date or assignment-group
    parameters; any supported discussion-topic settings present in the frontmatter
    (``capi.ANNOUNCEMENT_SETTABLE_FIELDS`` — e.g. ``delayed_post_at``, ``locked``,
    ``discussion_type``) are forwarded as-is. A ``delayed_post_at`` in the future
    schedules the post rather than publishing immediately.
    """
    course = ctx.course
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    synced_keys = ctx.synced_keys
    canvas_id = existing["canvas_id"] if existing else None
    extra = {
        k: frontmatter[k]
        for k in capi.ANNOUNCEMENT_SETTABLE_FIELDS
        if k in frontmatter
    }
    # Warn (now and in the end-of-run summary) about any frontmatter field that
    # is neither handled here nor a supported Canvas announcement setting, so a
    # typo or an unsupported field is never dropped silently.
    for key in frontmatter:
        if key in _ANNOUNCEMENT_HANDLED_KEYS or key in capi.ANNOUNCEMENT_SETTABLE_FIELDS:
            continue
        print(
            f"  WARNING: {local_key}: ignoring frontmatter field '{key}' — "
            "not a supported announcement setting, so it was not sent to Canvas"
        )
        ctx.ignored_fields.append((local_key, key))
    entry = capi.create_or_update_announcement(
        course, canvas_id, title, html, **extra
    )
    print(f"  Link: {entry['html_url']}")
    manifest_lib.record(
        manifest, manifest_path, local_key, entry["canvas_id"], "announcement",
        extra={"flags_used": flags_used} if flags_used else None,
    )
    if synced_keys is not None:
        synced_keys.add(local_key)


def _sync_content_file(ctx: SyncContext, md_file: Path) -> None:
    course = ctx.course
    repo_root = ctx.repo_path
    snippets_dir = ctx.snippets_dir
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    course_id = ctx.course_id
    force_uploads = ctx.force_uploads
    force_overwrite = ctx.force_overwrite
    newer_on_canvas = ctx.newer_on_canvas
    errors = ctx.errors
    verbose = ctx.verbose
    local_key = md_file.relative_to(repo_root).as_posix()

    if not manifest_lib.needs_sync(
        manifest, local_key, md_file, force_uploads,
        extra_mtime_paths=lambda: _file_referenced_snippets(md_file, snippets_dir),
        current_flags=ctx.flags, verbose=verbose,
    ):
        if verbose:
            print(f"Skipping (up-to-date): {local_key}")
        return

    if not force_overwrite:
        local_mtime = _effective_mtime([md_file], snippets_dir)
        if _canvas_is_newer(course, local_key, local_mtime, manifest, newer_on_canvas):
            return

    print(f"Processing: {local_key}")

    canvas_type = infer_canvas_type(local_key)

    parsed = _parse_frontmatter_or_warn(md_file, local_key, errors)
    if parsed is None:
        return
    frontmatter, body = parsed
    error_count_before_frontmatter_snippets = len(errors) if errors is not None else 0
    frontmatter, body = expand_frontmatter_snippets(frontmatter, body, md_file, snippets_dir, errors)
    if errors is not None and len(errors) > error_count_before_frontmatter_snippets:
        print(f"  Skipping upload due to errors: {local_key}")
        return

    # Canvas has no unpublished/draft state for announcements — an announcement
    # is posted the moment it is created and can never be un-posted via the
    # API, so `published_if` (which implies flipping publish state on a later
    # sync) is disallowed there; use a literal `published:` instead.
    published = resolve_published_if(
        frontmatter, ctx.flags, local_key, errors,
        forbid=(canvas_type == "announcement"),
        forbid_reason="announcements (Canvas cannot un-post one once created)",
    )
    if published is None:
        print(f"  Skipping upload due to errors: {local_key}")
        return

    body = apply_conditionals(body, ctx.flags, local_key, errors)
    if body is None:
        print(f"  Skipping upload due to errors: {local_key}")
        return
    error_count_before_snippets = len(errors) if errors is not None else 0
    body = preprocess_snippets(body, md_file, snippets_dir, errors, flags=ctx.flags)
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
        warn(msg, errors)
        return

    # Canvas has no unpublished/draft state for announcements — an announcement
    # is posted the moment it is created. So an announcement marked
    # `published: false` is intentionally NOT sent to Canvas: it stays staged in
    # the repo until you set `published: true` (then it posts). Skip it here,
    # before link rewriting could stub-create content it merely references.
    if canvas_type == "announcement" and not published:
        if verbose:
            if manifest.get(local_key) is not None:
                print(
                    f"  WARNING: {local_key}: published is false, but this announcement "
                    "is already posted on Canvas from an earlier run. Canvas cannot "
                    "un-post an announcement, so it is left as-is — delete it manually "
                    "in Canvas if you want it removed."
                )
            else:
                print(
                    f"  Skipping (unpublished announcement, not sent to Canvas): {local_key}\n"
                    "    Canvas has no draft state for announcements; set 'published: true' "
                    "to post it (optionally with a 'delayed_post_at' date to schedule it)."
                )
        return

    stub_creator = _make_stub_creator(
        course, manifest, manifest_path, "referenced but not yet synced"
    )

    error_count_before = len(errors) if errors is not None else 0
    html = rewrite_links(
        html, md_file, repo_root, manifest, course_id, stub_creator, errors
    )
    if errors is not None and len(errors) > error_count_before:
        print(f"  Skipping upload due to errors: {local_key}")
        return

    existing = manifest.get(local_key)
    title = frontmatter.get("title", md_file.stem)
    flags_used = _flags_used_for([md_file], snippets_dir, ctx.flags)

    print(f"  Uploading: {local_key}")
    if canvas_type == "page":
        _upload_page(ctx, existing, frontmatter, html, title, published, local_key, flags_used)
    elif canvas_type == "assignment":
        _upload_assignment(ctx, existing, frontmatter, html, title, published, local_key, flags_used)
    elif canvas_type == "discussion":
        _upload_discussion(ctx, existing, frontmatter, html, title, published, local_key, flags_used)
    elif canvas_type == "announcement":
        _upload_announcement(ctx, existing, frontmatter, html, title, published, local_key, flags_used)


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
    ctx: SyncContext,
    md_file: Path,
    position: int | None = None,
    force_this: bool = False,
) -> bool:
    # `force_this` (not ctx.force_uploads) drives the staleness check: run_sync
    # additionally forces a re-sync when a module references content synced this
    # run, so it varies per call.
    course = ctx.course
    repo_root = ctx.repo_path
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    snippets_dir = ctx.snippets_dir
    force_overwrite = ctx.force_overwrite
    newer_on_canvas = ctx.newer_on_canvas
    verbose = ctx.verbose
    unpublishable_items = ctx.unpublishable_items
    errors = ctx.errors
    local_key = md_file.relative_to(repo_root).as_posix()

    if not manifest_lib.needs_sync(
        manifest, local_key, md_file, force_this,
        extra_mtime_paths=lambda: _file_referenced_snippets(md_file, snippets_dir),
        current_flags=ctx.flags, verbose=verbose,
    ):
        if verbose:
            print(f"Skipping (up-to-date): {local_key}")
        return False

    if not force_overwrite:
        local_mtime = _effective_mtime([md_file], snippets_dir)
        if _canvas_is_newer(course, local_key, local_mtime, manifest, newer_on_canvas):
            return False

    print(f"Syncing module: {local_key}")

    parsed = _parse_frontmatter_or_warn(md_file, local_key, errors)
    if parsed is None:
        return False
    frontmatter, body = parsed
    frontmatter, body = expand_frontmatter_snippets(frontmatter, body, md_file, snippets_dir, errors)
    body = apply_conditionals(body, ctx.flags, local_key, errors)
    if body is None:
        print(f"  Skipping upload due to errors: {local_key}")
        return False
    body = preprocess_snippets(body, md_file, snippets_dir, flags=ctx.flags)
    items = parse_module_body(body, md_file, repo_root)
    for item in items:
        if item.get("type") == "content" and item.get("published") is None:
            item["published"] = _content_default_published(
                repo_root, item["local_path"], snippets_dir, ctx.flags
            )

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

    extra: dict[str, Any] = {"canvas_item_ids": canvas_item_ids}
    flags_used = _flags_used_for([md_file], snippets_dir, ctx.flags)
    if flags_used:
        extra["flags_used"] = flags_used
    # On item failures, still record the module's canvas_id (so the next run
    # updates this module rather than creating a duplicate) but leave the
    # entry unstamped so it is retried.
    manifest_lib.record(
        manifest,
        manifest_path,
        local_key,
        module.id,
        "module",
        extra=extra,
        mark_synced=not had_warnings,
    )
    if had_warnings:
        print(f"  Module will be retried on the next update: {local_key}")
    return had_warnings


def _quiz_needs_sync(
    quiz_md: Path,
    questions_dir: Path,
    local_key: str,
    manifest: manifest_lib.ManifestDict,
    force_uploads: bool,
    snippets_dir: Path,
    current_flags: dict[str, bool] | None = None,
    verbose: bool = False,
) -> bool:
    """Return True if the quiz .md, any question file, or any snippet they
    reference is newer than last_synced, or if any course flag recorded in
    the quiz entry's flags_used table changed (the quiz and its questions
    sync as one unit, so flags_used covers all of them)."""
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
    if current_flags is not None:
        change = manifest_lib.flag_change(entry, current_flags)
        if change is not None:
            if verbose:
                manifest_lib.print_flag_change_reason(change)
            return True
    return False


def _sync_quiz(ctx: SyncContext, quiz_folder: Path, quiz_md: Path) -> None:
    course = ctx.course
    repo_root = ctx.repo_path
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    config_course_id = ctx.course_id
    force_uploads = ctx.force_uploads
    force_overwrite = ctx.force_overwrite
    newer_on_canvas = ctx.newer_on_canvas
    errors = ctx.errors
    synced_keys = ctx.synced_keys
    verbose = ctx.verbose
    due_dates = ctx.due_dates
    assignment_group_ids = ctx.assignment_group_ids
    snippets_dir = ctx.snippets_dir
    local_key = quiz_md.relative_to(repo_root).as_posix()
    questions_dir = quiz_folder / "questions"

    if not _quiz_needs_sync(
        quiz_md, questions_dir, local_key, manifest, force_uploads, snippets_dir,
        current_flags=ctx.flags, verbose=verbose,
    ):
        if verbose:
            print(f"Skipping (up-to-date): {local_key}")
        return

    if not force_overwrite:
        all_files: list[Path] = [quiz_md]
        if questions_dir.exists():
            all_files.extend(questions_dir.glob("*.md"))
        local_max_mtime = _effective_mtime(all_files, snippets_dir)
        if _canvas_is_newer(
            course, local_key, local_max_mtime, manifest, newer_on_canvas
        ):
            return

    print(f"Processing quiz: {local_key}")

    error_count_before_parse = len(errors) if errors is not None else 0
    frontmatter, desc_html, question_paths = parse_quiz_file(
        quiz_md, snippets_dir, flags=ctx.flags, source_desc=local_key, errors=errors
    )
    if errors is not None and len(errors) > error_count_before_parse:
        print(f"  Skipping upload due to errors: {local_key}")
        return
    title = frontmatter.get("title", quiz_folder.name)
    published = resolve_published_if(frontmatter, ctx.flags, local_key, errors)
    if published is None:
        print(f"  Skipping upload due to errors: {local_key}")
        return

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
    _stub_creator = _make_stub_creator(
        course, manifest, manifest_path, "referenced from quiz"
    )

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
            warn(f"ERROR: {local_key}: question file not found: {q_path}", errors)
            continue
        rel_path = q_path.relative_to(quiz_folder.resolve()).as_posix()
        q_data = parse_question_file(
            q_path, snippets_dir,
            flags=ctx.flags,
            source_desc=f"{local_key.rsplit('/', 1)[0]}/{rel_path}",
            errors=errors,
        )
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
    # The publish state is applied after the questions are synced (see
    # finalize_quiz_publish_state) so a quiz being published this sync
    # includes its new questions without a manual save in the web UI.
    result = capi.create_or_update_quiz(
        course, canvas_id, title, desc_html, **quiz_kwargs
    )
    if result.get("date_warning"):
        msg = _date_rejection_message(
            local_key, title, quiz_kwargs.get("due_at"), result.get("html_url")
        )
        warn(msg, errors)
    print(f"  Link: {result['html_url']}")
    quiz_obj = course.get_quiz(result["canvas_id"])
    q_id_map = capi.sync_quiz_questions(course, quiz_obj, questions)
    if capi.finalize_quiz_publish_state(quiz_obj, published):
        warn(_quiz_manual_save_message(local_key, title, result.get("html_url")), errors)

    extra: dict[str, Any] = {"canvas_question_ids": q_id_map}
    # flags_used spans the quiz .md and every question file (globbed, not just
    # the ones currently listed) — the quiz syncs as one unit, and a question
    # excluded by a false flag must still make the quiz stale when it flips.
    quiz_files: list[Path] = [quiz_md]
    if questions_dir.exists():
        quiz_files.extend(questions_dir.glob("*.md"))
    flags_used = _flags_used_for(quiz_files, snippets_dir, ctx.flags)
    if flags_used:
        extra["flags_used"] = flags_used
    if override is not None and not result.get("date_warning"):
        extra["resolved_dates"] = resolve_dates_symbolic(override)
    manifest_lib.record(
        manifest,
        manifest_path,
        local_key,
        result["canvas_id"],
        "quiz",
        extra=extra,
    )
    if synced_keys is not None:
        synced_keys.add(local_key)


def _sync_question_banks(ctx: SyncContext) -> None:
    """Sync all question banks from question_banks/ to Canvas."""
    course = ctx.course
    repo_root = ctx.repo_path
    manifest = ctx.manifest
    manifest_path = ctx.manifest_path
    force_uploads = ctx.force_uploads
    matcher = ctx.matcher
    verbose = ctx.verbose
    snippets_dir = ctx.snippets_dir
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
        error_count_before = len(ctx.errors)
        questions: list[dict[str, Any]] = []
        if questions_dir.exists():
            for q_path in sorted(questions_dir.glob("*.md")):
                q_data = parse_question_file(
                    q_path, snippets_dir,
                    flags=ctx.flags,
                    source_desc=q_path.relative_to(repo_root).as_posix(),
                    errors=ctx.errors,
                )
                q_data["rel_path"] = q_path.relative_to(bank_folder).as_posix()
                questions.append(q_data)
        if len(ctx.errors) > error_count_before:
            print(f"  Skipping upload due to errors: {local_key}")
            continue
        print(f"  Uploading question bank: {local_key}")
        canvas_id = capi.sync_question_bank(course, bank_title, questions)
        manifest_lib.record(
            manifest, manifest_path, local_key, canvas_id, "question_bank"
        )


def _get_file_refs(
    local_key: str, file_path: Path, repo_root: Path, snippets_dir: Path
) -> set[str]:
    """Return the set of locally-referenced file keys from any file type, without uploading.

    Passive probe: course-flag conditionals are NOT applied, so refs in false
    branches still count (a conservative superset — at worst the BFS visits a
    file that active content no longer links). The actual per-file sync applies
    and reports the directives.
    """
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


def _print_ignored_fields_summary(items: list[tuple[str, str]]) -> None:
    if not items:
        return
    print(
        "\nThe following announcement frontmatter fields were ignored (not a"
        " supported Canvas announcement setting, so not sent):"
    )
    for local_key, field_name in items:
        print(f"  {local_key}: {field_name}")


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
    ignored_fields: list[tuple[str, str]] = []
    assignment_group_ids = capi.get_assignment_group_ids(course)
    rubric_ids = capi.get_rubric_ids(course)
    _targeted_position_map = _load_module_order(repo_path)
    course_flags = load_course_flags(repo_path)
    due_dates = filter_due_dates_by_flags(load_due_dates(repo_path), course_flags)

    ctx = SyncContext(
        course=course,
        repo_path=repo_path,
        snippets_dir=snippets_dir,
        manifest=manifest,
        manifest_path=manifest_path,
        course_id=config.course_id,
        force_uploads=force_uploads,
        force_overwrite=force_overwrite,
        verbose=verbose,
        newer_on_canvas=newer_on_canvas,
        errors=errors,
        due_dates=due_dates,
        flags=course_flags,
        assignment_group_ids=assignment_group_ids,
        rubric_ids=rubric_ids,
        unpublishable_items=unpublishable_items,
        ignored_fields=ignored_fields,
    )

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
                ctx,
                file_path,
                position=_targeted_position_map.get(file_path.name),
                force_this=force_uploads,
            )
            if had_module_warnings:
                errors.append(f"module {file_path.name}: some items could not be added")
        elif folder == "quizzes":
            quiz_folder = file_path.parent
            _sync_quiz(ctx, quiz_folder, file_path)
        else:
            _sync_content_file(ctx, file_path)

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
        _check_due_dates_coverage(due_dates, repo_path, flags=course_flags)

    check_course_flags_coverage(course_flags, repo_path)

    _print_newer_on_canvas_summary(newer_on_canvas)
    _print_unpublishable_summary(unpublishable_items)
    _print_ignored_fields_summary(ignored_fields)
    _print_errors_summary(errors)
    return bool(errors)
