import sys
import tomllib
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

from .config import load as load_config
from .imscc_import import run_import
from .sync import run_sync, run_targeted_sync

# TODO: all commands must use die() for user-facing errors — no tracebacks, no raw exceptions.


def die(msg: str) -> None:
    click.secho(f"Error: {msg}", fg="red", err=True)
    sys.exit(1)


@click.group()
def main() -> None:
    """Manage Canvas LMS course content from a Markdown GitHub repo."""


@main.command(name="import", no_args_is_help=True)
@click.argument("imscc_path", type=click.Path(exists=True, path_type=Path))
@click.argument("output_dir", type=click.Path(path_type=Path))
def import_cmd(imscc_path: Path, output_dir: Path) -> None:
    """Import a Canvas course from a local .imscc file into a Markdown repo.

    IMSCC_PATH is the path to a .imscc zip file or an already-extracted directory.
    OUTPUT_DIR is where the course repo will be written (must be empty or new).
    """
    try:
        run_import(imscc_path, output_dir)
    except ValueError as e:
        die(str(e))
    except Exception as e:
        die(str(e))


@main.command(no_args_is_help=True)
@click.option(
    "--repo",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the course content repo",
)
@click.option(
    "--config",
    default=None,
    type=click.Path(path_type=Path),
    help="Path to canvas.toml (default: <repo>/canvas.toml)",
)
@click.option(
    "--force-uploads",
    is_flag=True,
    default=False,
    help="Re-upload all files even if unchanged since last sync.",
)
@click.option(
    "--force-overwrite",
    is_flag=True,
    default=False,
    help=(
        "Upload even if Canvas has a newer version. Skips the Canvas timestamp check entirely "
        "(faster; avoids extra API calls)."
    ),
)
@click.option(
    "--target-recursively", "-t",
    default=None,
    metavar="FILE[,FILE...]",
    help=(
        "Comma-separated files to sync. Each file and all resources it transitively "
        "references are synced (BFS). Skips the full course sync."
    ),
)
@click.option(
    "--single-target", "-s",
    default=None,
    metavar="FILE[,FILE...]",
    help=(
        "Comma-separated files to sync without traversing their references. "
        "Runs after --target-recursively; manifest timestamps updated by -t prevent "
        "redundant re-uploads. Skips the full course sync."
    ),
)
def update(
    repo: Path,
    config: Path | None,
    force_uploads: bool,
    force_overwrite: bool,
    target_recursively: str | None,
    single_target: str | None,
) -> None:
    """Sync a Markdown course repo to Canvas LMS."""
    if config is None:
        config = repo / "canvas.toml"
    # try:
    cfg = load_config(config)

    if target_recursively or single_target:
        recursive_list = (
            [p.strip() for p in target_recursively.split(",") if p.strip()]
            if target_recursively else []
        )
        single_list = (
            [p.strip() for p in single_target.split(",") if p.strip()]
            if single_target else []
        )
        run_targeted_sync(cfg, repo, recursive_list, single_list, force_uploads, force_overwrite)
    else:
        run_sync(cfg, repo, force_uploads=force_uploads, force_overwrite=force_overwrite)
    # except FileNotFoundError as e:
    #     die(f"Config file not found: {e.filename}")
    # except tomllib.TOMLDecodeError as e:
    #     die(f"Invalid canvas.toml: {e}")
    # except (ValueError, KeyError) as e:
    #     die("KeyError or ValueError:" + str(e))
    # except Exception as e:
    #     raise e  # For unknown errors print rich debugging info
