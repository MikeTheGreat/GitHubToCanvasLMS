"""Unit tests: snippet preprocessing and Pandoc conversion."""
from __future__ import annotations

from pathlib import Path

import pytest

from github_to_canvas.convert import markdown_to_html, preprocess_snippets

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
SNIPPETS_DIR = FIXTURES / "snippets"


def _make_snippet(tmp_path: Path, name: str, content: str) -> tuple[Path, Path]:
    """Create a snippets/ dir and one snippet file; return (snippets_dir, snippet_path)."""
    snippets_dir = tmp_path / "snippets"
    snippets_dir.mkdir()
    (snippets_dir / name).write_text(content)
    return snippets_dir, snippets_dir / name


# ---------------------------------------------------------------------------
# preprocess_snippets
# ---------------------------------------------------------------------------

def test_snippet_link_replaced(tmp_path: Path) -> None:
    snippets_dir, _ = _make_snippet(tmp_path, "tip.md", "Check the docs first.")
    source = tmp_path / "pages" / "notes.md"
    source.parent.mkdir()
    text = "Hint: [Tip](../snippets/tip.md)\n"
    result = preprocess_snippets(text, source, snippets_dir)
    assert "Check the docs first." in result
    assert "[Tip](" not in result


def test_non_snippet_link_unchanged(tmp_path: Path) -> None:
    snippets_dir = tmp_path / "snippets"
    snippets_dir.mkdir()
    source = tmp_path / "pages" / "notes.md"
    source.parent.mkdir()
    text = "See [Assignment](../assignments/week1.md) for work.\n"
    result = preprocess_snippets(text, source, snippets_dir)
    assert result == text


def test_external_link_unchanged(tmp_path: Path) -> None:
    snippets_dir = tmp_path / "snippets"
    snippets_dir.mkdir()
    source = tmp_path / "pages" / "notes.md"
    source.parent.mkdir()
    text = "Visit [Canvas](https://canvas.instructure.com).\n"
    result = preprocess_snippets(text, source, snippets_dir)
    assert result == text


def test_anchor_link_unchanged(tmp_path: Path) -> None:
    snippets_dir = tmp_path / "snippets"
    snippets_dir.mkdir()
    source = tmp_path / "pages" / "notes.md"
    source.parent.mkdir()
    text = "Jump to [section](#intro).\n"
    result = preprocess_snippets(text, source, snippets_dir)
    assert result == text


def test_missing_snippet_prints_error_and_leaves_link(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    snippets_dir = tmp_path / "snippets"
    snippets_dir.mkdir()
    source = tmp_path / "pages" / "notes.md"
    source.parent.mkdir()
    text = "[Missing](../snippets/gone.md)\n"
    result = preprocess_snippets(text, source, snippets_dir)
    assert "[Missing](" in result          # link left unchanged
    captured = capsys.readouterr()
    assert "ERROR" in captured.out
    assert "gone.md" in captured.out


def test_multiple_snippets_expanded(tmp_path: Path) -> None:
    snippets_dir = tmp_path / "snippets"
    snippets_dir.mkdir()
    (snippets_dir / "a.md").write_text("ATEXT")
    (snippets_dir / "b.md").write_text("BTEXT")
    source = tmp_path / "pages" / "notes.md"
    source.parent.mkdir()
    text = "[A](../snippets/a.md) and [B](../snippets/b.md)"
    result = preprocess_snippets(text, source, snippets_dir)
    assert "ATEXT" in result
    assert "BTEXT" in result
    assert "[A](" not in result
    assert "[B](" not in result


def test_nested_snippet_prints_error_inner_not_expanded(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    snippets_dir, _ = _make_snippet(
        tmp_path, "outer.md", "Outer text. [Inner](inner.md) more text."
    )
    (snippets_dir / "inner.md").write_text("INNER CONTENT")
    source = tmp_path / "pages" / "notes.md"
    source.parent.mkdir()
    text = "[Outer](../snippets/outer.md)\n"
    result = preprocess_snippets(text, source, snippets_dir)
    # outer snippet content IS included
    assert "Outer text." in result
    # inner snippet link left as plain link, not expanded
    assert "INNER CONTENT" not in result
    assert "[Inner](inner.md)" in result
    # error printed
    captured = capsys.readouterr()
    assert "ERROR" in captured.out
    assert "nested" in captured.out.lower()


def test_snippet_content_from_fixture() -> None:
    """Smoke-test against the committed fixture snippets."""
    source = FIXTURES / "pages" / "syllabus.md"
    text = source.read_text()
    result = preprocess_snippets(text, source, SNIPPETS_DIR)
    assert "Monday and Wednesday" in result
    assert "[My Office Hours](" not in result


def test_non_snippet_links_in_fixture_unchanged() -> None:
    source = FIXTURES / "pages" / "syllabus.md"
    text = source.read_text()
    result = preprocess_snippets(text, source, SNIPPETS_DIR)
    # Cross-file content link must remain — link rewriter handles it later
    assert "[Week 1 Assignment](../assignments/week1.md)" in result


# ---------------------------------------------------------------------------
# markdown_to_html
# ---------------------------------------------------------------------------

def test_markdown_to_html_basic() -> None:
    html = markdown_to_html("# Hello\n\nWorld.\n")
    assert "<h1" in html
    assert "Hello" in html
    assert "<p>World.</p>" in html


def test_markdown_to_html_no_standalone() -> None:
    html = markdown_to_html("# Title\n")
    assert "<!DOCTYPE" not in html
    assert "<html" not in html


def test_markdown_to_html_smart_quotes() -> None:
    html = markdown_to_html('"Hello"')
    # smart punctuation: straight quotes become curly
    assert '"Hello"' not in html
    assert "“" in html or "&ldquo;" in html


def test_markdown_to_html_bold_italic() -> None:
    html = markdown_to_html("**bold** and *italic*")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html


def test_markdown_to_html_list() -> None:
    html = markdown_to_html("- alpha\n- beta\n- gamma\n")
    assert "<ul>" in html or "<li>" in html
    assert "alpha" in html


def test_markdown_to_html_mathml() -> None:
    html = markdown_to_html("Inline math: $x^2 + y^2 = z^2$\n")
    assert "<math" in html
