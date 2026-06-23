"""Integration tests: full sync pipeline with mocked canvasapi."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from canvasapi.exceptions import ResourceDoesNotExist

from github_to_canvas.config import Config
from github_to_canvas.sync import (
    parse_frontmatter,
    parse_module_body,
    run_prune,
    run_sync,
    run_targeted_sync,
)

FIXTURES = Path(__file__).parent / "fixtures"
COURSE_ID = 999
_FUTURE_SYNCED = "2999-12-31T00:00:00+00:00"


def _make_old(path: Path) -> None:
    """Set a file's mtime to epoch so it appears older than any manifest last_synced."""
    os.utime(path, (0.0, 0.0))


# ---------------------------------------------------------------------------
# Config + fixtures
# ---------------------------------------------------------------------------


def _config() -> Config:
    return Config(base_url="https://school.instructure.com", course_id=COURSE_ID, api_token="tok")


def _mock_page(page_id: int, url: str) -> MagicMock:
    p = MagicMock()
    p.page_id = page_id
    p.url = url
    p.html_url = f"https://school.instructure.com/courses/1/pages/{url}"
    p.edit.return_value = p
    return p


def _mock_assignment(canvas_id: int) -> MagicMock:
    a = MagicMock()
    a.id = canvas_id
    a.html_url = f"https://school.instructure.com/courses/1/assignments/{canvas_id}"
    a.edit.return_value = a
    return a


def _mock_discussion(canvas_id: int) -> MagicMock:
    d = MagicMock()
    d.id = canvas_id
    d.html_url = f"https://school.instructure.com/courses/1/discussion_topics/{canvas_id}"
    d.update.return_value = d
    return d


def _mock_module(canvas_id: int, existing_items: list | None = None) -> MagicMock:
    m = MagicMock()
    m.id = canvas_id
    m.edit.return_value = m
    m.get_module_items.return_value = existing_items or []
    return m


def _mock_item(item_id: int) -> MagicMock:
    i = MagicMock()
    i.id = item_id
    return i


@pytest.fixture
def course_root(tmp_path: Path) -> Path:
    """Isolated copy of fixtures so tests never write manifest to the fixtures dir."""
    root = tmp_path / "course"
    shutil.copytree(FIXTURES, root, ignore=shutil.ignore_patterns(".canvas-manifest.toml"))
    return root


@pytest.fixture
def mock_course(mocker) -> MagicMock:
    """Patch canvasapi.Canvas; return the mock course object."""
    mock_canvas_cls = mocker.patch("github_to_canvas.canvas_api.Canvas")
    course = MagicMock()
    mock_canvas_cls.return_value.get_course.return_value = course
    return course


def _setup_first_sync_mocks(mock_course) -> MagicMock:
    """Configure mock_course for a first-sync scenario (no pre-existing manifest).

    Processing order: assignments/ → discussions/ → pages/ → modules/
    assignments/week1.md links to pages/syllabus.md → stub create, then real edit.
    """
    stub_page = _mock_page(99999, "syllabus-stub")
    mock_course.create_page.return_value = stub_page
    # When real pages/syllabus.md is processed, stub is already in manifest so we
    # call get_page(canvas_url).edit() rather than create_page().
    mock_course.get_page.return_value = stub_page

    mock_course.create_assignment.return_value = _mock_assignment(98765)
    mock_course.create_discussion_topic.return_value = _mock_discussion(55555)
    mock_course.upload.return_value = (
        True,
        {"id": 77777, "url": "https://school.instructure.com/files/77777/download"},
    )
    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.side_effect = [_mock_item(i) for i in [201, 202, 203, 204, 205]]
    return stub_page


# ---------------------------------------------------------------------------
# parse_frontmatter unit tests
# ---------------------------------------------------------------------------


def test_parse_frontmatter_basic() -> None:
    text = "---\ntitle: Hello\npublished: true\n---\n\nBody text.\n"
    fm, body = parse_frontmatter(text)
    assert fm == {"title": "Hello", "published": True}
    assert body.strip() == "Body text."


def test_parse_frontmatter_no_frontmatter() -> None:
    text = "Just a body.\n"
    fm, body = parse_frontmatter(text)
    assert fm == {}
    assert body == text


def test_parse_frontmatter_empty_body() -> None:
    fm, body = parse_frontmatter("---\ntitle: T\n---\n")
    assert fm == {"title": "T"}
    assert body == ""


# ---------------------------------------------------------------------------
# parse_module_body unit tests
# ---------------------------------------------------------------------------


def test_parse_module_body_items_and_subheaders(tmp_path: Path) -> None:
    course_root = tmp_path / "course"
    course_root.mkdir()
    (course_root / "modules").mkdir()
    module_file = course_root / "modules" / "week-1.md"
    body = (
        "## Readings\n"
        "- [Syllabus](../pages/syllabus.md)\n"
        "## Work\n"
        "- [Assignment](../assignments/week1.md)\n"
    )
    items = parse_module_body(body, module_file, course_root)
    assert items[0] == {"type": "SubHeader", "title": "Readings", "indent": 0}
    assert items[1]["type"] == "content"
    assert items[1]["local_path"] == "pages/syllabus.md"
    assert items[1]["indent"] == 0
    assert items[2] == {"type": "SubHeader", "title": "Work", "indent": 0}
    assert items[3]["local_path"] == "assignments/week1.md"
    assert items[3]["indent"] == 0


def test_parse_module_body_empty() -> None:
    items = parse_module_body("", Path("/course/modules/m.md"), Path("/course"))
    assert items == []


# ---------------------------------------------------------------------------
# Scenario 1: First sync — all items created, manifest updated after each
# ---------------------------------------------------------------------------


def test_first_sync_creates_all_content(mock_course, course_root, mocker) -> None:
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)

    run_sync(_config(), course_root)

    # One stub page create + no extra create_page (real page goes via edit)
    mock_course.create_page.assert_called_once()
    assert mock_course.get_page.call_count == 2  # Canvas timestamp check + actual update
    mock_course.create_assignment.assert_called_once()
    mock_course.create_discussion_topic.assert_called_once()
    mock_course.upload.assert_called_once()
    # Assets must overwrite in-place so existing links keep the same file ID/URL.
    assert mock_course.upload.call_args.kwargs["on_duplicate"] == "overwrite"
    mock_course.create_module.assert_called_once()


def test_first_sync_stub_created_before_real_page(mock_course, course_root, mocker) -> None:
    """assignments/week1.md references pages/syllabus.md → stub created first."""
    mocker.patch("github_to_canvas.manifest.flush")
    stub_page = _setup_first_sync_mocks(mock_course)

    run_sync(_config(), course_root)

    # Stub: published=False, empty body
    stub_call = mock_course.create_page.call_args
    assert stub_call[1]["wiki_page"]["published"] is False
    assert stub_call[1]["wiki_page"]["body"] == ""

    # Real page: edited from stub → published=True, non-empty body
    edit_call = stub_page.edit.call_args
    assert edit_call[1]["wiki_page"]["published"] is True
    assert edit_call[1]["wiki_page"]["body"] != ""


def test_first_sync_assignment_frontmatter_passed_to_canvas(mock_course, course_root, mocker) -> None:
    """points_possible, due_at, submission_types reach canvasapi."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)

    run_sync(_config(), course_root)

    call_kwargs = mock_course.create_assignment.call_args[1]["assignment"]
    assert call_kwargs["name"] == "Week 1 Problem Set"
    assert call_kwargs["points_possible"] == 50
    assert "due_at" in call_kwargs


# ---------------------------------------------------------------------------
# Scenario 2: Second sync — all items updated, no creates
# ---------------------------------------------------------------------------


def test_second_sync_updates_not_creates(mock_course, course_root, mocker) -> None:
    # Asset mtime set to epoch so it appears unchanged since last_synced
    _make_old(course_root / "assets" / "images" / "fig.png")
    preloaded = {
        "assets/images/fig.png": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "https://school.instructure.com/files/77777/download",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "assignments/week1.md": {
            "canvas_id": 98765, "canvas_type": "assignment",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "discussions/week1-intro.md": {
            "canvas_id": 55555, "canvas_type": "discussion",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "pages/syllabus.md": {
            "canvas_id": 11111, "canvas_type": "page", "canvas_url": "syllabus",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "modules/week-1.md": {
            "canvas_id": 66666, "canvas_type": "module",
            "canvas_item_ids": {},
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    real_page = _mock_page(11111, "syllabus")
    mock_course.get_page.return_value = real_page
    mock_course.get_assignment.return_value = _mock_assignment(98765)
    mock_course.get_discussion_topic.return_value = _mock_discussion(55555)
    module = _mock_module(66666)
    mock_course.get_module.return_value = module
    module.create_module_item.side_effect = [_mock_item(i) for i in [201, 202, 203, 204, 205]]

    run_sync(_config(), course_root)

    # No creates for content
    mock_course.create_page.assert_not_called()
    mock_course.create_assignment.assert_not_called()
    mock_course.create_discussion_topic.assert_not_called()
    # Asset already in manifest — no re-upload
    mock_course.upload.assert_not_called()

    # Updates used instead (each item fetched twice: Canvas timestamp check + actual update)
    assert mock_course.get_page.call_count == 2
    mock_course.get_page.assert_any_call("syllabus")
    real_page.edit.assert_called_once()
    assert mock_course.get_assignment.call_count == 2
    mock_course.get_assignment.assert_any_call(98765)
    assert mock_course.get_discussion_topic.call_count == 2
    mock_course.get_discussion_topic.assert_any_call(55555)
    assert mock_course.get_module.call_count == 2
    mock_course.get_module.assert_any_call(66666)


# ---------------------------------------------------------------------------
# Scenario 3: Interrupted sync — partial manifest resumes correctly
# ---------------------------------------------------------------------------


def test_interrupted_sync_skips_completed_asset(mock_course, course_root, mocker) -> None:
    """Asset in manifest is not re-uploaded; missing content still created."""
    # Asset mtime set to epoch so needs_sync returns False (file unchanged since last_synced)
    _make_old(course_root / "assets" / "images" / "fig.png")
    partial = {
        "assets/images/fig.png": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "https://school.instructure.com/files/77777/download",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=partial)
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)

    run_sync(_config(), course_root)

    mock_course.upload.assert_not_called()           # asset skipped
    mock_course.create_assignment.assert_called_once()  # content still created


# ---------------------------------------------------------------------------
# Scenario 4: Missing local file — tag removed, sync continues
# ---------------------------------------------------------------------------


def test_h1_heading_blocks_upload(
    mock_course, tmp_path: Path, mocker, capsys: pytest.CaptureFixture
) -> None:
    mocker.patch("github_to_canvas.manifest.flush")
    course_root = tmp_path / "course"
    (course_root / "pages").mkdir(parents=True)
    page = course_root / "pages" / "test.md"
    page.write_text("---\ntitle: Test\npublished: true\n---\n\n# Big Heading\n\nBody.\n")
    mock_course.create_page.return_value = _mock_page(1, "test")

    had_errors = run_sync(_config(), course_root)

    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "h1" in out.lower()
    assert had_errors is True
    mock_course.create_page.assert_not_called()


def test_no_h1_heading_no_warning(
    mock_course, tmp_path: Path, mocker, capsys: pytest.CaptureFixture
) -> None:
    mocker.patch("github_to_canvas.manifest.flush")
    course_root = tmp_path / "course"
    (course_root / "pages").mkdir(parents=True)
    page = course_root / "pages" / "test.md"
    page.write_text("---\ntitle: Test\npublished: true\n---\n\n## Sub Heading\n\nBody.\n")
    mock_course.create_page.return_value = _mock_page(1, "test")

    had_errors = run_sync(_config(), course_root)

    out = capsys.readouterr().out
    assert "h1" not in out.lower() or "heading" not in out.lower()
    assert had_errors is False


def test_missing_local_file_tag_removed_sync_continues(
    mock_course, tmp_path: Path, mocker, capsys: pytest.CaptureFixture
) -> None:
    mocker.patch("github_to_canvas.manifest.flush")
    course_root = tmp_path / "course"
    (course_root / "pages").mkdir(parents=True)
    page = course_root / "pages" / "test.md"
    page.write_text(
        "---\ntitle: Test\npublished: true\n---\n\n"
        "[Ghost](../assignments/ghost.md)\n"
    )
    mock_page = _mock_page(1, "test")
    mock_course.create_page.return_value = mock_page

    run_sync(_config(), course_root)

    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "ghost.md" in out
    # Page is NOT uploaded when it has broken links — retry on the next run
    mock_course.create_page.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 5: Module sync — correct item order with SubHeaders
# ---------------------------------------------------------------------------


def test_module_sync_item_order(mock_course, course_root, mocker) -> None:
    """Module items are created in Markdown order; SubHeaders interleaved correctly."""
    # Asset mtime set to epoch so it is skipped (already synced, unchanged)
    _make_old(course_root / "assets" / "images" / "fig.png")
    preloaded = {
        "assets/images/fig.png": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "https://school.instructure.com/files/77777/download",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "assignments/week1.md": {
            "canvas_id": 98765, "canvas_type": "assignment",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "discussions/week1-intro.md": {
            "canvas_id": 55555, "canvas_type": "discussion",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "pages/syllabus.md": {
            "canvas_id": 11111, "canvas_type": "page", "canvas_url": "syllabus",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    real_page = _mock_page(11111, "syllabus")
    mock_course.get_page.return_value = real_page
    mock_course.get_assignment.return_value = _mock_assignment(98765)
    mock_course.get_discussion_topic.return_value = _mock_discussion(55555)

    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.side_effect = [_mock_item(i) for i in [201, 202, 203, 204, 205]]

    run_sync(_config(), course_root)

    item_calls = module.create_module_item.call_args_list
    assert len(item_calls) == 5

    types = [c[1]["module_item"]["type"] for c in item_calls]
    assert types == ["SubHeader", "Page", "SubHeader", "Assignment", "Discussion"]

    assert item_calls[0][1]["module_item"]["title"] == "Readings"
    assert item_calls[0][1]["module_item"]["indent"] == 0
    assert item_calls[1][1]["module_item"]["page_url"] == "syllabus"
    assert item_calls[1][1]["module_item"]["indent"] == 0
    assert item_calls[2][1]["module_item"]["title"] == "Work"
    assert item_calls[2][1]["module_item"]["indent"] == 0
    assert item_calls[3][1]["module_item"]["content_id"] == 98765
    assert item_calls[3][1]["module_item"]["indent"] == 1
    assert item_calls[4][1]["module_item"]["content_id"] == 55555
    assert item_calls[4][1]["module_item"]["indent"] == 1


# ---------------------------------------------------------------------------
# Scenario 5b: Module re-synced when referenced content is updated
# ---------------------------------------------------------------------------


def test_module_resynced_when_referenced_page_updated(
    mock_course, course_root, mocker, capsys
) -> None:
    """When a page referenced by a module is synced, the module is also re-synced."""
    _make_old(course_root / "assets" / "images" / "fig.png")
    _make_old(course_root / "assignments" / "week1.md")
    _make_old(course_root / "discussions" / "week1-intro.md")
    # pages/syllabus.md is NOT made old — it has a new mtime (modified by user)

    preloaded = {
        "assets/images/fig.png": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "https://school.instructure.com/files/77777/download",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "assignments/week1.md": {
            "canvas_id": 98765, "canvas_type": "assignment",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "discussions/week1-intro.md": {
            "canvas_id": 55555, "canvas_type": "discussion",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "pages/syllabus.md": {
            "canvas_id": 11111, "canvas_type": "page", "canvas_url": "syllabus",
            "last_synced": "2025-01-01T00:00:00+00:00",
            # No future timestamp here — page appears modified
        },
        # Module has a future last_synced — would normally be skipped
        "modules/week-1.md": {
            "canvas_id": 66666, "canvas_type": "module",
            "canvas_item_ids": {"pages/syllabus.md": 201},
            "last_synced": _FUTURE_SYNCED,
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    real_page = _mock_page(11111, "syllabus")
    mock_course.get_page.return_value = real_page

    module = _mock_module(66666)
    mock_course.get_module.return_value = module
    module.create_module_item.side_effect = [_mock_item(i) for i in range(201, 210)]

    run_sync(_config(), course_root)

    # Page was re-uploaded
    mock_course.get_page.assert_called()
    real_page.edit.assert_called()

    # Module was also re-synced despite its own mtime being unchanged
    mock_course.get_module.assert_called_with(66666)
    module.edit.assert_called()
    out = capsys.readouterr().out
    assert "Syncing module: modules/week-1.md" in out


def test_module_not_resynced_when_referenced_content_unchanged(
    mock_course, course_root, mocker, capsys
) -> None:
    """A module whose referenced content was not updated is NOT re-synced."""
    _make_old(course_root / "assets" / "images" / "fig.png")
    _make_old(course_root / "assignments" / "week1.md")
    _make_old(course_root / "discussions" / "week1-intro.md")
    _make_old(course_root / "pages" / "syllabus.md")
    _make_old(course_root / "modules" / "week-1.md")

    preloaded = {
        "assets/images/fig.png": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "https://school.instructure.com/files/77777/download",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "assignments/week1.md": {
            "canvas_id": 98765, "canvas_type": "assignment",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "discussions/week1-intro.md": {
            "canvas_id": 55555, "canvas_type": "discussion",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "pages/syllabus.md": {
            "canvas_id": 11111, "canvas_type": "page", "canvas_url": "syllabus",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "modules/week-1.md": {
            "canvas_id": 66666, "canvas_type": "module",
            "canvas_item_ids": {},
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    run_sync(_config(), course_root, verbose=True)

    mock_course.create_module.assert_not_called()
    mock_course.get_module.assert_not_called()
    out = capsys.readouterr().out
    assert "Skipping (up-to-date): modules/week-1.md" in out


# ---------------------------------------------------------------------------
# Scenario 6: Timestamp skip — up-to-date content file is skipped
# ---------------------------------------------------------------------------


def test_up_to_date_content_file_is_skipped(mock_course, course_root, mocker, capsys) -> None:
    """A content file whose mtime predates last_synced is not re-uploaded."""
    _make_old(course_root / "assets" / "images" / "fig.png")
    _make_old(course_root / "assignments" / "week1.md")
    preloaded = {
        "assets/images/fig.png": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "https://school.instructure.com/files/77777/download",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "assignments/week1.md": {
            "canvas_id": 98765, "canvas_type": "assignment",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    mock_course.create_discussion_topic.return_value = _mock_discussion(55555)
    mock_course.create_page.return_value = _mock_page(11111, "syllabus")
    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.side_effect = [_mock_item(i) for i in range(201, 210)]

    run_sync(_config(), course_root, verbose=True)

    mock_course.create_assignment.assert_not_called()
    mock_course.get_assignment.assert_not_called()
    out = capsys.readouterr().out
    assert "Skipping (up-to-date): assignments/week1.md" in out


def test_force_uploads_re_uploads_up_to_date_file(mock_course, course_root, mocker) -> None:
    """--force-uploads causes all files to be re-uploaded regardless of mtime."""
    _make_old(course_root / "assets" / "images" / "fig.png")
    preloaded = {
        "assets/images/fig.png": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "https://school.instructure.com/files/77777/download",
            "last_synced": _FUTURE_SYNCED,
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")
    mock_course.upload.return_value = (
        True,
        {"id": 77777, "url": "https://school.instructure.com/files/77777/download"},
    )
    mock_course.create_page.return_value = _mock_page(11111, "syllabus")
    mock_course.create_assignment.return_value = _mock_assignment(98765)
    mock_course.create_discussion_topic.return_value = _mock_discussion(55555)
    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.side_effect = [_mock_item(i) for i in range(201, 210)]

    run_sync(_config(), course_root, force_uploads=True)

    mock_course.upload.assert_called_once()  # asset re-uploaded despite old mtime


# ---------------------------------------------------------------------------
# Scenario 7b: Canvas overwrite protection
# ---------------------------------------------------------------------------


def test_canvas_newer_skips_upload_and_prints_summary(
    mock_course, course_root, mocker, capsys
) -> None:
    """When Canvas updated_at > local mtime the upload is skipped and the file appears in the
    end-of-run summary."""
    preloaded = {
        "pages/syllabus.md": {
            "canvas_id": 11111, "canvas_type": "page", "canvas_url": "syllabus",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    page_mock = _mock_page(11111, "syllabus")
    page_mock.updated_at = "2999-12-31T00:00:00+00:00"  # Canvas far in future → newer
    mock_course.get_page.return_value = page_mock

    run_targeted_sync(_config(), course_root, [], [str(course_root / "pages" / "syllabus.md")])

    # get_page called once for Canvas timestamp check; upload skipped so no edit
    mock_course.get_page.assert_called_once_with("syllabus")
    page_mock.edit.assert_not_called()

    out = capsys.readouterr().out
    assert "pages/syllabus.md" in out
    assert "Canvas is newer" in out  # confirmed in the summary block


def test_canvas_older_upload_proceeds(mock_course, course_root, mocker) -> None:
    """When Canvas updated_at < local mtime the upload is not blocked."""
    preloaded = {
        "pages/syllabus.md": {
            "canvas_id": 11111, "canvas_type": "page", "canvas_url": "syllabus",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    page_mock = _mock_page(11111, "syllabus")
    page_mock.updated_at = "2020-01-01T00:00:00+00:00"  # Canvas is old → local file is newer
    mock_course.get_page.return_value = page_mock

    run_targeted_sync(_config(), course_root, [], [str(course_root / "pages" / "syllabus.md")])

    assert mock_course.get_page.call_count == 2  # timestamp check + actual update
    page_mock.edit.assert_called_once()


def test_force_overwrite_skips_canvas_check_and_uploads(mock_course, course_root, mocker) -> None:
    """--force-overwrite skips the Canvas timestamp check and uploads regardless."""
    preloaded = {
        "pages/syllabus.md": {
            "canvas_id": 11111, "canvas_type": "page", "canvas_url": "syllabus",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    page_mock = _mock_page(11111, "syllabus")
    page_mock.updated_at = "2999-12-31T00:00:00+00:00"  # Canvas "newer" but should be ignored
    mock_course.get_page.return_value = page_mock

    run_targeted_sync(
        _config(), course_root, [], [str(course_root / "pages" / "syllabus.md")],
        force_overwrite=True,
    )

    # Only the actual update call; no extra Canvas timestamp check
    mock_course.get_page.assert_called_once_with("syllabus")
    page_mock.edit.assert_called_once()  # upload proceeded


def test_canvas_newer_skips_asset_upload(mock_course, course_root, mocker) -> None:
    """Canvas overwrite protection applies to assets as well as content files."""
    preloaded = {
        "assets/images/fig.png": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "https://school.instructure.com/files/77777/download",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    file_mock = MagicMock()
    file_mock.updated_at = "2999-12-31T00:00:00+00:00"  # Canvas far in future → newer
    mock_course.get_file.return_value = file_mock

    run_targeted_sync(
        _config(), course_root, [], [str(course_root / "assets" / "images" / "fig.png")]
    )

    mock_course.get_file.assert_called_once_with(77777)  # timestamp check call
    mock_course.upload.assert_not_called()  # asset upload skipped


# ---------------------------------------------------------------------------
# Scenario 7: run_targeted_sync — single target
# ---------------------------------------------------------------------------


def test_single_target_syncs_only_specified_file(mock_course, course_root, mocker) -> None:
    """--single-target syncs the given file and nothing else."""
    mocker.patch("github_to_canvas.manifest.flush")
    mock_course.create_assignment.return_value = _mock_assignment(98765)
    mock_course.create_page.return_value = _mock_page(99999, "syllabus-stub")

    run_targeted_sync(
        _config(), course_root,
        recursive_targets=[],
        single_targets=[str(course_root / "assignments" / "week1.md")],
    )

    mock_course.create_assignment.assert_called_once()
    mock_course.create_discussion_topic.assert_not_called()
    mock_course.upload.assert_not_called()
    mock_course.create_module.assert_not_called()


def test_single_target_respects_timestamp(mock_course, course_root, mocker, capsys) -> None:
    """--single-target skips a file that is up-to-date per manifest timestamp."""
    _make_old(course_root / "assignments" / "week1.md")
    preloaded = {
        "assignments/week1.md": {
            "canvas_id": 98765, "canvas_type": "assignment",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    run_targeted_sync(
        _config(), course_root,
        recursive_targets=[],
        single_targets=[str(course_root / "assignments" / "week1.md")],
        verbose=True,
    )

    mock_course.create_assignment.assert_not_called()
    mock_course.get_assignment.assert_not_called()
    out = capsys.readouterr().out
    assert "Skipping" in out


# ---------------------------------------------------------------------------
# Scenario 8: run_targeted_sync — recursive BFS
# ---------------------------------------------------------------------------


def test_recursive_target_traverses_refs(mock_course, course_root, mocker) -> None:
    """--target-recursively syncs a module and all content items it lists.

    BFS defers the module until after all its referenced content is processed,
    so add_module_item can look up canvas IDs from the manifest.
    """
    mocker.patch("github_to_canvas.manifest.flush")

    real_page = _mock_page(11111, "syllabus")
    mock_course.create_page.return_value = real_page
    mock_course.get_page.return_value = real_page
    # create_assignment used for stubs (rewrite_links) and potentially real upload
    mock_course.create_assignment.return_value = _mock_assignment(98765)
    mock_course.get_assignment.return_value = _mock_assignment(98765)
    mock_course.create_discussion_topic.return_value = _mock_discussion(55555)
    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.side_effect = [_mock_item(i) for i in range(201, 210)]

    run_targeted_sync(
        _config(), course_root,
        recursive_targets=[str(course_root / "modules" / "week-1.md")],
        single_targets=[],
    )

    # Module is synced (deferred until after content)
    mock_course.create_module.assert_called_once()
    # Discussion referenced by module is synced
    mock_course.create_discussion_topic.assert_called_once()
    # Assets not referenced from any module-linked content are not uploaded
    mock_course.upload.assert_not_called()


def test_recursive_target_no_duplicate_processing(mock_course, course_root, mocker) -> None:
    """-t uploads a file and updates its manifest timestamp; -s then skips it via needs_sync."""
    # Make the file old so -t definitely uploads it (not in manifest, needs_sync=True),
    # setting last_synced=now. When -s runs, file_mtime=0 < last_synced=now → skipped.
    _make_old(course_root / "pages" / "syllabus.md")
    mocker.patch("github_to_canvas.manifest.flush")
    mock_course.create_page.return_value = _mock_page(11111, "syllabus")
    mock_course.create_assignment.return_value = _mock_assignment(98765)

    page_path = str(course_root / "pages" / "syllabus.md")
    run_targeted_sync(
        _config(), course_root,
        recursive_targets=[page_path],
        single_targets=[page_path],
    )

    # -t uploads via create_page; -s sees updated timestamp and skips (no second upload)
    mock_course.create_page.assert_called_once()


# ---------------------------------------------------------------------------
# Scenario 9: Quiz sync
# ---------------------------------------------------------------------------


def _mock_quiz(canvas_id: int) -> MagicMock:
    q = MagicMock()
    q.id = canvas_id
    q.html_url = f"https://school.instructure.com/courses/1/quizzes/{canvas_id}"
    q.edit.return_value = q
    q.get_questions.return_value = []
    return q


def _mock_quiz_question(q_id: int) -> MagicMock:
    qq = MagicMock()
    qq.id = q_id
    return qq


def _quiz_course_root(tmp_path: Path) -> Path:
    """Minimal course root with one quiz and no other content."""
    root = tmp_path / "course"
    quiz_dir = root / "quizzes" / "a-quiz"
    q_dir = quiz_dir / "questions"
    q_dir.mkdir(parents=True)
    (quiz_dir / "a-quiz.md").write_text(
        "---\ntitle: A Quiz\nquiz_type: assignment\npublished: true\n---\n\n"
        "1. [What is 2+2?](questions/what-is-2-plus-2.md)\n"
        "2. [Explain something](questions/explain-something.md)\n"
    )
    (q_dir / "what-is-2-plus-2.md").write_text(
        "---\ntitle: What is 2+2?\nquestion_type: multiple_choice_question\n"
        "points_possible: 1\ncorrect: 2\n---\n\n"
        "What is 2+2?\n\n## Answers\n\n1. 3\n2. 4\n3. 5\n"
    )
    (q_dir / "explain-something.md").write_text(
        "---\ntitle: Explain something\nquestion_type: essay_question\n"
        "points_possible: 5\n---\n\nExplain the concept.\n"
    )
    return root


def test_quiz_sync_creates_quiz_on_first_sync(mock_course, mocker, tmp_path) -> None:
    mocker.patch("github_to_canvas.manifest.flush")
    root = _quiz_course_root(tmp_path)
    quiz = _mock_quiz(12345)
    mock_course.create_quiz.return_value = quiz
    mock_course.get_quiz.return_value = quiz
    quiz.create_question.side_effect = [_mock_quiz_question(i) for i in [101, 102]]

    run_sync(_config(), root)

    mock_course.create_quiz.assert_called_once()
    call_params = mock_course.create_quiz.call_args[1]["quiz"]
    assert call_params["title"] == "A Quiz"
    assert call_params["published"] is True


def test_quiz_sync_updates_quiz_on_second_sync(mock_course, mocker, tmp_path) -> None:
    root = _quiz_course_root(tmp_path)
    preloaded = {
        "quizzes/a-quiz/a-quiz.md": {
            "canvas_id": 12345, "canvas_type": "quiz",
            "last_synced": "2025-01-01T00:00:00+00:00",
            "canvas_question_ids": {},
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")
    quiz = _mock_quiz(12345)
    mock_course.get_quiz.return_value = quiz
    quiz.create_question.side_effect = [_mock_quiz_question(i) for i in [101, 102]]

    run_sync(_config(), root)

    mock_course.create_quiz.assert_not_called()
    mock_course.get_quiz.assert_called_with(12345)
    quiz.edit.assert_called_once()


def test_quiz_questions_created_in_order(mock_course, mocker, tmp_path) -> None:
    mocker.patch("github_to_canvas.manifest.flush")
    root = _quiz_course_root(tmp_path)
    quiz = _mock_quiz(12345)
    mock_course.create_quiz.return_value = quiz
    mock_course.get_quiz.return_value = quiz
    quiz.create_question.side_effect = [_mock_quiz_question(i) for i in [101, 102]]

    run_sync(_config(), root)

    assert quiz.create_question.call_count == 2
    first_q = quiz.create_question.call_args_list[0][1]["question"]
    second_q = quiz.create_question.call_args_list[1][1]["question"]
    assert first_q["question_type"] == "multiple_choice_question"
    assert len(first_q["answers"]) == 3
    assert second_q["question_type"] == "essay_question"


def test_quiz_skipped_if_up_to_date(mock_course, mocker, tmp_path, capsys) -> None:
    root = _quiz_course_root(tmp_path)
    # Make all quiz files old
    quiz_dir = root / "quizzes" / "a-quiz"
    for f in [quiz_dir / "a-quiz.md",
              quiz_dir / "questions" / "what-is-2-plus-2.md",
              quiz_dir / "questions" / "explain-something.md"]:
        _make_old(f)
    preloaded = {
        "quizzes/a-quiz/a-quiz.md": {
            "canvas_id": 12345, "canvas_type": "quiz",
            "last_synced": "2025-01-01T00:00:00+00:00",
            "canvas_question_ids": {},
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    run_sync(_config(), root, verbose=True)

    mock_course.create_quiz.assert_not_called()
    mock_course.get_quiz.assert_not_called()
    out = capsys.readouterr().out
    assert "Skipping (up-to-date): quizzes/a-quiz/a-quiz.md" in out


def test_quiz_resynced_when_question_file_updated(mock_course, mocker, tmp_path) -> None:
    root = _quiz_course_root(tmp_path)
    quiz_dir = root / "quizzes" / "a-quiz"
    # Make quiz .md old but leave question files at current mtime
    _make_old(quiz_dir / "a-quiz.md")
    preloaded = {
        "quizzes/a-quiz/a-quiz.md": {
            "canvas_id": 12345, "canvas_type": "quiz",
            "last_synced": "2025-01-01T00:00:00+00:00",
            "canvas_question_ids": {},
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")
    quiz = _mock_quiz(12345)
    mock_course.get_quiz.return_value = quiz
    quiz.create_question.side_effect = [_mock_quiz_question(i) for i in [101, 102]]

    run_sync(_config(), root)

    # Question file mtime > last_synced → whole quiz re-synced via update
    mock_course.get_quiz.assert_called_with(12345)
    quiz.edit.assert_called_once()


def test_quiz_module_item_type_is_quiz(mock_course, mocker, tmp_path) -> None:
    """A module that references a quiz creates a Quiz-type module item."""
    root = _quiz_course_root(tmp_path)
    (root / "modules").mkdir()
    (root / "modules" / "week-1.md").write_text(
        "---\ntitle: Week 1\npublished: true\n---\n\n"
        "- [A Quiz](../quizzes/a-quiz/a-quiz.md)\n"
    )
    preloaded = {
        "quizzes/a-quiz/a-quiz.md": {
            "canvas_id": 12345, "canvas_type": "quiz",
            "last_synced": _FUTURE_SYNCED,
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")
    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.return_value = _mock_item(201)

    run_sync(_config(), root)

    module.create_module_item.assert_called_once()
    item_call = module.create_module_item.call_args[1]["module_item"]
    assert item_call["type"] == "Quiz"
    assert item_call["content_id"] == 12345


def test_file_module_item_type_is_file(mock_course, mocker, tmp_path) -> None:
    """A module that references an asset file creates a File-type module item."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    (root / "modules").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "assets" / "cheatsheet.pdf").write_bytes(b"fake-pdf")
    (root / "modules" / "m.md").write_text(
        "---\ntitle: Resources\npublished: true\n---\n\n"
        "- [Cheat Sheet](../assets/cheatsheet.pdf)\n"
    )
    preloaded = {
        "assets/cheatsheet.pdf": {
            "canvas_id": 77777, "canvas_type": "file",
            "canvas_url": "/files/77777/download",
            "last_synced": _FUTURE_SYNCED,
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.return_value = _mock_item(201)

    run_sync(_config(), root)

    module.create_module_item.assert_called_once()
    item_call = module.create_module_item.call_args[1]["module_item"]
    assert item_call["type"] == "File"
    assert item_call["content_id"] == 77777


def test_single_target_skipped_when_t_already_uploaded_it(mock_course, course_root, mocker) -> None:
    """-t BFS uploads pages/syllabus.md and updates last_synced. -s then skips it via needs_sync."""
    # Make the page old so -t uploads it (needs_sync=True), setting last_synced=now.
    # When -s runs independently, file_mtime=0 < last_synced=now → skipped.
    _make_old(course_root / "pages" / "syllabus.md")
    mocker.patch("github_to_canvas.manifest.flush")
    real_page = _mock_page(11111, "syllabus")
    mock_course.create_page.return_value = real_page
    mock_course.get_page.return_value = real_page
    mock_course.create_assignment.return_value = _mock_assignment(98765)
    mock_course.get_assignment.return_value = _mock_assignment(98765)
    mock_course.create_discussion_topic.return_value = _mock_discussion(55555)
    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.side_effect = [_mock_item(i) for i in range(201, 210)]

    run_targeted_sync(
        _config(), course_root,
        recursive_targets=[str(course_root / "modules" / "week-1.md")],
        single_targets=[str(course_root / "pages" / "syllabus.md")],
    )

    # -t BFS uploads page; -s skips it (needs_sync=False due to updated manifest timestamp)
    mock_course.create_page.assert_called_once()
    # Module synced once (deferred by BFS)
    mock_course.create_module.assert_called_once()


# ---------------------------------------------------------------------------
# parse_module_body — ExternalUrl items
# ---------------------------------------------------------------------------


def test_parse_module_body_external_url(tmp_path: Path) -> None:
    """Absolute URLs in module body produce ExternalUrl items."""
    course_root = tmp_path / "course"
    course_root.mkdir()
    (course_root / "modules").mkdir()
    module_file = course_root / "modules" / "week-1.md"
    body = "- [Canvas Site](https://canvas.example.com)\n"
    items = parse_module_body(body, module_file, course_root)
    assert len(items) == 1
    assert items[0]["type"] == "ExternalUrl"
    assert items[0]["url"] == "https://canvas.example.com"
    assert items[0]["title"] == "Canvas Site"
    assert items[0]["new_tab"] is False


def test_parse_module_body_external_url_new_tab(tmp_path: Path) -> None:
    """target='_blank' in HTML comment sets new_tab=True."""
    course_root = tmp_path / "course"
    course_root.mkdir()
    (course_root / "modules").mkdir()
    module_file = course_root / "modules" / "week-1.md"
    body = '- [Resource](https://example.com) <!-- target="_blank" windowFeatures="width=800" -->\n'
    items = parse_module_body(body, module_file, course_root)
    assert items[0]["type"] == "ExternalUrl"
    assert items[0]["new_tab"] is True


def test_parse_module_body_external_url_no_comment_new_tab_false(tmp_path: Path) -> None:
    """ExternalUrl items without a target comment have new_tab=False."""
    course_root = tmp_path / "course"
    course_root.mkdir()
    (course_root / "modules").mkdir()
    module_file = course_root / "modules" / "week-1.md"
    body = "- [Link](https://example.com)\n"
    items = parse_module_body(body, module_file, course_root)
    assert items[0]["new_tab"] is False


def test_parse_module_body_mixed_content_and_external(tmp_path: Path) -> None:
    """External URL and local content links can coexist in one module."""
    course_root = tmp_path / "course"
    (course_root / "modules").mkdir(parents=True)
    module_file = course_root / "modules" / "m.md"
    body = (
        "- [Local Page](../pages/intro.md)\n"
        "- [External](https://example.com)\n"
    )
    items = parse_module_body(body, module_file, course_root)
    assert items[0]["type"] == "content"
    assert items[0]["local_path"] == "pages/intro.md"
    assert items[1]["type"] == "ExternalUrl"


# ---------------------------------------------------------------------------
# parse_module_body — indentation levels
# ---------------------------------------------------------------------------


def test_parse_module_body_indentation_levels(tmp_path: Path) -> None:
    """Indented list items get correct indent levels (2 spaces per level)."""
    course_root = tmp_path / "course"
    (course_root / "modules").mkdir(parents=True)
    module_file = course_root / "modules" / "m.md"
    body = (
        "## Welcome\n"
        "- [Top Level](../pages/a.md)\n"
        "## Links\n"
        "  - [Indented Once](../pages/b.md)\n"
        "    - [Indented Twice](../pages/c.md)\n"
    )
    items = parse_module_body(body, module_file, course_root)
    assert items[0] == {"type": "SubHeader", "title": "Welcome", "indent": 0}
    assert items[1]["indent"] == 0
    assert items[2] == {"type": "SubHeader", "title": "Links", "indent": 0}
    assert items[3]["indent"] == 1
    assert items[4]["indent"] == 2


def test_parse_module_body_indent_clamped_at_max(tmp_path: Path, capsys) -> None:
    """Indent levels beyond MAX_CANVAS_INDENT are clamped with a warning."""
    from github_to_canvas.sync import MAX_CANVAS_INDENT

    course_root = tmp_path / "course"
    (course_root / "modules").mkdir(parents=True)
    module_file = course_root / "modules" / "m.md"
    # 12 spaces = indent level 6, exceeding the Canvas max of 5
    body = "            - [Too Deep](../pages/deep.md)\n"
    items = parse_module_body(body, module_file, course_root)
    assert items[0]["indent"] == MAX_CANVAS_INDENT
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "clamping" in captured.out


def test_parse_module_body_external_url_indentation(tmp_path: Path) -> None:
    """External URL items also get indent from leading whitespace."""
    course_root = tmp_path / "course"
    (course_root / "modules").mkdir(parents=True)
    module_file = course_root / "modules" / "m.md"
    body = "  - [Link](https://example.com)\n"
    items = parse_module_body(body, module_file, course_root)
    assert items[0]["type"] == "ExternalUrl"
    assert items[0]["indent"] == 1


def test_parse_module_body_subheaders_always_indent_zero(tmp_path: Path) -> None:
    """SubHeaders always have indent 0, regardless of any leading whitespace."""
    course_root = tmp_path / "course"
    (course_root / "modules").mkdir(parents=True)
    module_file = course_root / "modules" / "m.md"
    body = "## First\n  - [Item](../pages/a.md)\n## Second\n"
    items = parse_module_body(body, module_file, course_root)
    assert items[0]["indent"] == 0
    assert items[1]["indent"] == 1
    assert items[2]["indent"] == 0


# ---------------------------------------------------------------------------
# Scenario 10: Assignment extended fields — lock_at, unlock_at, grading_type
# ---------------------------------------------------------------------------


def test_assignment_lock_at_unlock_at_grading_type_passed_to_canvas(
    mock_course, course_root, mocker
) -> None:
    """lock_at, unlock_at, grading_type from assignment frontmatter reach canvasapi."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)

    run_sync(_config(), course_root)

    call_kwargs = mock_course.create_assignment.call_args[1]["assignment"]
    assert "lock_at" in call_kwargs
    assert "unlock_at" in call_kwargs
    assert call_kwargs["grading_type"] == "points"


def test_assignment_group_grading_peer_review_fields_passed_to_canvas(
    mock_course, course_root, mocker
) -> None:
    """Group, anonymous/moderated grading, and peer-review frontmatter reach canvasapi."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    (course_root / "assignments" / "week1.md").write_text(
        "---\n"
        "title: \"Week 1 Problem Set\"\n"
        "published: true\n"
        "group_category_id: 12345\n"
        "grade_group_students_individually: true\n"
        "anonymous_grading: true\n"
        "moderated_grading: true\n"
        "grader_count: 2\n"
        "final_grader_id: 567\n"
        "peer_reviews: true\n"
        "automatic_peer_reviews: true\n"
        "peer_review_count: 3\n"
        "intra_group_peer_reviews: true\n"
        "---\n\n"
        "Submit your work.\n"
    )

    run_sync(_config(), course_root)

    call_kwargs = mock_course.create_assignment.call_args[1]["assignment"]
    assert call_kwargs["group_category_id"] == 12345
    assert call_kwargs["grade_group_students_individually"] is True
    assert call_kwargs["anonymous_grading"] is True
    assert call_kwargs["moderated_grading"] is True
    assert call_kwargs["grader_count"] == 2
    assert call_kwargs["final_grader_id"] == 567
    assert call_kwargs["peer_reviews"] is True
    assert call_kwargs["automatic_peer_reviews"] is True
    assert call_kwargs["peer_review_count"] == 3
    assert call_kwargs["intra_group_peer_reviews"] is True


def test_assignment_group_id_numeric_passed_to_canvas(
    mock_course, course_root, mocker
) -> None:
    """assignment_group_id as a numeric value is passed through unchanged."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    mock_course.get_assignment_groups.return_value = []
    (course_root / "assignments" / "week1.md").write_text(
        "---\n"
        "title: \"Week 1 Problem Set\"\n"
        "assignment_group_id: 99\n"
        "---\n\n"
        "Body.\n"
    )

    run_sync(_config(), course_root)

    call_kwargs = mock_course.create_assignment.call_args[1]["assignment"]
    assert call_kwargs["assignment_group_id"] == 99


def test_assignment_group_id_by_name_resolved_to_canvas_id(
    mock_course, course_root, mocker
) -> None:
    """assignment_group_id as a name string is resolved to the Canvas numeric ID."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    labs_group = MagicMock()
    labs_group.name = "Labs"
    labs_group.id = 42
    mock_course.get_assignment_groups.return_value = [labs_group]
    (course_root / "assignments" / "week1.md").write_text(
        "---\n"
        "title: \"Week 1 Problem Set\"\n"
        "assignment_group_id: \"Labs\"\n"
        "---\n\n"
        "Body.\n"
    )

    run_sync(_config(), course_root)

    call_kwargs = mock_course.create_assignment.call_args[1]["assignment"]
    assert call_kwargs["assignment_group_id"] == 42


def test_assignment_group_id_unknown_name_skipped(
    mock_course, course_root, mocker, capsys
) -> None:
    """Unknown assignment group name prints a warning and omits the field."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    mock_course.get_assignment_groups.return_value = []
    (course_root / "assignments" / "week1.md").write_text(
        "---\n"
        "title: \"Week 1 Problem Set\"\n"
        "assignment_group_id: \"NoSuchGroup\"\n"
        "---\n\n"
        "Body.\n"
    )

    had_errors = run_sync(_config(), course_root)

    assert had_errors, "run_sync should return True (errors present)"
    call_kwargs = mock_course.create_assignment.call_args[1]["assignment"]
    assert "assignment_group_id" not in call_kwargs
    captured = capsys.readouterr()
    # Immediate inline warning
    assert "WARNING" in captured.out
    assert "NoSuchGroup" in captured.out
    # Also reprinted in the end-of-run errors summary
    assert "errors occurred" in captured.out


# ---------------------------------------------------------------------------
# Scenario 11: Graded discussion fields passed as nested assignment params
# ---------------------------------------------------------------------------


def test_graded_discussion_fields_passed_as_assignment_dict(
    mock_course, course_root, mocker
) -> None:
    """points_possible, due_at, lock_at, unlock_at passed as assignment= dict for discussions."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)

    run_sync(_config(), course_root)

    call_kwargs = mock_course.create_discussion_topic.call_args[1]
    assert "assignment" in call_kwargs
    assignment_params = call_kwargs["assignment"]
    assert assignment_params["points_possible"] == 10
    assert "due_at" in assignment_params
    assert "lock_at" in assignment_params
    assert "unlock_at" in assignment_params


# ---------------------------------------------------------------------------
# Scenario 12: Syllabus sync — course_settings/syllabus.md → course.update()
# ---------------------------------------------------------------------------


def _make_course_with_syllabus(tmp_path: Path) -> Path:
    """Minimal course repo with a syllabus file."""
    root = tmp_path / "course"
    cs_dir = root / "course_settings"
    cs_dir.mkdir(parents=True)
    (cs_dir / "syllabus.md").write_text(
        "---\ntitle: Syllabus\npublished: true\n---\n\n"
        "Welcome to the course.\n"
    )
    return root


def test_syllabus_synced_calls_course_update(mock_course, mocker, tmp_path) -> None:
    """sync_syllabus converts syllabus.md to HTML and calls course.update(syllabus_body=...)."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = _make_course_with_syllabus(tmp_path)

    run_sync(_config(), root)

    update_calls = mock_course.update.call_args_list
    syllabus_calls = [c for c in update_calls if "syllabus_body" in c[1].get("course", {})]
    assert len(syllabus_calls) == 1
    body_html = syllabus_calls[0][1]["course"]["syllabus_body"]
    assert "Welcome to the course" in body_html


def test_syllabus_missing_does_not_crash(mock_course, mocker, tmp_path) -> None:
    """If course_settings/syllabus.md is absent, sync proceeds without error."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    root.mkdir()

    run_sync(_config(), root)  # should not raise

    # No syllabus_body update when file is missing
    for c in mock_course.update.call_args_list:
        assert "syllabus_body" not in c[1].get("course", {})


# ---------------------------------------------------------------------------
# Scenario 13: Course metadata sync — course_settings.toml → course.update()
# ---------------------------------------------------------------------------


def _make_course_with_settings(tmp_path: Path) -> Path:
    """Minimal course repo with a course_settings/course_settings.toml."""
    root = tmp_path / "course"
    root.mkdir()
    cs_dir = root / "course_settings"
    cs_dir.mkdir()
    (cs_dir / "course_settings.toml").write_text(
        'title = "Intro to CS"\n'
        'course_code = "CS101"\n'
        'default_view = "modules"\n'
    )
    return root


def test_course_metadata_synced_calls_course_update(mock_course, mocker, tmp_path) -> None:
    """course_settings.toml fields reach course.update(course={...})."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = _make_course_with_settings(tmp_path)

    run_sync(_config(), root)

    update_calls = mock_course.update.call_args_list
    meta_calls = [c for c in update_calls if "name" in c[1].get("course", {})]
    assert len(meta_calls) == 1
    params = meta_calls[0][1]["course"]
    assert params["name"] == "Intro to CS"
    assert params["course_code"] == "CS101"
    assert params["default_view"] == "modules"


def test_course_settings_missing_does_not_crash(mock_course, mocker, tmp_path) -> None:
    """If course_settings.toml is absent, sync proceeds without error."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    root.mkdir()

    run_sync(_config(), root)  # should not raise


# ---------------------------------------------------------------------------
# Scenario 13a: Dashboard image — dashboard_image in course_settings.toml
# ---------------------------------------------------------------------------


def test_dashboard_image_uploaded_and_set(mock_course, mocker, tmp_path) -> None:
    """dashboard_image in course_settings.toml uploads the file and sets image_id."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    root.mkdir()
    cs_dir = root / "course_settings"
    cs_dir.mkdir()
    # Place image outside assets/ to avoid the asset walker also uploading it
    image_file = cs_dir / "banner.png"
    image_file.write_bytes(b"fake-png-data")
    (cs_dir / "course_settings.toml").write_text(
        'title = "Intro to CS"\n'
        'dashboard_image = "course_settings/banner.png"\n'
    )
    mock_course.upload.return_value = (True, {"id": 42, "url": "https://example.com/files/42"})

    run_sync(_config(), root)

    # The image file was uploaded via course.upload
    mock_course.upload.assert_called_once_with(
        str(image_file), parent_folder_path="course files"
    )
    # course.update was called with image_id
    image_update_calls = [
        c for c in mock_course.update.call_args_list
        if "image_id" in c[1].get("course", {})
    ]
    assert len(image_update_calls) == 1
    assert image_update_calls[0][1]["course"]["image_id"] == 42


def test_dashboard_image_missing_file_warns(mock_course, mocker, tmp_path, capsys) -> None:
    """dashboard_image pointing to a non-existent file prints a warning."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    root.mkdir()
    cs_dir = root / "course_settings"
    cs_dir.mkdir()
    (cs_dir / "course_settings.toml").write_text(
        'dashboard_image = "assets/nonexistent.png"\n'
    )

    run_sync(_config(), root)

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "nonexistent.png" in out
    # No upload attempted
    mock_course.upload.assert_not_called()


def test_dashboard_image_not_passed_to_course_metadata(mock_course, mocker, tmp_path) -> None:
    """dashboard_image is handled separately and not passed to course.update as metadata."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    root.mkdir()
    cs_dir = root / "course_settings"
    cs_dir.mkdir()
    (cs_dir / "course_settings.toml").write_text(
        'title = "Test"\n'
        'dashboard_image = "assets/banner.png"\n'
    )
    # No image file exists → warning, but metadata update should still happen without dashboard_image
    run_sync(_config(), root)

    meta_calls = [c for c in mock_course.update.call_args_list if "name" in c[1].get("course", {})]
    assert len(meta_calls) == 1
    assert "dashboard_image" not in meta_calls[0][1]["course"]
    assert "image_id" not in meta_calls[0][1]["course"]


def test_upload_course_image_unit() -> None:
    """Unit test: upload_course_image uploads and sets image_id on the course."""
    from github_to_canvas.canvas_api import upload_course_image

    course = MagicMock()
    course.upload.return_value = (True, {"id": 99, "url": "https://example.com/files/99"})

    file_id = upload_course_image(course, Path("/tmp/banner.png"))

    assert file_id == 99
    course.upload.assert_called_once_with(
        "/tmp/banner.png", parent_folder_path="course files"
    )
    course.update.assert_called_once_with(course={"image_id": 99})


# ---------------------------------------------------------------------------
# Scenario 13b: Course-navigation (tab_configuration) sync
# ---------------------------------------------------------------------------

from github_to_canvas import canvas_api as _capi  # noqa: E402


def _mock_tab(tab_id: str, label: str | None = None) -> MagicMock:
    t = MagicMock()
    t.id = tab_id
    t.label = label if label is not None else tab_id.title()
    return t


def _fake_course_with_tabs(*tab_ids: str) -> MagicMock:
    course = MagicMock()
    course.get_tabs.return_value = [_mock_tab(tid) for tid in tab_ids]
    return course


def test_tab_configuration_string_ids_reorder_and_hide() -> None:
    """The repo format uses string ids; position is 1-based; hidden passes through."""
    course = _fake_course_with_tabs("home", "assignments", "modules", "files")
    tabs = {t.id: t for t in course.get_tabs.return_value}

    _capi.sync_tab_configuration(
        course,
        [
            {"id": "home"},                  # unmanageable → skipped
            {"id": "modules"},
            {"id": "assignments"},
            {"id": "files", "hidden": True},
        ],
    )

    tabs["home"].update.assert_not_called()
    tabs["modules"].update.assert_called_once_with(position=2, hidden=False)
    tabs["assignments"].update.assert_called_once_with(position=3, hidden=False)
    tabs["files"].update.assert_called_once_with(position=4, hidden=True)


def test_tab_configuration_external_tool_matched_by_label() -> None:
    """External-tool tabs are matched by label, not by the (course-specific) id."""
    course = MagicMock()
    zoom = _mock_tab("context_external_tool_4567", label="Zoom")  # live, real Canvas id
    course.get_tabs.return_value = [_mock_tab("assignments"), zoom]

    _capi.sync_tab_configuration(
        course,
        [
            {"id": "assignments"},
            # repo carries the original cartridge id, but matching is by label:
            {"label": "Zoom", "id": "context_external_tool_gOLDHASH", "hidden": True},
        ],
    )

    zoom.update.assert_called_once_with(position=3, hidden=True)


def test_tab_configuration_label_match_is_case_insensitive() -> None:
    course = MagicMock()
    tool = _mock_tab("context_external_tool_99", label="Panopto Video")
    course.get_tabs.return_value = [tool]

    _capi.sync_tab_configuration(course, [{"label": "panopto video"}])

    tool.update.assert_called_once_with(position=2, hidden=False)


def test_tab_configuration_numeric_ids_still_accepted() -> None:
    """Legacy numeric ids (IMSCC escaped-JSON form) remain supported for back-compat."""
    course = _fake_course_with_tabs("modules", "assignments")
    tabs = {t.id: t for t in course.get_tabs.return_value}

    _capi.sync_tab_configuration(course, [{"id": 10}, {"id": 3}])

    tabs["modules"].update.assert_called_once_with(position=2, hidden=False)
    tabs["assignments"].update.assert_called_once_with(position=3, hidden=False)


def test_tab_configuration_warns_on_unresolved_external_tool(capsys) -> None:
    """A tool whose label isn't present in the course is warned + skipped, not created."""
    course = _fake_course_with_tabs("assignments")  # no tool tabs at all

    _capi.sync_tab_configuration(
        course, [{"label": "Zoom", "id": "context_external_tool_gOLDHASH"}]
    )

    out = capsys.readouterr().out
    assert "Zoom" in out and "WARNING" in out


def test_tab_configuration_empty_label_placeholder_warns(capsys) -> None:
    """An unfilled `label = ""` placeholder is reported and skipped, never matched by id."""
    tool = _mock_tab("context_external_tool_4567", label="Panopto")
    course = MagicMock()
    course.get_tabs.return_value = [_mock_tab("assignments"), tool]

    _capi.sync_tab_configuration(
        course,
        [
            {"id": "assignments"},
            {"label": "", "id": "context_external_tool_gOLDHASH", "hidden": True},
        ],
    )

    tool.update.assert_not_called()
    out = capsys.readouterr().out
    assert "no label" in out and "WARNING" in out


def test_tab_configuration_warns_on_missing_tab(capsys) -> None:
    """A tab not present in the course (e.g. an imported LTI tool tab) is warned + skipped."""
    course = _fake_course_with_tabs("home", "assignments")

    _capi.sync_tab_configuration(
        course,
        [
            {"id": 3},  # assignments — exists
            {"id": "context_external_tool_gdeadbeef", "hidden": True},  # not present
        ],
    )

    out = capsys.readouterr().out
    assert "context_external_tool_gdeadbeef" in out
    assert "WARNING" in out


def test_tab_configuration_id_falls_back_to_tool_label() -> None:
    """id and label are interchangeable: id="<tool name>" resolves to the tool tab."""
    course = MagicMock()
    panopto = _mock_tab("context_external_tool_25392", label="Panopto Recordings")
    course.get_tabs.return_value = [_mock_tab("assignments"), panopto]

    _capi.sync_tab_configuration(
        course, [{"id": "assignments"}, {"id": "Panopto Recordings"}]
    )

    panopto.update.assert_called_once_with(position=3, hidden=False)


def test_tab_configuration_id_is_case_insensitive_for_builtins() -> None:
    """A capitalized id like "Assignments" still matches the built-in "assignments"."""
    course = _fake_course_with_tabs("assignments", "modules")
    tabs = {t.id: t for t in course.get_tabs.return_value}

    _capi.sync_tab_configuration(course, [{"id": "Assignments"}, {"label": "Modules"}])

    tabs["assignments"].update.assert_called_once_with(position=2, hidden=False)
    tabs["modules"].update.assert_called_once_with(position=3, hidden=False)


def test_tab_configuration_warns_on_unknown_numeric_id(capsys) -> None:
    """An unrecognized numeric tab id is warned + skipped, not applied."""
    course = _fake_course_with_tabs("home")

    _capi.sync_tab_configuration(course, [{"id": 999}])

    out = capsys.readouterr().out
    assert "999" in out and "WARNING" in out


def test_tab_configuration_dedups_collaborations() -> None:
    """Ids 16 and 18 both resolve to 'collaborations'; first occurrence wins."""
    course = _fake_course_with_tabs("collaborations")
    tab = course.get_tabs.return_value[0]

    _capi.sync_tab_configuration(course, [{"id": 16}, {"id": 18, "hidden": True}])

    tab.update.assert_called_once_with(position=2, hidden=False)


def test_tab_configuration_synced_end_to_end(mock_course, mocker, tmp_path) -> None:
    """tab_configuration in course_settings.toml reaches the Tabs API via run_sync."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    root.mkdir()
    cs_dir = root / "course_settings"
    cs_dir.mkdir()
    # JSON string, exactly as the importer writes it (TOML escapes the inner quotes).
    (cs_dir / "course_settings.toml").write_text(
        'title = "Intro to CS"\n'
        'tab_configuration = "[{\\"id\\":3},{\\"id\\":10,\\"hidden\\":true}]"\n'
    )
    assignments_tab = _mock_tab("assignments")
    modules_tab = _mock_tab("modules")
    mock_course.get_tabs.return_value = [assignments_tab, modules_tab]

    run_sync(_config(), root)

    assignments_tab.update.assert_called_once_with(position=2, hidden=False)
    modules_tab.update.assert_called_once_with(position=3, hidden=True)


def test_tab_configuration_array_of_tables_end_to_end(mock_course, mocker, tmp_path) -> None:
    """The new [[tab_configuration]] array-of-tables form drives the Tabs API."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    root.mkdir()
    cs_dir = root / "course_settings"
    cs_dir.mkdir()
    (cs_dir / "course_settings.toml").write_text(
        'title = "Intro to CS"\n'
        "\n"
        "[[tab_configuration]]\n"
        'id = "assignments"\n'
        "\n"
        "[[tab_configuration]]\n"
        'label = "Zoom"\n'
        'id = "context_external_tool_gOLDHASH"\n'
        "hidden = true\n"
    )
    assignments_tab = _mock_tab("assignments")
    zoom_tab = _mock_tab("context_external_tool_4567", label="Zoom")
    mock_course.get_tabs.return_value = [assignments_tab, zoom_tab]

    run_sync(_config(), root)

    assignments_tab.update.assert_called_once_with(position=2, hidden=False)
    zoom_tab.update.assert_called_once_with(position=3, hidden=True)


def test_tab_configuration_misplaced_under_section_warns(mock_course, mocker, tmp_path, capsys) -> None:
    """tab_configuration accidentally nested under a [section] is detected and warned."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    root.mkdir()
    cs_dir = root / "course_settings"
    cs_dir.mkdir()
    # The classic TOML trap: a top-level key written AFTER a [section] header, so
    # TOML attaches it to that section instead.
    (cs_dir / "course_settings.toml").write_text(
        'title = "Intro to CS"\n'
        "\n"
        "[late_policy]\n"
        "missing_submission_deduction_enabled = false\n"
        "\n"
        "tab_configuration = [\n"
        '    { id = "modules" },\n'
        "]\n"
    )
    modules_tab = _mock_tab("modules")
    mock_course.get_tabs.return_value = [modules_tab]

    run_sync(_config(), root)

    modules_tab.update.assert_not_called()  # nested → never applied
    out = capsys.readouterr().out
    assert "late_policy.tab_configuration" in out and "top level" in out


# ---------------------------------------------------------------------------
# Scenario 13c: Post policy via GraphQL (not REST)
# ---------------------------------------------------------------------------


def _graphql_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_update_post_policy_uses_graphql_mutation() -> None:
    """Course post policy goes through the GraphQL endpoint, not a REST route."""
    course = MagicMock()
    course.id = 42
    course._requester.request.return_value = _graphql_response(
        {"data": {"setCoursePostPolicy": {"postPolicy": {"postManually": True}, "errors": None}}}
    )

    _capi.update_post_policy(course, True)

    args, kwargs = course._requester.request.call_args
    assert args[0] == "POST" and args[1] == "graphql"
    assert kwargs["_url"] == "graphql"  # hits /api/graphql, not /api/v1/...
    assert kwargs["json"]["variables"] == {"courseId": 42, "postManually": True}
    assert "setCoursePostPolicy" in kwargs["json"]["query"]


def test_update_post_policy_raises_on_mutation_error() -> None:
    """Mutation-level errors (HTTP 200 with an errors array) surface as an exception."""
    course = MagicMock()
    course.id = 7
    course._requester.request.return_value = _graphql_response(
        {"data": {"setCoursePostPolicy": {"errors": [{"message": "not allowed"}]}}}
    )

    with pytest.raises(RuntimeError, match="setCoursePostPolicy"):
        _capi.update_post_policy(course, False)


def test_graphql_raises_on_top_level_errors() -> None:
    """GraphQL transport-level errors are raised rather than silently ignored."""
    course = MagicMock()
    course._requester.request.return_value = _graphql_response(
        {"errors": [{"message": "Field 'x' doesn't exist"}]}
    )

    with pytest.raises(RuntimeError, match="GraphQL error"):
        _capi.graphql(course, "query {}", {})


# ---------------------------------------------------------------------------
# Scenario 14: course_settings/ folder not processed as Canvas Pages
# ---------------------------------------------------------------------------


def test_course_settings_folder_not_synced_as_page(mock_course, mocker, tmp_path) -> None:
    """Files inside course_settings/ are not uploaded as Canvas Pages."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    cs_dir = root / "course_settings"
    cs_dir.mkdir(parents=True)
    (cs_dir / "syllabus.md").write_text(
        "---\ntitle: Syllabus\npublished: true\n---\n\nBody.\n"
    )
    (cs_dir / "events.md").write_text(
        "---\ntitle: Events\n---\n\n## An Event\n\n**Date:** 2025-09-01\n"
    )
    # Provide a real page so create_page won't be called for course_settings files
    mock_course.update = MagicMock()

    run_sync(_config(), root)

    # No page should be created for course_settings/ content
    mock_course.create_page.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 15: ExternalUrl module item created via add_module_item
# ---------------------------------------------------------------------------


def test_module_external_url_item_created(mock_course, mocker, tmp_path) -> None:
    """ExternalUrl items in module body result in ExternalUrl module item calls."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    (root / "modules").mkdir(parents=True)
    (root / "modules" / "m.md").write_text(
        "---\ntitle: Module\npublished: true\n---\n\n"
        '- [External Resource](https://example.com) <!-- target="_blank" -->\n'
    )
    module = _mock_module(66666)
    mock_course.create_module.return_value = module
    module.create_module_item.return_value = _mock_item(201)

    run_sync(_config(), root)

    module.create_module_item.assert_called_once()
    item_call = module.create_module_item.call_args[1]["module_item"]
    assert item_call["type"] == "ExternalUrl"
    assert item_call["external_url"] == "https://example.com"
    assert item_call["new_tab"] is True


# ---------------------------------------------------------------------------
# Scenario 16: Graceful handling of missing optional fields
# ---------------------------------------------------------------------------


def test_module_item_missing_from_manifest_warns_and_skips(
    mock_course, mocker, tmp_path, capsys
) -> None:
    """A module referencing a non-existent file warns and skips that item without crashing.

    The module .md links to pages/ghost.md, which is never created in the repo.
    That file is never synced, so it never appears in the manifest.  When the module
    sync runs, add_module_item should print a WARNING and skip the item rather than
    raising a KeyError.
    """
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    (root / "modules").mkdir(parents=True)
    # pages/ghost.md is referenced but NEVER created — it won't be in the manifest
    (root / "modules" / "m.md").write_text(
        "---\ntitle: Module\n---\n\n"
        "- [Ghost Page](../pages/ghost.md)\n"
    )
    module = _mock_module(66666)
    mock_course.create_module.return_value = module

    run_sync(_config(), root)

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "ghost.md" in out
    # Module itself is still created; the missing item is skipped
    mock_course.create_module.assert_called_once()
    module.create_module_item.assert_not_called()


def test_assignment_without_optional_fields_still_uploads(
    mock_course, mocker, tmp_path
) -> None:
    """An assignment with only title and body (no dates, points, etc.) uploads successfully."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    (root / "assignments").mkdir(parents=True)
    (root / "assignments" / "simple.md").write_text(
        "---\ntitle: Simple Assignment\n---\n\nDo the work.\n"
    )
    mock_course.create_assignment.return_value = _mock_assignment(10001)

    run_sync(_config(), root)

    mock_course.create_assignment.assert_called_once()
    call_kwargs = mock_course.create_assignment.call_args[1]["assignment"]
    assert call_kwargs["name"] == "Simple Assignment"
    assert "due_at" not in call_kwargs
    assert "lock_at" not in call_kwargs
    assert "unlock_at" not in call_kwargs
    assert "points_possible" not in call_kwargs
    assert "submission_types" not in call_kwargs
    assert "grading_type" not in call_kwargs


def test_discussion_without_optional_fields_still_uploads(
    mock_course, mocker, tmp_path
) -> None:
    """A discussion with only title and body (no grading params) uploads successfully."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    (root / "discussions").mkdir(parents=True)
    (root / "discussions" / "intro.md").write_text(
        "---\ntitle: Intro Discussion\n---\n\nTell us about yourself.\n"
    )
    mock_course.create_discussion_topic.return_value = _mock_discussion(20001)

    run_sync(_config(), root)

    mock_course.create_discussion_topic.assert_called_once()
    call_kwargs = mock_course.create_discussion_topic.call_args[1]
    assert call_kwargs["title"] == "Intro Discussion"
    assert "assignment" not in call_kwargs
    assert "require_initial_post" not in call_kwargs


def test_content_file_without_frontmatter_still_uploads(
    mock_course, mocker, tmp_path
) -> None:
    """A page with no frontmatter at all is uploaded using the filename as title."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    (root / "pages").mkdir(parents=True)
    (root / "pages" / "my-notes.md").write_text("## Notes\n\nSome content here.\n")
    mock_course.create_page.return_value = _mock_page(30001, "my-notes")

    run_sync(_config(), root)

    mock_course.create_page.assert_called_once()
    call_kwargs = mock_course.create_page.call_args[1]["wiki_page"]
    assert call_kwargs["title"] == "my-notes"
    assert "Notes" in call_kwargs["body"]


# ---------------------------------------------------------------------------
# Scenario: module_order.toml — explicit module positions
# ---------------------------------------------------------------------------


def _make_minimal_module_repo(root: Path, module_names: list[str]) -> None:
    """Write a course repo with empty content dirs and one module file per name."""
    (root / "modules").mkdir(parents=True)
    for name in module_names:
        (root / "modules" / name).write_text(
            f'---\ntitle: "{name}"\npublished: true\n---\n'
        )


def test_module_position_passed_when_order_file_present(
    mock_course, mocker, tmp_path
) -> None:
    """Position is passed to create_module when module_order.toml lists the module."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    _make_minimal_module_repo(root, ["week-1.md", "week-2.md"])
    (root / "course_settings").mkdir()
    (root / "course_settings" / "module_order.toml").write_text(
        'order = ["week-1.md", "week-2.md"]\n'
    )

    mod1 = _mock_module(101)
    mod2 = _mock_module(102)
    mock_course.create_module.side_effect = [mod1, mod2]

    run_sync(_config(), root)

    calls = mock_course.create_module.call_args_list
    assert len(calls) == 2
    # week-1.md is position 1, week-2.md is position 2
    assert calls[0][1]["module"]["position"] == 1
    assert calls[1][1]["module"]["position"] == 2


def test_module_without_order_file_has_no_position(
    mock_course, mocker, tmp_path
) -> None:
    """No position kwarg is sent to Canvas when module_order.toml does not exist."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    _make_minimal_module_repo(root, ["week-1.md"])

    module = _mock_module(101)
    mock_course.create_module.return_value = module

    run_sync(_config(), root)

    call_kwargs = mock_course.create_module.call_args[1]["module"]
    assert "position" not in call_kwargs


def test_module_order_change_triggers_resync(
    mock_course, mocker, tmp_path
) -> None:
    """Modules listed in module_order.toml are re-synced when that file changes."""
    root = tmp_path / "course"
    _make_minimal_module_repo(root, ["week-1.md"])
    order_path = root / "course_settings" / "module_order.toml"
    order_path.parent.mkdir()
    order_path.write_text('order = ["week-1.md"]\n')

    # Manifest shows week-1.md synced recently (future timestamp) — would normally skip
    preloaded = {
        "modules/week-1.md": {
            "canvas_id": 101, "canvas_type": "module",
            "canvas_item_ids": {},
            "last_synced": _FUTURE_SYNCED,
        },
        # order file has no manifest entry → needs_sync returns True
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    module = _mock_module(101)
    mock_course.get_module.return_value = module

    run_sync(_config(), root)

    # Module was re-synced (updated, not created) with position=1
    mock_course.create_module.assert_not_called()
    edit_kwargs = module.edit.call_args[1]["module"]
    assert edit_kwargs["position"] == 1


def test_module_order_up_to_date_skips_resync(
    mock_course, mocker, tmp_path, capsys
) -> None:
    """Modules are NOT re-synced when module_order.toml itself is unchanged."""
    root = tmp_path / "course"
    _make_minimal_module_repo(root, ["week-1.md"])
    order_path = root / "course_settings" / "module_order.toml"
    order_path.parent.mkdir()
    order_path.write_text('order = ["week-1.md"]\n')
    _make_old(root / "modules" / "week-1.md")
    _make_old(order_path)

    preloaded = {
        "modules/week-1.md": {
            "canvas_id": 101, "canvas_type": "module",
            "canvas_item_ids": {},
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
        "course_settings/module_order.toml": {
            "canvas_id": 0, "canvas_type": "module_order",
            "last_synced": "2025-01-01T00:00:00+00:00",
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=preloaded)
    mocker.patch("github_to_canvas.manifest.flush")

    run_sync(_config(), root, verbose=True)

    mock_course.create_module.assert_not_called()
    mock_course.get_module.assert_not_called()
    out = capsys.readouterr().out
    assert "Skipping (up-to-date): modules/week-1.md" in out


def test_targeted_sync_passes_position_from_order_file(
    mock_course, mocker, tmp_path
) -> None:
    """run_targeted_sync applies position from module_order.toml when syncing a module."""
    mocker.patch("github_to_canvas.manifest.flush")
    root = tmp_path / "course"
    _make_minimal_module_repo(root, ["week-1.md", "week-2.md"])
    (root / "course_settings").mkdir()
    (root / "course_settings" / "module_order.toml").write_text(
        'order = ["week-1.md", "week-2.md"]\n'
    )

    module = _mock_module(101)
    mock_course.create_module.return_value = module

    run_targeted_sync(
        _config(), root,
        recursive_targets=[],
        single_targets=[str(root / "modules" / "week-2.md")],
    )

    call_kwargs = mock_course.create_module.call_args[1]["module"]
    assert call_kwargs["position"] == 2


# ---------------------------------------------------------------------------
# prune: delete / unpublish orphaned manifest entries
# ---------------------------------------------------------------------------


def _prune_repo(tmp_path: Path) -> Path:
    """A repo where only pages/kept.md exists on disk; everything else is orphaned."""
    root = tmp_path / "course"
    (root / "pages").mkdir(parents=True)
    (root / "pages" / "kept.md").write_text("---\ntitle: Kept\n---\nstill here\n")
    return root


def test_prune_delete_removes_orphans_and_keeps_present(
    mock_course, mocker, tmp_path
) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/gone.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "gone"},
        "assignments/gone.md": {"canvas_type": "assignment", "canvas_id": 22},
        "pages/kept.md": {"canvas_type": "page", "canvas_id": 33, "canvas_url": "kept"},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")

    had_errors = run_prune(_config(), root, "delete")

    assert had_errors is False
    # Orphans deleted on Canvas...
    mock_course.get_page.assert_called_once_with("gone")
    mock_course.get_page.return_value.delete.assert_called_once()
    mock_course.get_assignment.assert_called_once_with(22)
    mock_course.get_assignment.return_value.delete.assert_called_once()
    # ...and removed from the manifest; the present file is untouched.
    assert "pages/gone.md" not in manifest
    assert "assignments/gone.md" not in manifest
    assert "pages/kept.md" in manifest


def test_prune_unpublish_sets_published_false(mock_course, mocker, tmp_path) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/gone.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "gone"},
        "quizzes/gone/gone.md": {"canvas_type": "quiz", "canvas_id": 44},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")

    had_errors = run_prune(_config(), root, "unpublish")

    assert had_errors is False
    mock_course.get_page.return_value.edit.assert_called_once_with(
        wiki_page={"published": False}
    )
    mock_course.get_quiz.return_value.edit.assert_called_once_with(
        quiz={"published": False}
    )
    mock_course.get_page.return_value.delete.assert_not_called()
    assert manifest == {}


def test_prune_skips_nonprunable_type_and_keeps_entry(
    mock_course, mocker, tmp_path
) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "course_settings/module_order.toml": {
            "canvas_type": "module_order",
            "canvas_id": 0,
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")

    had_errors = run_prune(_config(), root, "delete")

    assert had_errors is False
    # No Canvas object exists for bookkeeping types; nothing is fetched or deleted.
    mock_course.get_page.assert_not_called()
    # The entry is preserved (skip + warn).
    assert "course_settings/module_order.toml" in manifest


def test_prune_question_bank_skipped_under_unpublish(
    mock_course, mocker, tmp_path
) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "question_banks/qb/qb.toml": {
            "canvas_type": "question_bank",
            "canvas_id": 88,
        },
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")

    had_errors = run_prune(_config(), root, "unpublish")

    assert had_errors is False
    # Question banks have no unpublish concept: skipped, entry kept.
    assert "question_banks/qb/qb.toml" in manifest


def test_prune_no_orphans_is_noop(mock_course, mocker, tmp_path) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/kept.md": {"canvas_type": "page", "canvas_id": 33, "canvas_url": "kept"},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    flush = mocker.patch("github_to_canvas.manifest.flush")

    had_errors = run_prune(_config(), root, "delete")

    assert had_errors is False
    mock_course.get_page.assert_not_called()
    flush.assert_not_called()
    assert "pages/kept.md" in manifest


def test_prune_reports_errors_but_continues(mock_course, mocker, tmp_path) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/bad.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "bad"},
        "assignments/gone.md": {"canvas_type": "assignment", "canvas_id": 22},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")
    mock_course.get_page.side_effect = RuntimeError("404 not found")

    had_errors = run_prune(_config(), root, "delete")

    assert had_errors is True
    # The failed entry is kept; the healthy one is still pruned.
    assert "pages/bad.md" in manifest
    assert "assignments/gone.md" not in manifest
    mock_course.get_assignment.return_value.delete.assert_called_once()


def _set_syllabus_body(mock_course, html: str) -> None:
    """Make course._requester.request(... syllabus_body ...) return the given HTML."""
    response = MagicMock()
    response.json.return_value = {"syllabus_body": html}
    mock_course._requester.request.return_value = response


def test_prune_keeps_front_page(mock_course, mocker, tmp_path) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/home.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "home"},
        "assignments/gone.md": {"canvas_type": "assignment", "canvas_id": 22},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")
    mock_course.show_front_page.return_value = SimpleNamespace(url="home")
    _set_syllabus_body(mock_course, "")

    had_errors = run_prune(_config(), root, "delete")

    assert had_errors is False
    # The front page is kept even though its local file is gone.
    assert "pages/home.md" in manifest
    mock_course.get_page.assert_not_called()
    # Other orphans still pruned.
    assert "assignments/gone.md" not in manifest
    mock_course.get_assignment.return_value.delete.assert_called_once()


def test_prune_keeps_page_linked_from_syllabus(mock_course, mocker, tmp_path) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/syl.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "syl-page"},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")
    mock_course.show_front_page.return_value = None
    _set_syllabus_body(
        mock_course,
        '<a href="/courses/123/pages/syl-page">syllabus link</a>',
    )

    had_errors = run_prune(_config(), root, "delete")

    assert had_errors is False
    # A page referenced from the syllabus body is kept.
    assert "pages/syl.md" in manifest
    mock_course.get_page.return_value.delete.assert_not_called()


def test_prune_unpublish_keeps_front_page(mock_course, mocker, tmp_path) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/home.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "home"},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")
    mock_course.show_front_page.return_value = SimpleNamespace(url="home")
    _set_syllabus_body(mock_course, "")

    had_errors = run_prune(_config(), root, "unpublish")

    assert had_errors is False
    # In-use pages are never unpublished either.
    assert "pages/home.md" in manifest
    mock_course.get_page.return_value.edit.assert_not_called()


def test_prune_delete_treats_already_gone_as_success(
    mock_course, mocker, tmp_path, capsys
) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/gone.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "gone"},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")
    _set_syllabus_body(mock_course, "")
    # The Canvas item was already deleted (manually or by a prior run).
    mock_course.get_page.return_value.delete.side_effect = ResourceDoesNotExist(
        "404 not found"
    )

    had_errors = run_prune(_config(), root, "delete")

    # Desired end state already reached: no error, stale entry dropped.
    assert had_errors is False
    assert "pages/gone.md" not in manifest
    # ...and the message reflects that it was already gone, not freshly deleted.
    out = capsys.readouterr().out
    assert "Does not exist on Canvas: pages/gone.md" in out
    assert "Deleted on Canvas: pages/gone.md" not in out


def test_prune_unpublish_treats_already_gone_as_success(
    mock_course, mocker, tmp_path, capsys
) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/gone.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "gone"},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    mocker.patch("github_to_canvas.manifest.flush")
    _set_syllabus_body(mock_course, "")
    mock_course.get_page.side_effect = ResourceDoesNotExist("404 not found")

    had_errors = run_prune(_config(), root, "unpublish")

    assert had_errors is False
    assert "pages/gone.md" not in manifest
    out = capsys.readouterr().out
    assert "Does not exist on Canvas: pages/gone.md" in out


def test_prune_manifest_only_drops_orphans_without_touching_canvas(
    mock_course, mocker, tmp_path
) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        # A normally-deletable orphan whose Canvas item is already gone...
        "pages/gone.md": {"canvas_type": "page", "canvas_id": 11, "canvas_url": "gone"},
        # ...an unsupported (otherwise un-prunable) type...
        "course_settings/module_order.toml": {
            "canvas_type": "module_order",
            "canvas_id": 0,
        },
        # ...and a present file that must be preserved.
        "pages/kept.md": {"canvas_type": "page", "canvas_id": 33, "canvas_url": "kept"},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    flush = mocker.patch("github_to_canvas.manifest.flush")

    had_errors = run_prune(_config(), root, "manifest")

    assert had_errors is False
    # Every orphan is dropped regardless of type or in-use status...
    assert "pages/gone.md" not in manifest
    assert "course_settings/module_order.toml" not in manifest
    # ...the present file is kept...
    assert "pages/kept.md" in manifest
    flush.assert_called_once()
    # ...and Canvas is never contacted.
    mock_course.get_page.assert_not_called()
    mock_course.get_assignment.assert_not_called()
    mock_course._requester.request.assert_not_called()


def test_prune_manifest_only_no_orphans_is_noop(
    mock_course, mocker, tmp_path
) -> None:
    root = _prune_repo(tmp_path)
    manifest = {
        "pages/kept.md": {"canvas_type": "page", "canvas_id": 33, "canvas_url": "kept"},
    }
    mocker.patch("github_to_canvas.manifest.load", return_value=manifest)
    flush = mocker.patch("github_to_canvas.manifest.flush")

    had_errors = run_prune(_config(), root, "manifest")

    assert had_errors is False
    flush.assert_not_called()
    assert "pages/kept.md" in manifest


# ---------------------------------------------------------------------------
# Ignore files (.gitignore / .canvasignore)
# ---------------------------------------------------------------------------


def test_ignored_asset_not_uploaded(mock_course, course_root, mocker) -> None:
    """A stray file matched by .gitignore (e.g. a Word temp file) is skipped."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    # Simulate a Word backup file sitting next to a real asset.
    (course_root / "assets" / "~$logo.docx").write_text("junk")
    (course_root / ".gitignore").write_text("~$*\n")

    run_sync(_config(), course_root)

    # Only the real asset (fig.png) is uploaded; the temp file is ignored.
    mock_course.upload.assert_called_once()
    uploaded_path = mock_course.upload.call_args[0][0]
    assert uploaded_path.endswith("fig.png")


def test_ignored_asset_uploaded_without_ignore_file(mock_course, course_root, mocker) -> None:
    """Baseline: with no ignore file, the stray file IS uploaded (proves the filter acts)."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    (course_root / "assets" / "~$logo.docx").write_text("junk")

    run_sync(_config(), course_root)

    assert mock_course.upload.call_count == 2


def test_ignored_content_file_not_synced(mock_course, course_root, mocker) -> None:
    """A page matched by .canvasignore is not created on Canvas."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    (course_root / "pages" / "scratch.md").write_text("---\ntitle: Scratch\n---\n\n## Draft\n")
    (course_root / ".canvasignore").write_text("scratch.md\n")

    run_sync(_config(), course_root)

    # Only the syllabus stub is created; scratch.md never reaches Canvas.
    mock_course.create_page.assert_called_once()


# ---------------------------------------------------------------------------
# Rubric sync: _build_criteria_dict
# ---------------------------------------------------------------------------


def test_build_criteria_dict_basic() -> None:
    from github_to_canvas.canvas_api import _build_criteria_dict

    criteria = [
        {
            "description": "Thesis",
            "points": 5,
            "ratings": [
                {"description": "Clear", "points": 5},
                {"description": "Missing", "points": 0},
            ],
        },
    ]
    result = _build_criteria_dict(criteria)
    assert result == {
        "0": {
            "description": "Thesis",
            "points": 5,
            "ratings": {
                "0": {"description": "Clear", "points": 5},
                "1": {"description": "Missing", "points": 0},
            },
        },
    }


def test_build_criteria_dict_long_description_included() -> None:
    from github_to_canvas.canvas_api import _build_criteria_dict

    criteria = [
        {
            "description": "Quality",
            "long_description": "Evaluates overall quality.",
            "points": 10,
            "ratings": [
                {
                    "description": "Excellent",
                    "long_description": "Exceeds expectations.",
                    "points": 10,
                },
                {"description": "Poor", "points": 0},
            ],
        },
    ]
    result = _build_criteria_dict(criteria)
    assert result["0"]["long_description"] == "Evaluates overall quality."
    assert result["0"]["ratings"]["0"]["long_description"] == "Exceeds expectations."
    assert "long_description" not in result["0"]["ratings"]["1"]


def test_build_criteria_dict_empty_long_description_omitted() -> None:
    from github_to_canvas.canvas_api import _build_criteria_dict

    criteria = [
        {
            "description": "Thesis",
            "long_description": "",
            "points": 5,
            "ratings": [{"description": "OK", "long_description": "", "points": 5}],
        },
    ]
    result = _build_criteria_dict(criteria)
    assert "long_description" not in result["0"]
    assert "long_description" not in result["0"]["ratings"]["0"]


# ---------------------------------------------------------------------------
# Rubric sync: sync_rubrics create vs update
# ---------------------------------------------------------------------------


def _mock_rubric(rubric_id: int, title: str) -> MagicMock:
    r = MagicMock()
    r.id = rubric_id
    r.title = title
    return r


def test_sync_rubrics_creates_new() -> None:
    from github_to_canvas.canvas_api import sync_rubrics

    course = MagicMock()
    course.get_rubrics.return_value = []
    new_rubric = _mock_rubric(42, "Essay Rubric")
    course.create_rubric.return_value = {"rubric": new_rubric}

    rubrics = [{"title": "Essay Rubric", "criteria": [{"description": "Thesis", "points": 5, "ratings": []}]}]
    ids, created = sync_rubrics(course, rubrics)

    course.create_rubric.assert_called_once()
    call_kwargs = course.create_rubric.call_args[1]
    assert call_kwargs["rubric"]["title"] == "Essay Rubric"
    assert ids == {"Essay Rubric": 42}
    assert created == ["Essay Rubric"]


def test_sync_rubrics_updates_existing() -> None:
    from github_to_canvas.canvas_api import sync_rubrics

    course = MagicMock()
    course.id = 999
    existing = _mock_rubric(42, "Essay Rubric")
    course.get_rubrics.return_value = [existing]

    rubrics = [{"title": "Essay Rubric", "criteria": [{"description": "Thesis Updated", "points": 10, "ratings": []}]}]
    ids, created = sync_rubrics(course, rubrics)

    course.create_rubric.assert_not_called()
    course._requester.request.assert_called_once()
    call_args = course._requester.request.call_args
    assert call_args[0][0] == "PUT"
    assert "rubrics/42" in call_args[0][1]
    assert ids == {"Essay Rubric": 42}
    assert created == []


def test_sync_rubrics_empty_returns_existing_ids() -> None:
    from github_to_canvas.canvas_api import sync_rubrics

    course = MagicMock()
    existing = _mock_rubric(42, "Essay Rubric")
    course.get_rubrics.return_value = [existing]

    ids, created = sync_rubrics(course, [])
    assert ids == {"Essay Rubric": 42}
    assert created == []
    course.create_rubric.assert_not_called()


def test_sync_rubrics_sends_reusable_and_read_only() -> None:
    from github_to_canvas.canvas_api import sync_rubrics

    course = MagicMock()
    course.get_rubrics.return_value = []
    new_rubric = _mock_rubric(42, "Lab Rubric")
    course.create_rubric.return_value = {"rubric": new_rubric}

    rubrics = [{"title": "Lab Rubric", "reusable": True, "read_only": False, "criteria": []}]
    sync_rubrics(course, rubrics)

    call_kwargs = course.create_rubric.call_args[1]
    assert call_kwargs["rubric"]["reusable"] is True
    assert call_kwargs["rubric"]["read_only"] is False


def test_sync_rubrics_omits_reusable_when_absent() -> None:
    from github_to_canvas.canvas_api import sync_rubrics

    course = MagicMock()
    course.get_rubrics.return_value = []
    new_rubric = _mock_rubric(42, "Lab Rubric")
    course.create_rubric.return_value = {"rubric": new_rubric}

    rubrics = [{"title": "Lab Rubric", "criteria": []}]
    sync_rubrics(course, rubrics)

    call_kwargs = course.create_rubric.call_args[1]
    assert "reusable" not in call_kwargs["rubric"]
    assert "read_only" not in call_kwargs["rubric"]


# ---------------------------------------------------------------------------
# Rubric-assignment association via frontmatter
# ---------------------------------------------------------------------------


def test_rubric_association_by_name(mock_course, course_root, mocker) -> None:
    """Assignment with rubric: 'Name' creates a rubric association."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    rubric = _mock_rubric(42, "Essay Rubric")
    mock_course.get_rubrics.return_value = [rubric]

    (course_root / "assignments" / "week1.md").write_text(
        '---\ntitle: "Week 1"\nrubric: "Essay Rubric"\npublished: true\n---\n\n## Work\n'
    )

    run_sync(_config(), course_root)

    mock_course.create_rubric_association.assert_called_once()
    call_kwargs = mock_course.create_rubric_association.call_args[1]
    assoc = call_kwargs["rubric_association"]
    assert assoc["rubric_id"] == 42
    assert assoc["association_type"] == "Assignment"
    assert assoc["use_for_grading"] is True


def test_rubric_association_by_numeric_id(mock_course, course_root, mocker) -> None:
    """Assignment with rubric: 999 (numeric) uses the ID directly."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)

    (course_root / "assignments" / "week1.md").write_text(
        "---\ntitle: \"Week 1\"\nrubric: 999\npublished: true\n---\n\n## Work\n"
    )

    run_sync(_config(), course_root)

    mock_course.create_rubric_association.assert_called_once()
    call_kwargs = mock_course.create_rubric_association.call_args[1]
    assert call_kwargs["rubric_association"]["rubric_id"] == 999


def test_rubric_unknown_name_warns(mock_course, course_root, mocker, capsys) -> None:
    """Unknown rubric name prints a warning and skips association."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    mock_course.get_rubrics.return_value = []

    (course_root / "assignments" / "week1.md").write_text(
        '---\ntitle: "Week 1"\nrubric: "Nonexistent"\npublished: true\n---\n\n## Work\n'
    )

    run_sync(_config(), course_root)

    mock_course.create_rubric_association.assert_not_called()
    captured = capsys.readouterr()
    assert "Nonexistent" in captured.out
    assert "not found" in captured.out


def test_rubric_use_for_grading_default_true(mock_course, course_root, mocker) -> None:
    """use_for_grading defaults to True when not specified."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    rubric = _mock_rubric(42, "Essay Rubric")
    mock_course.get_rubrics.return_value = [rubric]

    (course_root / "assignments" / "week1.md").write_text(
        '---\ntitle: "Week 1"\nrubric: "Essay Rubric"\npublished: true\n---\n\n## Work\n'
    )

    run_sync(_config(), course_root)

    call_kwargs = mock_course.create_rubric_association.call_args[1]
    assert call_kwargs["rubric_association"]["use_for_grading"] is True


def test_rubric_use_for_grading_false(mock_course, course_root, mocker) -> None:
    """use_for_grading can be explicitly set to false."""
    mocker.patch("github_to_canvas.manifest.flush")
    _setup_first_sync_mocks(mock_course)
    rubric = _mock_rubric(42, "Essay Rubric")
    mock_course.get_rubrics.return_value = [rubric]

    (course_root / "assignments" / "week1.md").write_text(
        '---\ntitle: "Week 1"\nrubric: "Essay Rubric"\nuse_for_grading: false\npublished: true\n---\n\n## Work\n'
    )

    run_sync(_config(), course_root)

    call_kwargs = mock_course.create_rubric_association.call_args[1]
    assert call_kwargs["rubric_association"]["use_for_grading"] is False


# ---------------------------------------------------------------------------
# Front page conditional sync
# ---------------------------------------------------------------------------


def _make_front_page_repo(tmp_path: Path) -> Path:
    """Minimal course repo with front_page in course_settings and a matching page."""
    root = tmp_path / "course"
    root.mkdir()
    cs_dir = root / "course_settings"
    cs_dir.mkdir()
    (cs_dir / "course_settings.toml").write_text(
        'title = "Test"\nfront_page = "pages/home.md"\n'
    )
    pages_dir = root / "pages"
    pages_dir.mkdir()
    (pages_dir / "home.md").write_text(
        '---\ntitle: Home\npublished: true\n---\n\nWelcome!\n'
    )
    return root


def _assert_front_page_set(page: MagicMock) -> None:
    """Assert that set_front_page was called (page.edit with front_page=True)."""
    page.edit.assert_any_call(wiki_page={"front_page": True})


def _front_page_was_set(page: MagicMock) -> bool:
    """Return True if set_front_page was called on the page mock."""
    return call(wiki_page={"front_page": True}) in page.edit.call_args_list


def test_front_page_set_on_first_sync(mock_course, mocker, tmp_path) -> None:
    """On a first sync both course_settings.toml and the page are new, so set_front_page fires."""
    page = _mock_page(111, "home")
    mock_course.create_page.return_value = page
    mock_course.get_page.return_value = page

    root = _make_front_page_repo(tmp_path)
    run_sync(_config(), root)

    _assert_front_page_set(page)


def test_front_page_skipped_when_nothing_changed(mock_course, mocker, tmp_path) -> None:
    """When both course_settings.toml and the page are up-to-date, set_front_page is NOT called."""
    page = _mock_page(111, "home")
    mock_course.create_page.return_value = page
    mock_course.get_page.return_value = page

    root = _make_front_page_repo(tmp_path)

    # First sync — everything is new; manifest is written to disk.
    run_sync(_config(), root)

    # Reset the page mock so we can check the second sync independently.
    page.edit.reset_mock()

    # Mark files as old so needs_sync returns False.
    _make_old(root / "course_settings" / "course_settings.toml")
    _make_old(root / "pages" / "home.md")

    run_sync(_config(), root)

    assert not _front_page_was_set(page), "set_front_page should not have been called"


def test_front_page_set_when_page_resynced(mock_course, mocker, tmp_path) -> None:
    """When the front page's .md file is re-synced, set_front_page fires."""
    page = _mock_page(111, "home")
    mock_course.create_page.return_value = page
    mock_course.get_page.return_value = page

    root = _make_front_page_repo(tmp_path)

    # First sync.
    run_sync(_config(), root)
    page.edit.reset_mock()

    # Mark course_settings as old, but touch the page to make it newer than last_synced.
    _make_old(root / "course_settings" / "course_settings.toml")
    (root / "pages" / "home.md").write_text(
        '---\ntitle: Home\npublished: true\n---\n\nUpdated welcome!\n'
    )

    run_sync(_config(), root)

    _assert_front_page_set(page)


def test_front_page_set_when_settings_resynced(mock_course, mocker, tmp_path) -> None:
    """When course_settings.toml is re-synced, set_front_page fires even if the page hasn't changed."""
    page = _mock_page(111, "home")
    mock_course.create_page.return_value = page
    mock_course.get_page.return_value = page

    root = _make_front_page_repo(tmp_path)

    # First sync.
    run_sync(_config(), root)
    page.edit.reset_mock()

    # Mark the page as old, but rewrite course_settings.toml to make it fresh.
    _make_old(root / "pages" / "home.md")
    (root / "course_settings" / "course_settings.toml").write_text(
        'title = "Test Updated"\nfront_page = "pages/home.md"\n'
    )

    run_sync(_config(), root)

    _assert_front_page_set(page)
