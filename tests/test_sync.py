"""Integration tests: full sync pipeline with mocked canvasapi."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from github_to_canvas.config import Config
from github_to_canvas.sync import parse_frontmatter, parse_module_body, run_sync, run_targeted_sync

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
    p.edit.return_value = p
    return p


def _mock_assignment(canvas_id: int) -> MagicMock:
    a = MagicMock()
    a.id = canvas_id
    a.edit.return_value = a
    return a


def _mock_discussion(canvas_id: int) -> MagicMock:
    d = MagicMock()
    d.id = canvas_id
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
    assert items[0] == {"type": "SubHeader", "title": "Readings"}
    assert items[1]["type"] == "content"
    assert items[1]["local_path"] == "pages/syllabus.md"
    assert items[2] == {"type": "SubHeader", "title": "Work"}
    assert items[3]["local_path"] == "assignments/week1.md"


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
    # Page was still uploaded despite the bad link
    mock_course.create_page.assert_called_once()
    uploaded_body = mock_course.create_page.call_args[1]["wiki_page"]["body"]
    assert "ghost" not in uploaded_body  # tag was removed


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
    assert item_calls[1][1]["module_item"]["content_id"] == 11111
    assert item_calls[2][1]["module_item"]["title"] == "Work"
    assert item_calls[3][1]["module_item"]["content_id"] == 98765
    assert item_calls[4][1]["module_item"]["content_id"] == 55555


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

    run_sync(_config(), course_root)

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

    run_sync(_config(), root)

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
