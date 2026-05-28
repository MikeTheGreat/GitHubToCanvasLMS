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


def test_module_quiz_skipped_with_comment(output_dir: Path, capsys: pytest.CaptureFixture) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "modules" / "week-1.md").read_text()
    assert "# SKIPPED" in text
    assert "Quiz" in text
    assert "WARNING" in capsys.readouterr().out


# --- Course settings ---

def test_syllabus_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "syllabus.md").exists()


def test_syllabus_has_content(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "syllabus.md").read_text()
    assert "Syllabus" in text
    assert "$IMS-CC-FILEBASE$" not in text


def test_course_settings_md_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "course_settings" / "course_settings.md").exists()


def test_course_settings_has_title(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    text = (output_dir / "course_settings" / "course_settings.md").read_text()
    assert "Test Course" in text


def test_canvas_toml_skeleton_created(output_dir: Path) -> None:
    run_import(FIXTURE_DIR, output_dir)
    assert (output_dir / "canvas.toml").exists()
    text = (output_dir / "canvas.toml").read_text()
    assert "base_url" in text
    assert "course_id" in text


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
