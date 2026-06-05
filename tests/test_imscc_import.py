"""Integration tests: full import pipeline from synthetic IMSCC fixture."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from github_to_canvas.imscc_import import open_imscc, run_import

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


def test_module_external_url_as_absolute_link(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "https://example.com/resource" in text


def test_module_quiz_included_as_link(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "# SKIPPED" not in text
    assert "../quizzes/a-quiz/a-quiz.md" in text


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
    assert (output_dir / "course_settings.toml").exists()


def test_course_settings_toml_has_title(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings.toml").read_text()
    assert "Test Course" in text


def test_course_settings_toml_has_all_basic_fields(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings.toml").read_text())
    assert data["course_code"] == "TEST101"
    assert data["default_view"] == "modules"
    assert data["is_public"] is False
    assert data["license"] == "private"


def test_course_settings_toml_booleans_typed(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings.toml").read_text())
    assert data["grading_standard_enabled"] is True
    assert isinstance(data["home_page_announcement_limit"], int)
    assert data["home_page_announcement_limit"] == 3


def test_course_settings_toml_post_policy(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings.toml").read_text())
    assert data["default_post_policy"]["post_manually"] is True


def test_course_settings_toml_has_last_modified(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings.toml").read_text())
    assert data.get("last_modified") == "2025-08-01"


def test_course_settings_toml_has_grading_standard(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings.toml").read_text())
    assert "grading_standards" in data
    gs = data["grading_standards"][0]
    assert gs["title"] == "Test Grade Scale"
    assert isinstance(gs["data"], list)
    assert gs["data"][0] == ["A", 0.93]


def test_course_settings_toml_has_assignment_groups(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings.toml").read_text())
    assert "assignment_groups" in data
    titles = [g["title"] for g in data["assignment_groups"]]
    assert "Homework" in titles
    assert "Exams" in titles


def test_course_settings_toml_assignment_group_rules(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings.toml").read_text())
    exams = next(g for g in data["assignment_groups"] if g["title"] == "Exams")
    assert exams["rules"][0]["drop_type"] == "drop_lowest"
    assert exams["rules"][0]["drop_count"] == 1


def test_course_settings_toml_has_late_policy(output_dir: Path) -> None:
    import tomllib
    run_import(FIXTURE_DIR, output_dir)
    data = tomllib.loads((output_dir / "course_settings.toml").read_text())
    assert "late_policy" in data
    lp = data["late_policy"]
    assert lp["late_submission_deduction_enabled"] is True
    assert lp["late_submission_deduction"] == 10.0
    assert lp["late_submission_interval"] == "day"


def test_canvas_toml_has_base_url_from_context(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "canvas.toml").read_text()
    assert "test.instructure.com" in text


def test_canvas_toml_has_course_id_from_context(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "canvas.toml").read_text()
    assert "12345" in text


def test_canvas_toml_has_base_url_key(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "canvas.toml").exists()
    text = (output_dir / "canvas.toml").read_text()
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
    assert (out / "canvas.toml").exists()
    assert (out / "course_settings.toml").exists()
