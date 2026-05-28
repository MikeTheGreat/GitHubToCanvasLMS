import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

from .config import load as load_config
from .sync import run_sync


@click.group()
def main() -> None:
    """Manage Canvas LMS course content from a Markdown GitHub repo."""


@main.command()
@click.option("--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Path to the course content repo")
@click.option("--config", default=None, type=click.Path(path_type=Path), help="Path to canvas.toml (default: <repo>/canvas.toml)")
def update(repo: Path, config: Path | None) -> None:
    """Sync a Markdown course repo to Canvas LMS."""
    if config is None:
        config = repo / "canvas.toml"
    cfg = load_config(config)
    run_sync(cfg, repo)
