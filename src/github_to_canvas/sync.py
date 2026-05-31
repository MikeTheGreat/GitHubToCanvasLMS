"""Main sync pipeline."""
from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import Any

import yaml

from . import canvas_api as capi
from . import manifest as manifest_lib
from .config import Config
from .convert import markdown_to_html, preprocess_snippets
from .link_rewrite import extract_local_refs, infer_canvas_type, rewrite_links


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_text). Body excludes the frontmatter block."""
    if not text.startswith("---\n"):
        return {}, text
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return {}, text
    return yaml.safe_load(text[4:end]) or {}, text[end + 5:]


_MODULE_LINK_RE = re.compile(r"^\s*-\s+\[([^\]]+)\]\(([^)]+)\)")
_MODULE_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)")


def parse_module_body(body: str, module_file: Path, course_root: Path) -> list[dict[str, Any]]:
    """Parse a module body into an ordered list of item dicts."""
    items: list[dict[str, Any]] = []
    for line in body.splitlines():
        link_m = _MODULE_LINK_RE.match(line)
        if link_m:
            title, href = link_m.group(1), link_m.group(2)
            resolved = (module_file.parent / href).resolve()
            local_path = resolved.relative_to(course_root.resolve()).as_posix()
            items.append({"type": "content", "title": title, "local_path": local_path})
            continue
        header_m = _MODULE_HEADER_RE.match(line)
        if header_m:
            items.append({"type": "SubHeader", "title": header_m.group(1)})
    return items


def run_sync(config: Config, repo_path: Path, force_uploads: bool = False) -> None:
    """Main sync pipeline: assets → content → modules."""
    manifest_path = repo_path / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    course = capi.get_course(config)
    snippets_dir = repo_path / "snippets"

    # 1. Assets (depth-first, files before subdirs, alphabetical)
    assets_dir = repo_path / "assets"
    if assets_dir.exists():
        _walk_assets(course, assets_dir, assets_dir, repo_path, manifest, manifest_path, force_uploads)

    # 2. Content folders (alphabetical, excl. assets, modules, snippets, hidden)
    skip = {"assets", "modules", "snippets"}
    content_dirs = sorted(
        d for d in repo_path.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name not in skip
    )
    for content_dir in content_dirs:
        for md_file in sorted(content_dir.glob("*.md")):
            _sync_content_file(
                course, md_file, repo_path, snippets_dir, manifest, manifest_path,
                config.course_id, force_uploads,
            )

    # 3. Modules (alphabetical)
    modules_dir = repo_path / "modules"
    if modules_dir.exists():
        for md_file in sorted(modules_dir.glob("*.md")):
            _sync_module(course, md_file, repo_path, manifest, manifest_path, force_uploads)


def _walk_assets(
    course, dir_path: Path, assets_root: Path, repo_root: Path, manifest, manifest_path,
    force_uploads: bool = False,
) -> None:
    entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_dir(), p.name))
    for entry in entries:
        if entry.is_file():
            local_key = entry.relative_to(repo_root).as_posix()
            if not manifest_lib.needs_sync(manifest, local_key, entry, force_uploads):
                continue
            print(f"Uploading asset: {local_key}")
            canvas_entry = capi.upload_asset(course, entry, assets_root)
            manifest_lib.record(
                manifest, manifest_path, local_key,
                canvas_entry["canvas_id"], "file",
                extra={"canvas_url": canvas_entry["canvas_url"]},
            )
        elif entry.is_dir():
            _walk_assets(course, entry, assets_root, repo_root, manifest, manifest_path, force_uploads)


def _sync_content_file(
    course, md_file: Path, repo_root: Path, snippets_dir: Path,
    manifest, manifest_path, course_id: int, force_uploads: bool = False,
) -> None:
    local_key = md_file.relative_to(repo_root).as_posix()

    if not manifest_lib.needs_sync(manifest, local_key, md_file, force_uploads):
        print(f"Skipping (up-to-date): {local_key}")
        return

    print(f"Processing: {local_key}")

    frontmatter, body = parse_frontmatter(md_file.read_text())
    body = preprocess_snippets(body, md_file, snippets_dir)
    html = markdown_to_html(body)
    canvas_type = infer_canvas_type(local_key)

    def stub_creator(ref_local_path: str, ref_canvas_type: str) -> dict[str, Any]:
        title = Path(ref_local_path).stem.replace("-", " ").replace("_", " ").title()
        print(f"  Stub-creating: {ref_local_path} (referenced but not yet synced)")
        entry = capi.create_stub(course, ref_canvas_type, title)
        extra = {k: v for k, v in entry.items() if k not in ("canvas_id", "canvas_type")}
        manifest_lib.record(
            manifest, manifest_path, ref_local_path,
            entry["canvas_id"], ref_canvas_type, extra=extra or None,
        )
        return entry

    html = rewrite_links(html, md_file, repo_root, manifest, course_id, stub_creator)

    existing = manifest.get(local_key)
    title = frontmatter.get("title", md_file.stem)
    published = frontmatter.get("published", False)

    print(f"  Uploading: {local_key}")
    if canvas_type == "page":
        canvas_url = existing.get("canvas_url") if existing else None
        entry = capi.create_or_update_page(
            course, canvas_url, title, html,
            published=published,
            editing_roles=frontmatter.get("editing_roles", "teachers"),
        )
        manifest_lib.record(
            manifest, manifest_path, local_key,
            entry["canvas_id"], "page", extra={"canvas_url": entry["canvas_url"]},
        )
    elif canvas_type == "assignment":
        canvas_id = existing["canvas_id"] if existing else None
        extra: dict[str, Any] = {}
        for key in ("points_possible", "due_at", "submission_types"):
            if key in frontmatter:
                extra[key] = frontmatter[key]
        entry = capi.create_or_update_assignment(
            course, canvas_id, title, html, published=published, **extra
        )
        manifest_lib.record(manifest, manifest_path, local_key, entry["canvas_id"], "assignment")
    elif canvas_type == "discussion":
        canvas_id = existing["canvas_id"] if existing else None
        extra = {}
        if "require_initial_post" in frontmatter:
            extra["require_initial_post"] = frontmatter["require_initial_post"]
        entry = capi.create_or_update_discussion(
            course, canvas_id, title, html, published=published, **extra
        )
        manifest_lib.record(manifest, manifest_path, local_key, entry["canvas_id"], "discussion")


def _sync_module(
    course, md_file: Path, repo_root: Path, manifest, manifest_path,
    force_uploads: bool = False,
) -> None:
    local_key = md_file.relative_to(repo_root).as_posix()

    if not manifest_lib.needs_sync(manifest, local_key, md_file, force_uploads):
        print(f"Skipping (up-to-date): {local_key}")
        return

    print(f"Syncing module: {local_key}")

    frontmatter, body = parse_frontmatter(md_file.read_text())
    items = parse_module_body(body, md_file, repo_root)

    existing = manifest.get(local_key)
    title = frontmatter.get("title", md_file.stem)

    module_kwargs: dict[str, Any] = {}
    for key in ("published", "unlock_at", "require_sequential_progress"):
        if key in frontmatter:
            module_kwargs[key] = frontmatter[key]

    module = capi.create_or_update_module(
        course, existing["canvas_id"] if existing else None, title, **module_kwargs
    )
    capi.clear_module_items(module)

    canvas_item_ids: dict[str, int] = {}
    for item in items:
        item_id = capi.add_module_item(module, item, manifest)
        if item["type"] == "content":
            canvas_item_ids[item["local_path"]] = item_id

    manifest_lib.record(
        manifest, manifest_path, local_key,
        module.id, "module", extra={"canvas_item_ids": canvas_item_ids},
    )


def _get_file_refs(
    local_key: str, file_path: Path, repo_root: Path, snippets_dir: Path
) -> set[str]:
    """Return the set of locally-referenced file keys from any file type, without uploading."""
    folder = local_key.split("/")[0]
    if folder == "modules":
        _, body = parse_frontmatter(file_path.read_text())
        items = parse_module_body(body, file_path, repo_root)
        return {item["local_path"] for item in items if item["type"] == "content"}
    if folder in ("assets", "snippets"):
        return set()
    _, body = parse_frontmatter(file_path.read_text())
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


def run_targeted_sync(
    config: Config,
    repo_path: Path,
    recursive_targets: list[str],
    single_targets: list[str],
    force_uploads: bool = False,
) -> None:
    """Sync only the specified targets.

    -t (recursive_targets) runs first: BFS each target plus all transitively referenced files.
    -s (single_targets) runs second, independently: no BFS, no dependency on -t's visited set.
    If -t already uploaded a file and updated its manifest timestamp, -s will skip it via needs_sync.
    """
    manifest_path = repo_path / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    course = capi.get_course(config)
    snippets_dir = repo_path / "snippets"
    assets_root = repo_path / "assets"

    visited: set[str] = set()

    def _process(local_key: str) -> None:
        file_path = repo_path / local_key
        folder = local_key.split("/")[0]
        if folder == "assets":
            if manifest_lib.needs_sync(manifest, local_key, file_path, force_uploads):
                print(f"Uploading asset: {local_key}")
                canvas_entry = capi.upload_asset(course, file_path, assets_root)
                manifest_lib.record(
                    manifest, manifest_path, local_key,
                    canvas_entry["canvas_id"], "file",
                    extra={"canvas_url": canvas_entry["canvas_url"]},
                )
            else:
                print(f"Skipping (up-to-date): {local_key}")
        elif folder == "modules":
            _sync_module(course, file_path, repo_path, manifest, manifest_path, force_uploads)
        else:
            _sync_content_file(
                course, file_path, repo_path, snippets_dir, manifest, manifest_path,
                config.course_id, force_uploads,
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
