"""Ignore-file support: skip uploading files matched by .canvasignore.

Patterns use git's wildmatch syntax (via the `pathspec` library), so an
optional `.canvasignore` at the repo root governs what is excluded from
Canvas. `.gitignore` is deliberately *not* consulted: this lets a repo
exclude per-term materials from git while still uploading them to Canvas.
Content that should be excluded from both git and Canvas must be listed in
both files.

Matching is purely additive: with no `.canvasignore` present, nothing is
matched and every file is processed exactly as before.
"""
from __future__ import annotations

from pathlib import Path

import pathspec

_IGNORE_FILES = (".canvasignore",)


class IgnoreMatcher:
    """Matches repo-root-relative paths against combined ignore patterns."""

    def __init__(self, spec: pathspec.GitIgnoreSpec) -> None:
        self._spec = spec

    def is_ignored(self, path: Path, repo_root: Path) -> bool:
        """Return True if `path` (a file or dir under `repo_root`) is ignored.

        Directories are matched with a trailing slash so directory-only
        patterns (e.g. `build/`) work the way git intends.
        """
        rel = path.relative_to(repo_root).as_posix()
        if path.is_dir():
            rel += "/"
        return self._spec.match_file(rel)


def load_ignore_matcher(repo_root: Path) -> IgnoreMatcher:
    """Build an IgnoreMatcher from `.canvasignore` at the repo root.

    A missing file contributes no patterns. The result always matches the
    tool's own manifest so it is never treated as uploadable content.
    """
    lines: list[str] = [".canvas-manifest.toml"]
    for name in _IGNORE_FILES:
        ignore_path = repo_root / name
        if ignore_path.exists():
            lines.extend(ignore_path.read_text().splitlines())
    spec = pathspec.GitIgnoreSpec.from_lines(lines)
    return IgnoreMatcher(spec)
