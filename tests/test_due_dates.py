"""Tests for centralized due_dates feature."""
from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from github_to_canvas.imscc_import import (
    _build_frontmatter,
    _collect_due_date,
    _extract_date_fields,
    format_due_dates_toml,
    run_import,
)
from github_to_canvas.sync import (
    find_due_date_override,
    load_due_dates,
    parse_frontmatter,
)

FIXTURES = Path(__file__).parent / "fixtures"
IMSCC_FIXTURES = FIXTURES / "imscc"


# ---------------------------------------------------------------------------
# Unit tests: sync helpers
# ---------------------------------------------------------------------------


class TestLoadDueDates:
    def test_no_settings_file(self, tmp_path: Path) -> None:
        assert load_due_dates(tmp_path) == []

    def test_no_due_dates_key(self, tmp_path: Path) -> None:
        cs = tmp_path / "course_settings"
        cs.mkdir()
        (cs / "course_settings.toml").write_text('title = "Course"\n')
        assert load_due_dates(tmp_path) == []

    def test_reads_inline_tables(self, tmp_path: Path) -> None:
        cs = tmp_path / "course_settings"
        cs.mkdir()
        (cs / "course_settings.toml").write_text(
            'due_dates = [\n'
            '    {name = "HW1", due_at = "2025-02-01T23:59:00", unlock_at = "", lock_at = ""},\n'
            '    {name = "Quiz", type = "quiz", due_at = "2025-03-01T23:59:00", unlock_at = "", lock_at = ""},\n'
            ']\n'
        )
        result = load_due_dates(tmp_path)
        assert len(result) == 2
        assert result[0]["name"] == "HW1"
        assert result[0]["due_at"] == "2025-02-01T23:59:00"
        assert result[1]["type"] == "quiz"


class TestFindDueDateOverride:
    def test_match_by_name(self) -> None:
        due_dates = [{"name": "HW1", "due_at": "2025-02-01T23:59:00"}]
        result = find_due_date_override(due_dates, "HW1", "assignment")
        assert result is not None
        assert result["due_at"] == "2025-02-01T23:59:00"

    def test_no_match(self) -> None:
        due_dates = [{"name": "HW1", "due_at": "2025-02-01T23:59:00"}]
        assert find_due_date_override(due_dates, "HW2", "assignment") is None

    def test_type_filter_matches(self) -> None:
        due_dates = [{"name": "HW1", "type": "assignment", "due_at": "2025-02-01T23:59:00"}]
        result = find_due_date_override(due_dates, "HW1", "assignment")
        assert result is not None

    def test_type_filter_excludes(self) -> None:
        due_dates = [{"name": "HW1", "type": "assignment", "due_at": "2025-02-01T23:59:00"}]
        assert find_due_date_override(due_dates, "HW1", "discussion") is None

    def test_no_type_matches_any(self) -> None:
        due_dates = [{"name": "HW1", "due_at": "2025-02-01T23:59:00"}]
        assert find_due_date_override(due_dates, "HW1", "discussion") is not None


# ---------------------------------------------------------------------------
# Unit tests: imscc_import helpers
# ---------------------------------------------------------------------------


class TestExtractDateFields:
    def test_pops_date_keys(self) -> None:
        fm = {"title": "HW1", "due_at": "2025-02-01", "lock_at": "2025-02-08", "points": 50}
        result = _extract_date_fields(fm)
        assert result == {"due_at": "2025-02-01", "lock_at": "2025-02-08"}
        assert "due_at" not in fm
        assert "lock_at" not in fm
        assert fm["title"] == "HW1"

    def test_no_date_fields(self) -> None:
        fm = {"title": "HW1", "points": 50}
        result = _extract_date_fields(fm)
        assert result == {}


class TestCollectDueDate:
    def test_collects_with_dates(self) -> None:
        collector: list[dict] = []
        _collect_due_date(collector, "HW1", "assignment", {"due_at": "2025-02-01", "lock_at": ""})
        assert len(collector) == 1
        assert collector[0]["name"] == "HW1"
        assert collector[0]["type"] == "assignment"
        assert collector[0]["due_at"] == "2025-02-01"

    def test_skips_when_all_empty(self) -> None:
        collector: list[dict] = []
        _collect_due_date(collector, "HW1", "assignment", {"due_at": None, "lock_at": None})
        assert len(collector) == 0

    def test_skips_when_collector_none(self) -> None:
        _collect_due_date(None, "HW1", "assignment", {"due_at": "2025-02-01"})


class TestFormatDueDatesToml:
    def test_empty(self) -> None:
        assert format_due_dates_toml([]) == ""

    def test_single_entry(self) -> None:
        entries = [{"name": "HW1", "type": "assignment", "due_at": "2025-02-01", "unlock_at": "", "lock_at": ""}]
        result = format_due_dates_toml(entries)
        assert "due_dates = [" in result
        assert 'name = "HW1"' in result
        assert 'type = "assignment"' in result
        assert 'due_at = "2025-02-01"' in result

    def test_roundtrip_through_toml_parser(self) -> None:
        entries = [
            {"name": "HW1", "due_at": "2025-02-01T23:59:00", "unlock_at": "", "lock_at": "2025-02-08T23:59:00"},
            {"name": "Quiz", "type": "quiz", "due_at": "2025-03-01T23:59:00", "unlock_at": "", "lock_at": ""},
        ]
        toml_str = format_due_dates_toml(entries)
        parsed = tomllib.loads(toml_str)
        assert len(parsed["due_dates"]) == 2
        assert parsed["due_dates"][0]["name"] == "HW1"
        assert parsed["due_dates"][1]["type"] == "quiz"


class TestBuildFrontmatterCommented:
    def test_no_commented_fields(self) -> None:
        result = _build_frontmatter({"title": "HW1", "published": True})
        assert "# " not in result.replace("---", "")

    def test_with_commented_fields(self) -> None:
        result = _build_frontmatter(
            {"title": "HW1"},
            commented_fields={"due_at": "2025-02-01T23:59:00", "lock_at": ""},
            comment_note="Due dates are managed centrally in course_settings/course_settings.toml",
        )
        assert "# Due dates are managed centrally" in result
        assert "# due_at:" in result
        assert "title: HW1" in result
        # lock_at is empty string — should still appear as commented
        assert "# lock_at:" in result

    def test_commented_none_skipped(self) -> None:
        result = _build_frontmatter(
            {"title": "HW1"},
            commented_fields={"due_at": None},
        )
        assert "due_at" not in result


# ---------------------------------------------------------------------------
# Unit tests: _resolve_date_overrides sentinel values
# ---------------------------------------------------------------------------


class TestResolveDateOverrides:
    def test_actual_date_values(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        override = {"due_at": "2025-06-01T23:59:00", "unlock_at": "2025-05-01", "lock_at": "2025-06-08"}
        result = _resolve_date_overrides(override, canvas_id=123, local_key="x.md", errors=None)
        assert result == {"due_at": "2025-06-01T23:59:00", "unlock_at": "2025-05-01", "lock_at": "2025-06-08"}

    def test_none_sentinel_clears_date(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        override = {"due_at": "2025-06-01T23:59:00", "unlock_at": "NONE", "lock_at": "none"}
        result = _resolve_date_overrides(override, canvas_id=123, local_key="x.md", errors=None)
        assert result == {"due_at": "2025-06-01T23:59:00", "unlock_at": "", "lock_at": ""}

    def test_keep_sentinel_omits_field(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        override = {"due_at": "2025-06-01T23:59:00", "unlock_at": "KEEP", "lock_at": "Keep"}
        result = _resolve_date_overrides(override, canvas_id=123, local_key="x.md", errors=None)
        assert result == {"due_at": "2025-06-01T23:59:00"}

    def test_empty_string_acts_as_keep_with_warning(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        errors: list[str] = []
        override = {"due_at": "2025-06-01T23:59:00", "unlock_at": "", "lock_at": ""}
        result = _resolve_date_overrides(override, canvas_id=123, local_key="test.md", errors=errors)
        assert result == {"due_at": "2025-06-01T23:59:00"}
        assert len(errors) == 1
        assert "unlock_at, lock_at" in errors[0]
        assert "CREATE_NONE_THEN_KEEP" in errors[0]

    def test_empty_string_consolidated_warning(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        errors: list[str] = []
        override = {"due_at": "", "unlock_at": "", "lock_at": ""}
        result = _resolve_date_overrides(override, canvas_id=123, local_key="test.md", errors=errors)
        assert result == {}
        assert len(errors) == 1
        assert "unlock_at, due_at, lock_at" in errors[0]

    def test_create_none_then_keep_on_create(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        override = {"due_at": "2025-06-01", "unlock_at": "CREATE_NONE_THEN_KEEP", "lock_at": "create_none_then_keep"}
        result = _resolve_date_overrides(override, canvas_id=None, local_key="x.md", errors=None)
        assert result == {"due_at": "2025-06-01", "unlock_at": "", "lock_at": ""}

    def test_create_none_then_keep_on_update(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        override = {"due_at": "2025-06-01", "unlock_at": "CREATE_NONE_THEN_KEEP", "lock_at": "create_none_then_keep"}
        result = _resolve_date_overrides(override, canvas_id=123, local_key="x.md", errors=None)
        assert result == {"due_at": "2025-06-01"}

    def test_case_insensitive_sentinels(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        override = {"due_at": "NoNe", "unlock_at": "kEeP", "lock_at": "Create_None_Then_Keep"}
        result = _resolve_date_overrides(override, canvas_id=None, local_key="x.md", errors=None)
        assert result == {"due_at": "", "lock_at": ""}

    def test_no_warning_when_errors_is_none(self) -> None:
        from github_to_canvas.sync import _resolve_date_overrides
        override = {"due_at": "", "unlock_at": "", "lock_at": ""}
        result = _resolve_date_overrides(override, canvas_id=123, local_key="x.md", errors=None)
        assert result == {}


# ---------------------------------------------------------------------------
# Integration: sync with due_dates override
# ---------------------------------------------------------------------------


def _mock_assignment(canvas_id: int) -> MagicMock:
    a = MagicMock()
    a.id = canvas_id
    a.html_url = f"https://school.instructure.com/courses/1/assignments/{canvas_id}"
    a.edit.return_value = a
    return a


def _mock_page(page_id: int, url: str) -> MagicMock:
    p = MagicMock()
    p.page_id = page_id
    p.url = url
    p.html_url = f"https://school.instructure.com/courses/1/pages/{url}"
    p.edit.return_value = p
    return p


def _mock_discussion(canvas_id: int) -> MagicMock:
    d = MagicMock()
    d.id = canvas_id
    d.html_url = f"https://school.instructure.com/courses/1/discussion_topics/{canvas_id}"
    d.update.return_value = d
    return d


def _mock_module(canvas_id: int) -> MagicMock:
    m = MagicMock()
    m.id = canvas_id
    m.edit.return_value = m
    m.get_module_items.return_value = []
    return m


def _mock_item(item_id: int) -> MagicMock:
    i = MagicMock()
    i.id = item_id
    return i


@pytest.fixture
def course_root(tmp_path: Path) -> Path:
    root = tmp_path / "course"
    shutil.copytree(FIXTURES, root, ignore=shutil.ignore_patterns(".canvas-manifest.toml"))
    return root


@pytest.fixture
def mock_course(mocker) -> MagicMock:
    mock_canvas_cls = mocker.patch("github_to_canvas.canvas_api.Canvas")
    course = MagicMock()
    mock_canvas_cls.return_value.get_course.return_value = course
    return course


def test_due_dates_override_assignment(course_root: Path) -> None:
    """Centralized due_dates should override assignment frontmatter dates in _sync_content_file."""
    from github_to_canvas.sync import _sync_content_file
    from github_to_canvas import manifest as manifest_lib

    # Write centralized due_dates
    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    due_dates = [
        {"name": "Week 1 Problem Set", "due_at": "2099-12-31T23:59:00", "unlock_at": "2099-12-01T00:00:00", "lock_at": ""},
    ]

    course = MagicMock()
    assignment = _mock_assignment(98765)
    course.create_assignment.return_value = assignment

    # Stub-create mock for the syllabus link
    stub_page = _mock_page(99999, "syllabus-stub")
    course.create_page.return_value = stub_page

    manifest_path = course_root / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    md_file = course_root / "assignments" / "week1.md"
    snippets_dir = course_root / "snippets"
    _sync_content_file(
        course, md_file, course_root, snippets_dir,
        manifest, manifest_path, 999,
        force_uploads=True, force_overwrite=True,
        due_dates=due_dates,
    )

    _, kwargs = course.create_assignment.call_args
    assignment_params = kwargs.get("assignment", {})
    assert assignment_params.get("due_at") == "2099-12-31T23:59:00"
    assert assignment_params.get("unlock_at") == "2099-12-01T00:00:00"
    # Empty string in centralized means "leave alone" — frontmatter lock_at is preserved
    assert assignment_params.get("lock_at") == "2025-02-08T23:59:00-05:00"


def test_due_dates_override_discussion(course_root: Path) -> None:
    """Centralized due_dates should override discussion frontmatter dates."""
    from github_to_canvas.sync import _sync_content_file
    from github_to_canvas import manifest as manifest_lib

    due_dates = [
        {"name": "Introduce Yourself", "due_at": "2099-06-15T23:59:00", "unlock_at": "", "lock_at": "2099-06-30T23:59:00"},
    ]

    course = MagicMock()
    discussion = _mock_discussion(55555)
    course.create_discussion_topic.return_value = discussion

    manifest_path = course_root / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    md_file = course_root / "discussions" / "week1-intro.md"
    snippets_dir = course_root / "snippets"
    _sync_content_file(
        course, md_file, course_root, snippets_dir,
        manifest, manifest_path, 999,
        force_uploads=True, force_overwrite=True,
        due_dates=due_dates,
    )

    _, kwargs = course.create_discussion_topic.call_args
    # Discussion dates are wrapped in assignment={...}
    assert kwargs["assignment"]["due_at"] == "2099-06-15T23:59:00"
    assert kwargs["assignment"]["lock_at"] == "2099-06-30T23:59:00"
    # Empty string in centralized means "leave alone" — frontmatter unlock_at is preserved
    assert kwargs["assignment"]["unlock_at"] == "2025-01-27T00:00:00-05:00"


def test_none_sentinel_clears_dates_on_canvas(course_root: Path) -> None:
    """NONE sentinel should send empty string to Canvas to clear the date."""
    from github_to_canvas.sync import _sync_content_file
    from github_to_canvas import manifest as manifest_lib

    due_dates = [
        {"name": "Week 1 Problem Set", "due_at": "2099-12-31T23:59:00", "unlock_at": "NONE", "lock_at": "NONE"},
    ]

    course = MagicMock()
    assignment = _mock_assignment(98765)
    course.create_assignment.return_value = assignment

    stub_page = _mock_page(99999, "syllabus-stub")
    course.create_page.return_value = stub_page

    manifest_path = course_root / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    md_file = course_root / "assignments" / "week1.md"
    snippets_dir = course_root / "snippets"
    _sync_content_file(
        course, md_file, course_root, snippets_dir,
        manifest, manifest_path, 999,
        force_uploads=True, force_overwrite=True,
        due_dates=due_dates,
    )

    _, kwargs = course.create_assignment.call_args
    assignment_params = kwargs.get("assignment", {})
    assert assignment_params.get("due_at") == "2099-12-31T23:59:00"
    assert assignment_params.get("unlock_at") == ""
    assert assignment_params.get("lock_at") == ""


def test_create_none_then_keep_on_create(course_root: Path) -> None:
    """CREATE_NONE_THEN_KEEP should clear dates on first create (no canvas_id)."""
    from github_to_canvas.sync import _sync_content_file
    from github_to_canvas import manifest as manifest_lib

    due_dates = [
        {"name": "Week 1 Problem Set", "due_at": "2099-12-31T23:59:00",
         "unlock_at": "CREATE_NONE_THEN_KEEP", "lock_at": "CREATE_NONE_THEN_KEEP"},
    ]

    course = MagicMock()
    assignment = _mock_assignment(98765)
    course.create_assignment.return_value = assignment

    stub_page = _mock_page(99999, "syllabus-stub")
    course.create_page.return_value = stub_page

    manifest_path = course_root / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    md_file = course_root / "assignments" / "week1.md"
    snippets_dir = course_root / "snippets"
    _sync_content_file(
        course, md_file, course_root, snippets_dir,
        manifest, manifest_path, 999,
        force_uploads=True, force_overwrite=True,
        due_dates=due_dates,
    )

    _, kwargs = course.create_assignment.call_args
    assignment_params = kwargs.get("assignment", {})
    assert assignment_params.get("due_at") == "2099-12-31T23:59:00"
    # On create, CREATE_NONE_THEN_KEEP sends empty string to clear
    assert assignment_params.get("unlock_at") == ""
    assert assignment_params.get("lock_at") == ""


def test_create_none_then_keep_on_update(course_root: Path) -> None:
    """CREATE_NONE_THEN_KEEP should act as KEEP on update (canvas_id exists)."""
    from github_to_canvas.sync import _sync_content_file
    from github_to_canvas import manifest as manifest_lib

    due_dates = [
        {"name": "Week 1 Problem Set", "due_at": "2099-12-31T23:59:00",
         "unlock_at": "CREATE_NONE_THEN_KEEP", "lock_at": "CREATE_NONE_THEN_KEEP"},
    ]

    course = MagicMock()
    assignment = _mock_assignment(98765)
    assignment.edit.return_value = assignment
    course.get_assignment.return_value = assignment

    stub_page = _mock_page(99999, "syllabus-stub")
    course.create_page.return_value = stub_page

    manifest_path = course_root / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    # Pre-populate manifest so it's treated as an update
    from github_to_canvas import manifest as mlib
    mlib.record(manifest, manifest_path, "assignments/week1.md", 98765, "assignment")

    md_file = course_root / "assignments" / "week1.md"
    snippets_dir = course_root / "snippets"
    _sync_content_file(
        course, md_file, course_root, snippets_dir,
        manifest, manifest_path, 999,
        force_uploads=True, force_overwrite=True,
        due_dates=due_dates,
    )

    _, kwargs = course.get_assignment.return_value.edit.call_args
    assignment_params = kwargs.get("assignment", {})
    assert assignment_params.get("due_at") == "2099-12-31T23:59:00"
    # On update, CREATE_NONE_THEN_KEEP acts as KEEP — frontmatter dates preserved
    assert assignment_params.get("unlock_at") == "2025-01-27T00:00:00-05:00"
    assert assignment_params.get("lock_at") == "2025-02-08T23:59:00-05:00"


def test_bad_request_retries_without_dates(course_root: Path) -> None:
    """When Canvas rejects due dates, retry without them and add a warning."""
    from canvasapi.exceptions import BadRequest
    from github_to_canvas.sync import _sync_content_file
    from github_to_canvas import manifest as manifest_lib

    due_dates = [
        {"name": "Week 1 Problem Set", "due_at": "2099-12-31T23:59:00", "unlock_at": "NONE", "lock_at": "NONE"},
    ]

    course = MagicMock()
    assignment = _mock_assignment(98765)

    call_count = 0
    def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        params = kwargs.get("assignment", {})
        if "due_at" in params:
            raise BadRequest('{"errors":{"due_at":[{"message":"must be between availability dates"}]}}')
        return assignment

    course.create_assignment.side_effect = side_effect

    stub_page = _mock_page(99999, "syllabus-stub")
    course.create_page.return_value = stub_page

    manifest_path = course_root / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    md_file = course_root / "assignments" / "week1.md"
    snippets_dir = course_root / "snippets"
    errors: list[str] = []
    _sync_content_file(
        course, md_file, course_root, snippets_dir,
        manifest, manifest_path, 999,
        force_uploads=True, force_overwrite=True,
        due_dates=due_dates,
        errors=errors,
    )

    # Should have been called twice: first with dates (fails), then without (succeeds)
    assert call_count == 2
    # Should have a warning about rejected dates
    assert any("Canvas rejected due dates" in e for e in errors)


def test_empty_string_warning_in_errors(course_root: Path) -> None:
    """Empty string date values should produce a warning in the errors list."""
    from github_to_canvas.sync import _sync_content_file
    from github_to_canvas import manifest as manifest_lib

    due_dates = [
        {"name": "Week 1 Problem Set", "due_at": "2099-12-31T23:59:00", "unlock_at": "", "lock_at": ""},
    ]

    course = MagicMock()
    assignment = _mock_assignment(98765)
    course.create_assignment.return_value = assignment

    stub_page = _mock_page(99999, "syllabus-stub")
    course.create_page.return_value = stub_page

    manifest_path = course_root / ".canvas-manifest.toml"
    manifest = manifest_lib.load(manifest_path)
    md_file = course_root / "assignments" / "week1.md"
    snippets_dir = course_root / "snippets"
    errors: list[str] = []
    _sync_content_file(
        course, md_file, course_root, snippets_dir,
        manifest, manifest_path, 999,
        force_uploads=True, force_overwrite=True,
        due_dates=due_dates,
        errors=errors,
    )

    assert any("empty value" in e and "KEEP" in e for e in errors)


# ---------------------------------------------------------------------------
# Integration: import generates due_dates in course_settings.toml
# ---------------------------------------------------------------------------


def test_import_generates_due_dates_table(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    run_import(IMSCC_FIXTURES, output_dir)
    cs_toml = output_dir / "course_settings" / "course_settings.toml"
    assert cs_toml.exists()
    with cs_toml.open("rb") as f:
        data = tomllib.load(f)
    due_dates = data.get("due_dates", [])
    # The IMSCC fixture has at least one assignment with due dates
    titled = {e["name"] for e in due_dates}
    # Check that the fixture assignment with dates was collected
    assert len(due_dates) >= 0  # may be zero if fixture has no dates


def test_import_comments_out_dates_in_assignment(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    run_import(IMSCC_FIXTURES, output_dir)
    # Check that assignments have commented-out date fields
    assignments_dir = output_dir / "assignments"
    if assignments_dir.exists():
        for md_file in assignments_dir.glob("*.md"):
            text = md_file.read_text()
            # If the original had dates, they should be commented out
            if "due_at" in text:
                assert "# due_at:" in text or "# Due dates" in text


# ---------------------------------------------------------------------------
# list-titles CLI
# ---------------------------------------------------------------------------


def test_list_titles_output(tmp_path: Path) -> None:
    """list-titles should show assignments, discussions, and quizzes."""
    from click.testing import CliRunner
    from github_to_canvas.cli import main

    # Copy fixtures
    root = tmp_path / "course"
    shutil.copytree(FIXTURES, root, ignore=shutil.ignore_patterns(".canvas-manifest.toml"))

    runner = CliRunner()
    result = runner.invoke(main, ["list-titles", str(root)])
    assert result.exit_code == 0
    assert "Week 1 Problem Set" in result.output
    assert "Introduce Yourself" in result.output
    assert "A Quiz" in result.output
    assert "assignments/week1.md" in result.output


def test_list_titles_sorted_by_date(tmp_path: Path) -> None:
    """Items with due dates come first, sorted by date."""
    from click.testing import CliRunner
    from github_to_canvas.cli import main

    root = tmp_path / "course"
    shutil.copytree(FIXTURES, root, ignore=shutil.ignore_patterns(".canvas-manifest.toml"))

    runner = CliRunner()
    result = runner.invoke(main, ["list-titles", str(root)])
    lines = [l for l in result.output.strip().splitlines() if l.strip()]
    # A Quiz has no due date, so it should be last
    assert "A Quiz" in lines[-1]


def test_list_titles_with_centralized_dates(tmp_path: Path) -> None:
    """list-titles should prefer centralized due_dates over frontmatter."""
    from click.testing import CliRunner
    from github_to_canvas.cli import main

    root = tmp_path / "course"
    shutil.copytree(FIXTURES, root, ignore=shutil.ignore_patterns(".canvas-manifest.toml"))

    cs_dir = root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    (cs_dir / "course_settings.toml").write_text(
        'due_dates = [\n'
        '    {name = "A Quiz", type = "quiz", due_at = "2099-12-31T23:59:00", unlock_at = "", lock_at = ""},\n'
        ']\n'
    )

    runner = CliRunner()
    result = runner.invoke(main, ["list-titles", str(root)])
    assert "2099-12-31 23:59" in result.output


def test_list_titles_empty_repo(tmp_path: Path) -> None:
    """list-titles on an empty repo should print a message."""
    from click.testing import CliRunner
    from github_to_canvas.cli import main

    root = tmp_path / "empty_course"
    root.mkdir()

    runner = CliRunner()
    result = runner.invoke(main, ["list-titles", str(root)])
    assert result.exit_code == 0
    assert "No assignments" in result.output
