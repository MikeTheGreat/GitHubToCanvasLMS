import sys
import tomllib
from pathlib import Path

import click
import pypandoc

#
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True, verbose=True)


from .canvas_api import get_course
from .config import load as load_config
from .imscc_import import run_import
from .publish import run_publish
from .sync import run_sync, run_targeted_sync


# all commands must use die() for user-facing errors — no tracebacks, no raw exceptions.
def die(msg: str) -> None:
    click.secho(f"Error: {msg}", fg="red", err=True)
    sys.exit(1)


def _ensure_pandoc() -> None:
    try:
        pypandoc.get_pandoc_version()
    except OSError:
        die("Pandoc not found. Run `github-to-canvas setup` to install it.")


@click.group()
def main() -> None:
    """Manage Canvas LMS course content from a Markdown GitHub repo."""


@main.command(name="setup")
def setup_cmd() -> None:
    """Download Pandoc into the current Python environment."""
    try:
        version = pypandoc.get_pandoc_version()
        path = pypandoc.get_pandoc_path()
        click.echo(f"Pandoc {version} already installed at {path}")
        return
    except OSError:
        pass

    bin_dir = Path(sys.executable).parent
    click.echo(f"Downloading Pandoc into {bin_dir} ...")
    try:
        pypandoc.download_pandoc(targetfolder=str(bin_dir))
        click.secho("Pandoc installed successfully.", fg="green")
    except Exception as e:
        die(f"Failed to download Pandoc: {e}")


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


@main.command(name="publish")
@click.argument(
    "course_dir",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--output-dir",
    default="site",
    type=click.Path(path_type=Path),
    help="Where `mkdocs build` writes the static HTML (default: site/).",
)
@click.option(
    "--deploy",
    is_flag=True,
    default=False,
    help="Run `mkdocs gh-deploy` to push to the gh-pages branch instead of a local build.",
)
@click.option(
    "--emit-workflow",
    is_flag=True,
    default=False,
    help="Write a starter .github/workflows/publish.yml into the course repo.",
)
def publish(course_dir: Path, output_dir: Path, deploy: bool, emit_workflow: bool) -> None:
    """Generate a public MkDocs static site from the course repo.

    COURSE_DIR is the course content repo (defaults to the current directory).
    """
    try:
        run_publish(course_dir, output_dir, deploy=deploy, emit_workflow_flag=emit_workflow)
    except ValueError as e:
        die(str(e))


@main.command(no_args_is_help=True)
@click.argument(
    "repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
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
    "--target-recursively",
    "-t",
    default=None,
    metavar="FILE[,FILE...]",
    help=(
        "Comma-separated files to sync. Each file and all resources it transitively "
        "references are synced (BFS). Skips the full course sync."
    ),
)
@click.option(
    "--single-target",
    "-s",
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
    _ensure_pandoc()
    if config is None:
        config = repo / "canvas.toml"
    try:
        cfg = load_config(config)

        click.echo(f"Repo:      {repo.resolve()}")
        click.echo(f"Course ID: {cfg.course_id}  ({cfg.base_url})")

        course = get_course(cfg)
        click.echo(f"Course:    {course.name}")

        if target_recursively or single_target:
            recursive_list = (
                [p.strip() for p in target_recursively.split(",") if p.strip()]
                if target_recursively
                else []
            )
            single_list = (
                [p.strip() for p in single_target.split(",") if p.strip()]
                if single_target
                else []
            )
            had_errors = run_targeted_sync(
                cfg, repo, recursive_list, single_list, force_uploads, force_overwrite
            )
        else:
            had_errors = run_sync(
                cfg, repo, force_uploads=force_uploads, force_overwrite=force_overwrite
            )

        if had_errors:
            click.secho("Update complete; please check errors listed above.", fg="yellow")
        else:
            click.secho("Update successful", fg="green")
    except FileNotFoundError as e:
        die(f"Config file not found: {e.filename}")
    except tomllib.TOMLDecodeError as e:
        die(f"Invalid canvas.toml: {e}")
    except (ValueError, KeyError) as e:
        die("KeyError or ValueError:" + str(e))
    except Exception as e:
        raise e  # For unknown errors print rich debugging info
