"""Unit tests: HTML link/img rewriting logic."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from github_to_canvas.link_rewrite import (
    canvas_content_url,
    infer_canvas_type,
    rewrite_links,
)

COURSE_ID = 999

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """Return (course_root, source_file) with fixture files on disk."""
    course_root = tmp_path / "course"
    course_root.mkdir()
    (course_root / "pages").mkdir()
    (course_root / "assignments").mkdir()
    (course_root / "discussions").mkdir()
    (course_root / "assets" / "images").mkdir(parents=True)
    (course_root / "pages" / "syllabus.md").touch()
    (course_root / "assignments" / "week1.md").touch()
    (course_root / "discussions" / "intro.md").touch()
    (course_root / "assets" / "images" / "fig.png").touch()
    source_file = course_root / "pages" / "notes.md"
    return course_root, source_file


def _no_stub(local_path: str, canvas_type: str):
    raise AssertionError(f"stub_creator should not have been called for {local_path!r}")


# ---------------------------------------------------------------------------
# infer_canvas_type
# ---------------------------------------------------------------------------


def test_infer_type_page() -> None:
    assert infer_canvas_type("pages/syllabus.md") == "page"


def test_infer_type_assignment() -> None:
    assert infer_canvas_type("assignments/week1.md") == "assignment"


def test_infer_type_discussion() -> None:
    assert infer_canvas_type("discussions/intro.md") == "discussion"


def test_infer_type_file() -> None:
    assert infer_canvas_type("assets/images/fig.png") == "file"


def test_infer_type_unknown_defaults_to_page() -> None:
    assert infer_canvas_type("other/thing.md") == "page"


# ---------------------------------------------------------------------------
# canvas_content_url
# ---------------------------------------------------------------------------


def test_canvas_url_page() -> None:
    entry = {"canvas_type": "page", "canvas_id": 1, "canvas_url": "syllabus"}
    assert canvas_content_url(entry, COURSE_ID) == f"/courses/{COURSE_ID}/pages/syllabus"


def test_canvas_url_assignment() -> None:
    entry = {"canvas_type": "assignment", "canvas_id": 98765}
    assert canvas_content_url(entry, COURSE_ID) == f"/courses/{COURSE_ID}/assignments/98765"


def test_canvas_url_discussion() -> None:
    entry = {"canvas_type": "discussion", "canvas_id": 55555}
    assert canvas_content_url(entry, COURSE_ID) == f"/courses/{COURSE_ID}/discussion_topics/55555"


def test_canvas_url_file() -> None:
    url = "https://school.instructure.com/files/77777/download"
    entry = {"canvas_type": "file", "canvas_id": 77777, "canvas_url": url}
    assert canvas_content_url(entry, COURSE_ID) == url


def test_canvas_url_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="Unknown canvas_type"):
        canvas_content_url({"canvas_type": "bogus", "canvas_id": 1}, COURSE_ID)


# ---------------------------------------------------------------------------
# rewrite_links — img tags
# ---------------------------------------------------------------------------


def test_img_src_rewritten_from_manifest(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    canvas_file_url = "https://school.instructure.com/files/77777/download"
    manifest = {
        "assets/images/fig.png": {
            "canvas_type": "file",
            "canvas_id": 77777,
            "canvas_url": canvas_file_url,
        }
    }
    html = '<p><img src="../assets/images/fig.png" alt="fig" /></p>'
    result = rewrite_links(html, source_file, course_root, manifest, COURSE_ID, _no_stub)
    assert canvas_file_url in result
    assert "../assets" not in result


def test_img_missing_local_file_removed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    course_root, source_file = _setup(tmp_path)
    html = '<p><img src="../assets/images/missing.png" alt="x" /></p>'
    result = rewrite_links(html, source_file, course_root, {}, COURSE_ID, _no_stub)
    assert "<img" not in result
    assert "ERROR" in capsys.readouterr().out


def test_img_external_src_unchanged(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    html = '<img src="https://example.com/img.png" alt="ext" />'
    result = rewrite_links(html, source_file, course_root, {}, COURSE_ID, _no_stub)
    assert result == html


# ---------------------------------------------------------------------------
# rewrite_links — a href tags
# ---------------------------------------------------------------------------


def test_a_href_page_rewritten_from_manifest(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    manifest = {
        "pages/syllabus.md": {"canvas_type": "page", "canvas_id": 11111, "canvas_url": "syllabus"}
    }
    html = '<a href="../pages/syllabus.md">Syllabus</a>'
    result = rewrite_links(html, source_file, course_root, manifest, COURSE_ID, _no_stub)
    assert f"/courses/{COURSE_ID}/pages/syllabus" in result
    assert "../pages" not in result


def test_a_href_assignment_rewritten_from_manifest(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    manifest = {
        "assignments/week1.md": {"canvas_type": "assignment", "canvas_id": 98765}
    }
    html = '<a href="../assignments/week1.md">Week 1</a>'
    result = rewrite_links(html, source_file, course_root, manifest, COURSE_ID, _no_stub)
    assert f"/courses/{COURSE_ID}/assignments/98765" in result


def test_a_href_discussion_rewritten_from_manifest(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    manifest = {
        "discussions/intro.md": {"canvas_type": "discussion", "canvas_id": 55555}
    }
    html = '<a href="../discussions/intro.md">Intro</a>'
    result = rewrite_links(html, source_file, course_root, manifest, COURSE_ID, _no_stub)
    assert f"/courses/{COURSE_ID}/discussion_topics/55555" in result


def test_a_href_external_unchanged(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    html = '<a href="https://example.com">External</a>'
    result = rewrite_links(html, source_file, course_root, {}, COURSE_ID, _no_stub)
    assert result == html


def test_a_href_anchor_unchanged(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    html = '<a href="#section-1">Jump</a>'
    result = rewrite_links(html, source_file, course_root, {}, COURSE_ID, _no_stub)
    assert result == html


def test_a_href_missing_local_file_removed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    course_root, source_file = _setup(tmp_path)
    html = '<a href="../pages/ghost.md">Ghost</a>'
    result = rewrite_links(html, source_file, course_root, {}, COURSE_ID, _no_stub)
    assert "<a " not in result
    assert "ERROR" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# rewrite_links — stub creation
# ---------------------------------------------------------------------------


def test_stub_creator_called_for_unknown_file(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    stub_entry = {"canvas_type": "page", "canvas_id": 42, "canvas_url": "syllabus"}
    stub_creator = MagicMock(return_value=stub_entry)
    html = '<a href="../pages/syllabus.md">Syllabus</a>'
    result = rewrite_links(html, source_file, course_root, {}, COURSE_ID, stub_creator)
    stub_creator.assert_called_once_with("pages/syllabus.md", "page")
    assert f"/courses/{COURSE_ID}/pages/syllabus" in result


def test_stub_creator_not_called_when_in_manifest(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    manifest = {
        "pages/syllabus.md": {"canvas_type": "page", "canvas_id": 11111, "canvas_url": "syllabus"}
    }
    stub_creator = MagicMock()
    html = '<a href="../pages/syllabus.md">Syllabus</a>'
    rewrite_links(html, source_file, course_root, manifest, COURSE_ID, stub_creator)
    stub_creator.assert_not_called()


def test_stub_creator_called_once_for_repeated_reference(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    stub_entry = {"canvas_type": "page", "canvas_id": 42, "canvas_url": "syllabus"}
    stub_creator = MagicMock(return_value=stub_entry)
    html = (
        '<a href="../pages/syllabus.md">Link 1</a> '
        '<a href="../pages/syllabus.md">Link 2</a>'
    )
    rewrite_links(html, source_file, course_root, {}, COURSE_ID, stub_creator)
    stub_creator.assert_called_once()  # not twice


# ---------------------------------------------------------------------------
# rewrite_links — mixed content
# ---------------------------------------------------------------------------


def test_multiple_different_links_all_rewritten(tmp_path: Path) -> None:
    course_root, source_file = _setup(tmp_path)
    manifest = {
        "pages/syllabus.md": {"canvas_type": "page", "canvas_id": 1, "canvas_url": "syllabus"},
        "assignments/week1.md": {"canvas_type": "assignment", "canvas_id": 2},
        "assets/images/fig.png": {
            "canvas_type": "file",
            "canvas_id": 3,
            "canvas_url": "https://school.instructure.com/files/3/download",
        },
    }
    html = (
        '<img src="../assets/images/fig.png" alt="f" /> '
        '<a href="../pages/syllabus.md">S</a> '
        '<a href="../assignments/week1.md">A</a> '
        '<a href="https://example.com">Ext</a> '
        '<a href="#top">Top</a>'
    )
    result = rewrite_links(html, source_file, course_root, manifest, COURSE_ID, _no_stub)
    assert "https://school.instructure.com/files/3/download" in result
    assert f"/courses/{COURSE_ID}/pages/syllabus" in result
    assert f"/courses/{COURSE_ID}/assignments/2" in result
    assert "https://example.com" in result
    assert "#top" in result
    assert "../" not in result
