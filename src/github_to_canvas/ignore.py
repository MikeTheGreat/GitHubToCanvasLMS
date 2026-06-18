"""Ignore-file support: skip uploading files matched by .gitignore / .canvasignore.

Patterns use git's wildmatch syntax (via the `pathspec` library), so a repo's
existing `.gitignore` governs what is uploaded to Canvas, and an optional
`.canvasignore` (same syntax) layers on top for Canvas-only exclusions.

Matching is purely additive: with no ignore files present, nothing is matched
and every file is processed exactly as before.
"""
from __future__ import annotations

from pathlib import Path

import pathspec

_IGNORE_FILES = (".gitignore", ".canvasignore")


class IgnoreMatcher:
    """Matches repo-root-relative paths against combined ignore patterns."""

    def __init__(self, spec: pathspec.PathSpec) -> None:
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
    """Build an IgnoreMatcher from `.gitignore` + `.canvasignore` at the repo root.

    Missing files contribute no patterns. The result always matches the
    tool's own manifest so it is never treated as uploadable content.
    """
    lines: list[str] = [".canvas-manifest.toml"]
    for name in _IGNORE_FILES:
        ignore_path = repo_root / name
        if ignore_path.exists():
            lines.extend(ignore_path.read_text().splitlines())
    spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
    return IgnoreMatcher(spec)
