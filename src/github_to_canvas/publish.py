"""`publish` subcommand: generate a public MkDocs static site from the course repo.

This module is intentionally split into small, pure-Python pieces (nav building,
content staging, study-guide rendering, mkdocs.yml generation) so they can be
unit-tested without MkDocs being installed.  Only ``run_publish`` shells out to
the ``mkdocs`` CLI.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import yaml

from .convert import preprocess_snippets
from .quiz import parse_question_file
from .sync import parse_frontmatter, parse_module_body


# ---------------------------------------------------------------------------
# Static assets (CSS + theme override)
# ---------------------------------------------------------------------------

EXTRA_CSS = """\
:root {
  --md-primary-fg-color: #2D3B45;        /* Canvas sidebar charcoal */
  --md-primary-fg-color--light: #3d4f5c;
  --md-primary-fg-color--dark:  #1e2a31;
  --md-accent-fg-color: #E66000;         /* Canvas default institution orange */
}

/* Make the nav sidebar background match Canvas's sidebar */
.md-nav--primary .md-nav__title,
[data-md-color-primary="custom"] .md-header {
  background-color: #2D3B45;
  color: #ffffff;
}

/* Active nav item: colored left-border indicator (Canvas style) */
.md-nav__item--active > .md-nav__link {
  border-left: 3px solid #E66000;
  padding-left: calc(0.6rem - 3px);
  color: #E66000;
  font-weight: 600;
}

/* Course name shown at the top of the navigation drawer */
.md-course-name {
  padding: 0.8rem 0.8rem 0.4rem;
  font-weight: 700;
  font-size: 0.9rem;
  color: #ffffff;
  background-color: #2D3B45;
}
"""

# Material customisation hook: prepend the course name to the navigation drawer,
# mirroring how Canvas shows the course name at the top of its sidebar.
OVERRIDES_MAIN = """\
{% extends "base.html" %}

{% block site_nav %}
  <div class="md-course-name">{{ config.site_name }}</div>
  {{ super() }}
{% endblock %}
"""

THEME_FEATURES = [
    "navigation.sections",
    "navigation.indexes",
    "navigation.top",
    "toc.integrate",
    "search.highlight",
    "search.suggest",
]


# ---------------------------------------------------------------------------
# Course name
# ---------------------------------------------------------------------------

def load_site_name(repo: Path) -> str:
    """Course name from course_settings.toml, falling back to the repo dir name."""
    settings_path = repo / "course_settings" / "course_settings.toml"
    if settings_path.exists():
        with settings_path.open("rb") as fh:
            settings = tomllib.load(fh)
        for key in ("title", "name", "course_code"):
            value = settings.get(key)
            if value:
                return str(value)
    return repo.name


# ---------------------------------------------------------------------------
# Path mapping
# ---------------------------------------------------------------------------

# A quiz lives at quizzes/<slug>/<slug>.md in the repo but is flattened to a
# single study-guide file at quizzes/<slug>.md in the published site.
_QUIZ_PATH_RE = re.compile(r"quizzes/([^/)]+)/\1\.md")


def staged_path(local_path: str) -> str:
    """Map a repo-relative content path to its path within the staged docs/ dir."""
    return _QUIZ_PATH_RE.sub(r"quizzes/\1.md", local_path)


def _rewrite_quiz_links(text: str) -> str:
    """Rewrite Markdown links pointing at quiz folders to the flattened quiz file."""
    return _QUIZ_PATH_RE.sub(r"quizzes/\1.md", text)


# ---------------------------------------------------------------------------
# Pandoc → MkDocs Markdown normalisation
# ---------------------------------------------------------------------------

# Matches Pandoc raw-HTML blocks:  ```{=html}\n<content>\n```  →  <content>
_RAW_HTML_BLOCK_RE = re.compile(
    r"^```\{=html\}\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL
)

# Matches Pandoc spans, including one level of nested [link](url):
#   [visible text]{#id .cls key="val"}  →  visible text
_PANDOC_SPAN_RE = re.compile(
    r"\[((?:[^\[\]]|\[[^\]]*\](?:\([^)]*\))?)*)\]"
    r"\{(?=[^}]*(?:#[\w-]|\.[\w-]|\w+=\"))[^}]+\}"
)

# Matches any remaining Pandoc attribute block:  {#id .cls key="val"}
_PANDOC_ATTR_RE = re.compile(
    r"\{(?=[^}]*(?:#[\w-]|\.[\w-]|\w+=\"))[^}]+\}"
)

# Backslash at end of line (Pandoc hard break) → two trailing spaces
_TRAILING_BACKSLASH_RE = re.compile(r"\\[ \t]*$", re.MULTILINE)


def _strip_pandoc_syntax(text: str) -> str:
    """Normalise Pandoc-flavoured Markdown into MkDocs-compatible Markdown.

    Handles raw-HTML blocks, attribute blocks, Pandoc spans, and backslash
    escapes while leaving real fenced code blocks untouched.
    """
    text = _RAW_HTML_BLOCK_RE.sub(r"\1", text)
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        if not in_fence:
            line = _PANDOC_SPAN_RE.sub(r"\1", line)
            line = _PANDOC_ATTR_RE.sub("", line)
            line = line.replace("\\'", "'").replace('\\"', '"')
            line = _TRAILING_BACKSLASH_RE.sub("  ", line)
        lines.append(line)
    return "".join(lines)


# ---------------------------------------------------------------------------
# Content discovery
# ---------------------------------------------------------------------------

CONTENT_DIRS = ["assignments", "discussions", "modules", "pages"]


def _is_published(md_path: Path) -> bool:
    frontmatter, _ = parse_frontmatter(md_path.read_text())
    return frontmatter.get("published", False) is True


def discover_published(repo: Path) -> dict[str, list[tuple[str, str]]]:
    """Discover all published content grouped by content type.

    Returns ``{type_label: [(title, repo_relative_path), ...]}`` for each
    content-type directory that contains at least one published item.
    Items within each type are sorted alphabetically by filename.
    """
    result: dict[str, list[tuple[str, str]]] = {}

    for content_type in CONTENT_DIRS:
        content_dir = repo / content_type
        if not content_dir.exists():
            continue

        items: list[tuple[str, str]] = []
        for md_file in sorted(content_dir.glob("*.md")):
            if not _is_published(md_file):
                continue
            fm, _ = parse_frontmatter(md_file.read_text())
            title = fm.get("title", md_file.stem)
            items.append((title, f"{content_type}/{md_file.name}"))

        if items:
            label = content_type.replace("_", " ").title()
            result[label] = items

    return result


# ---------------------------------------------------------------------------
# Navigation building
# ---------------------------------------------------------------------------

def discover_modules(repo: Path) -> list[Path]:
    """Return module .md files in alphabetical order (matching the sync order)."""
    modules_dir = repo / "modules"
    if not modules_dir.exists():
        return []
    return sorted(modules_dir.glob("*.md"))


def build_nav(repo: Path) -> list[Any]:
    """Build the MkDocs nav tree organised by content type.

    Returns the list assigned to ``mkdocs.yml``'s ``nav:`` key.  Each
    content-type directory that contains at least one ``published: true``
    file gets its own top-level section (Assignments, Discussions, Modules,
    Pages, Quizzes) with published items listed alphabetically.
    """
    nav: list[Any] = [{"Home": "index.md"}]
    published = discover_published(repo)

    for label, items in published.items():
        children: list[Any] = []
        for title, repo_path in items:
            children.append({title: staged_path(repo_path)})
        nav.append({label: children})

    return nav


# ---------------------------------------------------------------------------
# Content staging
# ---------------------------------------------------------------------------

_LEADING_H1_RE = re.compile(r"^\s*#\s+", re.MULTILINE)


def _has_leading_h1(body: str) -> bool:
    for line in body.splitlines():
        if not line.strip():
            continue
        return line.lstrip().startswith("# ")
    return False


def stage_content_markdown(md_path: Path, repo: Path) -> str:
    """Return the published Markdown for a page/assignment/discussion file.

    Strips the tool-specific frontmatter, expands snippet includes, rewrites
    quiz links to the flattened study-guide path, and ensures the document has
    an H1 heading (used as the page title).
    """
    frontmatter, body = parse_frontmatter(md_path.read_text())
    body = preprocess_snippets(body, md_path, repo / "snippets")
    body = _rewrite_quiz_links(body)
    body = _strip_pandoc_syntax(body)
    title = frontmatter.get("title", md_path.stem)
    if not _has_leading_h1(body):
        body = f"# {title}\n\n{body.lstrip()}"
    return body.rstrip() + "\n"


def render_quiz_study_guide(quiz_folder: Path) -> str:
    """Render a quiz folder as a single readable Markdown study guide.

    Shows the quiz description, then each question with its prompt and answer
    choices.  Correct choices are marked; manually-graded questions show none.
    """
    quiz_md = quiz_folder / f"{quiz_folder.name}.md"
    frontmatter, body = parse_frontmatter(quiz_md.read_text())
    title = frontmatter.get("title", quiz_folder.name)

    # Description = quiz body minus the numbered question-link list.
    desc_lines: list[str] = []
    question_files: list[Path] = []
    for line in body.splitlines():
        m = re.match(r"^\s*\d+\.\s+\[[^\]]+\]\(([^)]+\.md)\)\s*$", line)
        if m:
            question_files.append((quiz_md.parent / m.group(1)).resolve())
        else:
            desc_lines.append(line)

    out: list[str] = [f"# {title}", ""]
    desc = "\n".join(desc_lines).strip()
    if desc:
        out.append(desc)
        out.append("")

    for n, q_path in enumerate(question_files, start=1):
        if not q_path.exists():
            continue
        q = parse_question_file(q_path)
        out.append(f"## Question {n}: {q['title']}")
        out.append("")
        if q.get("question_text"):
            # question_text is HTML; Markdown passes raw HTML through unchanged.
            out.append(q["question_text"].strip())
            out.append("")
        answers = q.get("answers") or []
        if answers:
            for a in answers:
                mark = " **(correct)**" if a.get("weight", 0) > 0 else ""
                out.append(f"- {a['text']}{mark}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_module_overview(module_md: Path, repo: Path) -> str:
    """Render a module .md file as a clickable overview/index page."""
    frontmatter, body = parse_frontmatter(module_md.read_text())
    title = frontmatter.get("title", module_md.stem)
    items = parse_module_body(body, module_md, repo)

    out: list[str] = [f"# {title}", ""]
    for item in items:
        if item["type"] == "SubHeader":
            out.append("")
            out.append(f"## {item['title']}")
            out.append("")
        elif item["type"] == "ExternalUrl":
            out.append(f"- [{item['title']}]({item['url']})")
        elif item["type"] == "content":
            # Links are relative to docs/modules/, so step up one level.
            target = "../" + staged_path(item["local_path"])
            out.append(f"- [{item['title']}]({target})")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# mkdocs.yml
# ---------------------------------------------------------------------------

def mkdocs_config(site_name: str, nav: list[Any]) -> dict[str, Any]:
    """Build the mkdocs.yml configuration dict."""
    return {
        "site_name": site_name,
        "theme": {
            "name": "material",
            "custom_dir": "overrides",
            "palette": {"primary": "custom"},
            "features": list(THEME_FEATURES),
        },
        "extra_css": ["stylesheets/extra.css"],
        "nav": nav,
    }


def render_mkdocs_yml(site_name: str, nav: list[Any]) -> str:
    return yaml.safe_dump(
        mkdocs_config(site_name, nav), sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def _load_front_page(repo: Path) -> str | None:
    """Return the repo-relative path named as ``front_page`` in course_settings.toml."""
    settings_path = repo / "course_settings" / "course_settings.toml"
    if not settings_path.exists():
        return None
    with settings_path.open("rb") as fh:
        return tomllib.load(fh).get("front_page")


def _render_index(site_name: str, repo: Path, published: dict[str, list[tuple[str, str]]]) -> str:
    front_page_path = _load_front_page(repo)
    front_page_body = ""
    if front_page_path:
        fp = repo / front_page_path
        if fp.exists() and fp.suffix == ".md":
            front_page_body = stage_content_markdown(fp, repo)

    if front_page_body:
        return front_page_body

    out = [
        f"# {site_name}",
        "",
        f"Welcome to **{site_name}**. Use the navigation on the left to browse "
        "the course content.",
        "",
    ]
    for label, items in published.items():
        out.append(f"## {label}")
        out.append("")
        for title, repo_path in items:
            out.append(f"- [{title}]({staged_path(repo_path)})")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Staging orchestration
# ---------------------------------------------------------------------------

def stage(repo: Path, staging_dir: Path) -> dict[str, Any]:
    """Write the full MkDocs staging tree (mkdocs.yml + docs/ + overrides/).

    Returns a small info dict (site_name, content counts, staged file paths)
    for logging and tests.  Only ``published: true`` content is staged.
    """
    docs = staging_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "stylesheets").mkdir(parents=True, exist_ok=True)
    (staging_dir / "overrides").mkdir(parents=True, exist_ok=True)

    site_name = load_site_name(repo)
    published = discover_published(repo)
    nav = build_nav(repo)

    (docs / "stylesheets" / "extra.css").write_text(EXTRA_CSS)
    (staging_dir / "overrides" / "main.html").write_text(OVERRIDES_MAIN)
    (docs / "index.md").write_text(_render_index(site_name, repo, published))

    assets_src = repo / "assets"
    if assets_src.exists():
        shutil.copytree(assets_src, docs / "assets", dirs_exist_ok=True)

    staged_files: list[str] = []
    for label, items in published.items():
        for title, local_path in items:
            dest_rel = staged_path(local_path)
            dest = docs / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)

            if local_path.startswith("modules/"):
                dest.write_text(render_module_overview(
                    repo / local_path, repo,
                ))
            else:
                src = repo / local_path
                if not src.exists():
                    print(f"  WARNING: content not found, skipping: {local_path}")
                    continue
                dest.write_text(stage_content_markdown(src, repo))

            staged_files.append(dest_rel)
            print(f"  Staging {label.lower()}: {dest_rel}")

    (staging_dir / "mkdocs.yml").write_text(render_mkdocs_yml(site_name, nav))

    return {
        "site_name": site_name,
        "module_count": len(published.get("Modules", [])),
        "staged_files": staged_files,
    }


# ---------------------------------------------------------------------------
# GitHub Actions workflow scaffold
# ---------------------------------------------------------------------------

WORKFLOW_YML = """\
name: Publish course site

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install Pandoc
        run: sudo apt-get update && sudo apt-get install -y pandoc
      - name: Install github-to-canvas
        run: pip install "github-to-canvas[publish] @ git+https://github.com/MikeTheGreat/GitHubToCanvasLMS"
      - name: Build site
        run: github-to-canvas publish .
      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
"""


def _github_pages_url(repo: Path) -> str | None:
    """Derive the GitHub Pages URL from the repo's git remote, or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        url = result.stdout.strip()
    except FileNotFoundError:
        return None
    if not url:
        return None
    # https://github.com/OWNER/REPO.git or git@github.com:OWNER/REPO.git
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return None
    owner, repo_name = m.group(1), m.group(2)
    return f"https://{owner}.github.io/{repo_name}/"


def emit_workflow(repo: Path) -> Path:
    """Write a starter GitHub Actions workflow into the course repo."""
    dest = repo / ".github" / "workflows" / "publish.yml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(WORKFLOW_YML)
    print(f"Wrote workflow: {dest}")
    print()
    print("Before the workflow will run, you need to go to your GitHub")
    print("repo's Settings > Pages and set the source to \"GitHub Actions\".")
    pages_url = _github_pages_url(repo)
    if pages_url:
        print()
        print(f"Once deployed, your site will be at: {pages_url}")
    else:
        print()
        print("Once deployed, your site will be at:")
        print("  https://<your-username>.github.io/<repo-name>/")
    return dest


# ---------------------------------------------------------------------------
# mkdocs invocation
# ---------------------------------------------------------------------------

def _run_mkdocs(args: list[str], staging_dir: Path, cwd: Path) -> None:
    cmd = [sys.executable, "-m", "mkdocs", *args, "-f", str(staging_dir / "mkdocs.yml")]
    print(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=str(cwd), check=True)
    except FileNotFoundError:
        raise ValueError(
            "mkdocs is not installed. Install the publish extra with "
            "`uv tool install github-to-canvas[publish]` (or `pip install mkdocs mkdocs-material`)."
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"mkdocs failed with exit code {exc.returncode}.")


def run_publish(
    course_dir: Path,
    output_dir: Path,
) -> None:
    """Top-level entry point for the `publish` subcommand."""
    repo = Path(course_dir).resolve()
    if not repo.is_dir():
        raise ValueError(f"Course directory not found: {course_dir}")

    staging_dir = Path(tempfile.mkdtemp(prefix="g2c-publish-"))
    print(f"Staging site in: {staging_dir}")
    info = stage(repo, staging_dir)
    print(f"Site: {info['site_name']}  ({info['module_count']} module(s), "
          f"{len(info['staged_files'])} content file(s))")

    out = Path(output_dir).resolve()
    _run_mkdocs(["build", "--site-dir", str(out)], staging_dir, cwd=repo)
    print(f"Built static site: {out}")
