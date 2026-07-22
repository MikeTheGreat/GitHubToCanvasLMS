"""Load and validate canvas.toml configuration."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def find_repo_root(start_path: Path) -> Path | None:
    """Walk up from start_path looking for course_settings/course_settings.toml."""
    current = start_path if start_path.is_dir() else start_path.parent
    while True:
        if (current / "course_settings" / "course_settings.toml").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


@dataclass(frozen=True)
class Config:
    base_url: str
    course_id: int
    api_token: str


def load(path: Path, require_token: bool = True) -> Config:
    """require_token=False is for commands that never contact Canvas
    (e.g. `update --check-all`) but still need base_url/course_id."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    api_token = os.environ.get("CANVAS_API_TOKEN") or data.get("auth", {}).get("api_token", "")
    if not api_token and require_token:
        raise ValueError("Canvas API token not set. Use CANVAS_API_TOKEN env var or canvas.toml [auth] api_token.")

    return Config(
        base_url=data["base_url"].rstrip("/"),
        course_id=int(data["course_id"]),
        api_token=api_token,
    )
