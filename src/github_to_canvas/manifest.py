"""Read and write .canvas-manifest.toml."""
from __future__ import annotations

import tomllib
import tomli_w
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ManifestDict = dict[str, dict[str, Any]]


def flag_change(
    entry: dict[str, Any], current_flags: dict[str, bool]
) -> tuple[str, bool, bool | None] | None:
    """First recorded course flag whose value no longer matches, if any.

    Compares the entry's ``flags_used`` sub-table (flag values in effect at
    the file's last successful sync) against ``current_flags``. Returns
    ``(name, recorded_value, current_value)`` — current_value is None when
    the flag was deleted from [course_flags] — or None if nothing changed.
    """
    flags_used = entry.get("flags_used") or {}
    for name, recorded in flags_used.items():
        if name not in current_flags:
            return name, bool(recorded), None
        if bool(current_flags[name]) != bool(recorded):
            return name, bool(recorded), bool(current_flags[name])
    return None


def print_flag_change_reason(change: tuple[str, bool, bool | None]) -> None:
    """Verbose-mode explanation for a flag-triggered re-sync."""
    name, recorded, current = change
    if current is None:
        print(f"  re-syncing: flag '{name}' deleted from course_settings.toml")
    else:
        print(
            f"  re-syncing: flag '{name}' changed "
            f"{str(recorded).lower()} → {str(current).lower()}"
        )


def needs_sync(
    manifest: ManifestDict,
    local_key: str,
    file_path: Path,
    force: bool = False,
    extra_mtime_paths: Callable[[], Iterable[Path]] | None = None,
    current_flags: dict[str, bool] | None = None,
    verbose: bool = False,
) -> bool:
    """Return True if the file should be synced to Canvas.

    True when: force=True, no manifest entry, no last_synced, file mtime is newer
    than last_synced, or (if file_path alone isn't newer) any path returned by
    extra_mtime_paths() is newer than last_synced. When ``current_flags`` is
    given, also True when any course flag recorded in the entry's
    ``flags_used`` sub-table is missing from current_flags or differs in value
    (in verbose mode the changed flag is printed as the reason).

    extra_mtime_paths is a zero-arg callable rather than a plain iterable so
    callers can defer the (potentially file-reading) work of computing it —
    it's only invoked when file_path's own mtime didn't already settle the
    question, e.g. to check whether a referenced snippet changed.
    """
    if force:
        return True
    entry = manifest.get(local_key)
    if entry is None or "last_synced" not in entry:
        return True
    last_synced = datetime.fromisoformat(entry["last_synced"])
    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    if file_mtime > last_synced:
        return True
    if extra_mtime_paths is not None and any(
        datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc) > last_synced
        for p in extra_mtime_paths()
    ):
        return True
    if current_flags is not None:
        change = flag_change(entry, current_flags)
        if change is not None:
            if verbose:
                print_flag_change_reason(change)
            return True
    return False


def load(path: Path) -> ManifestDict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return dict(tomllib.load(f))


def flush(path: Path, manifest: ManifestDict) -> None:
    with open(path, "wb") as f:
        tomli_w.dump(manifest, f)


def record(
    manifest: ManifestDict,
    manifest_path: Path,
    local_path: str,
    canvas_id: int,
    canvas_type: str,
    extra: dict[str, Any] | None = None,
    mark_synced: bool = True,
) -> None:
    """Update an entry and immediately flush to disk.

    mark_synced=False records the Canvas object (so a later run updates it
    instead of creating a duplicate) but omits ``last_synced``, leaving the
    entry stale so needs_sync() retries it on the next run — used when the
    upload partially failed.
    """
    entry: dict[str, Any] = {
        "canvas_id": canvas_id,
        "canvas_type": canvas_type,
    }
    if mark_synced:
        entry["last_synced"] = datetime.now(timezone.utc).isoformat()
    if extra:
        entry.update(extra)
    manifest[local_path] = entry
    flush(manifest_path, manifest)
