"""Unit tests: ignore-file (.gitignore / .canvasignore) pattern matching."""
from __future__ import annotations

from pathlib import Path

from github_to_canvas.ignore import load_ignore_matcher


def _write(path: Path, text: str) -> None:
    path.write_text(text)


def test_no_ignore_files_matches_nothing(tmp_path: Path) -> None:
    """With no ignore files present, every path is processed (matches nothing)."""
    matcher = load_ignore_matcher(tmp_path)
    f = tmp_path / "pages" / "syllabus.md"
    f.parent.mkdir(parents=True)
    f.touch()
    assert matcher.is_ignored(f, tmp_path) is False


def test_gitignore_patterns_matched(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "*.tmp\n~$*\nbuild/\n")
    matcher = load_ignore_matcher(tmp_path)
    for name in ("a.tmp", "~$report.docx"):
        f = tmp_path / name
        f.touch()
        assert matcher.is_ignored(f, tmp_path) is True
    keep = tmp_path / "keep.md"
    keep.touch()
    assert matcher.is_ignored(keep, tmp_path) is False


def test_directory_only_pattern(tmp_path: Path) -> None:
    """A `build/` pattern matches the directory (and thus prunes its contents)."""
    _write(tmp_path / ".gitignore", "build/\n")
    matcher = load_ignore_matcher(tmp_path)
    d = tmp_path / "build"
    d.mkdir()
    assert matcher.is_ignored(d, tmp_path) is True


def test_canvasignore_layers_on_top(tmp_path: Path) -> None:
    """.canvasignore patterns apply in addition to .gitignore patterns."""
    _write(tmp_path / ".gitignore", "*.tmp\n")
    _write(tmp_path / ".canvasignore", "drafts/\n")
    matcher = load_ignore_matcher(tmp_path)
    tmp_file = tmp_path / "x.tmp"
    tmp_file.touch()
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    assert matcher.is_ignored(tmp_file, tmp_path) is True
    assert matcher.is_ignored(drafts, tmp_path) is True


def test_manifest_always_ignored(tmp_path: Path) -> None:
    """The tool's own manifest is never treated as uploadable content."""
    matcher = load_ignore_matcher(tmp_path)
    manifest = tmp_path / ".canvas-manifest.toml"
    manifest.touch()
    assert matcher.is_ignored(manifest, tmp_path) is True


def test_negation_reincludes(tmp_path: Path) -> None:
    """git-style `!` negation re-includes a previously-ignored path."""
    _write(tmp_path / ".gitignore", "*.log\n!keep.log\n")
    matcher = load_ignore_matcher(tmp_path)
    dropped = tmp_path / "debug.log"
    dropped.touch()
    kept = tmp_path / "keep.log"
    kept.touch()
    assert matcher.is_ignored(dropped, tmp_path) is True
    assert matcher.is_ignored(kept, tmp_path) is False
