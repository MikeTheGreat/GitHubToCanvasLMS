"""Read and write .canvas-manifest.toml."""
from __future__ import annotations

import tomllib
import tomli_w
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ManifestDict = dict[str, dict[str, Any]]


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
