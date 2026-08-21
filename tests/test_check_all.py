"""Tests for `update --check-all`: offline dry-run of a fresh full sync.

The defining properties under test:
- the whole pipeline runs (everything reported as it would be on a first sync
  to an empty course), ignoring any on-disk manifest;
- Canvas is never contacted (no canvasapi object is ever constructed);
- nothing is written (.canvas-manifest.toml untouched);
- local problems (broken links, unknown rubric names, ...) are detected and
  flip the error exit.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from markdown_to_canvas.config import Config
from markdown_to_canvas.sync import run_sync

FIXTURES = Path(__file__).parent / "fixtures"
COURSE_ID = 999


def _config() -> Config:
    return Config(
        base_url="https://school.instructure.com",
        course_id=COURSE_ID,
        api_token="",
    )


@pytest.fixture
def course_root(tmp_path: Path) -> Path:
    """Isolated copy of fixtures so tests never write into the fixtures dir."""
    root = tmp_path / "course"
    shutil.copytree(FIXTURES, root, ignore=shutil.ignore_patterns(".canvas-manifest.toml"))
    return root


@pytest.fixture
def no_canvas(mocker) -> None:
    """Fail the test if anything tries to construct a Canvas connection."""
    mocker.patch(
        "markdown_to_canvas.canvas_api.Canvas",
        side_effect=AssertionError("check-all must not contact Canvas"),
    )


# ---------------------------------------------------------------------------
# Core semantics
# ---------------------------------------------------------------------------


def test_check_all_full_run_offline_and_clean(course_root, no_canvas, capsys) -> None:
    """The clean fixture repo passes a full check without touching Canvas."""
    had_errors = run_sync(_config(), course_root, check_all=True)

    assert had_errors is False
    out = capsys.readouterr().out
    assert "CHECK MODE" in out
    # Every content type took the full first-sync path.
    assert "Would upload asset: assets/images/fig.png" in out
    assert "Would upload: pages/syllabus.md" in out
    assert "Would upload: assignments/week1.md" in out
    assert "Would upload: discussions/week1-intro.md" in out
    assert "Would upload: quizzes/a-quiz/a-quiz.md" in out
    assert "Would sync module: modules/week-1.md" in out
    # No manifest was created.
    assert not (course_root / ".canvas-manifest.toml").exists()


def test_check_all_ignores_and_preserves_stale_manifest(course_root, no_canvas, capsys) -> None:
    """A manifest saying "everything is up to date" is ignored (fresh-course
    simulation) and is byte-identical after the run."""
    manifest_path = course_root / ".canvas-manifest.toml"
    manifest_path.write_text(
        '["pages/syllabus.md"]\n'
        'canvas_id = 11111\n'
        'canvas_type = "page"\n'
        'canvas_url = "syllabus"\n'
        'last_synced = "2999-12-31T00:00:00+00:00"\n'
    )
    before = manifest_path.read_bytes()

    run_sync(_config(), course_root, check_all=True)

    out = capsys.readouterr().out
    # Processed despite the future last_synced stamp...
    assert "Would upload: pages/syllabus.md" in out
    # ...and never re-written.
    assert manifest_path.read_bytes() == before


# ---------------------------------------------------------------------------
# Problem detection
# ---------------------------------------------------------------------------


def test_check_all_detects_broken_link(course_root, no_canvas, capsys) -> None:
    (course_root / "pages" / "broken.md").write_text(
        "---\ntitle: Broken\npublished: true\n---\n\n"
        "See [missing](../pages/no-such-page.md).\n"
    )

    had_errors = run_sync(_config(), course_root, check_all=True)

    assert had_errors is True
    out = capsys.readouterr().out
    assert "local file not found" in out
    assert "no-such-page.md" in out


def test_check_all_detects_unknown_rubric(course_root, no_canvas, capsys) -> None:
    """A rubric name with no match in rubrics.toml warns exactly as a real
    fresh sync would (rubric ids come from the simulated rubrics sync)."""
    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    (cs_dir / "rubrics.toml").write_text(
        '[[rubrics]]\ntitle = "Real Rubric"\n'
        '[[rubrics.criteria]]\ndescription = "Quality"\npoints = 5\n'
    )
    (course_root / "assignments" / "rubricked.md").write_text(
        "---\ntitle: Rubricked\nrubric: No Such Rubric\npublished: true\n---\n\nBody.\n"
    )

    had_errors = run_sync(_config(), course_root, check_all=True)

    assert had_errors is True
    out = capsys.readouterr().out
    assert "rubric 'No Such Rubric' not found" in out
    assert "Real Rubric" in out  # the known-rubrics hint lists the defined one


def test_check_all_detects_unknown_assignment_group(course_root, no_canvas, capsys) -> None:
    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    (cs_dir / "course_settings.toml").write_text(
        '[[assignment_groups]]\ntitle = "Homework"\nposition = 1\n'
    )
    (course_root / "assignments" / "grouped.md").write_text(
        "---\ntitle: Grouped\nassignment_group_id: Labs\npublished: true\n---\n\nBody.\n"
    )

    had_errors = run_sync(_config(), course_root, check_all=True)

    assert had_errors is True
    out = capsys.readouterr().out
    assert "assignment group 'Labs' not found" in out
    assert "Homework" in out


def test_check_all_detects_due_dates_entry_matching_nothing(course_root, no_canvas, capsys) -> None:
    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    (cs_dir / "course_settings.toml").write_text(
        '[[due_dates]]\nname = "Ghost Assignment"\ndue_at = "2099-01-01T00:00:00"\n'
    )

    run_sync(_config(), course_root, check_all=True)

    out = capsys.readouterr().out
    assert "due_dates entry 'Ghost Assignment'" in out
    assert "did not match any content file" in out


def test_check_all_detects_title_collision(course_root, no_canvas, capsys) -> None:
    (course_root / "pages" / "dupe.md").write_text(
        "---\ntitle: Syllabus\n---\n\nDuplicate title.\n"
    )

    had_errors = run_sync(_config(), course_root, check_all=True)

    assert had_errors is True
    out = capsys.readouterr().out
    assert "title collision" in out


# ---------------------------------------------------------------------------
# Syllabus links on a fresh course (regression: no-op stub creator KeyError)
# ---------------------------------------------------------------------------


def test_check_all_syllabus_link_gets_stub(course_root, no_canvas, capsys) -> None:
    """course_settings/syllabus.md links to a page not yet synced: on the
    empty-manifest simulation this must stub-create, not KeyError."""
    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    (cs_dir / "syllabus.md").write_text(
        "Welcome! Start with the [Syllabus page](../pages/syllabus.md).\n"
    )

    had_errors = run_sync(_config(), course_root, check_all=True)

    assert had_errors is False
    out = capsys.readouterr().out
    assert "Stub-creating: pages/syllabus.md (referenced from syllabus)" in out


def test_syllabus_link_stub_created_on_real_first_sync(course_root, tmp_path) -> None:
    """Same regression on the real path: sync_syllabus with an empty manifest
    stub-creates the referenced page and rewrites the link to a Canvas URL."""
    from markdown_to_canvas.sync import SyncContext, sync_syllabus

    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    syllabus_md = cs_dir / "syllabus.md"
    syllabus_md.write_text(
        "Welcome! Start with the [Syllabus page](../pages/syllabus.md).\n"
    )

    course = MagicMock()
    stub_page = MagicMock()
    stub_page.page_id = 42424
    stub_page.url = "syllabus-stub"
    course.create_page.return_value = stub_page

    ctx = SyncContext(
        course=course,
        repo_path=course_root,
        snippets_dir=course_root / "snippets",
        manifest={},
        manifest_path=tmp_path / "manifest.toml",
        course_id=COURSE_ID,
        force_uploads=True,
    )
    sync_syllabus(ctx)

    course.create_page.assert_called_once()  # the stub
    course.update.assert_called_once()
    body = course.update.call_args.kwargs["course"]["syllabus_body"]
    assert f"/courses/{COURSE_ID}/pages/syllabus-stub" in body


def test_check_all_syllabus_asset_link_uploads_instead_of_stubbing(
    course_root, no_canvas, capsys
) -> None:
    """A syllabus link to an asset has no stub type in the Canvas API, so it must
    be uploaded outright rather than routed through create_stub (which raises)."""
    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    handout = course_root / "assets" / "syllabus" / "handout.docx"
    handout.parent.mkdir(parents=True, exist_ok=True)
    handout.write_bytes(b"docx bytes")
    (cs_dir / "syllabus.md").write_text(
        "Read the [handout](../assets/syllabus/handout.docx).\n"
    )

    had_errors = run_sync(_config(), course_root, check_all=True)

    assert had_errors is False
    out = capsys.readouterr().out
    assert (
        "Uploading referenced asset: assets/syllabus/handout.docx "
        "(referenced from syllabus)" in out
    )
    assert "Stub-creating: assets/syllabus/handout.docx" not in out


def test_syllabus_asset_uploaded_on_real_first_sync(course_root, tmp_path) -> None:
    """Same regression on the real path: the asset is uploaded through
    course.upload() and the syllabus link points at the returned Canvas URL."""
    from markdown_to_canvas.sync import SyncContext, sync_syllabus

    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    handout = course_root / "assets" / "syllabus" / "handout.docx"
    handout.parent.mkdir(parents=True, exist_ok=True)
    handout.write_bytes(b"docx bytes")
    (cs_dir / "syllabus.md").write_text(
        "Read the [handout](../assets/syllabus/handout.docx).\n"
    )

    course = MagicMock()
    course.upload.return_value = (True, {"id": 9911, "url": "/files/9911/download"})

    ctx = SyncContext(
        course=course,
        repo_path=course_root,
        snippets_dir=course_root / "snippets",
        manifest={},
        manifest_path=tmp_path / "manifest.toml",
        course_id=COURSE_ID,
        force_uploads=True,
    )
    sync_syllabus(ctx)

    course.upload.assert_called_once()
    # uploaded into the folder mirroring its path under assets/, not the root
    assert course.upload.call_args.kwargs["parent_folder_path"] == "course files/syllabus"
    body = course.update.call_args.kwargs["course"]["syllabus_body"]
    assert "/files/9911/download" in body
    assert ctx.manifest["assets/syllabus/handout.docx"]["canvas_id"] == 9911


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _write_canvas_toml(course_root: Path) -> None:
    cs_dir = course_root / "course_settings"
    cs_dir.mkdir(exist_ok=True)
    (cs_dir / "canvas.toml").write_text(
        f'base_url = "https://school.instructure.com"\ncourse_id = {COURSE_ID}\n'
    )


def test_cli_check_all_rejects_targeting_flags(course_root) -> None:
    from markdown_to_canvas.cli import main

    runner = CliRunner()
    result = runner.invoke(
        main, ["update", str(course_root), "--check-all", "-t", "pages/syllabus.md"]
    )
    assert result.exit_code != 0

    result = runner.invoke(
        main, ["update", str(course_root), "--check-all", "--force-uploads"]
    )
    assert result.exit_code != 0


def test_cli_check_all_success_without_token(course_root, no_canvas, monkeypatch) -> None:
    """--check-all needs no API token and exits 0 on a clean repo."""
    from markdown_to_canvas.cli import main

    _write_canvas_toml(course_root)
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)

    runner = CliRunner()
    result = runner.invoke(main, ["update", str(course_root), "--check-all"])

    assert result.exit_code == 0, result.output
    assert "Check successful" in result.output
    assert not (course_root / ".canvas-manifest.toml").exists()


def test_cli_check_all_exit_code_on_problems(course_root, no_canvas, monkeypatch) -> None:
    from markdown_to_canvas.cli import main

    _write_canvas_toml(course_root)
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
    (course_root / "pages" / "broken.md").write_text(
        "---\ntitle: Broken\n---\n\nSee [missing](../pages/no-such-page.md).\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["update", str(course_root), "--check-all"])

    assert result.exit_code == 1
    assert "Check complete; please fix the problems listed above" in result.output


def test_check_all_accepts_canvas_only_module_order_entry(
    course_root, no_canvas, capsys
) -> None:
    """A module_order.toml entry naming a Canvas-only module is not reported
    missing: the simulated course is empty, but the real one has that module."""
    order_path = course_root / "course_settings" / "module_order.toml"
    order_path.parent.mkdir(parents=True, exist_ok=True)
    order_path.write_text(
        'order = ["Getting Started at Cascadia", "week-1.md"]\n'
    )

    had_errors = run_sync(_config(), course_root, check_all=True)

    assert had_errors is False
    out = capsys.readouterr().out
    assert "was found on Canvas" not in out
