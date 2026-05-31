"""Post-Pandoc HTML link/img rewriting."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

ManifestDict = dict[str, dict[str, Any]]

_IMG_RE = re.compile(r"<img\b[^>]*/?>", re.IGNORECASE)
_A_RE = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'\bsrc="([^"]*)"')
_HREF_ATTR_RE = re.compile(r'\bhref="([^"]*)"')

_FOLDER_TO_TYPE = {
    "pages": "page",
    "assignments": "assignment",
    "discussions": "discussion",
    "assets": "file",
    "quizzes": "quiz",
}


def infer_canvas_type(local_path: str) -> str:
    folder = local_path.split("/")[0]
    return _FOLDER_TO_TYPE.get(folder, "page")


def canvas_content_url(entry: dict[str, Any], course_id: int) -> str:
    t = entry["canvas_type"]
    if t == "file":
        return entry["canvas_url"]
    if t == "page":
        return f"/courses/{course_id}/pages/{entry['canvas_url']}"
    if t == "assignment":
        return f"/courses/{course_id}/assignments/{entry['canvas_id']}"
    if t == "discussion":
        return f"/courses/{course_id}/discussion_topics/{entry['canvas_id']}"
    if t == "quiz":
        return f"/courses/{course_id}/quizzes/{entry['canvas_id']}"
    raise ValueError(f"Unknown canvas_type: {t!r}")


def _to_local_key(href: str, source_file: Path, course_root: Path) -> str | None:
    """Convert a relative href to a course-root-relative key, or None if external/anchor."""
    if href.startswith(("http://", "https://", "#", "mailto:")):
        return None
    resolved = (source_file.parent / href).resolve()
    try:
        return resolved.relative_to(course_root.resolve()).as_posix()
    except ValueError:
        return None


def extract_local_refs(html: str, source_file: Path, course_root: Path) -> set[str]:
    """Return local course-root-relative keys for all local <img src>/<a href> refs in html."""
    keys: set[str] = set()
    for m in _IMG_RE.finditer(html):
        src_m = _SRC_ATTR_RE.search(m.group(0))
        if src_m:
            key = _to_local_key(src_m.group(1), source_file, course_root)
            if key:
                keys.add(key)
    for m in _A_RE.finditer(html):
        href_m = _HREF_ATTR_RE.search(m.group(0))
        if href_m:
            key = _to_local_key(href_m.group(1), source_file, course_root)
            if key:
                keys.add(key)
    return keys


def rewrite_links(
    html: str,
    source_file: Path,
    course_root: Path,
    manifest: ManifestDict,
    course_id: int,
    stub_creator: Callable[[str, str], dict[str, Any]],
) -> str:
    """Rewrite local <img src> and <a href> references to Canvas URLs.

    `stub_creator(local_path, canvas_type)` is called when a referenced file
    exists locally but has no manifest entry yet. It must create a Canvas stub,
    update the manifest, flush to disk, and return the new manifest entry.
    """

    def _resolve_entry(attr_value: str) -> tuple[str | None, dict[str, Any] | None]:
        """Return (local_key, entry) or (None, None) for external/missing."""
        local_key = _to_local_key(attr_value, source_file, course_root)
        if local_key is None:
            return None, None
        local_file = course_root / local_key
        if not local_file.exists():
            print(f"  ERROR: local file not found, removing tag: {local_key}")
            return local_key, None  # signals removal
        if local_key not in manifest:
            entry = stub_creator(local_key, infer_canvas_type(local_key))
            manifest[local_key] = entry
        return local_key, manifest[local_key]

    def _replace_img(m: re.Match) -> str:
        tag = m.group(0)
        src_m = _SRC_ATTR_RE.search(tag)
        if src_m is None:
            return tag
        local_key, entry = _resolve_entry(src_m.group(1))
        if local_key is None:
            return tag  # external — unchanged
        if entry is None:
            return ""  # local file missing — remove tag
        new_url = canvas_content_url(entry, course_id)
        return tag[: src_m.start(1)] + new_url + tag[src_m.end(1) :]

    def _replace_a(m: re.Match) -> str:
        tag = m.group(0)
        href_m = _HREF_ATTR_RE.search(tag)
        if href_m is None:
            return tag
        local_key, entry = _resolve_entry(href_m.group(1))
        if local_key is None:
            return tag  # external/anchor — unchanged
        if entry is None:
            return ""  # local file missing — remove tag
        new_url = canvas_content_url(entry, course_id)
        return tag[: href_m.start(1)] + new_url + tag[href_m.end(1) :]

    html = _IMG_RE.sub(_replace_img, html)
    html = _A_RE.sub(_replace_a, html)
    return html
