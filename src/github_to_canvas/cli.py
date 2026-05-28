import click


@click.command()
@click.option("--repo", required=True, help="Path to the course content repo")
@click.option("--config", default="canvas.toml", show_default=True, help="Path to canvas.toml config file")
def main(repo: str, config: str) -> None:
    """Sync a Markdown course repo to Canvas LMS."""
    raise NotImplementedError("sync pipeline not yet implemented")
