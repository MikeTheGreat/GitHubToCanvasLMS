"""Markdown → HTML conversion and snippet preprocessing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pypandoc
import yaml

_SNIPPET_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

# Matches $path.md$ — an inline snippet ref embedded anywhere in text, including
# inside Markdown link URLs.  Requiring the .md suffix avoids false positives on
# math expressions ($x^2$) and currency values ($5.99$).
_INLINE_SNIPPET_RE = re.compile(r"\$([^$\n]+\.md)\$")

# Matches a standalone [PASTE_SNIPPET_INTO_FRONTMATTER](path) line — used to
# merge a shared YAML snippet's keys into a file's frontmatter. Must be the
# only thing on the line (surrounding whitespace is allowed).
_FRONTMATTER_SNIPPET_RE = re.compile(
    r"^\[PASTE_SNIPPET_INTO_FRONTMATTER\]\(([^)]+)\)$"
)

_IMG_TAG_RE = re.compile(r"<img\b[^>]*/?>", re.IGNORECASE)
_ALT_ATTR_RE = re.compile(r'\balt="([^"]*)"')
_ROLE_ATTR_RE = re.compile(r'\brole="[^"]*"')


def warn(msg: str, errors: list[str] | None) -> None:
    """Print a message with the standard two-space indent and, if an error
    accumulator is provided, record it there as well."""
    print(f"  {msg}")
    if errors is not None:
        errors.append(msg)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_text). Body excludes the frontmatter block."""
    if not text.startswith("---\n"):
        return {}, text
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return {}, text
    return yaml.safe_load(text[4:end]) or {}, text[end + 5 :]


def preprocess_snippets(
    text: str,
    source_file: Path,
    snippets_dir: Path,
    errors: list[str] | None = None,
) -> str:
    """Replace snippet references with the snippet file's contents.

    Two forms are supported:

    1. **Inline** — ``$path.md$`` anywhere in text (path relative to source file).
       The content is stripped of leading/trailing whitespace, making it safe
       to embed inside a Markdown link URL::

           [Modules](https://example.com/courses/$../snippets/inline/CANVAS_COURSE_ID.md$/modules)

    2. **Block** — ``[display text](path.md)`` where the path resolves into
       the snippets directory.  The full file content replaces the link.
       Useful for reusable policy paragraphs, office-hour blocks, etc.

    Nested snippet includes (a snippet that links to another snippet) are not
    expanded; an error is printed and the inner link is left as-is.
    """
    resolved_snippets_dir = snippets_dir.resolve()

    try:
        rel_source = source_file.relative_to(snippets_dir.parent)
    except ValueError:
        rel_source = source_file

    def _report_error(msg: str) -> None:
        warn(msg, errors)

    def _load_snippet(link_target: str, snippet_ref: str, is_inline: bool) -> tuple[str, Path] | None:
        """Resolve link_target to a snippet file. Returns (content, path) or None."""
        target_path = (source_file.parent / link_target).resolve()
        if not target_path.is_relative_to(resolved_snippets_dir):
            if is_inline or "snippets" in Path(link_target).parts:
                _report_error(
                    f"ERROR: {rel_source}: snippet path {snippet_ref} "
                    f"resolves outside the snippets directory — check that the "
                    f"relative path is correct for the file's current location"
                )
            return None
        if not target_path.exists():
            _report_error(f"ERROR: snippet not found: {target_path}")
            return None
        return target_path.read_text(), target_path

    def _replace_inline(m: re.Match) -> str:
        """Expand a $path.md$ inline snippet ref (content is stripped)."""
        result = _load_snippet(m.group(1), m.group(0), is_inline=True)
        if result is None:
            return m.group(0)
        content, _ = result
        return content.strip()

    def _replace(m: re.Match) -> str:
        link_target = m.group(2)
        result = _load_snippet(link_target, m.group(0), is_inline=False)
        if result is None:
            return m.group(0)
        content, target_path = result
        for inner_m in _SNIPPET_LINK_RE.finditer(content):
            inner_path = (target_path.parent / inner_m.group(2)).resolve()
            if inner_path.is_relative_to(resolved_snippets_dir):
                print(
                    f"  ERROR: nested snippet include not supported: "
                    f"{inner_m.group(2)} inside {target_path.name}"
                )
        return content

    # Pass 1: expand $path.md$ inline snippet refs (stripped, safe for URLs)
    text = _INLINE_SNIPPET_RE.sub(_replace_inline, text)
    # Pass 2: expand [text](snippet_path) block snippet links
    return _SNIPPET_LINK_RE.sub(_replace, text)


def find_referenced_snippets(text: str, source_file: Path, snippets_dir: Path) -> set[Path]:
    """Return the set of existing snippet files referenced anywhere in text.

    For staleness checks only: resolves every ``$path.md$`` / ``[text](path)``
    candidate (this also catches ``PASTE_SNIPPET_INTO_FRONTMATTER`` references,
    which use the same ``[text](path)`` syntax) and keeps the ones that land
    inside ``snippets_dir`` and exist. Unlike ``preprocess_snippets`` /
    ``expand_frontmatter_snippets``, this never reports errors — it's a
    passive probe, not part of the expansion pipeline.
    """
    resolved_snippets_dir = snippets_dir.resolve()
    found: set[Path] = set()

    def _maybe_add(link_target: str) -> None:
        target_path = (source_file.parent / link_target).resolve()
        if target_path.is_relative_to(resolved_snippets_dir) and target_path.exists():
            found.add(target_path)

    for m in _INLINE_SNIPPET_RE.finditer(text):
        _maybe_add(m.group(1))
    for m in _SNIPPET_LINK_RE.finditer(text):
        _maybe_add(m.group(2))
    return found


def expand_frontmatter_snippets(
    frontmatter: dict[str, Any],
    body: str,
    source_file: Path,
    snippets_dir: Path,
    errors: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    """Merge PASTE_SNIPPET_INTO_FRONTMATTER references into frontmatter.

    A file may lead its body with one or more lines of the form::

        [PASTE_SNIPPET_INTO_FRONTMATTER](../../snippets/worksheet-defaults.md)
        [PASTE_SNIPPET_INTO_FRONTMATTER](../../snippets/another-snippet.md)

    Blank/whitespace-only lines between or around them are ignored. Each
    referenced file is parsed as a YAML mapping and its keys are merged into
    a copy of ``frontmatter`` (later snippets override earlier ones; the
    file's own frontmatter always wins over snippet values). The marker
    lines are stripped from the returned body. If the body has no such
    leading lines, ``(frontmatter, body)`` is returned unchanged.
    """
    resolved_snippets_dir = snippets_dir.resolve()
    try:
        rel_source = source_file.relative_to(snippets_dir.parent)
    except ValueError:
        rel_source = source_file

    def _report_error(msg: str) -> None:
        warn(msg, errors)

    lines = body.splitlines(keepends=True)

    # Lookahead: only enter "marker mode" if the first non-blank line is a
    # PASTE_SNIPPET_INTO_FRONTMATTER reference, so ordinary files (which may
    # happen to start with blank lines) are left completely untouched.
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i >= len(lines) or not _FRONTMATTER_SNIPPET_RE.match(lines[i].strip()):
        return frontmatter, body

    defaults: dict[str, Any] = {}
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "":
            i += 1
            continue
        m = _FRONTMATTER_SNIPPET_RE.match(stripped)
        if not m:
            break
        i += 1
        link_target = m.group(1)
        target_path = (source_file.parent / link_target).resolve()
        if not target_path.is_relative_to(resolved_snippets_dir):
            _report_error(
                f"ERROR: {rel_source}: frontmatter snippet path {link_target} "
                f"resolves outside the snippets directory — check that the "
                f"relative path is correct for the file's current location"
            )
            continue
        if not target_path.exists():
            _report_error(f"ERROR: {rel_source}: frontmatter snippet not found: {target_path}")
            continue
        try:
            snippet_data = yaml.safe_load(target_path.read_text()) or {}
        except yaml.YAMLError as exc:
            _report_error(
                f"ERROR: {rel_source}: malformed frontmatter snippet {target_path.name}: {exc}"
            )
            continue
        if not isinstance(snippet_data, dict):
            _report_error(
                f"ERROR: {rel_source}: frontmatter snippet {target_path.name} must contain "
                f"a YAML mapping (got {type(snippet_data).__name__})"
            )
            continue
        defaults.update(snippet_data)

    remaining_body = "".join(lines[i:])
    merged = {**defaults, **frontmatter}
    return merged, remaining_body


def mark_decorative_images(html: str) -> str:
    """Mark alt-less images as decorative for the Canvas accessibility checker.

    Pandoc drops the alt attribute entirely for ``![](image.png)``, and the
    Canvas accessibility checker flags such images as missing alt text. An
    ``<img>`` whose alt is missing or whitespace-only is treated as
    decorative: it gets ``alt=""`` plus ``role="presentation"`` — the same
    markup the Canvas editor writes when an image is marked decorative.
    Images with real alt text, or with an explicit role attribute already
    set, are left untouched.
    """

    def _fix(m: re.Match) -> str:
        tag = m.group(0)
        alt_m = _ALT_ATTR_RE.search(tag)
        if alt_m is not None and alt_m.group(1).strip():
            return tag  # real alt text — not decorative
        if alt_m is not None and alt_m.group(1):
            # whitespace-only alt — normalize to alt=""
            tag = tag[: alt_m.start(1)] + tag[alt_m.end(1) :]
        additions = []
        if alt_m is None:
            additions.append('alt=""')
        if _ROLE_ATTR_RE.search(tag) is None:
            additions.append('role="presentation"')
        if not additions:
            return tag
        if tag.endswith("/>"):
            head, close = tag[:-2].rstrip(), " />"
        else:
            head, close = tag[:-1].rstrip(), ">"
        return f"{head} {' '.join(additions)}{close}"

    return _IMG_TAG_RE.sub(_fix, html)


def markdown_to_html(text: str) -> str:
    html = pypandoc.convert_text(
        text,
        to="html5",
        format="markdown+smart",
        extra_args=["--mathml"],
    )
    return mark_decorative_images(html)
