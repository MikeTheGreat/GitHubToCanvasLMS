"""Unit tests: frontmatter extraction, HTML body extraction, module item generation."""
from __future__ import annotations

from pathlib import Path

import pytest

from github_to_canvas.imscc_import import (
    _build_frontmatter,
    _extract_html_body,
    parse_assignment_settings,
    parse_topic_meta,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "imscc"


# ---------------------------------------------------------------------------
# _extract_html_body
# ---------------------------------------------------------------------------


def test_extract_body_strips_wrapper() -> None:
    html = "<html><head><title>T</title></head><body><p>Hello</p></body></html>"
    assert _extract_html_body(html) == "<p>Hello</p>"


def test_extract_body_no_wrapper_returns_as_is() -> None:
    html = "<p>No wrapper here</p>"
    assert _extract_html_body(html) == "<p>No wrapper here</p>"


def test_extract_body_multiline() -> None:
    html = "<html><body>\n<h1>Title</h1>\n<p>Text</p>\n</body></html>"
    result = _extract_html_body(html)
    assert "<h1>Title</h1>" in result
    assert "<html>" not in result


# ---------------------------------------------------------------------------
# parse_assignment_settings
# ---------------------------------------------------------------------------


def test_assignment_title_extracted() -> None:
    path = FIXTURE_DIR / "g_assignment_1" / "assignment_settings.xml"
    fields = parse_assignment_settings(path)
    assert fields["title"] == "My Test Assignment"


def test_assignment_points_extracted() -> None:
    path = FIXTURE_DIR / "g_assignment_1" / "assignment_settings.xml"
    fields = parse_assignment_settings(path)
    assert fields["points_possible"] == 50.0


def test_assignment_due_at_extracted() -> None:
    path = FIXTURE_DIR / "g_assignment_1" / "assignment_settings.xml"
    fields = parse_assignment_settings(path)
    assert fields["due_at"] == "2025-10-01T23:59:00"


def test_assignment_lock_unlock_extracted() -> None:
    path = FIXTURE_DIR / "g_assignment_1" / "assignment_settings.xml"
    fields = parse_assignment_settings(path)
    assert fields["lock_at"] == "2025-10-08T23:59:00"
    assert fields["unlock_at"] == "2025-09-15T00:00:00"


def test_assignment_submission_types_as_list() -> None:
    path = FIXTURE_DIR / "g_assignment_1" / "assignment_settings.xml"
    fields = parse_assignment_settings(path)
    assert fields["submission_types"] == ["online_upload"]


def test_assignment_grading_type_extracted() -> None:
    path = FIXTURE_DIR / "g_assignment_1" / "assignment_settings.xml"
    fields = parse_assignment_settings(path)
    assert fields["grading_type"] == "points"


def test_assignment_published_true_when_workflow_published() -> None:
    path = FIXTURE_DIR / "g_assignment_1" / "assignment_settings.xml"
    fields = parse_assignment_settings(path)
    assert fields["published"] is True


def test_assignment_empty_due_at_returns_none(tmp_path: Path) -> None:
    xml = tmp_path / "a.xml"
    xml.write_text(
        '<?xml version="1.0"?>'
        '<assignment xmlns="http://canvas.instructure.com/xsd/cccv1p0">'
        "<title>X</title><due_at/><points_possible>10</points_possible>"
        "<workflow_state>unpublished</workflow_state>"
        "<submission_types>none</submission_types>"
        "</assignment>",
        encoding="utf-8",
    )
    fields = parse_assignment_settings(xml)
    assert fields["due_at"] is None
    assert fields["published"] is False


# ---------------------------------------------------------------------------
# parse_topic_meta
# ---------------------------------------------------------------------------


def test_discussion_title_extracted() -> None:
    path = FIXTURE_DIR / "g_discussion_1_meta.xml"
    fields = parse_topic_meta(path)
    assert fields["title"] == "Week 01 Forum"


def test_discussion_published_from_active() -> None:
    path = FIXTURE_DIR / "g_discussion_1_meta.xml"
    fields = parse_topic_meta(path)
    assert fields["published"] is True


def test_discussion_require_initial_post() -> None:
    path = FIXTURE_DIR / "g_discussion_1_meta.xml"
    fields = parse_topic_meta(path)
    assert fields["require_initial_post"] is True


def test_discussion_not_announcement() -> None:
    path = FIXTURE_DIR / "g_discussion_1_meta.xml"
    fields = parse_topic_meta(path)
    assert fields["is_announcement"] is False


def test_graded_discussion_points_extracted() -> None:
    path = FIXTURE_DIR / "g_discussion_1_meta.xml"
    fields = parse_topic_meta(path)
    assert fields["points_possible"] == 10.0


def test_graded_discussion_due_at_extracted() -> None:
    path = FIXTURE_DIR / "g_discussion_1_meta.xml"
    fields = parse_topic_meta(path)
    assert fields["due_at"] == "2025-09-10T23:59:00"


def test_announcement_flagged(tmp_path: Path) -> None:
    xml = tmp_path / "meta.xml"
    xml.write_text(
        '<?xml version="1.0"?>'
        '<topicMeta xmlns="http://canvas.instructure.com/xsd/cccv1p0">'
        "<title>News</title><type>announcement</type>"
        "<workflow_state>active</workflow_state>"
        "<require_initial_post>false</require_initial_post>"
        "</topicMeta>",
        encoding="utf-8",
    )
    fields = parse_topic_meta(xml)
    assert fields["is_announcement"] is True


# ---------------------------------------------------------------------------
# _build_frontmatter
# ---------------------------------------------------------------------------


def test_frontmatter_basic() -> None:
    result = _build_frontmatter({"title": "Hello", "published": True})
    assert result.startswith("---")
    assert result.endswith("---")
    assert "title: Hello" in result
    assert "published: true" in result


def test_frontmatter_none_values_omitted() -> None:
    result = _build_frontmatter({"title": "T", "due_at": None})
    assert "due_at" not in result


def test_frontmatter_list_rendered_inline() -> None:
    result = _build_frontmatter({"submission_types": ["online_upload", "online_text_entry"]})
    assert "[online_upload, online_text_entry]" in result


def test_frontmatter_float() -> None:
    result = _build_frontmatter({"points_possible": 50.0})
    assert "50.0" in result
