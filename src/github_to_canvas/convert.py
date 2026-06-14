"""Markdown → HTML conversion and snippet preprocessing."""
from __future__ import annotations

import re
from pathlib import Path

import pypandoc


_SNIPPET_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

# Matches [outer](prefix[inner](inner-url)suffix) — used to expand snippet refs
# that are embedded inside the URL of a surrounding Markdown link.
_URL_EMBEDDED_SNIPPET_RE = re.compile(
    r"\[([^\]]*)\]\(([^\[\]]*)\[([^\]]*)\]\(([^)]+)\)([^)]*)\)"
)


def preprocess_snippets(text: str, source_file: Path, snippets_dir: Path) -> str:
    """Replace [text](../snippets/foo.md) links with the snippet file contents.

    Also handles snippet refs embedded inside link URLs, e.g.:
      [Go to modules](https://example.com/courses/[CANVAS_COURSE_ID](../snippets/inline/CANVAS_COURSE_ID.md)/modules)

    Nested snippet includes (a snippet that links to another snippet) are not
    expanded; an error is printed and the inner link is left as-is.
    """
    resolved_snippets_dir = snippets_dir.resolve()

    def _load_snippet(link_target: str) -> tuple[str, Path] | None:
        """Resolve link_target to a snippet file. Returns (content, path) or None."""
        target_path = (source_file.parent / link_target).resolve()
        if not target_path.is_relative_to(resolved_snippets_dir):
            return None
        if not target_path.exists():
            print(f"  ERROR: snippet not found: {target_path}")
            return None
        return target_path.read_text(), target_path

    def _replace_url_embedded(m: re.Match) -> str:
        """Expand a snippet ref embedded in a link URL."""
        outer_text = m.group(1)
        url_prefix = m.group(2)
        inner_url = m.group(4)
        url_suffix = m.group(5)
        result = _load_snippet(inner_url)
        if result is None:
            return m.group(0)
        content, _ = result
        return f"[{outer_text}]({url_prefix}{content.strip()}{url_suffix})"

    def _replace(m: re.Match) -> str:
        link_target = m.group(2)
        result = _load_snippet(link_target)
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

    # Pass 1: expand snippet refs embedded inside link URLs
    text = _URL_EMBEDDED_SNIPPET_RE.sub(_replace_url_embedded, text)
    # Pass 2: expand direct snippet refs
    return _SNIPPET_LINK_RE.sub(_replace, text)


def markdown_to_html(text: str) -> str:
    return pypandoc.convert_text(
        text,
        to="html5",
        format="markdown+smart",
        extra_args=["--mathml"],
    )
