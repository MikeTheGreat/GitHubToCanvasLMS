"""Markdown → HTML conversion and snippet preprocessing."""

from __future__ import annotations

import re
from pathlib import Path

import pypandoc

_SNIPPET_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

# Matches $path.md$ — an inline snippet ref embedded anywhere in text, including
# inside Markdown link URLs.  Requiring the .md suffix avoids false positives on
# math expressions ($x^2$) and currency values ($5.99$).
_INLINE_SNIPPET_RE = re.compile(r"\$([^$\n]+\.md)\$")


def preprocess_snippets(text: str, source_file: Path, snippets_dir: Path) -> str:
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

    def _load_snippet(link_target: str) -> tuple[str, Path] | None:
        """Resolve link_target to a snippet file. Returns (content, path) or None."""
        target_path = (source_file.parent / link_target).resolve()
        if not target_path.is_relative_to(resolved_snippets_dir):
            return None
        if not target_path.exists():
            print(f"  ERROR: snippet not found: {target_path}")
            return None
        return target_path.read_text(), target_path

    def _replace_inline(m: re.Match) -> str:
        """Expand a $path.md$ inline snippet ref (content is stripped)."""
        result = _load_snippet(m.group(1))
        if result is None:
            return m.group(0)
        content, _ = result
        return content.strip()

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

    # Pass 1: expand $path.md$ inline snippet refs (stripped, safe for URLs)
    text = _INLINE_SNIPPET_RE.sub(_replace_inline, text)
    # Pass 2: expand [text](snippet_path) block snippet links
    return _SNIPPET_LINK_RE.sub(_replace, text)


def markdown_to_html(text: str) -> str:
    return pypandoc.convert_text(
        text,
        to="html5",
        format="markdown+smart",
        extra_args=["--mathml"],
    )
