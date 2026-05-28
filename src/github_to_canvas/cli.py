import sys
from pathlib import Path

import click

from .config import load as load_config
from .sync import run_sync


@click.command()
@click.option("--repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Path to the course content repo")
@click.option("--config", default="canvas.toml", show_default=True, type=click.Path(path_type=Path), help="Path to canvas.toml config file")
def main(repo: Path, config: Path) -> None:
    """Sync a Markdown course repo to Canvas LMS."""
    cfg = load_config(config)
    run_sync(cfg, repo)
