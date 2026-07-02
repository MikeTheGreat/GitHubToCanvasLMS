import os
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import click
import pypandoc
import requests
from dotenv import find_dotenv, load_dotenv

# load_dotenv() must run before the local package imports below, because .config
# (imported transitively by canvas_api/sync/etc.) reads CANVAS_API_TOKEN from the
# environment at import time. That is why those imports deliberately come after
# this call rather than at the top of the file — ruff's E402 is ignored for this
# file in pyproject.toml for exactly this reason.
load_dotenv(find_dotenv(usecwd=True), override=True, verbose=True)


from .canvas_api import get_course, read_tab_configuration
from .config import load as load_config
from .imscc_import import run_import
from .mv import run_mv
from .orphans import find_orphans, print_report
from .publish import run_publish
from .sync import run_prune, run_sync, run_targeted_sync


# all commands must use die() for user-facing errors — no tracebacks, no raw exceptions.
def die(msg: str) -> None:
    click.secho(f"Error: {msg}", fg="red", err=True)
    sys.exit(1)


def _ensure_pandoc() -> None:
    try:
        pypandoc.get_pandoc_version()
    except OSError:
        die("Pandoc not found. Run `github-to-canvas setup` to install it.")


class _FullHelpGroup(click.Group):
    """Show full summary sentences in the command listing instead of truncating."""

    def format_commands(self, ctx, formatter):
        commands = []
        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None or cmd.hidden:
                continue
            help_text = cmd.get_short_help_str(limit=300)
            commands.append((subcommand, help_text))

        if commands:
            with formatter.section("Commands"):
                formatter.write_dl(commands)


@click.group(cls=_FullHelpGroup, context_settings={"help_option_names": ["-h", "--help"]})
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


def _detect_shell() -> str:
    shell_path = os.environ.get("SHELL", "")
    for name in ("zsh", "fish", "bash"):
        if name in shell_path:
            return name
    return "bash"


def _completion_path(shell: str, prog_name: str) -> Path:
    home = Path.home()
    if shell == "bash":
        return home / ".local/share/bash-completion/completions" / prog_name
    if shell == "zsh":
        return home / ".zfunc" / f"_{prog_name}"
    if shell == "fish":
        return home / ".config/fish/completions" / f"{prog_name}.fish"
    raise ValueError(f"Unsupported shell: {shell}")


@main.command(name="install-completion")
@click.option(
    "--shell",
    type=click.Choice(["bash", "zsh", "fish"]),
    default=None,
    help="Shell to install completion for (auto-detected from $SHELL if omitted).",
)
def install_completion(shell: str | None) -> None:
    """Install shell tab-completion for github-to-canvas."""
    from click.shell_completion import BashComplete, FishComplete, ZshComplete

    if shell is None:
        shell = _detect_shell()

    comp_cls = {"bash": BashComplete, "zsh": ZshComplete, "fish": FishComplete}[shell]

    prog_name = "github-to-canvas"
    complete_var = "_GITHUB_TO_CANVAS_COMPLETE"
    comp = comp_cls(cli=main, ctx_args={}, prog_name=prog_name, complete_var=complete_var)
    script = comp.source()

    dest = _completion_path(shell, prog_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(script)

    click.secho(f"Completion script installed to {dest}", fg="green")
    if shell == "zsh":
        click.echo(
            "Make sure your .zshrc contains:\n"
            '  fpath+=~/.zfunc\n'
            '  autoload -Uz compinit && compinit'
        )
    click.echo("Restart your shell (or open a new tab) to activate.")


@main.command(name="mv", no_args_is_help=True)
@click.argument("src", type=click.Path(path_type=Path))
@click.argument("dest", type=click.Path(path_type=Path))
@click.option(
    "--noop",
    "-n",
    is_flag=True,
    default=False,
    help="Show what would change without making any modifications.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Print each individual change (moved file, updated link, etc.).",
)
def mv_cmd(src: Path, dest: Path, noop: bool, verbose: bool) -> None:
    """Move or rename a file/directory, updating the manifest and all references.

    SRC is the file or directory to move. DEST is the new path.
    Both must be within the same course repo and the same content-type
    directory (e.g. both under pages/, or both under assets/).

    Updates .canvas-manifest.toml, all Markdown cross-references,
    snippet references, and module_order.toml as needed.

    Uses git mv when inside a git repository.
    """
    try:
        run_mv(src, dest, noop=noop, verbose=verbose)
    except ValueError as e:
        die(str(e))
    except subprocess.CalledProcessError as e:
        die(f"git mv failed: {e}")
    except Exception as e:
        die(str(e))


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
def publish(
    course_dir: Path, output_dir: Path
) -> None:
    """Generate a public MkDocs static site from the course repo.

    COURSE_DIR is the course content repo (defaults to the current directory).
    """
    try:
        run_publish(course_dir, output_dir)
    except ValueError as e:
        die(str(e))


@main.command(name="emit-workflow")
@click.argument(
    "course_dir",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
def emit_workflow_cmd(course_dir: Path) -> None:
    """Write a GitHub Actions workflow for publishing to GitHub Pages.

    COURSE_DIR is the course content repo (defaults to the current directory).
    """
    from .publish import emit_workflow

    try:
        emit_workflow(Path(course_dir).resolve())
    except Exception as e:
        die(str(e))


@main.command(name="prune", no_args_is_help=True)
@click.argument(
    "repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--config",
    default=None,
    type=click.Path(path_type=Path),
    help="Path to canvas.toml (default: <repo>/course_settings/canvas.toml)",
)
@click.option(
    "--delete",
    "mode",
    flag_value="delete",
    help="Delete the orphaned items from Canvas.",
)
@click.option(
    "--unpublish",
    "mode",
    flag_value="unpublish",
    help="Unpublish (set published=False) the orphaned items on Canvas.",
)
@click.option(
    "--manifest-only",
    "mode",
    flag_value="manifest",
    help="Remove orphaned entries from the local manifest only; never touch Canvas.",
)
def prune(repo: Path, config: Path | None, mode: str | None) -> None:
    """Delete or unpublish Canvas items whose local source file no longer exists.

    REPO is the course content repo. An item is pruned when its manifest entry's
    local file is gone (deleted or renamed). Exactly one of --delete / --unpublish /
    --manifest-only is required. --manifest-only just drops the stale manifest
    entries without contacting Canvas; the others apply changes immediately.
    """
    if mode is None:
        die("Exactly one of --delete, --unpublish, or --manifest-only is required.")
    if config is None:
        config = repo / "course_settings" / "canvas.toml"
    try:
        cfg = load_config(config)

        click.echo(f"Repo:      {repo.resolve()}")
        click.echo(f"Course ID: {cfg.course_id}  ({cfg.base_url})")

        if mode != "manifest":
            course = get_course(cfg)
            click.echo(f"Course:    {course.name}")

        had_errors = run_prune(cfg, repo, mode)

        if had_errors:
            click.secho(
                "Prune complete; please check warnings listed above.", fg="yellow"
            )
        else:
            click.secho("Prune successful", fg="green")
    except FileNotFoundError as e:
        die(f"Config file not found: {e.filename}")
    except tomllib.TOMLDecodeError as e:
        die(f"Invalid canvas.toml: {e}")
    except (ValueError, KeyError) as e:
        die("KeyError or ValueError:" + str(e))
    except requests.exceptions.ConnectionError:
        die("could not connect to Canvas - are you offline?")
    # unknown errors: let them traceback for debugging


@main.command(name="find-orphans")
@click.argument(
    "repo",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--config",
    default=None,
    type=click.Path(path_type=Path),
    help="Path to canvas.toml (default: <repo>/course_settings/canvas.toml)",
)
def find_orphans_cmd(repo: Path, config: Path | None) -> None:
    """Find Canvas resources not referenced by any other resource in the course.

    Scans all pages, assignments, discussions, and quizzes for internal links,
    checks module item membership, and identifies the front page. Resources
    with zero inbound references are reported.

    REPO is the course content repo (defaults to the current directory).
    """
    if config is None:
        config = repo / "course_settings" / "canvas.toml"
    try:
        cfg = load_config(config)

        click.echo(f"Course ID: {cfg.course_id}  ({cfg.base_url})")

        course = get_course(cfg)
        click.echo(f"Course:    {course.name}")
        click.echo()

        orphans = find_orphans(course)
        print_report(orphans, cfg.base_url)
    except FileNotFoundError as e:
        die(f"Config file not found: {e.filename}")
    except tomllib.TOMLDecodeError as e:
        die(f"Invalid canvas.toml: {e}")
    except (ValueError, KeyError) as e:
        die("KeyError or ValueError:" + str(e))
    except requests.exceptions.ConnectionError:
        die("could not connect to Canvas - are you offline?")
    # unknown errors: let them traceback for debugging


def _parse_canvas_url(url: str) -> tuple[str, int]:
    """Extract (base_url, course_id) from a Canvas course URL.

    Accepts URLs like ``https://school.instructure.com/courses/12345`` or
    ``https://school.instructure.com/courses/12345/rubrics``.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise click.BadParameter(
            f"Not a valid Canvas URL: {url!r}\n"
            "Expected something like https://school.instructure.com/courses/12345"
        )
    parts = parsed.path.strip("/").split("/")
    try:
        idx = parts.index("courses")
        course_id = int(parts[idx + 1])
    except (ValueError, IndexError):
        raise click.BadParameter(
            f"Could not find /courses/<id> in URL: {url!r}\n"
            "Expected something like https://school.instructure.com/courses/12345"
        )
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    return base_url, course_id


def _format_tab_configuration(tab_config: list[dict]) -> str:
    """Format tab_configuration as an inline TOML array of inline tables."""
    lines = ["tab_configuration = ["]
    for entry in tab_config:
        parts = []
        if "label" in entry:
            parts.append(f'label = "{entry["label"]}"')
        parts.append(f'id = "{entry["id"]}"')
        if entry.get("hidden"):
            parts.append("hidden = true")
        lines.append(f"    {{ {', '.join(parts)} }},")
    lines.append("]")
    return "\n".join(lines) + "\n"


@main.command(name="create-tool-aliases", no_args_is_help=True)
@click.argument("course_url")
def create_tool_aliases(course_url: str) -> None:
    """Read navigation tabs from a Canvas course and print a tab_configuration block.

    COURSE_URL is any Canvas URL containing /courses/<id>, e.g.
    https://school.instructure.com/courses/12345 or
    https://school.instructure.com/courses/12345/rubrics.

    The API token is read from the CANVAS_API_TOKEN environment variable.

    The output is a TOML tab_configuration block with external-tool labels
    filled in, ready to paste into course_settings/course_settings.toml.
    """
    from .config import Config

    base_url, course_id = _parse_canvas_url(course_url)
    api_token = os.environ.get("CANVAS_API_TOKEN", "")
    if not api_token:
        die("CANVAS_API_TOKEN environment variable is not set.")

    try:
        cfg = Config(base_url=base_url, course_id=course_id, api_token=api_token)

        click.echo(f"Course ID: {cfg.course_id}  ({cfg.base_url})", err=True)

        course = get_course(cfg)
        click.echo(f"Course:    {course.name}", err=True)

        tab_config = read_tab_configuration(course)
        click.echo(_format_tab_configuration(tab_config))
    except (ValueError, KeyError) as e:
        die(str(e))
    except requests.exceptions.ConnectionError:
        die("could not connect to Canvas - are you offline?")
    # unknown errors: let them traceback for debugging


@main.command(name="list-titles")
@click.argument(
    "repo",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
def list_titles(repo: Path) -> None:
    """List all assignments, discussions, and quizzes with their due dates and file paths.

    REPO is the course content repo (defaults to the current directory).
    Items are sorted by due date (earliest first), then items without
    a due date are listed alphabetically by title.
    """
    from .sync import load_due_dates, find_due_date_override, parse_frontmatter

    repo = repo.resolve()
    due_dates = load_due_dates(repo)

    items: list[tuple[str | None, str, str]] = []  # (due_at, title, path)

    # Assignments
    assignments_dir = repo / "assignments"
    if assignments_dir.exists():
        for md_file in sorted(assignments_dir.rglob("*.md")):
            fm, _ = parse_frontmatter(md_file.read_text())
            title = fm.get("title", md_file.stem)
            override = find_due_date_override(due_dates, title, "assignment")
            due_at = (override or {}).get("due_at") or fm.get("due_at") or None
            rel = md_file.relative_to(repo).as_posix()
            items.append((due_at if due_at else None, title, rel))

    # Discussions
    discussions_dir = repo / "discussions"
    if discussions_dir.exists():
        for md_file in sorted(discussions_dir.rglob("*.md")):
            fm, _ = parse_frontmatter(md_file.read_text())
            title = fm.get("title", md_file.stem)
            override = find_due_date_override(due_dates, title, "discussion")
            due_at = (override or {}).get("due_at") or fm.get("due_at") or None
            rel = md_file.relative_to(repo).as_posix()
            items.append((due_at if due_at else None, title, rel))

    # Quizzes
    quizzes_dir = repo / "quizzes"
    if quizzes_dir.exists():
        for quiz_folder in sorted(d for d in quizzes_dir.iterdir() if d.is_dir()):
            quiz_md = quiz_folder / f"{quiz_folder.name}.md"
            if quiz_md.exists():
                fm, _ = parse_frontmatter(quiz_md.read_text())
                title = fm.get("title", quiz_folder.name)
                override = find_due_date_override(due_dates, title, "quiz")
                due_at = (override or {}).get("due_at") or fm.get("due_at") or None
                rel = quiz_md.relative_to(repo).as_posix()
                items.append((due_at if due_at else None, title, rel))

    if not items:
        click.echo("No assignments, discussions, or quizzes found.")
        return

    # Sort: items with due dates first (by date), then items without (alphabetical by title)
    with_dates = [(d, t, p) for d, t, p in items if d]
    without_dates = [(d, t, p) for d, t, p in items if not d]
    with_dates.sort(key=lambda x: x[0])
    without_dates.sort(key=lambda x: x[1].lower())

    # Calculate column widths
    all_sorted = with_dates + without_dates
    max_title = max(len(t) for _, t, _ in all_sorted)
    max_date = 16  # "YYYY-MM-DD HH:MM"

    for due_at, title, path in all_sorted:
        if due_at:
            date_str = _format_concise_date(due_at)
        else:
            date_str = ""
        click.echo(f"{title:<{max_title}}  {date_str:<{max_date}}  {path}")


def _format_concise_date(iso_date: str) -> str:
    """Format an ISO 8601 date string as 'YYYY-MM-DD HH:MM'."""
    try:
        # Strip trailing timezone info for display
        clean = iso_date.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_date[:16] if len(iso_date) >= 16 else iso_date


@main.command(no_args_is_help=True)
@click.argument(
    "repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--config",
    default=None,
    type=click.Path(path_type=Path),
    help="Path to canvas.toml (default: <repo>/course_settings/canvas.toml)",
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
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Print messages for items that are skipped (up-to-date or newer on Canvas).",
)
def update(
    repo: Path,
    config: Path | None,
    force_uploads: bool,
    force_overwrite: bool,
    target_recursively: str | None,
    single_target: str | None,
    verbose: bool,
) -> None:
    """Sync a Markdown course repo to Canvas LMS."""
    _ensure_pandoc()
    if config is None:
        config = repo / "course_settings" / "canvas.toml"
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
                cfg, repo, recursive_list, single_list, force_uploads, force_overwrite,
                verbose=verbose,
            )
        else:
            had_errors = run_sync(
                cfg, repo, force_uploads=force_uploads, force_overwrite=force_overwrite,
                verbose=verbose,
            )

        if had_errors:
            click.secho(
                "Update complete; please check errors listed above.", fg="yellow"
            )
        else:
            click.secho("Update successful", fg="green")
    except FileNotFoundError as e:
        die(f"Config file not found: {e.filename}")
    except tomllib.TOMLDecodeError as e:
        die(f"Invalid canvas.toml: {e}")
    except (ValueError, KeyError) as e:
        die("KeyError or ValueError:" + str(e))
    except requests.exceptions.ConnectionError:
        die("could not connect to Canvas - are you offline?")
    # unknown errors: let them traceback for debugging
