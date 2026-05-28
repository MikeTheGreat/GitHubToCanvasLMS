"""Canvas upload logic via the canvasapi library."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from canvasapi import Canvas

from .config import Config


def get_course(config: Config):
    canvas = Canvas(config.base_url, config.api_token)
    return canvas.get_course(config.course_id)
