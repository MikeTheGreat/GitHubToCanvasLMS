"""Markdown → HTML conversion and snippet preprocessing."""
from __future__ import annotations

import re
from pathlib import Path

import pypandoc


_SNIPPET_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def preprocess_snippets(text: str, source_file: Path, snippets_dir: Path) -> str:
    """Replace [text](../snippets/foo.md) links with the snippet file contents.

    Nested snippet includes (a snippet that links to another snippet) are not
    expanded; an error is printed and the inner link is left as-is.
    """
    resolved_snippets_dir = snippets_dir.resolve()

    def _replace(m: re.Match) -> str:
        link_target = m.group(2)
        target_path = (source_file.parent / link_target).resolve()
        if not target_path.is_relative_to(resolved_snippets_dir):
            return m.group(0)
        if not target_path.exists():
            print(f"  ERROR: snippet not found: {target_path}")
            return m.group(0)
        content = target_path.read_text()
        for inner_m in _SNIPPET_LINK_RE.finditer(content):
            inner_path = (target_path.parent / inner_m.group(2)).resolve()
            if inner_path.is_relative_to(resolved_snippets_dir):
                print(
                    f"  ERROR: nested snippet include not supported: "
                    f"{inner_m.group(2)} inside {target_path.name}"
                )
        return content

    return _SNIPPET_LINK_RE.sub(_replace, text)


def markdown_to_html(text: str) -> str:
    return pypandoc.convert_text(
        text,
        to="html5",
        format="markdown+smart",
        extra_args=["--mathml"],
    )
