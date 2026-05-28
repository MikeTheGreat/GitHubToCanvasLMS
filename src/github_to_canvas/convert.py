"""Markdown → HTML conversion and snippet preprocessing."""
from __future__ import annotations

import re
from pathlib import Path

import pypandoc


_SNIPPET_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def preprocess_snippets(text: str, source_file: Path, snippets_dir: Path) -> str:
    """Replace [text](../snippets/foo.md) links with the snippet file contents."""

    def _replace(m: re.Match) -> str:
        link_target = m.group(2)
        target_path = (source_file.parent / link_target).resolve()
        if not target_path.is_relative_to(snippets_dir.resolve()):
            return m.group(0)  # not a snippet link — leave unchanged
        if not target_path.exists():
            print(f"  ERROR: snippet not found: {target_path}")
            return m.group(0)
        return target_path.read_text()

    return _SNIPPET_LINK_RE.sub(_replace, text)


def markdown_to_html(text: str) -> str:
    return pypandoc.convert_text(
        text,
        to="html5",
        format="markdown+smart",
        extra_args=["--mathml"],
    )
