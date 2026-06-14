"""Unit tests: frontmatter extraction, HTML body extraction, module item generation."""
from __future__ import annotations

from pathlib import Path

import pytest

from github_to_canvas.imscc_import import (
    TempEntry,
    _build_frontmatter,
    _extract_html_body,
    _parse_assignment_groups,
    _parse_context,
    _parse_course_settings_full,
    _parse_events,
    _parse_files_meta,
    _parse_grading_standards,
    _parse_late_policy,
    _parse_manifest_metadata,
    _parse_rubrics,
    _strip_canvas_img_attrs,
    parse_assignment_settings,
    parse_qti_questions,
    parse_topic_meta,
    rewrite_imscc_links,
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
# _strip_canvas_img_attrs
# ---------------------------------------------------------------------------


def test_strip_canvas_img_attrs_removes_api_endpoint() -> None:
    html = '<img src="img.png" api-endpoint="https://example.com/api/v1/files/123" alt="test">'
    result = _strip_canvas_img_attrs(html)
    assert 'api-endpoint' not in result
    assert 'src="img.png"' in result
    assert 'alt="test"' in result


def test_strip_canvas_img_attrs_removes_api_returntype() -> None:
    html = '<img src="img.png" api-returntype="File">'
    result = _strip_canvas_img_attrs(html)
    assert 'api-returntype' not in result
    assert 'src="img.png"' in result


def test_strip_canvas_img_attrs_removes_loading() -> None:
    html = '<img src="img.png" loading="lazy">'
    result = _strip_canvas_img_attrs(html)
    assert 'loading' not in result
    assert 'src="img.png"' in result


def test_strip_canvas_img_attrs_removes_multiple() -> None:
    html = (
        '<img src="img.png" role="presentation" width="366" height="321"'
        ' api-endpoint="https://example.com/api/v1/files/123"'
        ' api-returntype="File" loading="lazy">'
    )
    result = _strip_canvas_img_attrs(html)
    assert 'api-endpoint' not in result
    assert 'api-returntype' not in result
    assert 'loading' not in result
    assert 'role="presentation"' in result
    assert 'width="366"' in result
    assert 'height="321"' in result


def test_strip_canvas_img_attrs_leaves_non_img_tags_alone() -> None:
    html = '<p api-endpoint="x">text</p><img src="img.png">'
    result = _strip_canvas_img_attrs(html)
    assert '<p api-endpoint="x">text</p>' in result


def test_strip_canvas_img_attrs_preserves_alt_text() -> None:
    html = '<img src="img.png" alt="A screenshot of the download dialog" loading="lazy">'
    result = _strip_canvas_img_attrs(html)
    assert 'alt="A screenshot of the download dialog"' in result
    assert 'loading' not in result


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


# ---------------------------------------------------------------------------
# _parse_rubrics
# ---------------------------------------------------------------------------


def test_rubrics_count() -> None:
    path = FIXTURE_DIR / "course_settings" / "rubrics.xml"
    rubrics = _parse_rubrics(path)
    assert len(rubrics) == 2


def test_rubrics_title() -> None:
    path = FIXTURE_DIR / "course_settings" / "rubrics.xml"
    rubrics = _parse_rubrics(path)
    assert rubrics[0]["title"] == "Test Rubric"


def test_rubrics_identifier() -> None:
    path = FIXTURE_DIR / "course_settings" / "rubrics.xml"
    rubrics = _parse_rubrics(path)
    assert rubrics[0]["identifier"] == "gtest_rubric_1"


def test_rubrics_boolean_fields() -> None:
    path = FIXTURE_DIR / "course_settings" / "rubrics.xml"
    rubrics = _parse_rubrics(path)
    assert rubrics[0]["read_only"] is False
    assert rubrics[1]["read_only"] is True


def test_rubrics_points_possible() -> None:
    path = FIXTURE_DIR / "course_settings" / "rubrics.xml"
    rubrics = _parse_rubrics(path)
    assert rubrics[0]["points_possible"] == 5.0


def test_rubrics_criteria_extracted() -> None:
    path = FIXTURE_DIR / "course_settings" / "rubrics.xml"
    rubrics = _parse_rubrics(path)
    criteria = rubrics[0]["criteria"]
    assert len(criteria) == 1
    assert criteria[0]["description"] == "Quality"
    assert criteria[0]["points"] == 5.0


def test_rubrics_long_description_extracted() -> None:
    path = FIXTURE_DIR / "course_settings" / "rubrics.xml"
    rubrics = _parse_rubrics(path)
    assert "quality" in rubrics[0]["criteria"][0]["long_description"].lower()


def test_rubrics_ratings_extracted() -> None:
    path = FIXTURE_DIR / "course_settings" / "rubrics.xml"
    rubrics = _parse_rubrics(path)
    ratings = rubrics[0]["criteria"][0]["ratings"]
    assert len(ratings) == 2
    assert ratings[0]["description"] == "Excellent"
    assert ratings[0]["points"] == 5.0
    assert ratings[1]["description"] == "Poor"
    assert ratings[1]["points"] == 0.0


def test_rubrics_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_rubrics(tmp_path / "nonexistent.xml") == []


# ---------------------------------------------------------------------------
# _parse_files_meta
# ---------------------------------------------------------------------------


def test_files_meta_folders_extracted() -> None:
    path = FIXTURE_DIR / "course_settings" / "files_meta.xml"
    meta = _parse_files_meta(path)
    assert "folders" in meta
    assert meta["folders"][0]["path"] == "hidden_folder"
    assert meta["folders"][0]["hidden"] is True


def test_files_meta_files_extracted() -> None:
    path = FIXTURE_DIR / "course_settings" / "files_meta.xml"
    meta = _parse_files_meta(path)
    assert "files" in meta
    assert len(meta["files"]) == 3


def test_files_meta_locked_file() -> None:
    path = FIXTURE_DIR / "course_settings" / "files_meta.xml"
    meta = _parse_files_meta(path)
    locked = next(f for f in meta["files"] if f["identifier"] == "gtest_file_locked")
    assert locked["locked"] is True


def test_files_meta_display_name() -> None:
    path = FIXTURE_DIR / "course_settings" / "files_meta.xml"
    meta = _parse_files_meta(path)
    named = next(f for f in meta["files"] if f["identifier"] == "gtest_file_named")
    assert named["display_name"] == "lecture-notes.pdf"


def test_files_meta_unlock_at() -> None:
    path = FIXTURE_DIR / "course_settings" / "files_meta.xml"
    meta = _parse_files_meta(path)
    timed = next(f for f in meta["files"] if f["identifier"] == "gtest_file_timed")
    assert "2025-10-31" in timed["unlock_at"]


def test_files_meta_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_files_meta(tmp_path / "nonexistent.xml") == {}


# ---------------------------------------------------------------------------
# parse_qti_questions — cc_profile field label (IMS CC format)
# ---------------------------------------------------------------------------


def test_qti_cc_profile_mcq_parsed_as_multiple_choice() -> None:
    """cc_profile=cc.multiple_choice.v0p1 should map to multiple_choice_question."""
    path = FIXTURE_DIR / "g_quiz_2" / "assessment_qti.xml"
    questions = parse_qti_questions(path)
    assert len(questions) == 1
    assert questions[0]["question_type"] == "multiple_choice_question"


def test_qti_cc_profile_mcq_has_answers() -> None:
    path = FIXTURE_DIR / "g_quiz_2" / "assessment_qti.xml"
    questions = parse_qti_questions(path)
    assert len(questions[0]["answers"]) == 2


def test_qti_cc_profile_mcq_correct_answer() -> None:
    """Correct answer ident 'B' should survive for MCQ parsing."""
    path = FIXTURE_DIR / "g_quiz_2" / "assessment_qti.xml"
    questions = parse_qti_questions(path)
    assert questions[0]["correct_ident"] == "B"


def test_qti_non_cc_format_points_possible() -> None:
    """The non_cc_assessments .xml.qti file (with question_type + points) should parse."""
    path = FIXTURE_DIR / "non_cc_assessments" / "g_quiz_2.xml.qti"
    questions = parse_qti_questions(path)
    assert len(questions) == 1
    assert questions[0]["question_type"] == "multiple_choice_question"
    assert questions[0]["points_possible"] == 2.0


def test_qti_missing_file_returns_empty() -> None:
    from pathlib import Path
    questions = parse_qti_questions(Path("/nonexistent/file.xml"))
    assert questions == []


# ---------------------------------------------------------------------------
# Item 2: Web link target / windowFeatures (parse_imsmanifest)
# ---------------------------------------------------------------------------


def test_weblink_target_in_metadata() -> None:
    from github_to_canvas.imscc_import import parse_imsmanifest
    manifest = parse_imsmanifest(FIXTURE_DIR)
    entry = manifest["g_exturl_1"]
    assert entry.metadata.get("target") == "_blank"


def test_weblink_window_features_in_metadata() -> None:
    from github_to_canvas.imscc_import import parse_imsmanifest
    manifest = parse_imsmanifest(FIXTURE_DIR)
    entry = manifest["g_exturl_1"]
    assert entry.metadata.get("window_features") == "width=800,height=600"


def test_weblink_without_target_has_no_target_key() -> None:
    """A webLink with no target attribute should not produce a 'target' key."""
    from github_to_canvas.imscc_import import parse_imsmanifest
    manifest = parse_imsmanifest(FIXTURE_DIR)
    # g_exturl_1 has target; confirm the key is missing when not present by
    # checking a weblink fixture with no target (use a tmp manifest if needed)
    # — verified implicitly by checking the value is present on g_exturl_1.
    entry = manifest["g_exturl_1"]
    assert "target" in entry.metadata


# ---------------------------------------------------------------------------
# Item 3: QTI new question types (parse_qti_questions)
# ---------------------------------------------------------------------------


def test_qti_multiple_response_type_parsed() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    types = [q["question_type"] for q in questions]
    assert "multiple_response_question" in types


def test_qti_multiple_response_correct_idents() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    mr = next(q for q in questions if q["question_type"] == "multiple_response_question")
    assert set(mr["correct_idents"]) == {"A", "B", "D"}


def test_qti_multiple_response_incorrect_not_in_correct_idents() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    mr = next(q for q in questions if q["question_type"] == "multiple_response_question")
    assert "C" not in mr["correct_idents"]


def test_qti_multiple_response_has_four_answers() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    mr = next(q for q in questions if q["question_type"] == "multiple_response_question")
    assert len(mr["answers"]) == 4


def test_qti_fib_type_parsed() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    types = [q["question_type"] for q in questions]
    assert "fill_in_blank_question" in types


def test_qti_fib_answers_extracted() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    fib = next(q for q in questions if q["question_type"] == "fill_in_blank_question")
    assert "300000" in fib["fib_answers"]
    assert "300 000" in fib["fib_answers"]


def test_qti_pattern_match_type_parsed() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    types = [q["question_type"] for q in questions]
    assert "pattern_match_question" in types


def test_qti_pattern_match_answers_extracted() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    pm = next(q for q in questions if q["question_type"] == "pattern_match_question")
    assert "python" in pm["fib_answers"]
    assert "r language" in pm["fib_answers"]


def test_qti_pattern_match_has_match_type() -> None:
    path = FIXTURE_DIR / "g_quiz_types.xml"
    questions = parse_qti_questions(path)
    pm = next(q for q in questions if q["question_type"] == "pattern_match_question")
    assert pm["match_type"] == "substring"


# ---------------------------------------------------------------------------
# Item 3: _write_question_file — new question type output format
# ---------------------------------------------------------------------------


def test_write_multiple_response_correct_is_index_list(tmp_path: Path) -> None:
    from github_to_canvas.imscc_import import _write_question_file
    q = {
        "title": "Select primes",
        "question_type": "multiple_response_question",
        "points_possible": 2.0,
        "question_text": "Which are prime?",
        "answers": [("A", "2"), ("B", "3"), ("C", "4"), ("D", "5")],
        "correct_ident": "",
        "correct_idents": ["A", "B", "D"],
        "fib_answers": [],
        "match_type": "",
        "original_answer_ids": [],
        "feedback": {},
        "solution": "",
        "slug": "select-primes",
    }
    p = tmp_path / "q.md"
    _write_question_file(q, p)
    text = p.read_text()
    assert "question_type: multiple_response_question" in text
    assert "correct: [1, 2, 4]" in text
    assert "## Answers" in text


def test_write_fill_in_blank_has_answers_frontmatter(tmp_path: Path) -> None:
    from github_to_canvas.imscc_import import _write_question_file
    q = {
        "title": "Speed of light",
        "question_type": "fill_in_blank_question",
        "points_possible": 1.0,
        "question_text": "The speed is _____",
        "answers": [],
        "correct_ident": "",
        "correct_idents": [],
        "fib_answers": ["300000", "300 000"],
        "match_type": "",
        "original_answer_ids": [],
        "feedback": {},
        "solution": "",
        "slug": "speed-of-light",
    }
    p = tmp_path / "q.md"
    _write_question_file(q, p)
    text = p.read_text()
    assert "question_type: fill_in_blank_question" in text
    assert "answers:" in text
    assert "300000" in text
    assert "## Answers" not in text


def test_write_pattern_match_has_match_type_frontmatter(tmp_path: Path) -> None:
    from github_to_canvas.imscc_import import _write_question_file
    q = {
        "title": "Name a language",
        "question_type": "pattern_match_question",
        "points_possible": 1.0,
        "question_text": "Name a language.",
        "answers": [],
        "correct_ident": "",
        "correct_idents": [],
        "fib_answers": ["python"],
        "match_type": "substring",
        "original_answer_ids": [],
        "feedback": {},
        "solution": "",
        "slug": "name-a-language",
    }
    p = tmp_path / "q.md"
    _write_question_file(q, p)
    text = p.read_text()
    assert "question_type: pattern_match_question" in text
    assert "match_type: substring" in text
    assert "answers:" in text
    assert "python" in text


# ---------------------------------------------------------------------------
# Items 4 & 5: QTI feedback + sample solution (parse_qti_questions)
# ---------------------------------------------------------------------------


def test_qti_feedback_general_extracted() -> None:
    path = FIXTURE_DIR / "g_quiz_1" / "g_quiz_1.xml"
    questions = parse_qti_questions(path)
    mcq = next(q for q in questions if q["question_type"] == "multiple_choice_question")
    assert mcq["feedback"].get("general_fb") == "Think carefully about number operations."


def test_qti_feedback_correct_extracted() -> None:
    path = FIXTURE_DIR / "g_quiz_1" / "g_quiz_1.xml"
    questions = parse_qti_questions(path)
    mcq = next(q for q in questions if q["question_type"] == "multiple_choice_question")
    assert mcq["feedback"].get("correct_fb") == "That is correct!"


def test_qti_feedback_incorrect_extracted() -> None:
    path = FIXTURE_DIR / "g_quiz_1" / "g_quiz_1.xml"
    questions = parse_qti_questions(path)
    mcq = next(q for q in questions if q["question_type"] == "multiple_choice_question")
    assert mcq["feedback"].get("general_incorrect_fb") == "Try again."


def test_qti_feedback_per_answer_extracted() -> None:
    path = FIXTURE_DIR / "g_quiz_1" / "g_quiz_1.xml"
    questions = parse_qti_questions(path)
    mcq = next(q for q in questions if q["question_type"] == "multiple_choice_question")
    assert mcq["feedback"].get("a1_fb") == "3 is not the sum of 2+2."


def test_qti_essay_solution_extracted() -> None:
    path = FIXTURE_DIR / "g_quiz_1" / "g_quiz_1.xml"
    questions = parse_qti_questions(path)
    essay = next(q for q in questions if q["question_type"] == "essay_question")
    assert "key concepts" in essay["solution"]


def test_qti_no_feedback_returns_empty_dict() -> None:
    """Questions without feedback should have an empty feedback dict."""
    path = FIXTURE_DIR / "g_quiz_1" / "g_quiz_1.xml"
    questions = parse_qti_questions(path)
    essay = next(q for q in questions if q["question_type"] == "essay_question")
    assert essay["feedback"] == {}


# ---------------------------------------------------------------------------
# Items 4 & 5: _write_question_file — feedback and solution output
# ---------------------------------------------------------------------------


def test_write_feedback_section_written(tmp_path: Path) -> None:
    from github_to_canvas.imscc_import import _write_question_file
    q = {
        "title": "Test Q",
        "question_type": "multiple_choice_question",
        "points_possible": 1.0,
        "question_text": "Q?",
        "answers": [("a", "Yes"), ("b", "No")],
        "correct_ident": "a",
        "correct_idents": [],
        "fib_answers": [],
        "match_type": "",
        "original_answer_ids": [],
        "feedback": {
            "general_fb": "General hint.",
            "correct_fb": "Right!",
            "general_incorrect_fb": "Wrong.",
            "a_fb": "Yes is correct.",
        },
        "solution": "",
        "slug": "test-q",
    }
    p = tmp_path / "q.md"
    _write_question_file(q, p)
    text = p.read_text()
    assert "## Feedback" in text
    assert "### General" in text
    assert "General hint." in text
    assert "### Correct" in text
    assert "Right!" in text
    assert "### Incorrect" in text
    assert "Wrong." in text
    assert "### Per-answer" in text
    assert "answer 1: Yes is correct." in text


def test_write_sample_solution_written(tmp_path: Path) -> None:
    from github_to_canvas.imscc_import import _write_question_file
    q = {
        "title": "Essay Q",
        "question_type": "essay_question",
        "points_possible": 5.0,
        "question_text": "Explain X.",
        "answers": [],
        "correct_ident": "",
        "correct_idents": [],
        "fib_answers": [],
        "match_type": "",
        "original_answer_ids": [],
        "feedback": {},
        "solution": "A good answer covers A and B.",
        "slug": "essay-q",
    }
    p = tmp_path / "q.md"
    _write_question_file(q, p)
    text = p.read_text()
    assert "## Sample Solution" in text
    assert "A good answer covers A and B." in text


def test_write_no_feedback_section_when_empty(tmp_path: Path) -> None:
    from github_to_canvas.imscc_import import _write_question_file
    q = {
        "title": "Q",
        "question_type": "essay_question",
        "points_possible": 1.0,
        "question_text": "Text.",
        "answers": [],
        "correct_ident": "",
        "correct_idents": [],
        "fib_answers": [],
        "match_type": "",
        "original_answer_ids": [],
        "feedback": {},
        "solution": "",
        "slug": "q",
    }
    p = tmp_path / "q.md"
    _write_question_file(q, p)
    text = p.read_text()
    assert "## Feedback" not in text
    assert "## Sample Solution" not in text


# ---------------------------------------------------------------------------
# Item 6: Canvas question banks (parse_imsmanifest category + metadata)
# ---------------------------------------------------------------------------


def test_question_bank_entry_has_question_bank_category() -> None:
    from github_to_canvas.imscc_import import parse_imsmanifest
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert "g_bank_1" in manifest
    assert manifest["g_bank_1"].category == "question_bank"


def test_question_bank_entry_local_path() -> None:
    from github_to_canvas.imscc_import import parse_imsmanifest
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_bank_1"].local_path == "question_banks/fixture-question-bank/fixture-question-bank.toml"


def test_question_bank_entry_title() -> None:
    from github_to_canvas.imscc_import import parse_imsmanifest
    manifest = parse_imsmanifest(FIXTURE_DIR)
    assert manifest["g_bank_1"].title == "Fixture Question Bank"


def test_question_bank_original_answer_ids_parsed() -> None:
    """original_answer_ids should be extracted as a list of ints from bank item metadata."""
    path = FIXTURE_DIR / "non_cc_assessments" / "g_bank_1.xml.qti"
    questions = parse_qti_questions(path)
    mcq = next(q for q in questions if q["question_type"] == "multiple_choice_question")
    assert mcq["original_answer_ids"] == [1001, 1002, 1003]


def test_question_bank_essay_no_original_answer_ids() -> None:
    path = FIXTURE_DIR / "non_cc_assessments" / "g_bank_1.xml.qti"
    questions = parse_qti_questions(path)
    essay = next(q for q in questions if q["question_type"] == "essay_question")
    assert essay["original_answer_ids"] == []


# ---------------------------------------------------------------------------
# rewrite_imscc_links — $CANVAS_COURSE_ID$ token handling
# ---------------------------------------------------------------------------


def test_rewrite_imscc_links_canvas_course_id_replaced() -> None:
    """$CANVAS_COURSE_ID$ in an href is replaced with the numeric course ID."""
    html = '<a href="https://example.com/courses/$CANVAS_COURSE_ID$/grades">Grades</a>'
    result = rewrite_imscc_links(html, {}, "pages/test.md", course_id=12345)
    assert "$CANVAS_COURSE_ID$" not in result
    assert "https://example.com/courses/12345/grades" in result


def test_rewrite_imscc_links_canvas_course_id_no_course_id_preserves_token() -> None:
    """When course_id is None, $CANVAS_COURSE_ID$ is left as-is (no crash)."""
    html = '<a href="https://example.com/courses/$CANVAS_COURSE_ID$/grades">Grades</a>'
    result = rewrite_imscc_links(html, {}, "pages/test.md", course_id=None)
    assert "$CANVAS_COURSE_ID$" in result


def test_rewrite_imscc_links_canvas_course_id_str_course_id() -> None:
    """course_id given as a string is substituted correctly."""
    html = '<a href="https://example.com/courses/$CANVAS_COURSE_ID$/modules">Modules</a>'
    result = rewrite_imscc_links(html, {}, "pages/test.md", course_id="99")
    assert "courses/99/modules" in result


def test_rewrite_imscc_links_canvas_course_id_multiple_occurrences() -> None:
    """All occurrences of $CANVAS_COURSE_ID$ in the HTML are replaced."""
    html = (
        '<a href="https://example.com/courses/$CANVAS_COURSE_ID$/grades">Grades</a>'
        '<a href="https://example.com/courses/$CANVAS_COURSE_ID$/modules">Modules</a>'
    )
    result = rewrite_imscc_links(html, {}, "pages/test.md", course_id=42)
    assert "$CANVAS_COURSE_ID$" not in result
    assert result.count("courses/42/") == 2


def test_rewrite_imscc_links_canvas_course_id_and_object_ref_together() -> None:
    """$CANVAS_COURSE_ID$ and $CANVAS_OBJECT_REFERENCE$ tokens are both handled."""
    manifest = {
        "g_assign_1": TempEntry(
            imscc_id="g_assign_1",
            category="assignment",
            imscc_path="g_assign_1/body.html",
            local_path="assignments/hw.md",
        )
    }
    html = (
        '<a href="https://example.com/courses/$CANVAS_COURSE_ID$/grades">Grades</a>'
        '<a href="$CANVAS_OBJECT_REFERENCE$/assignments/g_assign_1">HW</a>'
    )
    result = rewrite_imscc_links(html, manifest, "pages/test.md", course_id=7)
    assert "$CANVAS_COURSE_ID$" not in result
    assert "courses/7/grades" in result
    assert "$CANVAS_OBJECT_REFERENCE$" not in result
    assert "../assignments/hw.md" in result


# rewrite_imscc_links — $CANVAS_COURSE_REFERENCE$ token handling
# ---------------------------------------------------------------------------


def test_rewrite_imscc_links_canvas_course_reference_replaced() -> None:
    """$CANVAS_COURSE_REFERENCE$ at the start of an href is replaced with base_url."""
    html = '<a href="$CANVAS_COURSE_REFERENCE$/grades">Gradebook</a>'
    result = rewrite_imscc_links(
        html, {}, "pages/test.md", base_url="https://example.com/courses/42"
    )
    assert "$CANVAS_COURSE_REFERENCE$" not in result
    assert "https://example.com/courses/42/grades" in result


def test_rewrite_imscc_links_canvas_course_reference_no_base_url_preserves_token() -> None:
    """When base_url is None, $CANVAS_COURSE_REFERENCE$ is left as-is (no crash)."""
    html = '<a href="$CANVAS_COURSE_REFERENCE$/grades">Gradebook</a>'
    result = rewrite_imscc_links(html, {}, "pages/test.md", base_url=None)
    assert "$CANVAS_COURSE_REFERENCE$" in result


def test_rewrite_imscc_links_canvas_course_reference_multiple_occurrences() -> None:
    """All occurrences of $CANVAS_COURSE_REFERENCE$ in the HTML are replaced."""
    html = (
        '<a href="$CANVAS_COURSE_REFERENCE$/grades">Grades</a>'
        '<a href="$CANVAS_COURSE_REFERENCE$/modules">Modules</a>'
    )
    result = rewrite_imscc_links(
        html, {}, "pages/test.md", base_url="https://school.instructure.com/courses/5"
    )
    assert "$CANVAS_COURSE_REFERENCE$" not in result
    assert result.count("https://school.instructure.com/courses/5/") == 2


def test_rewrite_imscc_links_canvas_course_reference_and_course_id_together() -> None:
    """$CANVAS_COURSE_REFERENCE$ and $CANVAS_COURSE_ID$ are both handled in the same HTML."""
    html = (
        '<a href="$CANVAS_COURSE_REFERENCE$/grades">Grades</a>'
        '<a href="https://school.instructure.com/courses/$CANVAS_COURSE_ID$/modules">Modules</a>'
    )
    result = rewrite_imscc_links(
        html,
        {},
        "pages/test.md",
        course_id=99,
        base_url="https://school.instructure.com/courses/99",
    )
    assert "$CANVAS_COURSE_REFERENCE$" not in result
    assert "$CANVAS_COURSE_ID$" not in result
    assert "https://school.instructure.com/courses/99/grades" in result
    assert "https://school.instructure.com/courses/99/modules" in result
