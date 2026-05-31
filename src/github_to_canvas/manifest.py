"""Read and write .canvas-manifest.toml."""
from __future__ import annotations

import tomllib
import tomli_w
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ManifestDict = dict[str, dict[str, Any]]


def needs_sync(manifest: ManifestDict, local_key: str, file_path: Path, force: bool = False) -> bool:
    """Return True if the file should be synced to Canvas.

    True when: force=True, no manifest entry, no last_synced, or file mtime is newer than last_synced.
    """
    if force:
        return True
    entry = manifest.get(local_key)
    if entry is None or "last_synced" not in entry:
        return True
    last_synced = datetime.fromisoformat(entry["last_synced"])
    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    return file_mtime > last_synced


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
) -> None:
    """Update an entry and immediately flush to disk."""
    entry: dict[str, Any] = {
        "canvas_id": canvas_id,
        "canvas_type": canvas_type,
        "last_synced": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        entry.update(extra)
    manifest[local_path] = entry
    flush(manifest_path, manifest)
