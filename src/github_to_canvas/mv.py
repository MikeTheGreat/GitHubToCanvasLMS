"""Move/rename files within a course repo, updating manifest and links."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote

import tomli_w

from . import manifest as manifest_lib

_CONTENT_TYPE_DIRS = {
    "pages", "assignments", "discussions", "quizzes",
    "assets", "snippets", "modules", "question_banks",
    "course_settings",
}

_MD_LINK_RE = re.compile(r"(!?\[[^\]]*\])\(([^)]+)\)")
_INLINE_SNIPPET_RE = re.compile(r"\$([^$\n]+\.md)\$")


def find_repo_root(start_path: Path) -> Path | None:
    """Walk up from start_path looking for course_settings/course_settings.toml."""
    current = start_path if start_path.is_dir() else start_path.parent
    while True:
        if (current / "course_settings" / "course_settings.toml").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _content_type_dir(rel_path: str) -> str | None:
    """Return the top-level content directory from a repo-relative path."""
    first = rel_path.split("/")[0]
    return first if first in _CONTENT_TYPE_DIRS else None


def _is_git_repo(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(path),
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _has_tracked_content(repo_root: Path, path: Path) -> bool:
    """True if path (file or directory) contains any git-tracked file.

    `git mv` errors out instead of moving the file when nothing under
    path is tracked yet, so callers must fall back to a plain filesystem
    move in that case.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(path)],
            cwd=str(repo_root),
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _is_case_only_rename(src: Path, dest: Path) -> bool:
    """True if src and dest differ only in case (same path on case-insensitive FS)."""
    if str(src).lower() != str(dest).lower():
        return False
    if str(src) == str(dest):
        return False
    try:
        return src.resolve() == dest.resolve()
    except OSError:
        return dest.exists() and src.stat().st_ino == dest.stat().st_ino


def validate_move(src: Path, dest: Path, repo_root: Path) -> None:
    """Validate the move. Raises ValueError on failure."""
    if not src.exists():
        raise ValueError(f"Source does not exist: {src}")

    case_rename = _is_case_only_rename(src, dest)
    if dest.exists() and not case_rename:
        raise ValueError(f"Destination already exists: {dest}")

    try:
        src_rel = src.resolve().relative_to(repo_root.resolve())
    except ValueError:
        raise ValueError(
            f"Source is outside the course repo ({repo_root}): {src}"
        )
    try:
        dest_rel_parts = dest.resolve().relative_to(repo_root.resolve()) if not case_rename else None
        if case_rename:
            dest_rel = PurePosixPath(
                str(src.resolve().relative_to(repo_root.resolve()).parent)
            ) / dest.name
            if src.resolve().parent != dest.resolve().parent:
                dest_rel = dest.relative_to(repo_root)
        else:
            dest_rel = dest_rel_parts
    except ValueError:
        raise ValueError(
            f"Destination is outside the course repo ({repo_root}): {dest}"
        )

    src_type = _content_type_dir(src_rel.as_posix())
    dest_type = _content_type_dir(dest_rel.as_posix() if hasattr(dest_rel, 'as_posix') else str(dest_rel))
    if src_type != dest_type:
        raise ValueError(
            f"Cannot move across content types: {src_type}/ → {dest_type}/\n"
            f"Both source and destination must be in the same top-level directory."
        )

    dest_parent = dest.parent
    dest_parent_exists = dest_parent.exists()
    if not dest_parent_exists and case_rename:
        dest_parent_exists = dest_parent.resolve() == src.resolve().parent
    if not dest_parent_exists:
        raise ValueError(
            f"Destination parent directory does not exist: {dest_parent}\n"
            f"Create it first, or rename intermediate directories one at a time."
        )


def build_path_map(src: Path, dest: Path, repo_root: Path) -> dict[str, str]:
    """Build old→new repo-relative path mapping for all affected files.

    For quiz/question_bank folder renames, includes the inner file rename.
    """
    src_rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
    case_rename = _is_case_only_rename(src, dest)

    if case_rename:
        dest_rel = _compute_case_rename_dest_rel(src, dest, repo_root)
    else:
        dest_rel = dest.resolve().relative_to(repo_root.resolve()).as_posix()

    path_map: dict[str, str] = {}

    if src.is_file():
        path_map[src_rel] = dest_rel
    elif src.is_dir():
        for child in sorted(src.rglob("*")):
            if child.is_file():
                child_rel = child.resolve().relative_to(repo_root.resolve()).as_posix()
                suffix = child_rel[len(src_rel):]
                path_map[child_rel] = dest_rel + suffix

        _add_quiz_qbank_inner_renames(src, dest, src_rel, dest_rel, path_map)

    return path_map


def _compute_case_rename_dest_rel(src: Path, dest: Path, repo_root: Path) -> str:
    """Compute the dest repo-relative path for a case-only rename."""
    src_resolved = src.resolve()
    repo_resolved = repo_root.resolve()
    src_rel_path = src_resolved.relative_to(repo_resolved)

    src_str = str(src.absolute())
    dest_str = str(dest.absolute())
    repo_str = str(repo_resolved)

    if dest_str.startswith(repo_str + "/"):
        return dest_str[len(repo_str) + 1:]

    src_rel_str = src_rel_path.as_posix()
    old_name = src.name
    new_name = dest.name
    if src_rel_str.endswith(old_name):
        return src_rel_str[: -len(old_name)] + new_name

    return src_rel_str


def _add_quiz_qbank_inner_renames(
    src: Path, dest: Path,
    src_rel: str, dest_rel: str,
    path_map: dict[str, str],
) -> None:
    """For quiz/qbank folder renames, add inner file renames to path_map."""
    src_name = src.name
    dest_name = dest.name if not _is_case_only_rename(src, dest) else dest.name

    if src_name == dest_name:
        return

    parts = src_rel.split("/")
    if len(parts) < 2:
        return

    top_dir = parts[0]

    if top_dir == "quizzes":
        old_inner = f"{src_rel}/{src_name}.md"
        new_inner = f"{dest_rel}/{dest_name}.md"
        if old_inner in path_map:
            path_map[old_inner] = new_inner

    elif top_dir == "question_banks":
        old_inner = f"{src_rel}/{src_name}.toml"
        new_inner = f"{dest_rel}/{dest_name}.toml"
        if old_inner in path_map:
            path_map[old_inner] = new_inner


def _split_url_title(raw: str) -> tuple[str, str]:
    """Split 'url "title"' into (url, rest_including_space_and_title)."""
    stripped = raw.rstrip()
    for qchar in ('"', "'"):
        if stripped.endswith(qchar):
            start = stripped.rfind(qchar, 0, len(stripped) - 1)
            if start > 0 and stripped[start - 1] == " ":
                return stripped[: start - 1], stripped[start - 1 :]
    return raw, ""


def _resolve_repo_relative(base_dir: str, relative_path: str) -> str | None:
    """Resolve a relative path against a repo-relative base directory.

    Returns a normalized repo-relative POSIX path, or None if it escapes the repo.
    """
    if base_dir == ".":
        joined = relative_path
    else:
        joined = base_dir + "/" + relative_path
    normed = os.path.normpath(joined).replace(os.sep, "/")
    if normed.startswith("..") or normed.startswith("/"):
        return None
    return normed


def _compute_relative(from_dir: str, to_path: str) -> str:
    """Compute relative path from from_dir to to_path (both repo-relative)."""
    result = os.path.relpath(to_path, from_dir).replace(os.sep, "/")
    return result


def transform_links(
    content: str,
    old_dir: str,
    new_dir: str,
    path_map: dict[str, str],
) -> str:
    """Transform relative links in Markdown content.

    old_dir: repo-relative dir of the file before any move
    new_dir: repo-relative dir of the file after move (same as old_dir if not moved)
    path_map: old_repo_relative → new_repo_relative for moved files
    """
    file_moved = old_dir != new_dir
    reverse_map = {v: k for k, v in path_map.items()}

    def _transform_url(url: str) -> str | None:
        if url.startswith(("http://", "https://", "#", "mailto:")):
            return None
        if "$" in url:
            return None

        decoded = unquote(url)
        has_encoding = "%" in url

        target = _resolve_repo_relative(old_dir, decoded)
        if target is None:
            if not file_moved:
                return None
            target_from_new = _resolve_repo_relative(new_dir, decoded)
            if target_from_new is not None and target_from_new in reverse_map:
                target = reverse_map[target_from_new]
            else:
                return None

        new_target = path_map.get(target, target)
        target_moved = new_target != target

        if not target_moved and not file_moved:
            return None

        new_rel = _compute_relative(new_dir, new_target)
        if has_encoding:
            new_rel = quote(new_rel, safe="/-_.~")

        return new_rel

    def _replace_md_link(m: re.Match) -> str:
        prefix = m.group(1)
        raw_url = m.group(2)

        url, title_suffix = _split_url_title(raw_url)
        new_url = _transform_url(url)
        if new_url is None:
            return m.group(0)
        return f"{prefix}({new_url}{title_suffix})"

    def _replace_snippet(m: re.Match) -> str:
        path = m.group(1)
        new_path = _transform_url(path)
        if new_path is None:
            return m.group(0)
        return f"${new_path}$"

    content = _INLINE_SNIPPET_RE.sub(_replace_snippet, content)
    content = _MD_LINK_RE.sub(_replace_md_link, content)
    return content


def compute_manifest_updates(
    manifest: manifest_lib.ManifestDict,
    path_map: dict[str, str],
) -> manifest_lib.ManifestDict:
    """Return a new manifest dict with updated keys and canvas_item_ids."""
    new_manifest: manifest_lib.ManifestDict = {}
    for key, entry in manifest.items():
        new_key = path_map.get(key, key)
        new_entry = dict(entry)

        if "canvas_item_ids" in new_entry:
            old_ids = new_entry["canvas_item_ids"]
            new_ids = {}
            for item_path, item_id in old_ids.items():
                new_item_path = path_map.get(item_path, item_path)
                new_ids[new_item_path] = item_id
            new_entry["canvas_item_ids"] = new_ids

        new_manifest[new_key] = new_entry
    return new_manifest


def compute_module_order_updates(
    repo_root: Path,
    path_map: dict[str, str],
) -> list[str] | None:
    """If module files are in path_map, compute new module_order list. Returns None if no changes."""
    order_path = repo_root / "course_settings" / "module_order.toml"
    if not order_path.exists():
        return None

    module_renames: dict[str, str] = {}
    for old, new in path_map.items():
        if old.startswith("modules/"):
            old_name = PurePosixPath(old).name
            new_name = PurePosixPath(new).name
            if old_name != new_name:
                module_renames[old_name] = new_name

    if not module_renames:
        return None

    with order_path.open("rb") as f:
        data = tomllib.load(f)
    order = list(data.get("order", []))

    changed = False
    for i, entry in enumerate(order):
        if isinstance(entry, str) and entry in module_renames:
            order[i] = module_renames[entry]
            changed = True

    return order if changed else None


_COURSE_SETTINGS_PATH_FIELDS = ("dashboard_image", "front_page")


def compute_course_settings_updates(
    repo_root: Path,
    path_map: dict[str, str],
) -> dict[str, str] | None:
    """If course_settings.toml's dashboard_image/front_page was moved, return
    {field: new_repo_relative_path} for the changed fields. None if no changes."""
    settings_path = repo_root / "course_settings" / "course_settings.toml"
    if not settings_path.exists():
        return None

    with settings_path.open("rb") as f:
        data = tomllib.load(f)

    updates: dict[str, str] = {}
    for field in _COURSE_SETTINGS_PATH_FIELDS:
        old_value = data.get(field)
        if not old_value:
            continue
        new_value = path_map.get(old_value)
        if new_value is not None and new_value != old_value:
            updates[field] = new_value

    return updates or None


def compute_file_updates(
    repo_root: Path,
    path_map: dict[str, str],
) -> dict[str, tuple[str, str]]:
    """Compute {new_repo_rel_path: (old_content, new_content)} for files with link changes."""
    moved_files = set(path_map.keys())
    updates: dict[str, tuple[str, str]] = {}

    for md_file in sorted(repo_root.rglob("*.md")):
        if md_file.name.startswith("."):
            continue
        try:
            repo_rel = md_file.relative_to(repo_root).as_posix()
        except ValueError:
            continue

        if repo_rel in moved_files:
            old_dir = str(PurePosixPath(repo_rel).parent)
            new_path = path_map[repo_rel]
            new_dir = str(PurePosixPath(new_path).parent)
        else:
            old_dir = str(PurePosixPath(repo_rel).parent)
            new_path = repo_rel
            new_dir = old_dir

        try:
            content = md_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        new_content = transform_links(content, old_dir, new_dir, path_map)
        if new_content != content:
            updates[new_path] = (content, new_content)

    return updates


def _set_toml_string_value(content: str, key: str, value: str) -> str:
    """Replace a top-level `key = "..."` scalar assignment in raw TOML text.

    Edits the text directly instead of round-tripping through tomllib/tomli_w,
    so comments and existing array formatting (e.g. due_dates, which tomli_w
    would reflow into [[due_dates]] sections once lines exceed its 100-char
    inline-table heuristic) are left untouched.
    """
    escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
    pattern = re.compile(rf'^{re.escape(key)}\s*=\s*".*"$', re.MULTILINE)
    new_content, count = pattern.subn(f'{key} = "{escaped_value}"', content, count=1)
    if count == 0:
        raise ValueError(f"Could not find top-level {key!r} assignment in course_settings.toml")
    return new_content


def _do_move(
    src: Path, dest: Path, repo_root: Path, use_git: bool,
    inner_renames: list[tuple[Path, Path]] | None = None,
) -> None:
    """Execute the physical move(s)."""
    case_rename = _is_case_only_rename(src, dest)

    if case_rename:
        import uuid
        temp_name = dest.parent / f".mv-tmp-{uuid.uuid4().hex[:8]}"
        if use_git:
            subprocess.run(["git", "mv", str(src), str(temp_name)], cwd=str(repo_root), check=True)
            subprocess.run(["git", "mv", str(temp_name), str(dest)], cwd=str(repo_root), check=True)
        else:
            src.rename(temp_name)
            temp_name.rename(dest)
    else:
        if use_git:
            subprocess.run(["git", "mv", str(src), str(dest)], cwd=str(repo_root), check=True)
        else:
            shutil.move(str(src), str(dest))

    if inner_renames:
        for inner_src, inner_dest in inner_renames:
            inner_case = _is_case_only_rename(inner_src, inner_dest)
            if inner_case:
                import uuid
                temp = inner_dest.parent / f".mv-tmp-{uuid.uuid4().hex[:8]}"
                if use_git:
                    subprocess.run(["git", "mv", str(inner_src), str(temp)], cwd=str(repo_root), check=True)
                    subprocess.run(["git", "mv", str(temp), str(inner_dest)], cwd=str(repo_root), check=True)
                else:
                    inner_src.rename(temp)
                    temp.rename(inner_dest)
            else:
                if use_git:
                    subprocess.run(["git", "mv", str(inner_src), str(inner_dest)], cwd=str(repo_root), check=True)
                else:
                    shutil.move(str(inner_src), str(inner_dest))


def _get_inner_renames(
    src: Path, dest: Path, src_rel: str, dest_rel: str,
) -> list[tuple[Path, Path]]:
    """Compute inner file renames for quiz/qbank folders after the directory move."""
    renames = []
    src_name = src.name
    dest_name = dest.name

    if src_name == dest_name:
        return renames

    parts = src_rel.split("/")
    if len(parts) < 2:
        return renames

    top_dir = parts[0]
    if top_dir == "quizzes":
        old_inner = dest / f"{src_name}.md"
        new_inner = dest / f"{dest_name}.md"
        if old_inner.exists() or (not dest.exists() and (src / f"{src_name}.md").exists()):
            renames.append((old_inner, new_inner))
    elif top_dir == "question_banks":
        old_inner = dest / f"{src_name}.toml"
        new_inner = dest / f"{dest_name}.toml"
        if old_inner.exists() or (not dest.exists() and (src / f"{src_name}.toml").exists()):
            renames.append((old_inner, new_inner))

    return renames


def _describe_changes(
    path_map: dict[str, str],
    manifest_changed: bool,
    manifest_updates: dict[str, str] | None,
    file_updates: dict[str, tuple[str, str]],
    module_order_updates: list[str] | None,
    course_settings_updates: dict[str, str] | None,
    noop: bool,
    verbose: bool,
) -> None:
    """Print summary or verbose output of changes."""
    prefix = "Would " if noop else ""

    if verbose or noop:
        for old, new in sorted(path_map.items()):
            print(f"  {prefix}move: {old} → {new}")

    files_moved = len(path_map)
    manifest_entries = sum(1 for o, n in path_map.items() if o != n) if manifest_changed else 0

    link_file_count = len(file_updates)
    link_total = 0
    if verbose or noop:
        for fpath, (old_content, new_content) in sorted(file_updates.items()):
            old_lines = old_content.splitlines()
            new_lines = new_content.splitlines()
            for i, (ol, nl) in enumerate(zip(old_lines, new_lines)):
                if ol != nl:
                    link_total += 1
                    if verbose or noop:
                        print(f"  {prefix}update link in {fpath}:{i+1}")
                        print(f"    From: {ol.strip()}")
                        print(f"    To:   {nl.strip()}")
    else:
        for fpath, (old_content, new_content) in file_updates.items():
            old_lines = old_content.splitlines()
            new_lines = new_content.splitlines()
            link_total += sum(1 for o, n in zip(old_lines, new_lines) if o != n)

    parts = []
    parts.append(f"{prefix}{'M' if not noop else 'm'}oved {files_moved} file(s)")
    if manifest_changed:
        parts.append(f"updated {manifest_entries} manifest entry/entries")
    if link_total:
        parts.append(f"updated {link_total} link(s) across {link_file_count} file(s)")
    if module_order_updates is not None:
        parts.append("updated module_order.toml")
    if course_settings_updates:
        fields = ", ".join(sorted(course_settings_updates))
        parts.append(f"updated {fields} in course_settings.toml")

    print(", ".join(parts) + ".")


def run_mv(src: Path, dest: Path, noop: bool = False, verbose: bool = False) -> None:
    """Move/rename a file or directory, updating manifest and all references."""
    src = src.absolute()
    dest = dest.absolute()

    repo_root = find_repo_root(src)
    if repo_root is None:
        repo_root = find_repo_root(dest)
    if repo_root is None:
        raise ValueError(
            "Could not find course repo root.\n"
            "Looked for course_settings/course_settings.toml by walking up from\n"
            f"  {src}\n"
            "Create course_settings/course_settings.toml (it can be empty) to mark the repo root."
        )

    validate_move(src, dest, repo_root)

    src_rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
    case_rename = _is_case_only_rename(src, dest)
    if case_rename:
        dest_rel = _compute_case_rename_dest_rel(src, dest, repo_root)
    else:
        dest_rel = dest.resolve().relative_to(repo_root.resolve()).as_posix()

    path_map = build_path_map(src, dest, repo_root)

    manifest_path = repo_root / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    new_manifest = compute_manifest_updates(manifest, path_map)
    manifest_changed = new_manifest != manifest

    file_updates = compute_file_updates(repo_root, path_map)

    module_order_updates = compute_module_order_updates(repo_root, path_map)
    course_settings_updates = compute_course_settings_updates(repo_root, path_map)

    inner_renames_paths: list[tuple[Path, Path]] = []
    if src.is_dir():
        inner_renames_paths = _get_inner_renames(src, dest, src_rel, dest_rel)

    _describe_changes(
        path_map, manifest_changed, None, file_updates,
        module_order_updates, course_settings_updates, noop, verbose,
    )

    if noop:
        return

    use_git = _is_git_repo(repo_root) and _has_tracked_content(repo_root, src)
    _do_move(src, dest, repo_root, use_git, inner_renames_paths)

    for file_new_path, (_, new_content) in file_updates.items():
        target = repo_root / file_new_path
        target.write_text(new_content)

    if manifest_changed:
        manifest_lib.flush(manifest_path, new_manifest)

    if module_order_updates is not None:
        order_path = repo_root / "course_settings" / "module_order.toml"
        with order_path.open("rb") as f:
            data = tomllib.load(f)
        data["order"] = module_order_updates
        with order_path.open("wb") as f:
            tomli_w.dump(data, f)

    if course_settings_updates:
        settings_path = repo_root / "course_settings" / "course_settings.toml"
        content = settings_path.read_text(encoding="utf-8")
        for field, new_value in course_settings_updates.items():
            content = _set_toml_string_value(content, field, new_value)
        settings_path.write_text(content, encoding="utf-8")
