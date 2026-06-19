"""Integration tests: full import pipeline from synthetic IMSCC fixture."""
from __future__ import annotations

import shutil
import tomllib
import zipfile
from pathlib import Path

import pytest

from github_to_canvas.imscc_import import (
    open_imscc,
    parse_imsmanifest,
    run_import,
    _write_canvas_course_reference_snippet,
    _replace_canvas_course_url_in_md_files,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "imscc"


# ---------------------------------------------------------------------------
# open_imscc
# ---------------------------------------------------------------------------


def test_open_imscc_directory_returned_as_is() -> None:
    path, tmp = open_imscc(FIXTURE_DIR)
    assert path == FIXTURE_DIR
    assert tmp is None


def test_open_imscc_zip_extracted(tmp_path: Path) -> None:
    zip_path = tmp_path / "test.imscc"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(FIXTURE_DIR / "imsmanifest.xml", "imsmanifest.xml")

    path, tmp = open_imscc(zip_path)
    try:
        assert path.is_dir()
        assert (path / "imsmanifest.xml").exists()
    finally:
        if tmp:
            tmp.cleanup()


def test_open_imscc_invalid_path_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not_a_thing.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="Not a directory or zip"):
        open_imscc(bad)


# ---------------------------------------------------------------------------
# Full pipeline: directory input
# ---------------------------------------------------------------------------


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "output"


def test_run_import_creates_output_dir(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert output_dir.is_dir()


def test_run_import_fails_if_output_nonempty(tmp_path: Path) -> None:
    (tmp_path / "existing.txt").write_text("hi")
    with pytest.raises(ValueError, match="not empty"):
        run_import(FIXTURE_DIR, tmp_path)


# --- Assets ---

def test_assets_copied(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "assets" / "images" / "logo.png").exists()


# --- Pages ---

def test_page_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "pages" / "my-page.md").exists()


def test_page_has_title_frontmatter(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    assert "title: My Page" in text


def test_page_headings_shifted_down(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    assert "\n## My Page" in text
    assert "\n# My Page" not in text


def test_page_canvas_ref_rewritten(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    assert "$CANVAS_OBJECT_REFERENCE$" not in text
    assert "../assignments/my-assignment.md" in text


def test_page_filebase_ref_rewritten(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    assert "$IMS-CC-FILEBASE$" not in text
    assert "../assets/images/logo.png" in text


def test_page_external_link_preserved(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    assert "https://example.com" in text


# --- Assignments ---

def test_assignment_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "assignments" / "my-assignment.md").exists()


def test_assignment_has_points_frontmatter(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "assignments" / "my-assignment.md").read_text()
    assert "points_possible: 50.0" in text


def test_assignment_has_due_at_frontmatter(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "assignments" / "my-assignment.md").read_text()
    # datetime strings are quoted in frontmatter to prevent YAML auto-casting
    assert "due_at:" in text
    assert "2025-10-01T23:59:00" in text


def test_assignment_has_submission_types(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "assignments" / "my-assignment.md").read_text()
    assert "online_upload" in text


def test_assignment_canvas_ref_rewritten(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "assignments" / "my-assignment.md").read_text()
    assert "$CANVAS_OBJECT_REFERENCE$" not in text
    assert "../pages/my-page.md" in text


def test_assignment_group_and_grading_fields_imported(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "assignments" / "my-assignment.md").read_text()
    assert "group_category_id: 12345" in text
    assert "grade_group_students_individually: true" in text
    assert "anonymous_grading: true" in text
    assert "moderated_grading: true" in text
    assert "grader_count: 2" in text
    assert "final_grader_id: 567" in text
    assert "grader_comments_visible_to_graders: true" in text
    assert "grader_names_visible_to_final_grader: true" in text


def test_assignment_peer_review_fields_imported(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "assignments" / "my-assignment.md").read_text()
    assert "peer_reviews: true" in text
    assert "automatic_peer_reviews: true" in text
    assert "peer_review_count: 3" in text
    assert "peer_reviews_assign_at:" in text
    assert "2025-10-05T00:00:00" in text
    assert "intra_group_peer_reviews: true" in text


# --- Discussions ---

def test_discussion_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "discussions" / "week-01-forum.md").exists()


def test_discussion_has_title_frontmatter(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "discussions" / "week-01-forum.md").read_text()
    assert "title: Week 01 Forum" in text


def test_discussion_has_points_frontmatter(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "discussions" / "week-01-forum.md").read_text()
    assert "points_possible: 10.0" in text


def test_discussion_require_initial_post_frontmatter(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "discussions" / "week-01-forum.md").read_text()
    assert "require_initial_post: true" in text


# --- Modules ---

def test_module_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "modules" / "week-1.md").exists()


def test_module_has_title_frontmatter(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "title: Week 1" in text


def test_module_subheaders_as_headings(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "## Readings" in text
    assert "## Work" in text


def test_module_page_link(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "../pages/my-page.md" in text


def test_module_assignment_link(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "../assignments/my-assignment.md" in text


def test_module_discussion_link(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "../discussions/week-01-forum.md" in text


def test_module_discussion_topic_link(output_dir: Path) -> None:
    """DiscussionTopic content_type (Canvas export name) should resolve like Discussion."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "Week 01 Forum (Canvas Style)" in text
    assert "week-01-forum.md" in text


def test_module_external_url_as_absolute_link(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "https://example.com/resource" in text


def test_module_quiz_included_as_link(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "# SKIPPED" not in text
    assert "../quizzes/a-quiz/a-quiz.md" in text


def test_module_order_toml_created(output_dir: Path) -> None:
    """import generates course_settings/module_order.toml."""
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "module_order.toml").exists()


def test_module_order_toml_is_valid_toml(output_dir: Path) -> None:
    """module_order.toml is parseable TOML with an 'order' list."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "module_order.toml").read_text()
    data = tomllib.loads(text)
    assert "order" in data
    assert isinstance(data["order"], list)
    assert len(data["order"]) > 0


def test_module_order_toml_lists_module_files(output_dir: Path) -> None:
    """Every filename in module_order.toml matches an actual module .md file."""
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads(
        (output_dir / "course_settings" / "module_order.toml").read_text()
    )
    for filename in data["order"]:
        assert (output_dir / "modules" / filename).exists(), (
            f"module_order.toml lists {filename!r} but no such file was generated"
        )


def test_module_order_toml_preserves_position_order(output_dir: Path) -> None:
    """Filenames in module_order.toml appear in the same order as the IMSCC positions."""
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads(
        (output_dir / "course_settings" / "module_order.toml").read_text()
    )
    # The fixture has exactly one module ("Week 1") at position 1
    assert data["order"][0] == "week-1.md"


# --- Quizzes ---

def test_quiz_folder_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "quizzes" / "a-quiz").is_dir()


def test_quiz_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "quizzes" / "a-quiz" / "a-quiz.md").exists()


def test_quiz_md_has_title(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "a-quiz.md").read_text()
    assert "title: A Quiz" in text


def test_quiz_md_has_quiz_type(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "a-quiz.md").read_text()
    assert "quiz_type: assignment" in text


def test_quiz_md_has_points(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "a-quiz.md").read_text()
    assert "points_possible: 6.0" in text


def test_quiz_md_lists_questions_in_order(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "a-quiz.md").read_text()
    # Both question files are listed — titles slugify to what-is-22 and explain-something
    assert "questions/what-is-22.md" in text
    assert "questions/explain-something.md" in text
    # MCQ appears before essay (as in the QTI file)
    assert text.index("what-is-22") < text.index("explain-something")


def test_quiz_mcq_question_file_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    q_dir = output_dir / "quizzes" / "a-quiz" / "questions"
    assert (q_dir / "what-is-22.md").exists()


def test_quiz_essay_question_file_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    q_dir = output_dir / "quizzes" / "a-quiz" / "questions"
    assert (q_dir / "explain-something.md").exists()


def test_quiz_mcq_has_correct_frontmatter(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "what-is-22.md").read_text()
    assert "question_type: multiple_choice_question" in text
    assert "points_possible: 1.0" in text
    assert "correct: 2" in text


def test_quiz_mcq_has_answers_section(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "what-is-22.md").read_text()
    assert "## Answers" in text
    assert "3" in text
    assert "4" in text
    assert "5" in text


def test_quiz_essay_has_no_correct_field(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "explain-something.md").read_text()
    assert "correct:" not in text
    assert "question_type: essay_question" in text


def test_quiz_essay_has_question_text(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "explain-something.md").read_text()
    assert "Explain something" in text


# --- Course settings ---

def test_syllabus_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "syllabus.md").exists()


def test_syllabus_has_content(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "syllabus.md").read_text()
    assert "Syllabus" in text
    assert "$IMS-CC-FILEBASE$" not in text


def test_course_settings_toml_at_root(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "course_settings.toml").exists()


def test_course_settings_toml_has_title(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "course_settings.toml").read_text()
    assert "Test Course" in text


def test_course_settings_toml_has_all_basic_fields(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    assert data["course_code"] == "TEST101"
    assert data["default_view"] == "modules"
    assert data["is_public"] is False
    assert data["license"] == "private"


def test_course_settings_toml_booleans_typed(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    assert data["grading_standard_enabled"] is True
    assert isinstance(data["home_page_announcement_limit"], int)
    assert data["home_page_announcement_limit"] == 3


def test_course_settings_toml_post_policy(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    assert data["default_post_policy"]["post_manually"] is True


def test_course_settings_toml_has_last_modified(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    assert data.get("last_modified") == "2025-08-01"


def test_course_settings_toml_has_grading_standard(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    assert "grading_standards" in data
    gs = data["grading_standards"][0]
    assert gs["title"] == "Test Grade Scale"
    assert isinstance(gs["data"], list)
    assert gs["data"][0] == ["A", 0.93]


def test_course_settings_toml_has_assignment_groups(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    assert "assignment_groups" in data
    titles = [g["title"] for g in data["assignment_groups"]]
    assert "Homework" in titles
    assert "Exams" in titles


def test_course_settings_toml_assignment_group_rules(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    exams = next(g for g in data["assignment_groups"] if g["title"] == "Exams")
    assert exams["rules"][0]["drop_type"] == "drop_lowest"
    assert exams["rules"][0]["drop_count"] == 1


def test_course_settings_toml_has_late_policy(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    assert "late_policy" in data
    lp = data["late_policy"]
    assert lp["late_submission_deduction_enabled"] is True
    assert lp["late_submission_deduction"] == 10.0
    assert lp["late_submission_interval"] == "day"


def test_canvas_toml_has_base_url_from_context(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "canvas.toml").read_text()
    assert "test.instructure.com" in text


def test_canvas_toml_has_course_id_from_context(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "canvas.toml").read_text()
    assert "12345" in text


def test_canvas_toml_has_base_url_key(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "canvas.toml").exists()
    text = (output_dir / "course_settings" / "canvas.toml").read_text()
    assert "base_url" in text
    assert "course_id" in text


def test_events_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "events.md").exists()


def test_events_md_has_event_title(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "events.md").read_text()
    assert "No Class - Holiday" in text


def test_events_md_has_date(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "events.md").read_text()
    assert "2025-11-27" in text


def test_events_md_has_event_with_description(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "events.md").read_text()
    assert "Project Due" in text
    assert "final project" in text


def test_no_course_settings_md_in_subdir(output_dir: Path) -> None:
    """course_settings/ subdir should no longer contain a course_settings.md."""
    run_import(FIXTURE_DIR, output_dir)
    assert not (output_dir / "course_settings" / "course_settings.md").exists()


def test_no_canvas_manifest_written(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert not (output_dir / ".canvas-manifest.toml").exists()


# --- Rubrics ---

def test_rubrics_toml_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "rubrics.toml").exists()


def test_rubrics_toml_has_rubric_titles(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "rubrics.toml").read_text())
    titles = [r["title"] for r in data["rubrics"]]
    assert "Test Rubric" in titles
    assert "Participation Rubric" in titles


def test_rubrics_toml_has_criteria(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "rubrics.toml").read_text())
    rubric = next(r for r in data["rubrics"] if r["title"] == "Test Rubric")
    assert rubric["criteria"][0]["description"] == "Quality"


def test_rubrics_toml_has_ratings(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "rubrics.toml").read_text())
    rubric = next(r for r in data["rubrics"] if r["title"] == "Test Rubric")
    ratings = rubric["criteria"][0]["ratings"]
    assert ratings[0]["description"] == "Excellent"
    assert ratings[0]["points"] == 5.0


def test_rubrics_toml_overrides_read_only_and_reusable(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "rubrics.toml").read_text())
    for rubric in data["rubrics"]:
        assert rubric["read_only"] is False, f"{rubric['title']} should have read_only=false"
        assert rubric["reusable"] is True, f"{rubric['title']} should have reusable=true"


# --- Files meta ---

def test_files_meta_toml_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "files_meta.toml").exists()


def test_files_meta_toml_has_folders(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "files_meta.toml").read_text())
    assert "folders" in data
    assert data["folders"][0]["path"] == "hidden_folder"


def test_files_meta_toml_has_files(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "files_meta.toml").read_text())
    assert "files" in data
    identifiers = [f["identifier"] for f in data["files"]]
    assert "gtest_file_locked" in identifiers
    assert "gtest_file_named" in identifiers


def test_files_meta_toml_locked_preserved(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "files_meta.toml").read_text())
    locked = next(f for f in data["files"] if f["identifier"] == "gtest_file_locked")
    assert locked["locked"] is True


# ---------------------------------------------------------------------------
# Full pipeline: zip input
# ---------------------------------------------------------------------------


def test_run_import_from_zip(tmp_path: Path) -> None:
    """Zipping the fixture and importing it should produce the same output."""
    zip_path = tmp_path / "test.imscc"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in FIXTURE_DIR.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(FIXTURE_DIR))

    out = tmp_path / "output"
    run_import(zip_path, out)

    assert (out / "pages" / "my-page.md").exists()
    assert (out / "assignments" / "my-assignment.md").exists()
    assert (out / "discussions" / "week-01-forum.md").exists()
    assert (out / "modules" / "week-1.md").exists()
    assert (out / "course_settings" / "canvas.toml").exists()
    assert (out / "course_settings" / "course_settings.toml").exists()


# ---------------------------------------------------------------------------
# LTI resources
# ---------------------------------------------------------------------------


def test_lti_resource_imscc_path_set(output_dir: Path) -> None:
    """LTI resource entries should have imscc_path pointing to the XML file."""
    manifest = parse_imsmanifest(FIXTURE_DIR)
    lti_entry = manifest.get("g_lti_1")
    assert lti_entry is not None
    assert lti_entry.category == "lti"
    assert lti_entry.imscc_path == "lti_resource_links/g_lti_1.xml"


def test_lti_resource_not_in_pages(output_dir: Path) -> None:
    """LTI resources should not produce a pages/ file."""
    run_import(FIXTURE_DIR, output_dir)
    assert not any(output_dir.glob("pages/g_lti_1*"))


# ---------------------------------------------------------------------------
# ContextExternalTool module items
# ---------------------------------------------------------------------------


def test_module_context_external_tool_as_link(output_dir: Path) -> None:
    """ContextExternalTool items should appear as URL links in the module file."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "https://video.example.com/watch?v=abc123" in text
    assert "Video Lecture" in text


def test_module_context_external_tool_no_skip_comment(output_dir: Path) -> None:
    """ContextExternalTool items should not leave a SKIPPED comment."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "SKIPPED" not in text


# ---------------------------------------------------------------------------
# Canvas export format quiz (href="" + dependency resource)
# ---------------------------------------------------------------------------


def test_quiz_canvas_format_folder_created(output_dir: Path) -> None:
    """Quiz with Canvas export format (href='') should create its folder."""
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "quizzes" / "quiz-two").is_dir()


def test_quiz_canvas_format_md_has_correct_title(output_dir: Path) -> None:
    """Quiz with href='' should get its title from assessment_meta.xml via dependency."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "quiz-two" / "quiz-two.md").read_text()
    assert "title: Quiz Two" in text


def test_quiz_canvas_format_md_has_points(output_dir: Path) -> None:
    """Quiz with Canvas export format should include points_possible from assessment_meta."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "quiz-two" / "quiz-two.md").read_text()
    assert "points_possible: 2.0" in text


def test_quiz_canvas_format_question_uses_non_cc_qti(output_dir: Path) -> None:
    """The non_cc_assessments .xml.qti file (with points_possible) should be preferred."""
    run_import(FIXTURE_DIR, output_dir)
    q_dir = output_dir / "quizzes" / "quiz-two" / "questions"
    assert q_dir.is_dir()
    q_files = list(q_dir.glob("*.md"))
    assert len(q_files) == 1
    text = q_files[0].read_text()
    assert "points_possible: 2.0" in text   # only present in the .xml.qti file


# ---------------------------------------------------------------------------
# Standalone question banks (should be skipped, not crash or misclassify)
# ---------------------------------------------------------------------------


def test_question_bank_not_in_quizzes(output_dir: Path) -> None:
    """Standalone QTI objectbank resources should not produce a quizzes/ entry."""
    run_import(FIXTURE_DIR, output_dir)
    assert not (output_dir / "quizzes" / "g-bank-1").exists()
    assert not any(output_dir.glob("quizzes/*bank*"))


def test_question_bank_not_in_course_settings(output_dir: Path) -> None:
    """Standalone question bank should not overwrite course_settings.toml."""
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings" / "course_settings.toml").read_text())
    assert "Test Course" in data["title"]  # file is intact, not corrupted by question bank


# ---------------------------------------------------------------------------
# Item 1: Discussion attachments
# ---------------------------------------------------------------------------


def test_discussion_attachment_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "discussions" / "discussion-with-attachment.md").exists()


def test_discussion_attachment_section_present(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "discussions" / "discussion-with-attachment.md").read_text()
    assert "## Attachments" in text


def test_discussion_attachment_link_prefixed(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "discussions" / "discussion-with-attachment.md").read_text()
    assert "../assets/media/diagram.png" in text


def test_discussion_attachment_multiple_links(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "discussions" / "discussion-with-attachment.md").read_text()
    assert "../assets/media/notes.pdf" in text


def test_discussion_no_attachment_section_when_none(output_dir: Path) -> None:
    """The original discussion has no attachments — its output should have no ## Attachments."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "discussions" / "week-01-forum.md").read_text()
    assert "## Attachments" not in text


# ---------------------------------------------------------------------------
# Item 2: Web link target / windowFeatures in module output
# ---------------------------------------------------------------------------


def test_module_external_url_has_target_comment(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert 'target="_blank"' in text


def test_module_external_url_has_window_features_comment(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert 'windowFeatures="width=800,height=600"' in text


def test_module_external_url_comment_is_html_style(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "<!-- " in text and " -->" in text


# ---------------------------------------------------------------------------
# Item 3: QTI new question types (integration: written by _write_question_file)
# ---------------------------------------------------------------------------


def test_quiz_mcq_feedback_section_present(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "what-is-22.md").read_text()
    assert "## Feedback" in text


def test_quiz_mcq_general_feedback_text(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "what-is-22.md").read_text()
    assert "Think carefully about number operations." in text


def test_quiz_mcq_correct_feedback_subsection(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "what-is-22.md").read_text()
    assert "### Correct" in text
    assert "That is correct!" in text


def test_quiz_mcq_incorrect_feedback_subsection(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "what-is-22.md").read_text()
    assert "### Incorrect" in text
    assert "Try again." in text


def test_quiz_mcq_per_answer_feedback(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "what-is-22.md").read_text()
    assert "### Per-answer" in text
    assert "3 is not the sum of 2+2." in text


def test_quiz_essay_sample_solution_present(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "quizzes" / "a-quiz" / "questions" / "explain-something.md").read_text()
    assert "## Sample Solution" in text
    assert "A good answer would discuss the key concepts" in text


# ---------------------------------------------------------------------------
# Item 6: Canvas question banks
# ---------------------------------------------------------------------------


def test_question_bank_folder_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "question_banks" / "fixture-question-bank").is_dir()


def test_question_bank_toml_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "question_banks" / "fixture-question-bank" / "fixture-question-bank.toml").exists()


def test_question_bank_toml_has_title(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads(
        (output_dir / "question_banks" / "fixture-question-bank" / "fixture-question-bank.toml").read_text()
    )
    assert data["bank_title"] == "Fixture Question Bank"


def test_question_bank_toml_has_context_uuid(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads(
        (output_dir / "question_banks" / "fixture-question-bank" / "fixture-question-bank.toml").read_text()
    )
    assert data["bank_context_uuid"] == "FIXTURE123UyJjHbdsdzYFn1LYxMYjMYh4GITEORKR"


def test_question_bank_toml_has_state(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads(
        (output_dir / "question_banks" / "fixture-question-bank" / "fixture-question-bank.toml").read_text()
    )
    assert data["bank_state"] == "active"


def test_question_bank_questions_folder_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "question_banks" / "fixture-question-bank" / "questions").is_dir()


def test_question_bank_mcq_question_file_exists(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    q_dir = output_dir / "question_banks" / "fixture-question-bank" / "questions"
    assert (q_dir / "bank-mcq-question.md").exists()


def test_question_bank_essay_question_file_exists(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    q_dir = output_dir / "question_banks" / "fixture-question-bank" / "questions"
    assert (q_dir / "bank-essay-question.md").exists()


def test_question_bank_mcq_has_original_answer_ids(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (
        output_dir / "question_banks" / "fixture-question-bank" / "questions" / "bank-mcq-question.md"
    ).read_text()
    assert "original_answer_ids" in text
    assert "1001" in text


def test_question_bank_mcq_has_correct_field(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (
        output_dir / "question_banks" / "fixture-question-bank" / "questions" / "bank-mcq-question.md"
    ).read_text()
    assert "correct:" in text


# ---------------------------------------------------------------------------
# CANVAS_COURSE_REFERENCE snippet helpers
# ---------------------------------------------------------------------------

BASE_URL = "https://test.instructure.com/courses/12345"


def test_write_canvas_course_reference_snippet_creates_file(tmp_path: Path) -> None:
    _write_canvas_course_reference_snippet(BASE_URL, tmp_path)
    snippet = tmp_path / "snippets" / "inline" / "CANVAS_COURSE_REFERENCE.md"
    assert snippet.exists()
    assert snippet.read_text() == BASE_URL


def test_replace_canvas_course_url_in_md(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    md = tmp_path / "pages" / "test.md"
    md.write_text(f"[Modules]({BASE_URL}/modules)\n")
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    text = md.read_text()
    assert BASE_URL not in text
    assert "$../snippets/inline/CANVAS_COURSE_REFERENCE.md$" in text
    assert '[Modules]($../snippets/inline/CANVAS_COURSE_REFERENCE.md$/modules "Modules")' in text


def test_replace_canvas_course_url_includes_link_title(tmp_path: Path) -> None:
    """The link text is added as a Markdown title on the rewritten link."""
    (tmp_path / "pages").mkdir()
    md = tmp_path / "pages" / "test.md"
    md.write_text(f"[My Grades]({BASE_URL}/grades)\n")
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    text = md.read_text()
    assert '"My Grades"' in text
    assert '$../snippets/inline/CANVAS_COURSE_REFERENCE.md$/grades "My Grades"' in text


def test_replace_canvas_course_url_depth_adjusted(tmp_path: Path) -> None:
    """Files at depth 2 get ../../ prefix in the snippet ref."""
    (tmp_path / "quizzes" / "my-quiz").mkdir(parents=True)
    md = tmp_path / "quizzes" / "my-quiz" / "my-quiz.md"
    md.write_text(f"[Go]({BASE_URL}/grades)\n")
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    text = md.read_text()
    assert "$../../snippets/inline/CANVAS_COURSE_REFERENCE.md$" in text


def test_replace_canvas_course_url_skips_non_matching_domain(tmp_path: Path) -> None:
    """Links to a different Canvas domain are not replaced."""
    (tmp_path / "pages").mkdir()
    md = tmp_path / "pages" / "test.md"
    original = "[Grades](https://other.instructure.com/courses/12345/grades)\n"
    md.write_text(original)
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    assert md.read_text() == original


def test_replace_canvas_course_url_skips_frontmatter_values(tmp_path: Path) -> None:
    """Plain-text occurrences of the URL in frontmatter are not touched."""
    (tmp_path / "assignments").mkdir()
    md = tmp_path / "assignments" / "hw.md"
    md.write_text(f"---\ngroup_category_id: 12345\n---\n\nSome text.\n")
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    assert "group_category_id: 12345" in md.read_text()


def test_replace_canvas_course_url_multiple_links(tmp_path: Path) -> None:
    """All matching links in a file are replaced."""
    (tmp_path / "pages").mkdir()
    md = tmp_path / "pages" / "nav.md"
    md.write_text(f"[Modules]({BASE_URL}/modules)\n[Grades]({BASE_URL}/grades)\n")
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    text = md.read_text()
    assert text.count("$../snippets/inline/CANVAS_COURSE_REFERENCE.md$") == 2


def test_replace_canvas_course_url_skips_file_without_match(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    md = tmp_path / "pages" / "no-url.md"
    original = "No canvas URLs here.\n"
    md.write_text(original)
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    assert md.read_text() == original


# ---------------------------------------------------------------------------
# Integration: snippet created and course URL replaced end-to-end
# ---------------------------------------------------------------------------

def test_run_import_creates_canvas_course_reference_snippet(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    snippet = output_dir / "snippets" / "inline" / "CANVAS_COURSE_REFERENCE.md"
    assert snippet.exists()
    assert snippet.read_text() == "https://test.instructure.com/courses/12345"


def test_run_import_replaces_course_url_in_page(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    assert "https://test.instructure.com/courses/12345" not in text
    assert "$../snippets/inline/CANVAS_COURSE_REFERENCE.md$" in text
    assert '"course modules"' in text


def test_run_import_does_not_replace_course_id_outside_url(output_dir: Path) -> None:
    """group_category_id in assignment frontmatter must not be replaced."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "assignments" / "my-assignment.md").read_text()
    assert "group_category_id: 12345" in text


def test_run_import_snippet_roundtrip(output_dir: Path) -> None:
    """After import, preprocess_snippets on a generated page produces the real Canvas URL."""
    from github_to_canvas.convert import preprocess_snippets

    run_import(FIXTURE_DIR, output_dir)

    page = output_dir / "pages" / "my-page.md"
    snippets_dir = output_dir / "snippets"
    result = preprocess_snippets(page.read_text(), page, snippets_dir)

    assert "https://test.instructure.com/courses/12345" in result
    assert "$../snippets" not in result
    assert "https://test.instructure.com/courses/12345/modules" in result


def test_run_import_no_domain_no_snippet(tmp_path: Path) -> None:
    """When context.xml has no canvas_domain, no snippet file is created."""
    import shutil

    fixture_copy = tmp_path / "imscc"
    shutil.copytree(FIXTURE_DIR, fixture_copy)

    ctx = fixture_copy / "course_settings" / "context.xml"
    ctx.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<context_info xmlns="http://canvas.instructure.com/xsd/cccv1p0">\n'
        '  <course_id>12345</course_id>\n'
        '</context_info>\n'
    )

    output = tmp_path / "output"
    run_import(fixture_copy, output)
    assert not (output / "snippets" / "inline" / "CANVAS_COURSE_REFERENCE.md").exists()


def test_run_import_no_course_id_no_snippet(tmp_path: Path) -> None:
    """When context.xml has no course_id, no snippet file is created."""
    import shutil

    fixture_copy = tmp_path / "imscc"
    shutil.copytree(FIXTURE_DIR, fixture_copy)

    ctx = fixture_copy / "course_settings" / "context.xml"
    ctx.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<context_info xmlns="http://canvas.instructure.com/xsd/cccv1p0">\n'
        '  <canvas_domain>test.instructure.com</canvas_domain>\n'
        '</context_info>\n'
    )

    output = tmp_path / "output"
    run_import(fixture_copy, output)
    assert not (output / "snippets" / "inline" / "CANVAS_COURSE_REFERENCE.md").exists()


# ---------------------------------------------------------------------------
# $CANVAS_COURSE_ID$ placeholder token — end-to-end handling
# ---------------------------------------------------------------------------


def test_run_import_canvas_course_id_token_replaced_in_page(output_dir: Path) -> None:
    """$CANVAS_COURSE_ID$ nav links in page HTML are converted to CANVAS_COURSE_REFERENCE snippet."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    # The placeholder must not survive into the final markdown
    assert "$CANVAS_COURSE_ID$" not in text


def test_run_import_canvas_course_id_token_becomes_snippet(output_dir: Path) -> None:
    """A $CANVAS_COURSE_ID$ href is rewritten to use the CANVAS_COURSE_REFERENCE snippet."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    # Both the numeric URL (from the hardcoded link) and the token URL (from
    # $CANVAS_COURSE_ID$) should ultimately become snippet references.
    assert "$../snippets/inline/CANVAS_COURSE_REFERENCE.md$" in text
    assert "https://test.instructure.com/courses/12345" not in text


def test_run_import_canvas_course_id_snippet_roundtrip(output_dir: Path) -> None:
    """After import, preprocess_snippets expands the $CANVAS_COURSE_ID$-derived link to the real URL."""
    from github_to_canvas.convert import preprocess_snippets

    run_import(FIXTURE_DIR, output_dir)

    page = output_dir / "pages" / "my-page.md"
    snippets_dir = output_dir / "snippets"
    result = preprocess_snippets(page.read_text(), page, snippets_dir)

    # Both the /modules and the /grades (from $CANVAS_COURSE_ID$) links expand
    assert "https://test.instructure.com/courses/12345/modules" in result
    assert "https://test.instructure.com/courses/12345/grades" in result


# _replace_canvas_course_url — title attribute handling
# ---------------------------------------------------------------------------


def test_replace_canvas_course_url_with_pandoc_title_attr(tmp_path: Path) -> None:
    """Links with a Pandoc-generated title attribute ([text](url "title")) are matched and rewritten."""
    (tmp_path / "pages").mkdir()
    md = tmp_path / "pages" / "test.md"
    md.write_text(f'[Syllabus]({BASE_URL}/assignments/syllabus "Syllabus")\n')
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    text = md.read_text()
    assert BASE_URL not in text
    assert "$../snippets/inline/CANVAS_COURSE_REFERENCE.md$" in text
    assert "/assignments/syllabus" in text


def test_replace_canvas_course_url_converts_bare_token(tmp_path: Path) -> None:
    """A bare $CANVAS_COURSE_REFERENCE$ token (no resolved URL) is converted to the snippet ref."""
    (tmp_path / "pages").mkdir()
    md = tmp_path / "pages" / "test.md"
    md.write_text('[Gradebook]($CANVAS_COURSE_REFERENCE$/grades "Grades")\n')
    _replace_canvas_course_url_in_md_files(tmp_path, BASE_URL)
    text = md.read_text()
    assert "$CANVAS_COURSE_REFERENCE$/grades" not in text
    assert '[Gradebook]($../snippets/inline/CANVAS_COURSE_REFERENCE.md$/grades "Gradebook")' in text


# $CANVAS_COURSE_REFERENCE$ placeholder token — end-to-end handling
# ---------------------------------------------------------------------------


def test_run_import_canvas_course_reference_token_replaced_in_page(output_dir: Path) -> None:
    """$CANVAS_COURSE_REFERENCE$ nav links in page HTML are converted to CANVAS_COURSE_REFERENCE snippet."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    assert "$CANVAS_COURSE_REFERENCE$" not in text


def test_run_import_canvas_course_reference_token_becomes_snippet(output_dir: Path) -> None:
    """A $CANVAS_COURSE_REFERENCE$ href is rewritten to use the CANVAS_COURSE_REFERENCE snippet."""
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "pages" / "my-page.md").read_text()
    # The Syllabus link (from $CANVAS_COURSE_REFERENCE$ fixture) must become a snippet ref
    assert "$../snippets/inline/CANVAS_COURSE_REFERENCE.md$/assignments/syllabus" in text


def test_run_import_canvas_course_reference_snippet_roundtrip(output_dir: Path) -> None:
    """After import, preprocess_snippets expands the $CANVAS_COURSE_REFERENCE$-derived link."""
    from github_to_canvas.convert import preprocess_snippets

    run_import(FIXTURE_DIR, output_dir)

    page = output_dir / "pages" / "my-page.md"
    snippets_dir = output_dir / "snippets"
    result = preprocess_snippets(page.read_text(), page, snippets_dir)

    assert "https://test.instructure.com/courses/12345/assignments/syllabus" in result
