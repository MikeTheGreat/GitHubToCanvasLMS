"""Unit tests: manifest read/write/flush."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from github_to_canvas.manifest import flush, load, needs_sync, record


def test_load_missing_file(tmp_path: Path) -> None:
    assert load(tmp_path / "nonexistent.toml") == {}


def test_load_existing_file(tmp_path: Path) -> None:
    manifest_path = tmp_path / ".canvas-manifest.toml"
    manifest_path.write_bytes(
        b'["pages/syllabus.md"]\ncanvas_id = 11111\ncanvas_type = "page"\n'
    )
    result = load(manifest_path)
    assert result["pages/syllabus.md"]["canvas_id"] == 11111
    assert result["pages/syllabus.md"]["canvas_type"] == "page"


def test_flush_round_trip(tmp_path: Path) -> None:
    manifest_path = tmp_path / ".canvas-manifest.toml"
    data = {
        "pages/syllabus.md": {"canvas_id": 11111, "canvas_type": "page", "last_synced": "2025-01-01T00:00:00+00:00"},
        "assignments/week1.md": {"canvas_id": 98765, "canvas_type": "assignment", "last_synced": "2025-01-01T00:00:01+00:00"},
    }
    flush(manifest_path, data)
    loaded = load(manifest_path)
    assert loaded == data


def test_flush_overwrites_existing(tmp_path: Path) -> None:
    manifest_path = tmp_path / ".canvas-manifest.toml"
    flush(manifest_path, {"pages/old.md": {"canvas_id": 1, "canvas_type": "page", "last_synced": "2025-01-01T00:00:00+00:00"}})
    flush(manifest_path, {"pages/new.md": {"canvas_id": 2, "canvas_type": "page", "last_synced": "2025-01-01T00:00:00+00:00"}})
    loaded = load(manifest_path)
    assert "pages/old.md" not in loaded
    assert loaded["pages/new.md"]["canvas_id"] == 2


def test_record_creates_entry(tmp_path: Path) -> None:
    manifest: dict = {}
    manifest_path = tmp_path / ".canvas-manifest.toml"
    record(manifest, manifest_path, "pages/syllabus.md", 11111, "page")
    assert manifest["pages/syllabus.md"]["canvas_id"] == 11111
    assert manifest["pages/syllabus.md"]["canvas_type"] == "page"
    assert "last_synced" in manifest["pages/syllabus.md"]


def test_record_flushes_immediately(tmp_path: Path) -> None:
    manifest: dict = {}
    manifest_path = tmp_path / ".canvas-manifest.toml"
    record(manifest, manifest_path, "pages/syllabus.md", 11111, "page")
    # disk must reflect the write without a separate flush call
    on_disk = load(manifest_path)
    assert on_disk["pages/syllabus.md"]["canvas_id"] == 11111


def test_record_updates_existing_entry(tmp_path: Path) -> None:
    manifest: dict = {"pages/syllabus.md": {"canvas_id": 99, "canvas_type": "page", "last_synced": "2020-01-01T00:00:00+00:00"}}
    manifest_path = tmp_path / ".canvas-manifest.toml"
    flush(manifest_path, manifest)
    record(manifest, manifest_path, "pages/syllabus.md", 11111, "page")
    assert manifest["pages/syllabus.md"]["canvas_id"] == 11111
    on_disk = load(manifest_path)
    assert on_disk["pages/syllabus.md"]["canvas_id"] == 11111


def test_record_with_extra_fields(tmp_path: Path) -> None:
    manifest: dict = {}
    manifest_path = tmp_path / ".canvas-manifest.toml"
    record(
        manifest,
        manifest_path,
        "modules/week-1.md",
        55555,
        "module",
        extra={"canvas_item_ids": {"pages/syllabus.md": 201, "assignments/week1.md": 202}},
    )
    assert manifest["modules/week-1.md"]["canvas_item_ids"] == {
        "pages/syllabus.md": 201,
        "assignments/week1.md": 202,
    }
    on_disk = load(manifest_path)
    assert on_disk["modules/week-1.md"]["canvas_item_ids"]["pages/syllabus.md"] == 201


def test_record_multiple_entries_all_flushed(tmp_path: Path) -> None:
    manifest: dict = {}
    manifest_path = tmp_path / ".canvas-manifest.toml"
    record(manifest, manifest_path, "pages/a.md", 1, "page")
    record(manifest, manifest_path, "pages/b.md", 2, "page")
    record(manifest, manifest_path, "assignments/c.md", 3, "assignment")
    on_disk = load(manifest_path)
    assert len(on_disk) == 3
    assert on_disk["assignments/c.md"]["canvas_id"] == 3


def test_create_vs_update_lookup(tmp_path: Path) -> None:
    """Manifest presence determines create-vs-update at call site."""
    manifest: dict = {}
    manifest_path = tmp_path / ".canvas-manifest.toml"
    assert "pages/new.md" not in manifest  # → create path
    record(manifest, manifest_path, "pages/new.md", 42, "page")
    assert "pages/new.md" in manifest       # → update path on next run


# ---------------------------------------------------------------------------
# needs_sync
# ---------------------------------------------------------------------------


def _make_file(tmp_path: Path, mtime: float) -> Path:
    f = tmp_path / "a.md"
    f.write_text("")
    os.utime(f, (mtime, mtime))
    return f


_OLD_ENTRY = {"canvas_id": 1, "canvas_type": "page", "last_synced": "2020-01-01T00:00:00+00:00"}
_FUTURE_ENTRY = {"canvas_id": 1, "canvas_type": "page", "last_synced": "2999-12-31T00:00:00+00:00"}


def test_needs_sync_not_in_manifest(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 0.0)
    assert needs_sync({}, "pages/a.md", f) is True


def test_needs_sync_no_last_synced(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 0.0)
    entry = {"canvas_id": 1, "canvas_type": "page"}
    assert needs_sync({"pages/a.md": entry}, "pages/a.md", f) is True


def test_needs_sync_file_newer_than_last_synced(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 1_700_000_000.0)  # Nov 2023
    assert needs_sync({"pages/a.md": _OLD_ENTRY}, "pages/a.md", f) is True


def test_needs_sync_file_older_than_last_synced(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 0.0)  # Unix epoch
    assert needs_sync({"pages/a.md": _FUTURE_ENTRY}, "pages/a.md", f) is False


def test_needs_sync_force_overrides_up_to_date(tmp_path: Path) -> None:
    f = _make_file(tmp_path, 0.0)
    assert needs_sync({"pages/a.md": _FUTURE_ENTRY}, "pages/a.md", f, force=True) is True
