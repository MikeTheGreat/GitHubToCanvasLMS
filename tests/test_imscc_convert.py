"""Unit tests: frontmatter extraction, HTML body extraction, module item generation."""
from __future__ import annotations

from pathlib import Path

import pytest

from github_to_canvas.imscc_import import (
    _build_frontmatter,
    _extract_html_body,
    _parse_assignment_groups,
    _parse_context,
    _parse_course_settings_full,
    _parse_events,
    _parse_grading_standards,
    _parse_late_policy,
    _parse_manifest_metadata,
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


# ---------------------------------------------------------------------------
# _parse_manifest_metadata
# ---------------------------------------------------------------------------


def test_manifest_metadata_last_modified() -> None:
    path = FIXTURE_DIR / "imsmanifest.xml"
    meta = _parse_manifest_metadata(path)
    assert meta.get("last_modified") == "2025-08-01"


def test_manifest_metadata_copyright_restrictions() -> None:
    path = FIXTURE_DIR / "imsmanifest.xml"
    meta = _parse_manifest_metadata(path)
    assert meta.get("copyright_restrictions") == "yes"


def test_manifest_metadata_copyright_description() -> None:
    path = FIXTURE_DIR / "imsmanifest.xml"
    meta = _parse_manifest_metadata(path)
    assert "Private" in meta.get("copyright_description", "")


def test_manifest_metadata_missing_file_returns_empty(tmp_path: Path) -> None:
    meta = _parse_manifest_metadata(tmp_path / "nonexistent.xml")
    assert meta == {}


def test_manifest_metadata_no_lifecycle_omits_last_modified(tmp_path: Path) -> None:
    xml = tmp_path / "manifest.xml"
    xml.write_text(
        '<?xml version="1.0"?>'
        '<manifest xmlns:lomimscc="http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest">'
        "<metadata/></manifest>",
        encoding="utf-8",
    )
    meta = _parse_manifest_metadata(xml)
    assert "last_modified" not in meta


# ---------------------------------------------------------------------------
# _parse_course_settings_full
# ---------------------------------------------------------------------------


def test_course_settings_full_title() -> None:
    path = FIXTURE_DIR / "course_settings" / "course_settings.xml"
    data = _parse_course_settings_full(path)
    assert data["title"] == "Test Course: Introduction to Testing"


def test_course_settings_full_boolean_field() -> None:
    path = FIXTURE_DIR / "course_settings" / "course_settings.xml"
    data = _parse_course_settings_full(path)
    assert data["is_public"] is False
    assert data["grading_standard_enabled"] is True


def test_course_settings_full_int_field() -> None:
    path = FIXTURE_DIR / "course_settings" / "course_settings.xml"
    data = _parse_course_settings_full(path)
    assert data["home_page_announcement_limit"] == 3
    assert isinstance(data["home_page_announcement_limit"], int)


def test_course_settings_full_nested_post_policy() -> None:
    path = FIXTURE_DIR / "course_settings" / "course_settings.xml"
    data = _parse_course_settings_full(path)
    assert "default_post_policy" in data
    assert data["default_post_policy"]["post_manually"] is True


def test_course_settings_full_empty_file_returns_empty(tmp_path: Path) -> None:
    data = _parse_course_settings_full(tmp_path / "nonexistent.xml")
    assert data == {}


# ---------------------------------------------------------------------------
# _parse_grading_standards
# ---------------------------------------------------------------------------


def test_grading_standards_count() -> None:
    path = FIXTURE_DIR / "course_settings" / "grading_standards.xml"
    standards = _parse_grading_standards(path)
    assert len(standards) == 1


def test_grading_standards_title() -> None:
    path = FIXTURE_DIR / "course_settings" / "grading_standards.xml"
    standards = _parse_grading_standards(path)
    assert standards[0]["title"] == "Test Grade Scale"


def test_grading_standards_data_parsed_as_list() -> None:
    path = FIXTURE_DIR / "course_settings" / "grading_standards.xml"
    standards = _parse_grading_standards(path)
    data = standards[0]["data"]
    assert isinstance(data, list)
    assert data[0] == ["A", 0.93]


def test_grading_standards_boolean_field() -> None:
    path = FIXTURE_DIR / "course_settings" / "grading_standards.xml"
    standards = _parse_grading_standards(path)
    assert standards[0]["points_based"] is False


def test_grading_standards_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_grading_standards(tmp_path / "nonexistent.xml") == []


# ---------------------------------------------------------------------------
# _parse_assignment_groups
# ---------------------------------------------------------------------------


def test_assignment_groups_count() -> None:
    path = FIXTURE_DIR / "course_settings" / "assignment_groups.xml"
    groups = _parse_assignment_groups(path)
    assert len(groups) == 2


def test_assignment_groups_titles() -> None:
    path = FIXTURE_DIR / "course_settings" / "assignment_groups.xml"
    groups = _parse_assignment_groups(path)
    titles = [g["title"] for g in groups]
    assert "Homework" in titles
    assert "Exams" in titles


def test_assignment_groups_weight() -> None:
    path = FIXTURE_DIR / "course_settings" / "assignment_groups.xml"
    groups = _parse_assignment_groups(path)
    homework = next(g for g in groups if g["title"] == "Homework")
    assert homework["group_weight"] == 60.0


def test_assignment_groups_rules_extracted() -> None:
    path = FIXTURE_DIR / "course_settings" / "assignment_groups.xml"
    groups = _parse_assignment_groups(path)
    exams = next(g for g in groups if g["title"] == "Exams")
    assert "rules" in exams
    assert exams["rules"][0]["drop_type"] == "drop_lowest"
    assert exams["rules"][0]["drop_count"] == 1


def test_assignment_groups_no_rules_when_absent() -> None:
    path = FIXTURE_DIR / "course_settings" / "assignment_groups.xml"
    groups = _parse_assignment_groups(path)
    homework = next(g for g in groups if g["title"] == "Homework")
    assert "rules" not in homework


def test_assignment_groups_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_assignment_groups(tmp_path / "nonexistent.xml") == []


# ---------------------------------------------------------------------------
# _parse_late_policy
# ---------------------------------------------------------------------------


def test_late_policy_deduction_enabled() -> None:
    path = FIXTURE_DIR / "course_settings" / "late_policy.xml"
    lp = _parse_late_policy(path)
    assert lp["late_submission_deduction_enabled"] is True
    assert lp["missing_submission_deduction_enabled"] is False


def test_late_policy_numeric_fields() -> None:
    path = FIXTURE_DIR / "course_settings" / "late_policy.xml"
    lp = _parse_late_policy(path)
    assert lp["late_submission_deduction"] == 10.0
    assert lp["late_submission_minimum_percent"] == 0.0


def test_late_policy_interval() -> None:
    path = FIXTURE_DIR / "course_settings" / "late_policy.xml"
    lp = _parse_late_policy(path)
    assert lp["late_submission_interval"] == "day"


def test_late_policy_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_late_policy(tmp_path / "nonexistent.xml") == {}


# ---------------------------------------------------------------------------
# _parse_context
# ---------------------------------------------------------------------------


def test_context_canvas_domain() -> None:
    path = FIXTURE_DIR / "course_settings" / "context.xml"
    ctx = _parse_context(path)
    assert ctx["canvas_domain"] == "test.instructure.com"


def test_context_course_id_as_int() -> None:
    path = FIXTURE_DIR / "course_settings" / "context.xml"
    ctx = _parse_context(path)
    assert ctx["course_id"] == 12345
    assert isinstance(ctx["course_id"], int)


def test_context_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_context(tmp_path / "nonexistent.xml") == {}


# ---------------------------------------------------------------------------
# _parse_events
# ---------------------------------------------------------------------------


def test_events_count() -> None:
    path = FIXTURE_DIR / "course_settings" / "events.xml"
    events = _parse_events(path)
    assert len(events) == 2


def test_events_title() -> None:
    path = FIXTURE_DIR / "course_settings" / "events.xml"
    events = _parse_events(path)
    assert events[0]["title"] == "No Class - Holiday"


def test_events_all_day_flag() -> None:
    path = FIXTURE_DIR / "course_settings" / "events.xml"
    events = _parse_events(path)
    assert events[0]["all_day"] is True
    assert events[1]["all_day"] is False


def test_events_all_day_date() -> None:
    path = FIXTURE_DIR / "course_settings" / "events.xml"
    events = _parse_events(path)
    assert events[0].get("all_day_date") == "2025-11-27"


def test_events_description() -> None:
    path = FIXTURE_DIR / "course_settings" / "events.xml"
    events = _parse_events(path)
    assert "final project" in events[1]["description"]


def test_events_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_events(tmp_path / "nonexistent.xml") == []
